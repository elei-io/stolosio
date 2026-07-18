from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    Domain,
    DomainProviderRuntimeState,
    ProviderRoutingProfile,
    SessionDomainProviderCompatibility,
)
from backend.proxy.capabilities import CapabilityRegistry, capability_registry
from backend.proxy.contracts import ProviderName


@dataclass(slots=True)
class DomainCommands:
    first_seen_at: datetime
    commands: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)


class SessionCompatibilityTracker:
    """Keeps exact command shapes in memory for one automatic session."""

    def __init__(self) -> None:
        self._before_navigation: list[tuple[str, dict[str, Any] | None]] = []
        self._domains: dict[str, DomainCommands] = {}
        self._current_domain: str | None = None

    def navigate(
        self,
        hostname: str,
        method: str,
        params: dict[str, Any] | None,
    ) -> None:
        now = datetime.now(UTC)
        commands = self._domains.get(hostname)
        if commands is None:
            commands = DomainCommands(now, list(self._before_navigation))
            self._domains[hostname] = commands
        self._current_domain = hostname
        commands.commands.append((method, params))

    def command(self, method: str, params: dict[str, Any] | None) -> None:
        if self._current_domain is None:
            self._before_navigation.append((method, params))
            return
        self._domains[self._current_domain].commands.append((method, params))

    def first_seen_at(self, hostname: str) -> datetime | None:
        commands = self._domains.get(hostname)
        return commands.first_seen_at if commands is not None else None

    @property
    def domains(self) -> dict[str, DomainCommands]:
        return self._domains


class RuntimeCompatibilityRepository:
    """Owns durable suppression and one-later-session restoration."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        capabilities: CapabilityRegistry = capability_registry,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities

    async def suppress(
        self,
        *,
        session_id: str,
        hostname: str,
        provider: ProviderName,
        method: str,
    ) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            domain_id = await self._domain_id(database, hostname, now)
            profile = await database.get(ProviderRoutingProfile, provider.value)
            contract_version = (
                profile.provider_contract_version if profile is not None else 1
            )
            await self._suppress_locked(
                database,
                domain_id=domain_id,
                provider=provider,
                session_id=session_id,
                method=method,
                observed_at=now,
                contract_version=contract_version,
            )

    async def record_session(
        self,
        session_id: str,
        observations: dict[str, DomainCommands],
    ) -> None:
        if not observations:
            return
        evaluated_at = datetime.now(UTC)
        async with self._sessions.begin() as database:
            profiles = list(await database.scalars(select(ProviderRoutingProfile)))
            for hostname, observation in observations.items():
                domain_id = await self._domain_id(
                    database, hostname, observation.first_seen_at
                )
                for profile in profiles:
                    provider = ProviderName(profile.provider)
                    incompatible_method = next(
                        (
                            method
                            for method, params in observation.commands
                            if not self._capabilities.supports(
                                provider, method, params
                            )
                        ),
                        None,
                    )
                    compatible = incompatible_method is None
                    inserted = await database.scalar(
                        insert(SessionDomainProviderCompatibility)
                        .values(
                            session_id=session_id,
                            domain_id=domain_id,
                            provider=provider.value,
                            compatible=compatible,
                            incompatible_method=incompatible_method,
                            domain_first_seen_at=observation.first_seen_at,
                            evaluated_at=evaluated_at,
                            provider_contract_version=(
                                profile.provider_contract_version
                            ),
                        )
                        .on_conflict_do_nothing()
                        .returning(
                            SessionDomainProviderCompatibility.session_id
                        )
                    )
                    if inserted is None:
                        continue
                    if compatible:
                        await database.execute(
                            update(DomainProviderRuntimeState)
                            .where(
                                DomainProviderRuntimeState.domain_id == domain_id,
                                DomainProviderRuntimeState.provider
                                == provider.value,
                                DomainProviderRuntimeState.state == "suppressed",
                                DomainProviderRuntimeState.suppressed_at
                                < observation.first_seen_at,
                                DomainProviderRuntimeState.provider_contract_version
                                == profile.provider_contract_version,
                            )
                            .values(
                                state="eligible",
                                restored_at=evaluated_at,
                                restored_session_id=session_id,
                                last_evidence_at=evaluated_at,
                            )
                        )
                    else:
                        await self._suppress_locked(
                            database,
                            domain_id=domain_id,
                            provider=provider,
                            session_id=session_id,
                            method=incompatible_method,
                            observed_at=evaluated_at,
                            contract_version=profile.provider_contract_version,
                        )

    @staticmethod
    async def _domain_id(
        database: AsyncSession,
        hostname: str,
        observed_at: datetime,
    ) -> int:
        return await database.scalar(
            insert(Domain)
            .values(
                hostname=hostname,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                session_count=0,
            )
            .on_conflict_do_update(
                index_elements=[Domain.hostname],
                set_={
                    "first_seen_at": func.least(
                        Domain.first_seen_at, observed_at
                    ),
                    "last_seen_at": func.greatest(
                        Domain.last_seen_at, observed_at
                    ),
                },
            )
            .returning(Domain.id)
        )

    @staticmethod
    async def _suppress_locked(
        database: AsyncSession,
        *,
        domain_id: int,
        provider: ProviderName,
        session_id: str,
        method: str,
        observed_at: datetime,
        contract_version: int,
    ) -> None:
        same_suppression = and_(
            DomainProviderRuntimeState.state == "suppressed",
            DomainProviderRuntimeState.provider_contract_version
            == contract_version,
        )
        await database.execute(
            insert(DomainProviderRuntimeState)
            .values(
                domain_id=domain_id,
                provider=provider.value,
                state="suppressed",
                suppressed_at=observed_at,
                suppressed_session_id=session_id,
                incompatible_method=method,
                restored_at=None,
                restored_session_id=None,
                last_evidence_at=observed_at,
                provider_contract_version=contract_version,
            )
            .on_conflict_do_update(
                index_elements=[
                    DomainProviderRuntimeState.domain_id,
                    DomainProviderRuntimeState.provider,
                ],
                set_={
                    "state": "suppressed",
                    "suppressed_at": case(
                        (
                            same_suppression,
                            DomainProviderRuntimeState.suppressed_at,
                        ),
                        else_=observed_at,
                    ),
                    "suppressed_session_id": case(
                        (
                            same_suppression,
                            DomainProviderRuntimeState.suppressed_session_id,
                        ),
                        else_=session_id,
                    ),
                    "incompatible_method": case(
                        (
                            same_suppression,
                            DomainProviderRuntimeState.incompatible_method,
                        ),
                        else_=method,
                    ),
                    "restored_at": case(
                        (
                            same_suppression,
                            DomainProviderRuntimeState.restored_at,
                        ),
                        else_=None,
                    ),
                    "restored_session_id": case(
                        (
                            same_suppression,
                            DomainProviderRuntimeState.restored_session_id,
                        ),
                        else_=None,
                    ),
                    "last_evidence_at": func.greatest(
                        DomainProviderRuntimeState.last_evidence_at,
                        observed_at,
                    ),
                    "provider_contract_version": contract_version,
                },
            )
        )
