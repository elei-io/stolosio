from functools import lru_cache

from pydantic import AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Harbor"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://harbor:harbor@localhost:5432/harbor"
    nats_url: AnyUrl = AnyUrl("nats://localhost:4222")
    nats_connect_timeout_seconds: float = 1
    browserless_url: AnyUrl = AnyUrl("ws://localhost:3000")
    camoufox_url: AnyUrl = AnyUrl("ws://localhost:1234/harbor")
    chromium_url: AnyUrl = AnyUrl("ws://localhost:9223")
    lightpanda_url: AnyUrl = AnyUrl("ws://localhost:9222")
    session_lease_seconds: int = 30
    session_heartbeat_seconds: float = 10
    session_queue_poll_ms: int = 1000
    session_queue_timeout_seconds: int = 30
    provider_acquisition_timeout_seconds: int = 10
    chromium_max_active_sessions: int = 1
    chromium_max_queued_sessions: int = 100
    browserless_max_active_sessions: int = 5
    browserless_max_queued_sessions: int = 100
    lightpanda_max_active_sessions: int = 1
    lightpanda_max_queued_sessions: int = 100
    camoufox_max_active_sessions: int = 1
    camoufox_max_queued_sessions: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
