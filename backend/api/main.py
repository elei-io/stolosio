import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.routes.health import router as health_router
from backend.api.routes.proxy import router as proxy_router
from backend.db.session import engine, session_factory
from backend.messaging import NatsCapacityNotifier, PollingNotifier
from backend.proxy.capabilities import capability_registry
from backend.proxy.gateway import Gateway
from backend.proxy.postgres import PostgresSessionRepository
from backend.proxy.postgres.repository import RepositorySettings
from backend.proxy.sessions import SessionManager
from backend.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repository = PostgresSessionRepository(
        session_factory,
        RepositorySettings(
            lease_seconds=settings.session_lease_seconds,
            queue_ttl_seconds=(
                settings.session_queue_timeout_seconds + settings.session_lease_seconds
            ),
        ),
    )
    try:
        notifier = await NatsCapacityNotifier.connect(
            str(settings.nats_url),
            connect_timeout_seconds=settings.nats_connect_timeout_seconds,
        )
    except Exception:
        logger.warning("NATS unavailable; capacity waiters will use polling", exc_info=True)
        notifier = PollingNotifier()
    sessions = SessionManager(repository, settings, notifier=notifier)
    app.state.gateway = Gateway(
        sessions,
        capability_registry,
        settings.provider_acquisition_timeout_seconds,
    )
    try:
        yield
    finally:
        await notifier.close()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(proxy_router)
