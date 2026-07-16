import asyncio
from uuid import uuid4

import nats
import pytest

from backend.events import EventType, SessionEvent
from backend.messaging.jetstream import (
    EVENT_STREAM,
    EventStreamSettings,
    JetStreamEventPublisher,
)
from backend.settings import Settings


async def event_publisher():
    settings = Settings()
    try:
        client = await nats.connect(
            str(settings.nats_url),
            connect_timeout=0.5,
            allow_reconnect=False,
        )
    except Exception:
        pytest.skip("NATS integration service is not available")
    publisher = await JetStreamEventPublisher.start(
        client,
        EventStreamSettings(
            max_age_seconds=settings.jetstream_event_max_age_seconds,
            max_bytes=settings.jetstream_event_max_bytes,
            max_message_bytes=settings.jetstream_event_max_message_bytes,
            duplicate_window_seconds=(settings.jetstream_event_duplicate_window_seconds),
            replicas=settings.jetstream_event_replicas,
        ),
    )
    return client, publisher


@pytest.mark.asyncio
async def test_one_publish_reaches_live_and_durable_consumers() -> None:
    client, publisher = await event_publisher()
    event = SessionEvent.create(EventType.SESSION_OPEN, uuid4())
    live = await client.subscribe(event.subject)
    jetstream = client.jetstream()
    durable = f"test-{uuid4().hex}"
    subscription = await jetstream.pull_subscribe(event.subject, durable=durable)
    try:
        await publisher.publish(event)

        live_message = await live.next_msg(timeout=1)
        durable_messages = await subscription.fetch(1, timeout=1)

        assert SessionEvent.from_json(live_message.data) == event
        assert SessionEvent.from_json(durable_messages[0].data) == event
        await durable_messages[0].ack()
    finally:
        await live.unsubscribe()
        await subscription.unsubscribe()
        await jetstream.delete_consumer(EVENT_STREAM, durable)
        await jetstream.purge_stream(EVENT_STREAM, subject=event.subject)
        await client.drain()


@pytest.mark.asyncio
async def test_stable_message_id_deduplicates_retry() -> None:
    client, publisher = await event_publisher()
    event = SessionEvent.create(EventType.SESSION_OPEN, uuid4())
    jetstream = client.jetstream()
    durable = f"test-{uuid4().hex}"
    subscription = await jetstream.pull_subscribe(event.subject, durable=durable)
    try:
        await publisher.publish(event)
        await publisher.publish(event)
        messages = await subscription.fetch(1, timeout=1)
        await messages[0].ack()
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            await subscription.fetch(1, timeout=0.1)
    finally:
        await subscription.unsubscribe()
        await jetstream.delete_consumer(EVENT_STREAM, durable)
        await jetstream.purge_stream(EVENT_STREAM, subject=event.subject)
        await client.drain()
