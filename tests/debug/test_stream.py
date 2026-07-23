import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import nats
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import GatewaySession
from backend.debug import DebugConsumerTooSlow, DebugStreamService
from backend.events import EventType, SessionEvent
from backend.messaging.jetstream import (
    EVENT_STREAM,
    EventStreamSettings,
    JetStreamEventPublisher,
)
from backend.proxy.contracts import ProviderName
from backend.settings import Settings


class CapturingWebSocket:
    def __init__(self) -> None:
        self.events = []

    async def receive(self):
        await asyncio.Future()

    async def send_text(self, value: str) -> None:
        self.events.append(json.loads(value))


@pytest.mark.asyncio
async def test_debug_stream_disconnects_before_its_byte_buffer_can_grow_unbounded() -> None:
    class FakeSubscription:
        async def consumer_info(self):
            return SimpleNamespace(name="ephemeral")

        async def unsubscribe(self) -> None:
            return None

    class FakeJetStream:
        async def delete_consumer(self, stream, consumer) -> None:
            return None

    class FakeClient:
        def jetstream(self):
            return FakeJetStream()

    class FloodingDebugStream(DebugStreamService):
        async def _subscribe(self, session_id, receive):
            await receive(SimpleNamespace(data=b"too-large"))
            return FakeSubscription()

    service = FloodingDebugStream(
        None,  # type: ignore[arg-type]
        FakeClient(),  # type: ignore[arg-type]
        reference_wait_seconds=0,
        max_pending_events=1,
        max_pending_bytes=1,
    )

    with pytest.raises(DebugConsumerTooSlow):
        await service._relay_session(CapturingWebSocket(), uuid4())


@pytest.mark.asyncio
async def test_debug_stream_replays_jetstream_events_in_order_until_session_closes(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    try:
        client = await nats.connect(
            str(Settings().nats_url),
            connect_timeout=0.5,
            allow_reconnect=False,
        )
    except Exception:
        pytest.skip("NATS integration service is not available")
    settings = Settings()
    publisher = await JetStreamEventPublisher.start(
        client,
        EventStreamSettings(
            max_age_seconds=settings.jetstream_event_max_age_seconds,
            max_bytes=10 * 1024 * 1024,
            max_message_bytes=settings.jetstream_event_max_message_bytes,
            duplicate_window_seconds=settings.jetstream_event_duplicate_window_seconds,
            replicas=settings.jetstream_event_replicas,
        ),
    )
    session_id = uuid4()
    reference = uuid4()
    async with database_sessions.begin() as database:
        database.add(
            GatewaySession(
                id=str(session_id),
                owner_id="test",
                lease_token=str(uuid4()),
                client_reference=str(reference),
                requested_settings={},
                state="closed",
            )
        )
    requested = SessionEvent.create(
        EventType.SESSION_OPEN,
        session_id,
        provider=ProviderName.BROWSERLESS,
    )
    closed = SessionEvent.create(
        EventType.SESSION_CLOSED,
        session_id,
        provider=ProviderName.BROWSERLESS,
        payload={"reason": "client_disconnected"},
    )
    try:
        await publisher.publish(requested)
        await publisher.publish(closed)
        websocket = CapturingWebSocket()
        service = DebugStreamService(
            database_sessions,
            client,
            reference_wait_seconds=0.5,
            max_pending_events=10,
            max_pending_bytes=64 * 1024,
        )

        assert await asyncio.wait_for(service.stream(websocket, reference), timeout=2)
        assert [event["event_type"] for event in websocket.events] == [
            "session.open",
            "session.closed",
        ]
    finally:
        await client.jetstream().purge_stream(EVENT_STREAM, subject=requested.subject)
        await client.drain()
