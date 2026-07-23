import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.js import api
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SessionEventRecord
from backend.events import EventType, SessionEvent
from backend.events.contracts import provider_from_storage
from backend.messaging.jetstream import EVENT_STREAM, EVENT_SUBJECT
from backend.proxy.contracts import ProviderName


class ActivityEventFamily(StrEnum):
    SESSION = "session"
    ATTEMPT = "attempt"
    COMMAND = "command"
    NAVIGATION = "navigation"
    PAGE = "page"
    CONSOLE = "console"
    JAVASCRIPT = "javascript"
    PROVIDER = "provider"
    EXECUTION = "execution"


class ActivityEventOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"


_FAILURE_TYPES = {
    EventType.SESSION_FAILED,
    EventType.ATTEMPT_FAILED,
    EventType.COMMAND_FAILED,
    EventType.NAVIGATION_FAILED,
    EventType.PAGE_CRASHED,
    EventType.JAVASCRIPT_EXCEPTION,
    EventType.PROVIDER_DISCONNECTED,
}
_SUCCESS_TYPES = {
    EventType.SESSION_CLOSED,
    EventType.ATTEMPT_CONNECTED,
    EventType.ATTEMPT_CLOSED,
    EventType.NAVIGATION_RESPONSE,
    EventType.PAGE_CONTENT_OBSERVED,
}
_INTERRUPTED_TYPES = {EventType.COMMAND_INTERRUPTED}
_OUTCOME_TYPES = {
    ActivityEventOutcome.FAILURE: _FAILURE_TYPES,
    ActivityEventOutcome.SUCCESS: _SUCCESS_TYPES,
    ActivityEventOutcome.INTERRUPTED: _INTERRUPTED_TYPES,
}


@dataclass(frozen=True, slots=True)
class ActivityEventFilters:
    providers: tuple[ProviderName, ...] = ()
    families: tuple[ActivityEventFamily, ...] = ()
    event_types: tuple[EventType, ...] = ()
    outcomes: tuple[ActivityEventOutcome, ...] = ()
    session_id: UUID | None = None
    attempt_id: UUID | None = None

    def matches(self, event: SessionEvent) -> bool:
        event_type = EventType(event.event_type)
        if self.providers and event.provider not in self.providers:
            return False
        if self.families and activity_event_family(event_type) not in self.families:
            return False
        if self.event_types and event_type not in self.event_types:
            return False
        if self.outcomes and activity_event_outcome(event_type) not in self.outcomes:
            return False
        if self.session_id is not None and event.session_id != self.session_id:
            return False
        return not (self.attempt_id is not None and event.attempt_id != self.attempt_id)


@dataclass(frozen=True, slots=True)
class ActivityEventPage:
    events: list[dict[str, object]]
    next_cursor: str | None


class ActivityConsumerTooSlow(Exception):
    pass


def activity_event_family(event_type: EventType) -> ActivityEventFamily:
    return ActivityEventFamily(event_type.value.partition(".")[0])


def activity_event_outcome(event_type: EventType) -> ActivityEventOutcome | None:
    for outcome, event_types in _OUTCOME_TYPES.items():
        if event_type in event_types:
            return outcome
    return None


def activity_event_dict(event: SessionEvent) -> dict[str, object]:
    event_type = EventType(event.event_type)
    outcome = activity_event_outcome(event_type)
    return {
        "event_id": str(event.event_id),
        "schema_version": event.schema_version,
        "event_type": event.event_type,
        "event_family": activity_event_family(event_type).value,
        "outcome": outcome.value if outcome is not None else None,
        "session_id": str(event.session_id),
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "provider": event.provider.value if event.provider else None,
        "attempt_id": str(event.attempt_id) if event.attempt_id else None,
        "payload": event.payload,
    }


def _encode_cursor(record_id: int) -> str:
    value = base64.urlsafe_b64encode(f"v1:{record_id}".encode()).decode()
    return value.rstrip("=")


def decode_activity_cursor(cursor: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding).decode()
        version, value = decoded.split(":", 1)
        record_id = int(value)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid event cursor") from error
    if version != "v1" or record_id < 1:
        raise ValueError("invalid event cursor")
    return record_id


class ActivityHistoryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def events(
        self,
        filters: ActivityEventFilters,
        *,
        before: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int = 100,
    ) -> ActivityEventPage:
        query = select(SessionEventRecord)
        query = self._apply_filters(query, filters)
        if before is not None:
            query = query.where(SessionEventRecord.id < decode_activity_cursor(before))
        if occurred_after is not None:
            query = query.where(SessionEventRecord.occurred_at >= occurred_after)
        if occurred_before is not None:
            query = query.where(SessionEventRecord.occurred_at <= occurred_before)
        query = query.order_by(SessionEventRecord.id.desc()).limit(limit + 1)

        async with self._sessions() as database:
            rows = list(await database.scalars(query))

        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].id) if has_more and rows else None
        events = [activity_event_dict(self._from_record(row)) for row in reversed(rows)]
        return ActivityEventPage(events=events, next_cursor=next_cursor)

    @staticmethod
    def _apply_filters(
        query: Select[tuple[SessionEventRecord]],
        filters: ActivityEventFilters,
    ) -> Select[tuple[SessionEventRecord]]:
        if filters.providers:
            query = query.where(
                SessionEventRecord.provider.in_([provider.value for provider in filters.providers])
            )
        if filters.families:
            query = query.where(
                or_(
                    *[
                        SessionEventRecord.event_type.startswith(f"{family.value}.")
                        for family in filters.families
                    ]
                )
            )
        if filters.event_types:
            query = query.where(
                SessionEventRecord.event_type.in_(
                    [event_type.value for event_type in filters.event_types]
                )
            )
        if filters.outcomes:
            outcome_types = {
                event_type.value
                for outcome in filters.outcomes
                for event_type in _OUTCOME_TYPES[outcome]
            }
            query = query.where(SessionEventRecord.event_type.in_(outcome_types))
        if filters.session_id is not None:
            query = query.where(SessionEventRecord.session_id == str(filters.session_id))
        if filters.attempt_id is not None:
            query = query.where(SessionEventRecord.attempt_id == str(filters.attempt_id))
        return query

    @staticmethod
    def _from_record(row: SessionEventRecord) -> SessionEvent:
        return SessionEvent(
            event_id=row.event_id,
            schema_version=row.schema_version,
            event_type=row.event_type,
            session_id=UUID(row.session_id),
            occurred_at=row.occurred_at,
            provider=provider_from_storage(row.provider),
            attempt_id=UUID(row.attempt_id) if row.attempt_id else None,
            payload=row.payload,
        )


class ActivityStreamService:
    def __init__(
        self,
        client: NatsClient,
        *,
        max_pending_events: int,
        max_pending_bytes: int,
        initial_replay_seconds: int = 30,
        heartbeat_seconds: float = 10,
    ) -> None:
        self._client = client
        self._max_pending_events = max_pending_events
        self._max_pending_bytes = max_pending_bytes
        self._initial_replay_seconds = initial_replay_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def events(
        self,
        filters: ActivityEventFilters,
        *,
        after_sequence: int | None = None,
    ) -> AsyncIterator[str]:
        if after_sequence is not None:
            stream = await self._client.jetstream().stream_info(EVENT_STREAM)
            if after_sequence > stream.state.last_seq:
                yield self._sse(
                    "replay-unavailable",
                    {"reason": "stream_recreated"},
                    event_id=None,
                )
                return
            if stream.state.messages > 0 and after_sequence < stream.state.first_seq - 1:
                yield self._sse(
                    "replay-unavailable",
                    {"reason": "cursor_expired"},
                    event_id=None,
                )
                return

        queue: asyncio.Queue[Msg] = asyncio.Queue(maxsize=self._max_pending_events)
        overflow = asyncio.Event()
        pending_bytes = 0

        async def receive(message: Msg) -> None:
            nonlocal pending_bytes
            size = len(message.data)
            if pending_bytes + size > self._max_pending_bytes:
                overflow.set()
                return
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                overflow.set()
            else:
                pending_bytes += size

        subscription = await self._subscribe(receive, after_sequence=after_sequence)
        last_sequence = after_sequence
        last_cursor_emitted_at = asyncio.get_running_loop().time()
        try:
            while True:
                message_task = asyncio.create_task(queue.get())
                overflow_task = asyncio.create_task(overflow.wait())
                timeout_task = asyncio.create_task(asyncio.sleep(self._heartbeat_seconds))
                waiters = {message_task, overflow_task, timeout_task}
                done: set[asyncio.Task[object]] = set()
                try:
                    done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in waiters:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)

                if overflow_task in done:
                    raise ActivityConsumerTooSlow
                if timeout_task in done:
                    yield self._sse("heartbeat", {}, event_id=last_sequence)
                    last_cursor_emitted_at = asyncio.get_running_loop().time()
                    continue

                message = await message_task
                pending_bytes -= len(message.data)
                with suppress(Exception):
                    last_sequence = message.metadata.sequence.stream
                try:
                    event = SessionEvent.from_json(message.data)
                except Exception:
                    continue
                if filters.matches(event):
                    yield self._sse(
                        "harbor-event",
                        activity_event_dict(event),
                        event_id=last_sequence,
                    )
                    last_cursor_emitted_at = asyncio.get_running_loop().time()
                elif (
                    asyncio.get_running_loop().time() - last_cursor_emitted_at
                    >= self._heartbeat_seconds
                ):
                    yield self._sse("cursor", {}, event_id=last_sequence)
                    last_cursor_emitted_at = asyncio.get_running_loop().time()
        finally:
            consumer_name = None
            with suppress(Exception):
                consumer_name = (await subscription.consumer_info()).name
            with suppress(Exception):
                await subscription.unsubscribe()
            if consumer_name is not None:
                with suppress(Exception):
                    await self._client.jetstream().delete_consumer(
                        EVENT_STREAM,
                        consumer_name,
                    )

    async def _subscribe(
        self,
        receive: Callable[[Msg], Awaitable[None]],
        *,
        after_sequence: int | None,
    ):
        if after_sequence is not None and after_sequence < 0:
            raise ValueError("Last-Event-ID must be a non-negative JetStream sequence")
        config = api.ConsumerConfig()
        if after_sequence is not None:
            config.deliver_policy = api.DeliverPolicy.BY_START_SEQUENCE
            config.opt_start_seq = after_sequence + 1
        else:
            config.deliver_policy = api.DeliverPolicy.BY_START_TIME
            config.opt_start_time = datetime.now(UTC) - timedelta(
                seconds=self._initial_replay_seconds
            )
        return await self._client.jetstream().subscribe(
            EVENT_SUBJECT,
            cb=receive,
            stream=EVENT_STREAM,
            config=config,
            ordered_consumer=True,
            inactive_threshold=30,
            pending_msgs_limit=self._max_pending_events,
            pending_bytes_limit=self._max_pending_bytes,
        )

    @staticmethod
    def _sse(
        event: str,
        data: dict[str, object],
        *,
        event_id: int | None,
    ) -> str:
        fields = []
        if event_id is not None:
            fields.append(f"id: {event_id}")
        fields.extend(
            (
                f"event: {event}",
                f"data: {json.dumps(data, separators=(',', ':'), sort_keys=True)}",
                "",
                "",
            )
        )
        return "\n".join(fields)
