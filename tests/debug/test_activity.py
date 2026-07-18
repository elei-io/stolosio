import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import GatewaySession, SessionEventRecord
from backend.debug import (
    ActivityEventFamily,
    ActivityEventFilters,
    ActivityEventOutcome,
    ActivityHistoryService,
    ActivityStreamService,
)
from backend.events import EventType, SessionEvent
from backend.proxy.contracts import ProviderName


async def _record_events(
    database_sessions: async_sessionmaker[AsyncSession],
    *events: SessionEvent,
) -> None:
    session_ids = {event.session_id for event in events}
    async with database_sessions.begin() as database:
        for session_id in session_ids:
            database.add(
                GatewaySession(
                    id=str(session_id),
                    owner_id="test",
                    lease_token=str(uuid4()),
                    requested_settings={},
                    state="closed",
                )
            )
        for event in events:
            database.add(
                SessionEventRecord(
                    event_id=event.event_id,
                    schema_version=event.schema_version,
                    session_id=str(event.session_id),
                    event_type=event.event_type,
                    attempt_id=str(event.attempt_id) if event.attempt_id else None,
                    provider=event.provider.value if event.provider else None,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                )
            )


@pytest.mark.asyncio
async def test_activity_history_filters_and_pages_events(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    events = [
        SessionEvent.create(
            EventType.SESSION_OPEN,
            session_id,
            provider=ProviderName.CHROMIUM,
        ),
        SessionEvent.create(
            EventType.COMMAND_FAILED,
            session_id,
            provider=ProviderName.CHROMIUM,
            payload={"command_id": 1, "method": "Page.printToPDF", "reason": "unsupported"},
        ),
        SessionEvent.create(
            EventType.SESSION_CLOSED,
            session_id,
            provider=ProviderName.CHROMIUM,
        ),
    ]
    await _record_events(database_sessions, *events)
    history = ActivityHistoryService(database_sessions)

    first_page = await history.events(ActivityEventFilters(), limit=2)
    assert [event["event_type"] for event in first_page.events] == [
        "command.failed",
        "session.closed",
    ]
    assert first_page.next_cursor is not None

    second_page = await history.events(
        ActivityEventFilters(),
        before=first_page.next_cursor,
        limit=2,
    )
    assert [event["event_type"] for event in second_page.events] == ["session.open"]

    failures = await history.events(
        ActivityEventFilters(
            providers=(ProviderName.CHROMIUM,),
            families=(ActivityEventFamily.COMMAND,),
            outcomes=(ActivityEventOutcome.FAILURE,),
        )
    )
    assert len(failures.events) == 1
    assert failures.events[0]["outcome"] == "failure"
    assert failures.events[0]["session_id"] == str(session_id)


@pytest.mark.asyncio
async def test_activity_history_treats_retired_unknown_provider_as_unbound(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    event = SessionEvent.create(EventType.SESSION_REQUESTED, session_id)
    async with database_sessions.begin() as database:
        database.add(
            GatewaySession(
                id=str(session_id),
                owner_id="test",
                lease_token=str(uuid4()),
                requested_settings={},
                state="closed",
            )
        )
        database.add(
            SessionEventRecord(
                event_id=event.event_id,
                schema_version=event.schema_version,
                session_id=str(session_id),
                event_type=event.event_type,
                provider="unknown",
                occurred_at=event.occurred_at,
                payload=event.payload,
            )
        )

    page = await ActivityHistoryService(database_sessions).events(
        ActivityEventFilters()
    )

    assert page.events[0]["provider"] is None


@pytest.mark.asyncio
async def test_activity_stream_filters_events_without_losing_sequence_position() -> None:
    session_id = uuid4()
    ignored = SessionEvent.create(
        EventType.SESSION_OPEN,
        session_id,
        provider=ProviderName.CAMOUFOX,
    )
    matched = SessionEvent.create(
        EventType.ATTEMPT_FAILED,
        session_id,
        provider=ProviderName.CHROMIUM,
        payload={"reason": "provider_connection_failed"},
    )

    class FakeSubscription:
        async def consumer_info(self):
            return SimpleNamespace(name="activity-test")

        async def unsubscribe(self):
            return None

    class FakeJetStream:
        async def delete_consumer(self, stream, consumer):
            return None

    class FakeClient:
        def jetstream(self):
            return FakeJetStream()

    class TestActivityStream(ActivityStreamService):
        async def _subscribe(self, receive, *, after_sequence):
            for sequence, event in enumerate((ignored, matched), start=41):
                await receive(
                    SimpleNamespace(
                        data=event.to_json(),
                        metadata=SimpleNamespace(
                            sequence=SimpleNamespace(stream=sequence)
                        ),
                    )
                )
            return FakeSubscription()

    stream = TestActivityStream(
        FakeClient(),  # type: ignore[arg-type]
        max_pending_events=10,
        max_pending_bytes=10_000,
        heartbeat_seconds=60,
    ).events(
        ActivityEventFilters(providers=(ProviderName.CHROMIUM,)),
    )
    try:
        event_frame = await anext(stream)
    finally:
        await stream.aclose()

    assert event_frame.startswith("id: 42\nevent: harbor-event")
    payload = json.loads(event_frame.split("data: ", 1)[1])
    assert payload["event_type"] == "attempt.failed"
    assert payload["outcome"] == "failure"


@pytest.mark.asyncio
async def test_activity_stream_reports_an_expired_resume_cursor() -> None:
    class FakeJetStream:
        async def stream_info(self, stream):
            return SimpleNamespace(
                state=SimpleNamespace(messages=10, first_seq=50, last_seq=59)
            )

    class FakeClient:
        def jetstream(self):
            return FakeJetStream()

    stream = ActivityStreamService(
        FakeClient(),  # type: ignore[arg-type]
        max_pending_events=10,
        max_pending_bytes=10_000,
    ).events(ActivityEventFilters(), after_sequence=12)

    assert await anext(stream) == (
        'event: replay-unavailable\ndata: {"reason":"cursor_expired"}\n\n'
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


def test_activity_filters_match_registered_event_contract() -> None:
    event = SessionEvent.create(
        EventType.NAVIGATION_FAILED,
        uuid4(),
        provider=ProviderName.LIGHTPANDA,
        occurred_at=datetime.now(UTC),
    )

    assert ActivityEventFilters(
        providers=(ProviderName.LIGHTPANDA,),
        families=(ActivityEventFamily.NAVIGATION,),
        outcomes=(ActivityEventOutcome.FAILURE,),
    ).matches(event)
    assert not ActivityEventFilters(
        providers=(ProviderName.CHROMIUM,),
    ).matches(event)
