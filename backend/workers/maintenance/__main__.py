import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

import nats
from nats.js import api
from prometheus_client import start_http_server
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SessionEventRecord
from backend.db.session import engine, session_factory
from backend.events import LifecycleOutboxPublisher, SessionEvent
from backend.events.contracts import provider_from_storage
from backend.messaging.connection import nats_auth_options
from backend.messaging.jetstream import (
    DEAD_LETTER_STREAM,
    DEAD_LETTER_SUBJECT,
    EVENT_STREAM,
    EVENT_SUBJECT,
    RECORDER_CONSUMER,
    EventStreamSettings,
    JetStreamEventPublisher,
    dead_letter_stream_config,
    ensure_harbor_topology,
)
from backend.metrics import REGISTRY, InstrumentedEventPublisher
from backend.metrics.definitions import (
    EVENT_DEAD_LETTERS,
    EVENT_RECORDER_EVENTS,
    EVENT_RECORDER_LAG,
    EVENT_RECORDER_PENDING,
    JETSTREAM_REHYDRATED_EVENTS,
    JETSTREAM_TOPOLOGY_READY,
    NATS_CONNECTED,
    RETENTION_DELETED_ROWS,
    RETENTION_DURATION,
)
from backend.settings import settings
from backend.workers.maintenance.recorder import EventRecorder
from backend.workers.maintenance.retention import RetentionJob

logger = logging.getLogger(__name__)


def _event_stream_settings() -> EventStreamSettings:
    return EventStreamSettings(
        max_age_seconds=settings.jetstream_event_max_age_seconds,
        max_bytes=settings.jetstream_event_max_bytes,
        max_message_bytes=settings.jetstream_event_max_message_bytes,
        duplicate_window_seconds=settings.jetstream_event_duplicate_window_seconds,
        replicas=settings.jetstream_event_replicas,
    )


async def _ensure_dead_letters(jetstream) -> None:
    config = dead_letter_stream_config(
        _event_stream_settings(),
        max_bytes=settings.jetstream_dead_letter_max_bytes,
    )
    try:
        await jetstream.stream_info(DEAD_LETTER_STREAM)
    except Exception as error:
        if getattr(error, "err_code", None) != 10059:
            raise
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
    await jetstream.publish(DEAD_LETTER_SUBJECT, dead_letter)
    EVENT_DEAD_LETTERS.labels(reason).inc()
    await message.term()


async def _record_loop(jetstream, recorder: EventRecorder) -> None:
    consumer = api.ConsumerConfig(
        durable_name=RECORDER_CONSUMER,
        name=RECORDER_CONSUMER,
        deliver_policy=api.DeliverPolicy.ALL,
        ack_policy=api.AckPolicy.EXPLICIT,
        ack_wait=settings.event_recorder_ack_wait_seconds,
        max_deliver=settings.event_recorder_max_deliver,
        filter_subject=EVENT_SUBJECT,
    )
    subscription = await jetstream.pull_subscribe(
        EVENT_SUBJECT,
        durable=RECORDER_CONSUMER,
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
        info = await jetstream.consumer_info(EVENT_STREAM, RECORDER_CONSUMER)
        EVENT_RECORDER_PENDING.set(info.num_pending + info.num_ack_pending)


async def _rehydrate_event_stream(
    publisher: JetStreamEventPublisher,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.jetstream_event_max_age_seconds)
    last_id = 0
    published = 0
    while True:
        async with sessions() as database:
            rows = list(
                await database.scalars(
                    select(SessionEventRecord)
                    .where(
                        SessionEventRecord.id > last_id,
                        SessionEventRecord.occurred_at >= cutoff,
                    )
                    .order_by(SessionEventRecord.id)
                    .limit(250)
                )
            )
        if not rows:
            return published
        for row in rows:
            await publisher.publish(
                SessionEvent(
                    event_id=row.event_id,
                    schema_version=row.schema_version,
                    event_type=row.event_type,
                    session_id=UUID(row.session_id),
                    occurred_at=row.occurred_at,
                    provider=provider_from_storage(row.provider),
                    attempt_id=UUID(row.attempt_id) if row.attempt_id else None,
                    payload=row.payload,
                )
            )
            published += 1
        last_id = rows[-1].id
        JETSTREAM_REHYDRATED_EVENTS.inc(len(rows))


async def _topology_loop(jetstream, publisher: JetStreamEventPublisher) -> None:
    while True:
        topology = await ensure_harbor_topology(
            jetstream,
            _event_stream_settings(),
            dead_letter_max_bytes=settings.jetstream_dead_letter_max_bytes,
        )
        JETSTREAM_TOPOLOGY_READY.set(1)
        if topology.event_stream_created:
            rehydrated = await _rehydrate_event_stream(publisher)
            logger.info(
                "Rehydrated %d retained PostgreSQL events into a fresh JetStream",
                rehydrated,
            )
        await asyncio.sleep(5)


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


async def _run_connected() -> None:
    client = await nats.connect(
        str(settings.nats_url),
        connect_timeout=settings.nats_connect_timeout_seconds,
        max_reconnect_attempts=-1,
        **nats_auth_options(settings.nats_seed),
    )
    try:
        await client.flush(timeout=settings.nats_connect_timeout_seconds)
        NATS_CONNECTED.set(1)
        jetstream = client.jetstream()
        topology = await ensure_harbor_topology(
            jetstream,
            _event_stream_settings(),
            dead_letter_max_bytes=settings.jetstream_dead_letter_max_bytes,
        )
        JETSTREAM_TOPOLOGY_READY.set(1)
        raw_publisher = JetStreamEventPublisher.connected(client)
        if topology.event_stream_created:
            rehydrated = await _rehydrate_event_stream(raw_publisher)
            logger.info(
                "Rehydrated %d retained PostgreSQL events into a fresh JetStream",
                rehydrated,
            )
        publisher = InstrumentedEventPublisher(raw_publisher)
        outbox = LifecycleOutboxPublisher(session_factory, publisher)
        recorder = EventRecorder(session_factory)
        retention = RetentionJob(
            session_factory,
            event_days=settings.session_event_retention_days,
            terminal_session_days=settings.terminal_session_retention_days,
            domain_days=settings.domain_history_retention_days,
            batch_size=settings.retention_delete_batch_size,
        )
        tasks = [
            asyncio.create_task(_topology_loop(jetstream, raw_publisher)),
            asyncio.create_task(_record_loop(jetstream, recorder)),
            asyncio.create_task(_outbox_loop(outbox)),
            asyncio.create_task(_retention_loop(retention)),
        ]
        try:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        NATS_CONNECTED.set(0)
        JETSTREAM_TOPOLOGY_READY.set(0)
        with suppress(Exception):
            await client.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    start_http_server(settings.maintenance_metrics_port, registry=REGISTRY)
    while True:
        try:
            await _run_connected()
        except asyncio.CancelledError:
            raise
        except Exception:
            NATS_CONNECTED.set(0)
            JETSTREAM_TOPOLOGY_READY.set(0)
            logger.exception("NATS maintenance runtime failed; reconciling again")
            await asyncio.sleep(1)
        else:
            logger.warning("NATS maintenance runtime stopped; reconciling again")
            await asyncio.sleep(1)


async def _shutdown() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_shutdown())
