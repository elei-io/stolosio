from functools import lru_cache

from pydantic import AnyUrl, RedisDsn
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
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    browserless_url: AnyUrl = AnyUrl("ws://localhost:3000")
    camoufox_url: AnyUrl = AnyUrl("ws://localhost:1234/harbor")
    chromium_url: AnyUrl = AnyUrl("ws://localhost:9223")
    lightpanda_url: AnyUrl = AnyUrl("ws://localhost:9222")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
