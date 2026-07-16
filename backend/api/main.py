import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import nats
from fastapi import FastAPI

from backend.api.routes.admin_fleets import router as admin_fleets_router
from backend.api.routes.debug import router as debug_router
from backend.api.routes.fleet import router as fleet_router
from backend.api.routes.health import router as health_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.proxy import router as proxy_router
from backend.db.session import engine, session_factory
from backend.debug import DebugStreamService
from backend.fleet import FleetRepository, FleetService
from backend.messaging import NatsCapacityNotifier, PollingNotifier
from backend.messaging.jetstream import (
    EventStreamSettings,
    JetStreamEventPublisher,
)
from backend.metrics import FleetSnapshotService, InstrumentedEventPublisher
from backend.proxy.attempts import AttemptAdmission
from backend.proxy.capabilities import capability_registry
from backend.proxy.contracts import ProviderName
from backend.proxy.gateway import Gateway
from backend.proxy.postgres import (
    PostgresAttemptRepository,
    PostgresSessionRepository,
    SessionRepositorySettings,
)
from backend.proxy.sessions import SessionAdmission
from backend.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    fleet_repository = FleetRepository(session_factory)
    await fleet_repository.ensure_fleet(
        ProviderName.CHROMIUM,
        minimum_instances=settings.chromium_minimum_instances,
        maximum_instances=settings.chromium_maximum_instances,
        session_capacity_per_instance=settings.chromium_session_capacity_per_instance,
        scale_down_cooldown_seconds=settings.chromium_scale_down_cooldown_seconds,
    )
    repository = PostgresSessionRepository(
        session_factory,
        SessionRepositorySettings(
            lease_seconds=settings.session_lease_seconds,
        ),
    )
    attempt_repository = PostgresAttemptRepository(session_factory)
    nats_client = None
    try:
        async with asyncio.timeout(settings.nats_connect_timeout_seconds):
            nats_client = await nats.connect(
                str(settings.nats_url),
                connect_timeout=settings.nats_connect_timeout_seconds,
                max_reconnect_attempts=-1,
            )
        notifier = await NatsCapacityNotifier.start(nats_client)
        event_publisher = InstrumentedEventPublisher(
            await JetStreamEventPublisher.start(
                nats_client,
                EventStreamSettings(
                    max_age_seconds=settings.jetstream_event_max_age_seconds,
                    max_bytes=settings.jetstream_event_max_bytes,
                    max_message_bytes=settings.jetstream_event_max_message_bytes,
                    duplicate_window_seconds=(settings.jetstream_event_duplicate_window_seconds),
                    replicas=settings.jetstream_event_replicas,
                ),
            )
        )
    except Exception:
        logger.warning("NATS unavailable; capacity waiters will use polling", exc_info=True)
        if nats_client is not None:
            await nats_client.close()
            nats_client = None
        notifier = PollingNotifier()
    sessions = SessionAdmission(repository, settings)
    attempts = AttemptAdmission(
        attempt_repository,
        settings,
        notifier=notifier,
    )
    app.state.fleet = FleetSnapshotService(session_factory, settings)
    app.state.fleet_admin = FleetService(fleet_repository)
    app.state.environment = settings.environment
    app.state.debug_stream = (
        DebugStreamService(
            session_factory,
            nats_client,
            reference_wait_seconds=settings.debug_reference_wait_seconds,
            max_pending_events=settings.debug_stream_max_pending_events,
            max_pending_bytes=settings.debug_stream_max_pending_bytes,
        )
        if nats_client is not None
        else None
    )
    app.state.gateway = Gateway(
        sessions,
        attempts,
        capability_registry,
        settings,
        event_publisher if nats_client is not None else None,
    )
    try:
        yield
    finally:
        await notifier.close()
        if nats_client is not None:
            await nats_client.drain()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(admin_fleets_router)
app.include_router(health_router)
app.include_router(debug_router)
app.include_router(fleet_router)
app.include_router(metrics_router)
app.include_router(proxy_router)
