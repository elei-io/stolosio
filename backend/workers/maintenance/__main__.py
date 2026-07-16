import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

import nats
from nats.js import api
from nats.js.errors import NotFoundError
from prometheus_client import start_http_server

from backend.db.session import engine, session_factory
from backend.events import LifecycleOutboxPublisher, SessionEvent
from backend.messaging.jetstream import (
    EVENT_STREAM,
    EVENT_SUBJECT,
    EventStreamSettings,
    JetStreamEventPublisher,
)
from backend.metrics import REGISTRY, InstrumentedEventPublisher
from backend.metrics.definitions import (
    EVENT_DEAD_LETTERS,
    EVENT_RECORDER_EVENTS,
    EVENT_RECORDER_LAG,
    EVENT_RECORDER_PENDING,
    RETENTION_DELETED_ROWS,
    RETENTION_DURATION,
)
from backend.settings import settings
from backend.workers.maintenance.recorder import EventRecorder
from backend.workers.maintenance.retention import RetentionJob

logger = logging.getLogger(__name__)
_RECORDER = "harbor-recorder-v1"
_DEAD_STREAM = "HARBOR_DEAD_LETTERS"
_DEAD_SUBJECT = "harbor.v1.dead_letters"


async def _ensure_dead_letters(jetstream) -> None:
    config = api.StreamConfig(
        name=_DEAD_STREAM,
        subjects=[_DEAD_SUBJECT],
        retention=api.RetentionPolicy.LIMITS,
        storage=api.StorageType.FILE,
        discard=api.DiscardPolicy.OLD,
        max_msgs=10_000,
        max_age=30 * 86_400,
        num_replicas=settings.jetstream_event_replicas,
    )
    try:
        await jetstream.stream_info(_DEAD_STREAM)
    except NotFoundError:
        await jetstream.add_stream(config=config)
    else:
        await jetstream.update_stream(config=config)


async def _reject_message(
    jetstream,
    message,
    *,
    event_id: str,
    schema_version: str,
    reason: str,
) -> None:
    delivered = message.metadata.num_delivered if message.metadata else 1
    if delivered < settings.event_recorder_max_deliver:
        await message.nak(delay=1)
        return
    dead_letter = json.dumps(
        {
            "event_id": event_id,
            "schema_version": schema_version,
            "subject": message.subject,
            "reason": reason,
        },
        separators=(",", ":"),
    ).encode()
    await jetstream.publish(_DEAD_SUBJECT, dead_letter)
    EVENT_DEAD_LETTERS.labels(reason).inc()
    await message.term()


async def _record_loop(jetstream, recorder: EventRecorder) -> None:
    consumer = api.ConsumerConfig(
        durable_name=_RECORDER,
        name=_RECORDER,
        deliver_policy=api.DeliverPolicy.ALL,
        ack_policy=api.AckPolicy.EXPLICIT,
        ack_wait=settings.event_recorder_ack_wait_seconds,
        max_deliver=settings.event_recorder_max_deliver,
        filter_subject=EVENT_SUBJECT,
    )
    subscription = await jetstream.pull_subscribe(
        EVENT_SUBJECT,
        durable=_RECORDER,
        stream=EVENT_STREAM,
        config=consumer,
    )
    while True:
        try:
            messages = await subscription.fetch(
                settings.event_recorder_batch_size,
                timeout=settings.event_recorder_fetch_timeout_seconds,
            )
        except TimeoutError:
            continue

        valid = []
        valid_messages = []
        for message in messages:
            try:
                valid.append(SessionEvent.from_json(message.data))
                valid_messages.append(message)
            except Exception:
                EVENT_RECORDER_EVENTS.labels("invalid").inc()
                event_id = "unknown"
                schema_version = "unknown"
                with suppress(Exception):
                    raw = json.loads(message.data)
                    event_id = str(raw.get("event_id", "unknown"))
                    schema_version = str(raw.get("schema_version", "unknown"))
                await _reject_message(
                    jetstream,
                    message,
                    event_id=event_id,
                    schema_version=schema_version,
                    reason="validation_error",
                )

        if valid:
            try:
                known = await recorder.known_session_ids({str(event.session_id) for event in valid})
            except Exception:
                logger.exception("Could not validate recorder session references")
                await asyncio.sleep(1)
                continue
            accepted_events = []
            accepted_messages = []
            for event, message in zip(valid, valid_messages, strict=True):
                if str(event.session_id) in known:
                    accepted_events.append(event)
                    accepted_messages.append(message)
                    continue
                EVENT_RECORDER_EVENTS.labels("unknown_session").inc()
                await _reject_message(
                    jetstream,
                    message,
                    event_id=str(event.event_id),
                    schema_version=str(event.schema_version),
                    reason="unknown_session",
                )
            valid = accepted_events
            valid_messages = accepted_messages

        if valid:
            try:
                recorded = await recorder.record(valid)
            except Exception:
                logger.exception("Recorder transaction failed; events remain unacknowledged")
                await asyncio.sleep(1)
                continue
            EVENT_RECORDER_EVENTS.labels("recorded").inc(recorded)
            EVENT_RECORDER_EVENTS.labels("duplicate").inc(len(valid) - recorded)
            newest = max(event.occurred_at for event in valid)
            EVENT_RECORDER_LAG.set(max(0, (datetime.now(UTC) - newest).total_seconds()))
            for message in valid_messages:
                await message.ack()
        info = await jetstream.consumer_info(EVENT_STREAM, _RECORDER)
        EVENT_RECORDER_PENDING.set(info.num_pending + info.num_ack_pending)


async def _outbox_loop(outbox: LifecycleOutboxPublisher) -> None:
    while True:
        try:
            published = await outbox.publish_pending(limit=250)
        except Exception:
            logger.exception("Lifecycle outbox publication failed")
            await asyncio.sleep(1)
            continue
        if published == 0:
            await asyncio.sleep(0.5)


async def _retention_loop(retention: RetentionJob) -> None:
    while True:
        started_at = time.monotonic()
        try:
            deleted = await retention.run_once()
        except Exception:
            logger.exception("Retention pass failed")
        else:
            for table, count in deleted.items():
                RETENTION_DELETED_ROWS.labels(table).inc(count)
            RETENTION_DURATION.observe(time.monotonic() - started_at)
        await asyncio.sleep(settings.maintenance_retention_interval_seconds)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(settings.maintenance_metrics_port, registry=REGISTRY)
    client = await nats.connect(str(settings.nats_url), max_reconnect_attempts=-1)
    publisher = InstrumentedEventPublisher(
        await JetStreamEventPublisher.start(
            client,
            EventStreamSettings(
                max_age_seconds=settings.jetstream_event_max_age_seconds,
                max_bytes=settings.jetstream_event_max_bytes,
                max_message_bytes=settings.jetstream_event_max_message_bytes,
                duplicate_window_seconds=(settings.jetstream_event_duplicate_window_seconds),
                replicas=settings.jetstream_event_replicas,
            ),
        )
    )
    jetstream = client.jetstream()
    await _ensure_dead_letters(jetstream)
    outbox = LifecycleOutboxPublisher(session_factory, publisher)
    recorder = EventRecorder(session_factory)
    retention = RetentionJob(
        session_factory,
        event_days=settings.session_event_retention_days,
        terminal_session_days=settings.terminal_session_retention_days,
        domain_days=settings.domain_history_retention_days,
        batch_size=settings.retention_delete_batch_size,
    )
    try:
        await asyncio.gather(
            _record_loop(jetstream, recorder),
            _outbox_loop(outbox),
            _retention_loop(retention),
        )
    finally:
        await client.drain()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
