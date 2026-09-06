from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    ExternalProviderLimit,
    ExternalProviderLimitEvent,
    ProviderState,
)
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class ExternalCapacity:
    provider: ProviderName
    enabled: bool
    max_active_sessions: int
    max_queued_attempts: int
    configuration_version: int


class ExternalCapacityEnablementError(RuntimeError):
    pass


class ExternalCapacityRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        browserbase_api_key: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._browserbase_api_key_configured = bool(
            browserbase_api_key and browserbase_api_key.strip()
        )

    async def ensure(
        self,
        provider: ProviderName,
        *,
        enabled: bool,
        max_active_sessions: int,
        max_queued_attempts: int,
    ) -> ExternalCapacity:
        self._validate(max_active_sessions, max_queued_attempts)
        async with self._sessions.begin() as database:
            await self._lock_provider(database, provider)
            await database.execute(
                insert(ExternalProviderLimit)
                .values(
                    provider=provider.value,
                    enabled=enabled,
                    max_active_sessions=max_active_sessions,
                    max_queued_attempts=max_queued_attempts,
                )
                .on_conflict_do_nothing(index_elements=[ExternalProviderLimit.provider])
            )
            row = await database.get(ExternalProviderLimit, provider.value)
            assert row is not None
            return self._contract(row)

    async def list(self) -> list[ExternalCapacity]:
        async with self._sessions() as database:
            rows = list(
                await database.scalars(
                    select(ExternalProviderLimit).order_by(ExternalProviderLimit.provider)
                )
            )
            return [self._contract(row) for row in rows]

    async def get(self, provider: ProviderName) -> ExternalCapacity | None:
        async with self._sessions() as database:
            row = await database.get(ExternalProviderLimit, provider.value)
            return self._contract(row) if row is not None else None

    async def update(
        self,
        provider: ProviderName,
        values: Mapping[str, Any],
        *,
        actor: str,
    ) -> ExternalCapacity | None:
        allowed = {"enabled", "max_active_sessions", "max_queued_attempts"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown external capacity fields: {sorted(unknown)}")
        if (
            provider is ProviderName.BROWSERBASE
            and values.get("enabled") is True
            and not self._browserbase_api_key_configured
        ):
            raise ExternalCapacityEnablementError(
                "Cannot enable Browserbase because its API key is not configured. "
                "Configure browserbase_api_key and restart Stolosio."
            )
        async with self._sessions.begin() as database:
            await self._lock_provider(database, provider)
            row = await database.get(
                ExternalProviderLimit,
                provider.value,
                with_for_update=True,
            )
            if row is None:
                return None
            previous = {
                "enabled": row.enabled,
                "max_active_sessions": row.max_active_sessions,
                "max_queued_attempts": row.max_queued_attempts,
            }
            updated = {**previous, **values}
            self._validate(
                updated["max_active_sessions"],
                updated["max_queued_attempts"],
            )
            row.enabled = updated["enabled"]
            row.max_active_sessions = updated["max_active_sessions"]
            row.max_queued_attempts = updated["max_queued_attempts"]
            row.configuration_version += 1
            row.updated_at = datetime.now(UTC)
            database.add(
                ExternalProviderLimitEvent(
                    provider=provider.value,
                    configuration_version=row.configuration_version,
                    previous_values=previous,
                    new_values=updated,
                    actor=actor,
                )
            )
            return self._contract(row)

    @staticmethod
    async def _lock_provider(
        database: AsyncSession,
        provider: ProviderName,
    ) -> None:
        await database.execute(
            insert(ProviderState)
            .values(provider=provider.value)
            .on_conflict_do_nothing(index_elements=[ProviderState.provider])
        )
        await database.scalar(
            select(ProviderState)
            .where(ProviderState.provider == provider.value)
            .with_for_update()
        )

    @staticmethod
    def _validate(max_active_sessions: int, max_queued_attempts: int) -> None:
        if max_active_sessions < 0 or max_queued_attempts < 0:
            raise ValueError("External capacity limits must be non-negative")

    @staticmethod
    def _contract(row: ExternalProviderLimit) -> ExternalCapacity:
        return ExternalCapacity(
            provider=ProviderName(row.provider),
            enabled=row.enabled,
            max_active_sessions=row.max_active_sessions,
            max_queued_attempts=row.max_queued_attempts,
            configuration_version=row.configuration_version,
        )
