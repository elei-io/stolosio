from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderRuntimeState,
    ProviderRoutingProfile,
    RoutingConfiguration,
)
from backend.proxy.capabilities import CapabilityRegistry, capability_registry
from backend.proxy.contracts import ProviderName

_DEFAULT_COSTS = {
    ProviderName.HTTP: 1,
    ProviderName.LIGHTPANDA: 10,
    ProviderName.CHROMIUM: 100,
    ProviderName.BROWSERLESS: 100,
    ProviderName.CAMOUFOX: 120,
}


class NoSupportedProvider(RuntimeError):
    """No healthy, runtime-eligible provider can execute the journey."""


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider: ProviderName
    estimated_cost_units: int
    position: int


@dataclass(frozen=True, slots=True)
class ProviderPlan:
    candidates: tuple[ProviderCandidate, ...]
    reason: str
    configuration_version: int
    health_policy_version: int

    @property
    def first(self) -> ProviderCandidate:
        if not self.candidates:
            raise NoSupportedProvider("No eligible provider remains in the plan")
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class RoutingSettings:
    default_provider: ProviderName
    existing_domain_probe_rate_basis_points: int
    required_health_confirmations: int
    health_policy_version: int
    configuration_version: int


class RoutingRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        capabilities: CapabilityRegistry = capability_registry,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities

    async def ensure_defaults(self) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            await database.execute(
                insert(RoutingConfiguration)
                .values(
                    key="global",
                    default_provider=ProviderName.CAMOUFOX.value,
                    existing_domain_probe_rate_basis_points=100,
                    required_health_confirmations=1,
                    health_policy_version=1,
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
                        provider_contract_version=1,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[ProviderRoutingProfile.provider]
                    )
                )

    async def settings(self) -> RoutingSettings:
        async with self._sessions() as database:
            row = await database.get(RoutingConfiguration, "global")
        if row is None:
            return RoutingSettings(ProviderName.CAMOUFOX, 100, 1, 1, 1)
        return RoutingSettings(
            ProviderName(row.default_provider),
            row.existing_domain_probe_rate_basis_points,
            row.required_health_confirmations,
            row.health_policy_version,
            row.configuration_version,
        )

    async def update_settings(
        self,
        *,
        default_provider: ProviderName | None = None,
        existing_domain_probe_rate_basis_points: int | None = None,
        required_health_confirmations: int | None = None,
    ) -> RoutingSettings:
        async with self._sessions.begin() as database:
            row = await database.get(
                RoutingConfiguration, "global", with_for_update=True
            )
            if row is None:
                raise RuntimeError("Routing configuration is not initialized")
            if default_provider is not None:
                profile = await database.get(
                    ProviderRoutingProfile, default_provider.value
                )
                if profile is None or not profile.automatic_enabled:
                    raise ValueError(
                        "Default provider must be enabled for automatic routing"
                    )
                row.default_provider = default_provider.value
            if existing_domain_probe_rate_basis_points is not None:
                row.existing_domain_probe_rate_basis_points = (
                    existing_domain_probe_rate_basis_points
                )
            if required_health_confirmations is not None:
                row.required_health_confirmations = required_health_confirmations
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
                ProviderRoutingProfile, provider.value, with_for_update=True
            )
            if row is None:
                raise ValueError("Unknown provider routing profile")
            configuration = await database.get(
                RoutingConfiguration, "global", with_for_update=True
            )
            if automatic_enabled is not None:
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
            if configuration is not None:
                configuration.configuration_version += 1
                configuration.updated_at = datetime.now(UTC)
        return row

    async def plan(
        self,
        hostname: str,
        *,
        required_commands: tuple[tuple[str, dict | None], ...] = (),
        exclude: frozenset[ProviderName] = frozenset(),
    ) -> ProviderPlan:
        async with self._sessions() as database:
            configuration = await database.get(RoutingConfiguration, "global")
            if configuration is None:
                configuration_version = 1
                health_policy_version = 1
                default_provider = ProviderName.CAMOUFOX
            else:
                configuration_version = configuration.configuration_version
                health_policy_version = configuration.health_policy_version
                default_provider = ProviderName(configuration.default_provider)
            profiles = list(
                await database.scalars(
                    select(ProviderRoutingProfile).where(
                        ProviderRoutingProfile.automatic_enabled.is_(True)
                    )
                )
            )
            profile_by_provider = {
                ProviderName(profile.provider): profile for profile in profiles
            }
            domain_id = await database.scalar(
                select(Domain.id).where(Domain.hostname == hostname)
            )
            health = (
                list(
                    await database.scalars(
                        select(DomainProviderHealth).where(
                            DomainProviderHealth.domain_id == domain_id
                        )
                    )
                )
                if domain_id is not None
                else []
            )
            runtime = {
                ProviderName(row.provider): row
                for row in (
                    list(
                        await database.scalars(
                            select(DomainProviderRuntimeState).where(
                                DomainProviderRuntimeState.domain_id == domain_id
                            )
                        )
                    )
                    if domain_id is not None
                    else []
                )
            }
            costs = {
                ProviderName(row.provider): row
                for row in (
                    list(
                        await database.scalars(
                            select(DomainProviderCostStat).where(
                                DomainProviderCostStat.domain_id == domain_id
                            )
                        )
                    )
                    if domain_id is not None
                    else []
                )
            }

        def command_compatible(provider: ProviderName) -> bool:
            return all(
                self._capabilities.supports(provider, method, params)
                for method, params in required_commands
            )

        eligible: list[tuple[ProviderName, int]] = []
        for evidence in health:
            provider = ProviderName(evidence.provider)
            profile = profile_by_provider.get(provider)
            runtime_state = runtime.get(provider)
            suppressed = (
                runtime_state is not None
                and runtime_state.provider_contract_version
                == profile.provider_contract_version
                and runtime_state.state == "suppressed"
            ) if profile is not None else False
            if (
                profile is None
                or provider in exclude
                or evidence.health_state != "healthy"
                or evidence.health_policy_version != health_policy_version
                or evidence.provider_contract_version
                != profile.provider_contract_version
                or suppressed
                or not command_compatible(provider)
            ):
                continue
            cost = costs.get(provider)
            expected_cost = (
                cost.total_cost_units // cost.observed_attempt_count
                if cost is not None and cost.observed_attempt_count
                else profile.cost_units_per_second
            )
            eligible.append((provider, expected_cost))

        eligible.sort(key=lambda item: (item[1], item[0].value))
        if eligible:
            return ProviderPlan(
                tuple(
                    ProviderCandidate(provider, cost, position)
                    for position, (provider, cost) in enumerate(eligible)
                ),
                "cheapest_eligible",
                configuration_version,
                health_policy_version,
            )

        # The configured default is a bootstrap path only. Once Harbor has current
        # healthy evidence, runtime suppression and health conclusions are binding.
        has_current_health = any(
            row.health_state == "healthy"
            and row.health_policy_version == health_policy_version
            for row in health
        )
        default_profile = profile_by_provider.get(default_provider)
        if (
            not has_current_health
            and default_profile is not None
            and default_provider not in exclude
            and command_compatible(default_provider)
        ):
            return ProviderPlan(
                (
                    ProviderCandidate(
                        default_provider,
                        default_profile.cost_units_per_second,
                        0,
                    ),
                ),
                "configured_default_bootstrap",
                configuration_version,
                health_policy_version,
            )
        raise NoSupportedProvider(
            f"No healthy, runtime-eligible provider for {hostname} "
            "can execute the required commands"
        )

    async def record_selection(
        self,
        attempt_id: str,
        hostname: str,
        plan: ProviderPlan,
        candidate: ProviderCandidate,
        *,
        transition_trigger: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            domain_id = await database.scalar(
                insert(Domain)
                .values(
                    hostname=hostname,
                    first_seen_at=now,
                    last_seen_at=now,
                    session_count=0,
                )
                .on_conflict_do_update(
                    index_elements=[Domain.hostname],
                    set_={"last_seen_at": now},
                )
                .returning(Domain.id)
            )
            row = await database.get(
                AcquisitionAttempt, attempt_id, with_for_update=True
            )
            if row is None:
                return
            row.domain_id = domain_id
            row.selection_reason = plan.reason
            row.plan_version = plan.configuration_version
            row.plan_position = candidate.position
            row.transition_trigger = transition_trigger
            row.estimated_cost_units = candidate.estimated_cost_units
