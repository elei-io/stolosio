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
    database_url: str = "postgresql+asyncpg://harbor:harbor@localhost:5433/harbor"
    nats_url: AnyUrl = AnyUrl("nats://localhost:4223")
    nats_connect_timeout_seconds: float = 1
    jetstream_event_max_age_seconds: int = 86_400
    jetstream_event_max_bytes: int = 10 * 1024 * 1024 * 1024
    jetstream_event_max_message_bytes: int = 256 * 1024
    jetstream_event_duplicate_window_seconds: int = 600
    jetstream_event_replicas: int = 1
    event_recorder_batch_size: int = 250
    event_recorder_fetch_timeout_seconds: float = 1
    event_recorder_ack_wait_seconds: int = 60
    event_recorder_max_deliver: int = 5
    debug_reference_wait_seconds: float = 30
    debug_stream_max_pending_events: int = 1_000
    debug_stream_max_pending_bytes: int = 4 * 1024 * 1024
    maintenance_metrics_port: int = 9000
    maintenance_retention_interval_seconds: int = 3600
    session_event_retention_days: int = 30
    terminal_session_retention_days: int = 90
    domain_history_retention_days: int = 365
    retention_delete_batch_size: int = 10_000
    browserless_url: AnyUrl = AnyUrl("ws://localhost:3000")
    camoufox_url: AnyUrl = AnyUrl("ws://localhost:1234/harbor")
    chromium_url: AnyUrl = AnyUrl("ws://localhost:9223")
    lightpanda_url: AnyUrl = AnyUrl("ws://localhost:9222")
    harbor_max_active_sessions: int = 100
    session_lease_seconds: float = 30
    session_heartbeat_seconds: float = 10
    provider_queue_poll_ms: int = 1000
    provider_queue_timeout_seconds: float = 30
    provider_acquisition_timeout_seconds: int = 10
    session_cleanup_timeout_seconds: float = 2
    chromium_max_active_sessions: int = 1
    chromium_max_queued_attempts: int = 100
    browserless_max_active_sessions: int = 5
    browserless_max_queued_attempts: int = 100
    lightpanda_max_active_sessions: int = 1
    lightpanda_max_queued_attempts: int = 100
    camoufox_max_active_sessions: int = 1
    camoufox_max_queued_attempts: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
