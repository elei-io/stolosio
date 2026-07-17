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
    http_max_active_sessions: int = 100
    http_max_queued_attempts: int = 100
    http_request_timeout_seconds: float = 20
    http_max_response_bytes: int = 10 * 1024 * 1024
    http_user_agent: str = "HarborBot/0.1 (https://github.com/ekkuleivonen/harbor)"
    http_accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    http_accept_language: str = "en-US,en;q=0.5"
    no_browser_replay_max_commands: int = 100
    no_browser_replay_max_bytes: int = 2 * 1024 * 1024
    no_browser_replay_timeout_seconds: float = 30
    qualification_harbor_cdp_url: str = "ws://localhost:8411/v1/connect"
    qualification_schedule_delay_seconds: float = 2
    qualification_poll_seconds: float = 1
    qualification_lease_seconds: float = 60
    chromium_minimum_instances: int = 1
    chromium_maximum_instances: int = 4
    chromium_session_capacity_per_instance: int = 4
    chromium_scale_down_cooldown_seconds: int = 30
    chromium_max_queued_attempts: int = 100
    browserless_minimum_instances: int = 1
    browserless_maximum_instances: int = 4
    browserless_session_capacity_per_instance: int = 5
    browserless_scale_down_cooldown_seconds: int = 30
    browserless_max_queued_attempts: int = 100
    lightpanda_max_active_sessions: int = 1
    lightpanda_max_queued_attempts: int = 100
    lightpanda_minimum_instances: int = 1
    lightpanda_maximum_instances: int = 4
    lightpanda_session_capacity_per_instance: int = 1
    lightpanda_scale_down_cooldown_seconds: int = 30
    camoufox_minimum_instances: int = 1
    camoufox_maximum_instances: int = 4
    camoufox_session_capacity_per_instance: int = 1
    camoufox_scale_down_cooldown_seconds: int = 30
    camoufox_max_queued_attempts: int = 100
    fleet_reconcile_interval_seconds: float = 1
    fleet_observation_ttl_seconds: float = 5
    fleet_instance_startup_timeout_seconds: float = 30
    fleet_controller_backoff_seconds: float = 2
    fleet_controller_metrics_port: int = 9101
    fleet_compose_project_name: str = "harbor"
    fleet_compose_workdir: str = "."
    chromium_fleet_compose_service: str = "chromium"
    browserless_fleet_compose_service: str = "browserless"
    lightpanda_fleet_compose_service: str = "lightpanda"
    camoufox_fleet_compose_service: str = "camoufox"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
