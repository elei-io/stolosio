from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderProfile,
    ProviderRoutingProfile,
    RoutingConfiguration,
)
from backend.proxy.contracts import ProviderName

_DEFAULT_COSTS = {
    ProviderName.HTTP: 1,
    ProviderName.LIGHTPANDA: 10,
    ProviderName.CHROMIUM: 100,
    ProviderName.BROWSERLESS: 100,
    ProviderName.CAMOUFOX: 120,
}


@dataclass(frozen=True, slots=True)
class RoutingChoice:
    provider: ProviderName
    reason: str
    configuration_version: int
    estimated_cost_units: int


@dataclass(frozen=True, slots=True)
class RoutingSettings:
    default_provider: ProviderName
    existing_domain_probe_rate_basis_points: int
    required_successful_probes: int
    comparison_policy_version: int
    configuration_version: int


class RoutingRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_defaults(self) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            await database.execute(
                insert(RoutingConfiguration)
                .values(
                    key="global",
                    default_provider=ProviderName.CAMOUFOX.value,
                    existing_domain_probe_rate_basis_points=100,
                    required_successful_probes=1,
                    comparison_policy_version=1,
                    configuration_version=1,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[RoutingConfiguration.key])
            )
            for provider, cost in _DEFAULT_COSTS.items():
                await database.execute(
                    insert(ProviderRoutingProfile)
                    .values(
                        provider=provider.value,
                        automatic_enabled=True,
                        cost_units_per_second=cost,
                        capability_manifest_version=1,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[ProviderRoutingProfile.provider])
                )

    async def settings(self) -> RoutingSettings:
        async with self._sessions() as database:
            row = await database.get(RoutingConfiguration, "global")
        if row is None:
            return RoutingSettings(ProviderName.CAMOUFOX, 100, 1, 1, 1)
        return RoutingSettings(
            ProviderName(row.default_provider),
            row.existing_domain_probe_rate_basis_points,
            row.required_successful_probes,
            row.comparison_policy_version,
            row.configuration_version,
        )

    async def update_settings(
        self,
        *,
        default_provider: ProviderName | None = None,
        existing_domain_probe_rate_basis_points: int | None = None,
        required_successful_probes: int | None = None,
    ) -> RoutingSettings:
        async with self._sessions.begin() as database:
            row = await database.get(RoutingConfiguration, "global", with_for_update=True)
            if row is None:
                raise RuntimeError("Routing configuration is not initialized")
            if default_provider is not None:
                profile = await database.get(ProviderRoutingProfile, default_provider.value)
                if profile is None or not profile.automatic_enabled:
                    raise ValueError("Default provider must be enabled for automatic routing")
                row.default_provider = default_provider.value
            if existing_domain_probe_rate_basis_points is not None:
                row.existing_domain_probe_rate_basis_points = (
                    existing_domain_probe_rate_basis_points
                )
            if required_successful_probes is not None:
                row.required_successful_probes = required_successful_probes
            row.configuration_version += 1
            row.updated_at = datetime.now(UTC)
        return await self.settings()

    async def provider_profiles(self) -> list[ProviderRoutingProfile]:
        async with self._sessions() as database:
            return list(
                await database.scalars(
                    select(ProviderRoutingProfile).order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            )

    async def update_provider(
        self,
        provider: ProviderName,
        *,
        automatic_enabled: bool | None = None,
        cost_units_per_second: int | None = None,
    ) -> ProviderRoutingProfile:
        async with self._sessions.begin() as database:
            row = await database.get(
                ProviderRoutingProfile,
                provider.value,
                with_for_update=True,
            )
            if row is None:
                raise ValueError("Unknown provider routing profile")
            if automatic_enabled is not None:
                configuration = await database.get(
                    RoutingConfiguration,
                    "global",
                    with_for_update=True,
                )
                if (
                    not automatic_enabled
                    and configuration is not None
                    and configuration.default_provider == provider.value
                ):
                    raise ValueError("The default provider cannot be disabled")
                row.automatic_enabled = automatic_enabled
            if cost_units_per_second is not None:
                row.cost_units_per_second = cost_units_per_second
            row.updated_at = datetime.now(UTC)
            if configuration := await database.get(
                RoutingConfiguration,
                "global",
                with_for_update=True,
            ):
                configuration.configuration_version += 1
                configuration.updated_at = datetime.now(UTC)
        return row

    async def choose(self, hostname: str) -> RoutingChoice:
        async with self._sessions() as database:
            configuration = await database.get(RoutingConfiguration, "global")
            if configuration is None:
                return RoutingChoice(ProviderName.CAMOUFOX, "unknown_domain_default", 1, 120)
            default_profile = await database.get(
                ProviderRoutingProfile,
                configuration.default_provider,
            )
            default_cost = default_profile.cost_units_per_second if default_profile else 0
            domain_id = await database.scalar(select(Domain.id).where(Domain.hostname == hostname))
            if domain_id is None:
                return RoutingChoice(
                    ProviderName(configuration.default_provider),
                    "unknown_domain_default",
                    configuration.configuration_version,
                    default_cost,
                )
            rows = list(
                await database.execute(
                    select(DomainProviderProfile, ProviderRoutingProfile)
                    .join(
                        ProviderRoutingProfile,
                        ProviderRoutingProfile.provider == DomainProviderProfile.provider,
                    )
                    .where(
                        DomainProviderProfile.domain_id == domain_id,
                        DomainProviderProfile.qualification_state == "qualified",
                        ProviderRoutingProfile.automatic_enabled.is_(True),
                    )
                )
            )
        if not rows:
            return RoutingChoice(
                ProviderName(configuration.default_provider),
                "unqualified_domain_default",
                configuration.configuration_version,
                default_cost,
            )

        def expected_cost(row) -> int:
            evidence, provider = row
            if evidence.observed_session_count:
                return evidence.total_cost_units // evidence.observed_session_count
            return provider.cost_units_per_second

        evidence, profile = min(
            rows,
            key=lambda row: (expected_cost(row), row[1].provider),
        )
        return RoutingChoice(
            ProviderName(profile.provider),
            "cheapest_qualified",
            configuration.configuration_version,
            expected_cost((evidence, profile)),
        )

    async def record_choice(
        self,
        attempt_id: str,
        hostname: str,
        choice: RoutingChoice,
    ) -> None:
        async with self._sessions.begin() as database:
            domain_id = await database.scalar(
                insert(Domain)
                .values(
                    hostname=hostname,
                    first_seen_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                    session_count=0,
                )
                .on_conflict_do_update(
                    index_elements=[Domain.hostname],
                    set_={"last_seen_at": datetime.now(UTC)},
                )
                .returning(Domain.id)
            )
            row = await database.get(AcquisitionAttempt, attempt_id, with_for_update=True)
            if row is None:
                return
            row.domain_id = domain_id
            row.routing_reason = choice.reason
            row.routing_version = choice.configuration_version
            row.estimated_cost_units = choice.estimated_cost_units
