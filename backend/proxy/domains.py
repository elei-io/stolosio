import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderTransitionStat,
    ExternalProviderLimit,
    GatewaySession,
    HealthProbe,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
)


class DomainEligibilityState(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    ELIGIBLE = "eligible"
    UNHEALTHY = "unhealthy"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class DomainFilters:
    search: str | None = None
    eligibility_state: DomainEligibilityState | None = None
    has_active_probes: bool | None = None
    has_transitions: bool | None = None


@dataclass(frozen=True, slots=True)
class DomainPage:
    domains: list[dict[str, object]]
    summary: dict[str, int]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DomainProbePage:
    probes: list[dict[str, object]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DomainSessionPage:
    sessions: list[dict[str, object]]
    next_cursor: str | None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _encode_cursor(kind: str, occurred_at: datetime, identifier: str | int) -> str:
    payload = json.dumps(
        [kind, occurred_at.astimezone(UTC).isoformat(), str(identifier)],
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, kind: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        version, occurred_at, identifier = json.loads(base64.urlsafe_b64decode(cursor + padding))
        value = datetime.fromisoformat(occurred_at)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {kind} cursor") from error
    if version != kind or value.tzinfo is None or not identifier:
        raise ValueError(f"invalid {kind} cursor")
    return value, identifier


def _health_counts(rows: list[DomainProviderHealth]) -> dict[str, int]:
    counts = {
        "unknown": 0,
        # In-flight checks are reported separately through active_probe_count.
        "checking": 0,
        "healthy": 0,
        "unhealthy": 0,
        "inconclusive": 0,
    }
    for row in rows:
        counts[row.health_state] += 1
    return counts


def _expected_plan(
    health: list[DomainProviderHealth],
    costs: dict[str, DomainProviderCostStat],
    profiles: dict[str, ProviderRoutingProfile],
    configuration: RoutingConfiguration | None,
    *,
    browserbase_available: bool,
) -> dict[str, object]:
    policy_version = configuration.health_policy_version if configuration else 1
    candidates: list[dict[str, object]] = []
    for row in health:
        if row.provider == "browserbase":
            continue
        profile = profiles.get(row.provider)
        if (
            profile is None
            or not profile.automatic_enabled
            or row.health_state != "healthy"
            or row.health_policy_version != policy_version
            or row.provider_contract_version != profile.provider_contract_version
        ):
            continue
        cost = costs.get(row.provider)
        expected_cost = (
            cost.total_cost_units // cost.observed_attempt_count
            if cost is not None and cost.observed_attempt_count
            else profile.cost_units_per_second
        )
        candidates.append({"provider": row.provider, "estimated_cost_units": expected_cost})
    browserbase_profile = profiles.get("browserbase")
    browserbase_candidate = (
        {
            "provider": "browserbase",
            "estimated_cost_units": (
                costs["browserbase"].total_cost_units // costs["browserbase"].observed_attempt_count
                if "browserbase" in costs and costs["browserbase"].observed_attempt_count
                else browserbase_profile.cost_units_per_second
            ),
        }
        if browserbase_profile is not None
        and browserbase_profile.automatic_enabled
        and browserbase_available
        else None
    )
    if candidates and browserbase_candidate is not None:
        candidates.append(browserbase_candidate)
    candidates.sort(
        key=lambda candidate: (
            int(candidate["estimated_cost_units"]),
            str(candidate["provider"]),
        )
    )
    if candidates:
        return {"reason": "cheapest_eligible", "candidates": candidates}

    has_current_health = any(row.health_policy_version == policy_version for row in health)
    default_provider = configuration.default_provider if configuration else "browserless"
    default_profile = profiles.get(default_provider)
    if (
        not has_current_health
        and default_profile is not None
        and default_profile.automatic_enabled
        and (default_provider != "browserbase" or browserbase_available)
    ):
        bootstrap = [
            {
                "provider": default_provider,
                "estimated_cost_units": default_profile.cost_units_per_second,
            }
        ]
        if browserbase_candidate is not None and default_provider != "browserbase":
            bootstrap.append(browserbase_candidate)
        return {
            "reason": "configured_default_bootstrap",
            "candidates": bootstrap,
        }
    if browserbase_candidate is not None:
        return {
            "reason": "cheapest_eligible",
            "candidates": [browserbase_candidate],
        }
    return {"reason": "no_eligible_provider", "candidates": []}


def _check(
    state: str,
    *,
    checked_at: datetime | None,
    checking: bool,
) -> dict[str, object]:
    return {
        "state": ("checking" if checking else "not_checked" if state == "unknown" else state),
        "checked_at": _iso(checked_at),
    }


class DomainQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def domains(
        self,
        filters: DomainFilters,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> DomainPage:
        query = select(Domain)
        if filters.search:
            query = query.where(Domain.hostname.ilike(f"%{filters.search.strip()}%"))
        if filters.eligibility_state is not None:
            state = filters.eligibility_state
            if state is DomainEligibilityState.ELIGIBLE:
                query = query.where(
                    exists(
                        select(DomainProviderHealth.domain_id).where(
                            DomainProviderHealth.domain_id == Domain.id,
                            DomainProviderHealth.health_state == "healthy",
                        )
                    )
                )
            elif state is DomainEligibilityState.UNKNOWN:
                query = query.where(
                    ~exists(
                        select(DomainProviderHealth.domain_id).where(
                            DomainProviderHealth.domain_id == Domain.id
                        )
                    )
                )
            elif state is DomainEligibilityState.CHECKING:
                query = query.where(
                    exists(
                        select(HealthProbe.domain_id).where(
                            HealthProbe.domain_id == Domain.id,
                            HealthProbe.state.in_(("queued", "running")),
                        )
                    )
                )
            else:
                query = query.where(
                    exists(
                        select(DomainProviderHealth.domain_id).where(
                            DomainProviderHealth.domain_id == Domain.id,
                            DomainProviderHealth.health_state == state.value,
                        )
                    )
                )
        if filters.has_active_probes is not None:
            active = exists(
                select(HealthProbe.domain_id).where(
                    HealthProbe.domain_id == Domain.id,
                    HealthProbe.state.in_(("queued", "running")),
                )
            )
            query = query.where(active if filters.has_active_probes else ~active)
        if filters.has_transitions is not None:
            transitioned = exists(
                select(DomainProviderTransitionStat.domain_id).where(
                    DomainProviderTransitionStat.domain_id == Domain.id
                )
            )
            query = query.where(transitioned if filters.has_transitions else ~transitioned)
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "domain-v3")
            try:
                domain_id = int(identifier)
            except ValueError as error:
                raise ValueError("invalid domain cursor") from error
            query = query.where(
                (Domain.last_seen_at < occurred_at)
                | ((Domain.last_seen_at == occurred_at) & (Domain.id < domain_id))
            )
        query = query.order_by(Domain.last_seen_at.desc(), Domain.id.desc()).limit(limit + 1)
        async with self._sessions() as database:
            rows = list(await database.scalars(query))
            has_more = len(rows) > limit
            rows = rows[:limit]
            domain_ids = [domain.id for domain in rows]
            health = (
                list(
                    await database.scalars(
                        select(DomainProviderHealth).where(
                            DomainProviderHealth.domain_id.in_(domain_ids)
                        )
                    )
                )
                if domain_ids
                else []
            )
            costs = (
                list(
                    await database.scalars(
                        select(DomainProviderCostStat).where(
                            DomainProviderCostStat.domain_id.in_(domain_ids)
                        )
                    )
                )
                if domain_ids
                else []
            )
            active_rows = (
                list(
                    await database.execute(
                        select(HealthProbe.domain_id, func.count())
                        .where(
                            HealthProbe.domain_id.in_(domain_ids),
                            HealthProbe.state.in_(("queued", "running")),
                        )
                        .group_by(HealthProbe.domain_id)
                    )
                )
                if domain_ids
                else []
            )
            transition_rows = (
                list(
                    await database.execute(
                        select(
                            DomainProviderTransitionStat.domain_id,
                            func.sum(DomainProviderTransitionStat.transition_count),
                        )
                        .where(DomainProviderTransitionStat.domain_id.in_(domain_ids))
                        .group_by(DomainProviderTransitionStat.domain_id)
                    )
                )
                if domain_ids
                else []
            )
            profiles = {
                row.provider: row for row in await database.scalars(select(ProviderRoutingProfile))
            }
            configuration = await database.get(RoutingConfiguration, "global")
            browserbase_capacity = await database.get(
                ExternalProviderLimit,
                "browserbase",
            )
            summary = await self._summary(database)
        health_by_domain: dict[int, list[DomainProviderHealth]] = {}
        costs_by_domain: dict[int, dict[str, DomainProviderCostStat]] = {}
        for row in health:
            health_by_domain.setdefault(row.domain_id, []).append(row)
        for row in costs:
            costs_by_domain.setdefault(row.domain_id, {})[row.provider] = row
        active = {domain_id: int(count) for domain_id, count in active_rows}
        transitions = {domain_id: int(count or 0) for domain_id, count in transition_rows}
        result = [
            {
                "id": domain.id,
                "hostname": domain.hostname,
                "first_seen_at": _iso(domain.first_seen_at),
                "last_seen_at": _iso(domain.last_seen_at),
                "session_count": domain.session_count,
                "eligible_acquisition_count": domain.eligible_acquisition_count,
                "active_probe_count": active.get(domain.id, 0),
                "transition_count": transitions.get(domain.id, 0),
                "health_counts": _health_counts(health_by_domain.get(domain.id, [])),
                "expected_plan": _expected_plan(
                    health_by_domain.get(domain.id, []),
                    costs_by_domain.get(domain.id, {}),
                    profiles,
                    configuration,
                    browserbase_available=(
                        browserbase_capacity is not None
                        and browserbase_capacity.enabled
                        and browserbase_capacity.max_active_sessions > 0
                    ),
                ),
            }
            for domain in rows
        ]
        next_cursor = (
            _encode_cursor("domain-v3", rows[-1].last_seen_at, rows[-1].id)
            if has_more and rows
            else None
        )
        return DomainPage(result, summary, next_cursor)

    async def domain(self, domain_id: int) -> dict[str, object] | None:
        async with self._sessions() as database:
            domain = await database.get(Domain, domain_id)
            if domain is None:
                return None
            configuration = await database.get(RoutingConfiguration, "global")
            profile_rows = list(
                await database.scalars(
                    select(ProviderRoutingProfile).order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            )
            profiles = {row.provider: row for row in profile_rows}
            health = {
                row.provider: row
                for row in await database.scalars(
                    select(DomainProviderHealth).where(DomainProviderHealth.domain_id == domain_id)
                )
            }
            browserbase_capacity = await database.get(
                ExternalProviderLimit,
                "browserbase",
            )
            costs = {
                row.provider: row
                for row in await database.scalars(
                    select(DomainProviderCostStat).where(
                        DomainProviderCostStat.domain_id == domain_id
                    )
                )
            }
            active_providers = set(
                await database.scalars(
                    select(HealthProbe.candidate_provider).where(
                        HealthProbe.domain_id == domain_id,
                        HealthProbe.state.in_(("queued", "running")),
                    )
                )
            )
            transition_count = int(
                await database.scalar(
                    select(
                        func.coalesce(
                            func.sum(DomainProviderTransitionStat.transition_count),
                            0,
                        )
                    ).where(DomainProviderTransitionStat.domain_id == domain_id)
                )
                or 0
            )
        browserbase_available = (
            browserbase_capacity is not None
            and browserbase_capacity.enabled
            and browserbase_capacity.max_active_sessions > 0
        )
        providers = []
        for profile in profile_rows:
            row = health.get(profile.provider)
            cost = costs.get(profile.provider)
            checking = profile.provider in active_providers
            checked_at = row.last_checked_at if row else None
            observed = cost.observed_attempt_count if cost else 0
            total_cost = cost.total_cost_units if cost else 0
            health_current = (
                row is not None
                and row.health_state == "healthy"
                and row.health_policy_version
                == (configuration.health_policy_version if configuration else 1)
                and row.provider_contract_version == profile.provider_contract_version
            )
            providers.append(
                {
                    "provider": profile.provider,
                    "automatic_enabled": profile.automatic_enabled,
                    "health_state": row.health_state if row else "unknown",
                    "routing_eligible": (
                        profile.automatic_enabled
                        and (
                            browserbase_available
                            if profile.provider == "browserbase"
                            else health_current
                        )
                    ),
                    "successful_probe_count": (row.successful_probe_count if row else 0),
                    "failed_probe_count": row.failed_probe_count if row else 0,
                    "inconclusive_probe_count": (row.inconclusive_probe_count if row else 0),
                    "observed_attempt_count": observed,
                    "total_cost_units": total_cost,
                    "average_cost_units": (
                        total_cost // observed if observed else profile.cost_units_per_second
                    ),
                    "cost_is_estimate": observed == 0,
                    "last_status_code": row.last_status_code if row else None,
                    "last_checked_at": _iso(checked_at),
                    "last_healthy_at": (_iso(row.last_healthy_at) if row else None),
                    "failure_reason_code": (row.failure_reason_code if row else None),
                    "health_policy_version": (row.health_policy_version if row else None),
                    "provider_contract_version": (profile.provider_contract_version),
                    "checks": {
                        "navigation": _check(
                            row.navigation_state if row else "unknown",
                            checked_at=checked_at,
                            checking=checking,
                        ),
                        "status": _check(
                            row.status_state if row else "unknown",
                            checked_at=checked_at,
                            checking=checking,
                        ),
                        "headers": _check(
                            row.headers_state if row else "unknown",
                            checked_at=checked_at,
                            checking=checking,
                        ),
                        "content": _check(
                            row.content_state if row else "unknown",
                            checked_at=checked_at,
                            checking=checking,
                        ),
                    },
                }
            )
        return {
            "id": domain.id,
            "hostname": domain.hostname,
            "first_seen_at": _iso(domain.first_seen_at),
            "last_seen_at": _iso(domain.last_seen_at),
            "session_count": domain.session_count,
            "eligible_acquisition_count": domain.eligible_acquisition_count,
            "active_probe_count": len(active_providers),
            "transition_count": transition_count,
            "expected_plan": _expected_plan(
                list(health.values()),
                costs,
                profiles,
                configuration,
                browserbase_available=browserbase_available,
            ),
            "providers": providers,
        }

    async def probes(
        self,
        domain_id: int,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> DomainProbePage | None:
        query = select(HealthProbe).where(HealthProbe.domain_id == domain_id)
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "health-probe-v1")
            query = query.where(
                (HealthProbe.created_at < occurred_at)
                | ((HealthProbe.created_at == occurred_at) & (HealthProbe.id < identifier))
            )
        query = query.order_by(HealthProbe.created_at.desc(), HealthProbe.id.desc()).limit(
            limit + 1
        )
        async with self._sessions() as database:
            if await database.get(Domain, domain_id) is None:
                return None
            rows = list(await database.scalars(query))
        has_more = len(rows) > limit
        rows = rows[:limit]
        probes = [
            {
                "id": row.id,
                "cohort_id": row.cohort_id,
                "source_session_id": row.source_session_id,
                "candidate_provider": row.candidate_provider,
                "trigger": row.trigger,
                "state": row.state,
                "outcome": row.outcome,
                "navigation_state": row.navigation_state,
                "status_state": row.status_state,
                "headers_state": row.headers_state,
                "content_state": row.content_state,
                "status_code": row.status_code,
                "reason_codes": row.reason_codes,
                "content_facts": row.content_facts,
                "comparison_state": row.comparison_state,
                "cost_units": row.cost_units,
                "created_at": _iso(row.created_at),
                "finished_at": _iso(row.finished_at),
            }
            for row in rows
        ]
        next_cursor = (
            _encode_cursor("health-probe-v1", rows[-1].created_at, rows[-1].id)
            if has_more and rows
            else None
        )
        return DomainProbePage(probes, next_cursor)

    async def sessions(
        self,
        domain_id: int,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> DomainSessionPage | None:
        query = (
            select(GatewaySession)
            .join(SessionDomain, SessionDomain.session_id == GatewaySession.id)
            .where(SessionDomain.domain_id == domain_id)
        )
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "domain-session-v1")
            query = query.where(
                (GatewaySession.created_at < occurred_at)
                | ((GatewaySession.created_at == occurred_at) & (GatewaySession.id < identifier))
            )
        query = query.order_by(GatewaySession.created_at.desc(), GatewaySession.id.desc()).limit(
            limit + 1
        )
        async with self._sessions() as database:
            if await database.get(Domain, domain_id) is None:
                return None
            rows = list(await database.scalars(query))
            has_more = len(rows) > limit
            rows = rows[:limit]
            session_ids = [row.id for row in rows]
            attempts = (
                list(
                    await database.scalars(
                        select(AcquisitionAttempt)
                        .where(AcquisitionAttempt.session_id.in_(session_ids))
                        .order_by(
                            AcquisitionAttempt.session_id,
                            AcquisitionAttempt.ordinal,
                        )
                    )
                )
                if session_ids
                else []
            )
        attempts_by_session: dict[str, list[AcquisitionAttempt]] = {}
        for attempt in attempts:
            attempts_by_session.setdefault(attempt.session_id, []).append(attempt)
        sessions = []
        for row in rows:
            session_attempts = attempts_by_session.get(row.id, [])
            sessions.append(
                {
                    "id": row.id,
                    "client_reference": row.client_reference,
                    "state": row.state,
                    "selection_mode": (
                        "explicit"
                        if "harbor.provider.slug" in row.requested_settings
                        and row.requested_settings["harbor.provider.slug"] != "auto"
                        else "automatic"
                    ),
                    "providers": [attempt.provider for attempt in session_attempts],
                    "selection_reason": next(
                        (
                            attempt.selection_reason
                            for attempt in reversed(session_attempts)
                            if attempt.selection_reason is not None
                        ),
                        None,
                    ),
                    "transition_triggers": [
                        attempt.transition_trigger
                        for attempt in session_attempts
                        if attempt.transition_trigger is not None
                    ],
                    "modeled_cost_units": sum(
                        attempt.modeled_cost_units or 0
                        for attempt in session_attempts
                    ),
                    "created_at": _iso(row.created_at),
                    "closed_at": _iso(row.closed_at),
                    "terminal_reason": row.terminal_reason,
                }
            )
        next_cursor = (
            _encode_cursor("domain-session-v1", rows[-1].created_at, rows[-1].id)
            if has_more and rows
            else None
        )
        return DomainSessionPage(sessions, next_cursor)

    @staticmethod
    async def _summary(database: AsyncSession) -> dict[str, int]:
        known = int(await database.scalar(select(func.count()).select_from(Domain)) or 0)
        healthy = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderHealth.domain_id))).where(
                    DomainProviderHealth.health_state == "healthy"
                )
            )
            or 0
        )
        checking = int(
            await database.scalar(
                select(func.count(func.distinct(HealthProbe.domain_id))).where(
                    HealthProbe.state.in_(("queued", "running"))
                )
            )
            or 0
        )
        unhealthy = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderHealth.domain_id))).where(
                    DomainProviderHealth.health_state == "unhealthy"
                )
            )
            or 0
        )
        transitioned = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderTransitionStat.domain_id)))
            )
            or 0
        )
        observed = set(await database.scalars(select(DomainProviderHealth.domain_id).distinct()))
        return {
            "known_domains": known,
            "healthy_domains": healthy,
            "checking_domains": checking,
            "unhealthy_domains": unhealthy,
            "no_evidence_domains": max(0, known - len(observed)),
            "transitioned_domains": transitioned,
        }
