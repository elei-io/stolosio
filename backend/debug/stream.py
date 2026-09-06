import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from uuid import UUID

from fastapi import WebSocket
from nats.aio.client import Client as NatsClient
from nats.aio.msg import Msg
from nats.js import api
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from backend.db.models import GatewaySession
from backend.debug.timeline import HistoricalDebugTimeline
from backend.events import EventType, SessionEvent
from backend.messaging.jetstream import EVENT_STREAM
from backend.proxy.contracts import SessionState


class DebugSessionNotFound(Exception):
    pass


class DebugConsumerTooSlow(Exception):
    pass


class DebugStreamService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: NatsClient,
        *,
        reference_wait_seconds: float,
        max_pending_events: int,
        max_pending_bytes: int,
    ) -> None:
        self._sessions = sessions
        self._client = client
        self._reference_wait_seconds = reference_wait_seconds
        self._max_pending_events = max_pending_events
        self._max_pending_bytes = max_pending_bytes

    async def stream(self, websocket: WebSocket, reference: UUID) -> bool:
        disconnected = asyncio.create_task(self._wait_for_disconnect(websocket))
        resolution = asyncio.create_task(self._resolve(reference))
        try:
            done, _ = await asyncio.wait(
                {disconnected, resolution},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            disconnected.cancel()
            resolution.cancel()
            await asyncio.gather(disconnected, resolution, return_exceptions=True)
            raise
        if disconnected in done:
            resolution.cancel()
            await asyncio.gather(resolution, return_exceptions=True)
            return False

        try:
            session_id = await resolution
        except Exception:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            raise
        if session_id is None:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)
            raise DebugSessionNotFound

        try:
            terminal = asyncio.create_task(self._relay_session(websocket, UUID(session_id)))
            try:
                done, pending = await asyncio.wait(
                    {disconnected, terminal},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                terminal.cancel()
                await asyncio.gather(terminal, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if terminal in done:
                await terminal
                return True
            return False
        finally:
            if not disconnected.done():
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)

    async def _resolve(self, reference: UUID) -> str | None:
        deadline = asyncio.get_running_loop().time() + self._reference_wait_seconds
        while True:
            async with self._sessions() as database:
                session_id = await database.scalar(
                    select(GatewaySession.id).where(
                        GatewaySession.client_reference == str(reference),
                        GatewaySession.state.in_(
                            (
                                SessionState.OPEN.value,
                                SessionState.CLOSING.value,
                                SessionState.CLOSED.value,
                                SessionState.FAILED.value,
                            )
                        ),
                    )
                )
            if session_id is not None:
                return session_id
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(0.1, remaining))

    async def _relay_session(self, websocket: WebSocket, session_id: UUID) -> None:
        queue: asyncio.Queue[Msg] = asyncio.Queue(maxsize=self._max_pending_events)
        overflow = asyncio.Event()
        pending_bytes = 0

        async def receive(message: Msg) -> None:
            nonlocal pending_bytes
            message_size = len(message.data)
            if pending_bytes + message_size > self._max_pending_bytes:
                overflow.set()
                return
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                overflow.set()
            else:
                pending_bytes += message_size

        subscription = await self._subscribe(session_id, receive)
        terminal_types = {EventType.SESSION_CLOSED, EventType.SESSION_FAILED}
        seen: set[UUID] = set()
        try:
            if self._sessions is not None:
                history = await HistoricalDebugTimeline(self._sessions).events(session_id)
                for event in history:
                    seen.add(event.event_id)
                    await websocket.send_text(event.to_json().decode())
                    if EventType(event.event_type) in terminal_types:
                        return
            while True:
                next_message = asyncio.create_task(queue.get())
                fell_behind = asyncio.create_task(overflow.wait())
                waiters = {next_message, fell_behind}
                done = set()
                try:
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for task in waiters:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
                if fell_behind in done:
                    raise DebugConsumerTooSlow
                message = await next_message
                pending_bytes -= len(message.data)
                try:
                    event = SessionEvent.from_json(message.data)
                except Exception:
                    continue
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                await websocket.send_text(event.to_json().decode())
                if EventType(event.event_type) in terminal_types:
                    return
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
        session_id: UUID,
        receive: Callable[[Msg], Awaitable[None]],
    ):
        jetstream = self._client.jetstream()
        return await jetstream.subscribe(
            f"stolosio.v1.events.session.{session_id}",
            cb=receive,
            stream=EVENT_STREAM,
            ordered_consumer=True,
            deliver_policy=api.DeliverPolicy.ALL,
            inactive_threshold=30,
            pending_msgs_limit=self._max_pending_events,
            pending_bytes_limit=self._max_pending_bytes,
        )

    @staticmethod
    async def _wait_for_disconnect(websocket: WebSocket) -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            return
