import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

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
from backend.events import DynamicEventPublisher
from backend.fleet import FleetRepository, FleetService
from backend.fleet.bootstrap import ensure_managed_fleets
from backend.messaging import (
    DynamicCapacityNotifier,
    NatsCapacityNotifier,
    nats_auth_options,
)
from backend.messaging.jetstream import EVENT_STREAM, JetStreamEventPublisher
from backend.metrics import FleetSnapshotService, InstrumentedEventPublisher
from backend.metrics.definitions import JETSTREAM_TOPOLOGY_READY, NATS_CONNECTED
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


def _clear_nats_event_services(
    app: FastAPI,
    event_publisher: DynamicEventPublisher,
) -> None:
    event_publisher.replace(None)
    app.state.activity_stream = None
    app.state.debug_stream = None
    app.state.nats_topology_ready = False
    JETSTREAM_TOPOLOGY_READY.set(0)


async def _nats_runtime(
    app: FastAPI,
    notifier: DynamicCapacityNotifier,
    event_publisher: DynamicEventPublisher,
    stopped: asyncio.Event,
) -> None:
    while not stopped.is_set():
        client = None
        try:
            async with asyncio.timeout(settings.nats_connect_timeout_seconds):
                client = await nats.connect(
                    str(settings.nats_url),
                    connect_timeout=settings.nats_connect_timeout_seconds,
                    max_reconnect_attempts=-1,
                    **nats_auth_options(settings.nats_seed),
                )
                await client.flush(timeout=settings.nats_connect_timeout_seconds)
            await notifier.replace(await NatsCapacityNotifier.start(client))
            app.state.nats_connected = True
            NATS_CONNECTED.set(1)
            topology_ready = False
            while not stopped.is_set() and not client.is_closed:
                connected = client.is_connected
                app.state.nats_connected = connected
                NATS_CONNECTED.set(int(connected))
                if connected:
                    try:
                        await client.jetstream().stream_info(EVENT_STREAM)
                    except Exception:
                        if topology_ready:
                            _clear_nats_event_services(app, event_publisher)
                            topology_ready = False
                    else:
                        if not topology_ready:
                            publisher = InstrumentedEventPublisher(
                                JetStreamEventPublisher.connected(client)
                            )
                            event_publisher.replace(publisher)
                            app.state.activity_stream = ActivityStreamService(
                                client,
                                max_pending_events=settings.debug_stream_max_pending_events,
                                max_pending_bytes=settings.debug_stream_max_pending_bytes,
                            )
                            app.state.debug_stream = DebugStreamService(
                                session_factory,
                                client,
                                reference_wait_seconds=settings.debug_reference_wait_seconds,
                                max_pending_events=settings.debug_stream_max_pending_events,
                                max_pending_bytes=settings.debug_stream_max_pending_bytes,
                            )
                            app.state.nats_topology_ready = True
                            JETSTREAM_TOPOLOGY_READY.set(1)
                            topology_ready = True
                elif topology_ready:
                    _clear_nats_event_services(app, event_publisher)
                    topology_ready = False
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=1)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("NATS unavailable; Harbor remains in PostgreSQL mode", exc_info=True)
        finally:
            app.state.nats_connected = False
            NATS_CONNECTED.set(0)
            _clear_nats_event_services(app, event_publisher)
            with suppress(Exception):
                await notifier.replace(None)
            if client is not None:
                with suppress(Exception):
                    await client.close()
        if not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=1)
            except TimeoutError:
                pass


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
    notifier = DynamicCapacityNotifier()
    event_publisher = DynamicEventPublisher()
    app.state.nats_connected = False
    app.state.nats_topology_ready = False
    app.state.activity_stream = None
    app.state.debug_stream = None
    nats_stopped = asyncio.Event()
    nats_task = asyncio.create_task(_nats_runtime(app, notifier, event_publisher, nats_stopped))
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
    app.state.gateway = Gateway(
        sessions,
        attempts,
        settings,
        event_publisher,
        transition_repository=ProviderTransitionRepository(session_factory),
        routing=routing,
        network_policy=network_policy,
    )
    try:
        yield
    finally:
        nats_stopped.set()
        await nats_task
        await notifier.close()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.14", lifespan=lifespan)
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
