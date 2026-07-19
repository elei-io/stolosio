import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import nats
from fastapi import FastAPI

from backend.api.routes.admin_command_costs import (
    router as admin_command_costs_router,
)
from backend.api.routes.admin_costs import router as admin_costs_router
from backend.api.routes.admin_domains import router as admin_domains_router
from backend.api.routes.admin_events import router as admin_events_router
from backend.api.routes.admin_fleets import router as admin_fleets_router
from backend.api.routes.admin_network import router as admin_network_router
from backend.api.routes.admin_provider_capacity import router as admin_provider_capacity_router
from backend.api.routes.admin_routing import router as admin_routing_router
from backend.api.routes.admin_sessions import router as admin_sessions_router
from backend.api.routes.debug import router as debug_router
from backend.api.routes.fleet import router as fleet_router
from backend.api.routes.health import router as health_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.proxy import router as proxy_router
from backend.db.session import engine, session_factory
from backend.debug import ActivityHistoryService, ActivityStreamService, DebugStreamService
from backend.fleet import FleetRepository, FleetService
from backend.fleet.bootstrap import ensure_managed_fleets
from backend.messaging import NatsCapacityNotifier, PollingNotifier, nats_auth_options
from backend.messaging.jetstream import (
    EventStreamSettings,
    JetStreamEventPublisher,
)
from backend.metrics import FleetSnapshotService, InstrumentedEventPublisher
from backend.proxy.attempts import AttemptAdmission
from backend.proxy.command_costs import CommandCostQueryService
from backend.proxy.contracts import ProviderName
from backend.proxy.costs import CostQueryService
from backend.proxy.domains import DomainQueryService
from backend.proxy.external_capacity import ExternalCapacityRepository
from backend.proxy.gateway import Gateway
from backend.proxy.health import PromotionRepository
from backend.proxy.network_policy import NetworkPolicyRepository
from backend.proxy.postgres import (
    PostgresAttemptRepository,
    PostgresSessionRepository,
    SessionRepositorySettings,
)
from backend.proxy.provider_transition import ProviderTransitionRepository
from backend.proxy.routing import RoutingRepository
from backend.proxy.session_queries import SessionQueryService
from backend.proxy.sessions import SessionAdmission
from backend.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    fleet_repository = FleetRepository(session_factory)
    await ensure_managed_fleets(fleet_repository)
    external_capacity = ExternalCapacityRepository(
        session_factory,
        browserbase_api_key=settings.browserbase_api_key,
    )
    await external_capacity.ensure(
        ProviderName.HTTP,
        enabled=True,
        max_active_sessions=100,
        max_queued_attempts=100,
    )
    await external_capacity.ensure(
        ProviderName.BROWSERBASE,
        enabled=False,
        max_active_sessions=5,
        max_queued_attempts=100,
    )
    repository = PostgresSessionRepository(
        session_factory,
        SessionRepositorySettings(
            lease_seconds=settings.session_lease_seconds,
        ),
    )
    attempt_repository = PostgresAttemptRepository(session_factory)
    routing = RoutingRepository(session_factory)
    await routing.ensure_defaults()
    network_policy = NetworkPolicyRepository(session_factory)
    await network_policy.ensure_defaults()
    nats_client = None
    try:
        async with asyncio.timeout(settings.nats_connect_timeout_seconds):
            nats_client = await nats.connect(
                str(settings.nats_url),
                connect_timeout=settings.nats_connect_timeout_seconds,
                max_reconnect_attempts=-1,
                **nats_auth_options(settings.nats_seed),
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
    app.state.external_capacity = external_capacity
    app.state.routing = routing
    app.state.network_policy = network_policy
    app.state.domains = DomainQueryService(session_factory)
    app.state.session_queries = SessionQueryService(session_factory)
    app.state.command_costs = CommandCostQueryService(session_factory)
    app.state.costs = CostQueryService(session_factory)
    app.state.health = PromotionRepository(session_factory)
    app.state.activity_history = ActivityHistoryService(session_factory)
    app.state.activity_stream = (
        ActivityStreamService(
            nats_client,
            max_pending_events=settings.debug_stream_max_pending_events,
            max_pending_bytes=settings.debug_stream_max_pending_bytes,
        )
        if nats_client is not None
        else None
    )
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
        settings,
        event_publisher if nats_client is not None else None,
        transition_repository=ProviderTransitionRepository(session_factory),
        routing=routing,
        network_policy=network_policy,
    )
    try:
        yield
    finally:
        await notifier.close()
        if nats_client is not None:
            await nats_client.drain()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.12", lifespan=lifespan)
app.include_router(admin_command_costs_router)
app.include_router(admin_costs_router)
app.include_router(admin_events_router)
app.include_router(admin_fleets_router)
app.include_router(admin_network_router)
app.include_router(admin_provider_capacity_router)
app.include_router(admin_domains_router)
app.include_router(admin_routing_router)
app.include_router(admin_sessions_router)
app.include_router(health_router)
app.include_router(debug_router)
app.include_router(fleet_router)
app.include_router(metrics_router)
app.include_router(proxy_router)
