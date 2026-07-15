from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from backend.api.routes.health import router as health_router
from backend.api.routes.proxy import router as proxy_router
from backend.proxy.capabilities import capability_registry
from backend.proxy.gateway import Gateway
from backend.proxy.redis import RedisSessionRepository
from backend.proxy.redis.repository import RepositorySettings
from backend.proxy.sessions import SessionManager
from backend.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
    repository = RedisSessionRepository(
        redis,
        RepositorySettings(
            lease_ms=settings.session_lease_seconds * 1000,
            queue_ttl_ms=(
                settings.session_queue_timeout_seconds + settings.session_lease_seconds
            )
            * 1000,
            terminal_ttl_ms=settings.terminal_session_ttl_seconds * 1000,
            event_stream_maxlen=settings.session_event_stream_maxlen,
        ),
    )
    sessions = SessionManager(repository, settings)
    app.state.gateway = Gateway(
        sessions,
        capability_registry,
        settings.provider_acquisition_timeout_seconds,
    )
    try:
        yield
    finally:
        await repository.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(proxy_router)
