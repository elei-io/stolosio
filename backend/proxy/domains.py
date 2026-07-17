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
    DomainCommandStat,
    DomainProviderSupport,
    DomainProviderTransitionStat,
    GatewaySession,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
    SupportProbe,
)


class DomainSupportState(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class DomainFilters:
    search: str | None = None
    support_state: DomainSupportState | None = None
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
        version, occurred_at, identifier = json.loads(
            base64.urlsafe_b64decode(cursor + padding)
        )
        value = datetime.fromisoformat(occurred_at)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {kind} cursor") from error
    if version != kind or value.tzinfo is None or not identifier:
        raise ValueError(f"invalid {kind} cursor")
    return value, identifier


def _support_counts(rows: list[DomainProviderSupport]) -> dict[str, int]:
    counts = {"unknown": 0, "checking": 0, "supported": 0, "unsupported": 0}
    for row in rows:
        counts[row.support_state] += 1
    return counts


def _expected_plan(
    rows: list[DomainProviderSupport],
    profiles: dict[str, ProviderRoutingProfile],
    configuration: RoutingConfiguration | None,
) -> dict[str, object]:
    policy_version = configuration.support_policy_version if configuration else 1
    candidates: list[dict[str, object]] = []
    for row in rows:
        profile = profiles.get(row.provider)
        if (
            profile is None
            or not profile.automatic_enabled
            or row.support_state != "supported"
            or row.support_policy_version != policy_version
            or row.capability_manifest_version != profile.capability_manifest_version
        ):
            continue
        cost = (
            row.total_cost_units // row.observed_session_count
            if row.observed_session_count
            else profile.cost_units_per_second
        )
        candidates.append({"provider": row.provider, "estimated_cost_units": cost})
    candidates.sort(
        key=lambda candidate: (
            int(candidate["estimated_cost_units"]),
            str(candidate["provider"]),
        )
    )
    default_provider = configuration.default_provider if configuration else "camoufox"
    default_profile = profiles.get(default_provider)
    if (
        default_profile is not None
        and default_profile.automatic_enabled
        and all(candidate["provider"] != default_provider for candidate in candidates)
    ):
        candidates.append(
            {
                "provider": default_provider,
                "estimated_cost_units": default_profile.cost_units_per_second,
            }
        )
    if candidates:
        known_supported = any(
            row.support_state == "supported"
            and row.provider == candidates[0]["provider"]
            for row in rows
        )
        return {
            "reason": "cheapest_supported" if known_supported else "configured_default",
            "candidates": candidates,
        }
    return {"reason": "no_supported_provider", "candidates": []}


def _check(
    state: str,
    *,
    checked_at: datetime | None,
    checking: bool,
) -> dict[str, object]:
    return {
        "state": "checking"
        if checking
        else "not_checked"
        if state == "unknown"
        else state,
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
        if filters.support_state is not None:
            query = query.where(
                exists(
                    select(DomainProviderSupport.domain_id).where(
                        DomainProviderSupport.domain_id == Domain.id,
                        DomainProviderSupport.support_state
                        == filters.support_state.value,
                    )
                )
            )
        if filters.has_active_probes is not None:
            active = exists(
                select(SupportProbe.domain_id).where(
                    SupportProbe.domain_id == Domain.id,
                    SupportProbe.state.in_(("queued", "running")),
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
            occurred_at, identifier = _decode_cursor(before, "domain-v2")
            try:
                domain_id = int(identifier)
            except ValueError as error:
                raise ValueError("invalid domain cursor") from error
            query = query.where(
                (Domain.last_seen_at < occurred_at)
                | ((Domain.last_seen_at == occurred_at) & (Domain.id < domain_id))
            )
        query = query.order_by(Domain.last_seen_at.desc(), Domain.id.desc()).limit(
            limit + 1
        )
        async with self._sessions() as database:
            rows = list(await database.scalars(query))
            has_more = len(rows) > limit
            rows = rows[:limit]
            domain_ids = [domain.id for domain in rows]
            evidence = (
                list(
                    await database.scalars(
                        select(DomainProviderSupport).where(
                            DomainProviderSupport.domain_id.in_(domain_ids)
                        )
                    )
                )
                if domain_ids
                else []
            )
            active_rows = (
                list(
                    await database.execute(
                        select(SupportProbe.domain_id, func.count())
                        .where(
                            SupportProbe.domain_id.in_(domain_ids),
                            SupportProbe.state.in_(("queued", "running")),
                        )
                        .group_by(SupportProbe.domain_id)
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
                row.provider: row
                for row in await database.scalars(select(ProviderRoutingProfile))
            }
            configuration = await database.get(RoutingConfiguration, "global")
            summary = await self._summary(database)
        evidence_by_domain: dict[int, list[DomainProviderSupport]] = {}
        for row in evidence:
            evidence_by_domain.setdefault(row.domain_id, []).append(row)
        active = {domain_id: int(count) for domain_id, count in active_rows}
        transitions = {
            domain_id: int(count or 0) for domain_id, count in transition_rows
        }
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
                "support_counts": _support_counts(
                    evidence_by_domain.get(domain.id, [])
                ),
                "expected_plan": _expected_plan(
                    evidence_by_domain.get(domain.id, []),
                    profiles,
                    configuration,
                ),
            }
            for domain in rows
        ]
        next_cursor = (
            _encode_cursor("domain-v2", rows[-1].last_seen_at, rows[-1].id)
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
            evidence = {
                row.provider: row
                for row in await database.scalars(
                    select(DomainProviderSupport).where(
                        DomainProviderSupport.domain_id == domain_id
                    )
                )
            }
            commands = list(
                await database.scalars(
                    select(DomainCommandStat)
                    .where(DomainCommandStat.domain_id == domain_id)
                    .order_by(
                        DomainCommandStat.command_count.desc(),
                        DomainCommandStat.method,
                    )
                    .limit(50)
                )
            )
            active_providers = set(
                await database.scalars(
                    select(SupportProbe.candidate_provider).where(
                        SupportProbe.domain_id == domain_id,
                        SupportProbe.state.in_(("queued", "running")),
                    )
                )
            )
            transition_count = int(
                await database.scalar(
                    select(
                        func.coalesce(
                            func.sum(DomainProviderTransitionStat.transition_count), 0
                        )
                    ).where(DomainProviderTransitionStat.domain_id == domain_id)
                )
                or 0
            )
        provider_support = []
        for profile in profile_rows:
            row = evidence.get(profile.provider)
            checking = profile.provider in active_providers
            checked_at = row.last_checked_at if row else None
            observed = row.observed_session_count if row else 0
            total_cost = row.total_cost_units if row else 0
            provider_support.append(
                {
                    "provider": profile.provider,
                    "automatic_enabled": profile.automatic_enabled,
                    "support_state": row.support_state if row else "unknown",
                    "successful_probe_count": row.successful_probe_count if row else 0,
                    "failed_probe_count": row.failed_probe_count if row else 0,
                    "inconclusive_probe_count": (
                        row.inconclusive_probe_count if row else 0
                    ),
                    "observed_session_count": observed,
                    "total_cost_units": total_cost,
                    "average_cost_units": (
                        total_cost // observed
                        if observed
                        else profile.cost_units_per_second
                    ),
                    "cost_is_estimate": observed == 0,
                    "last_status_code": row.last_status_code if row else None,
                    "last_checked_at": _iso(checked_at),
                    "last_supported_at": _iso(row.last_supported_at) if row else None,
                    "failure_reason_code": row.failure_reason_code if row else None,
                    "support_policy_version": (
                        row.support_policy_version if row else None
                    ),
                    "capability_manifest_version": (
                        profile.capability_manifest_version
                    ),
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
                        "method_coverage": {
                            **_check(
                                row.method_coverage_state if row else "unknown",
                                checked_at=checked_at,
                                checking=checking,
                            ),
                            "observed_count": (
                                row.method_observed_count if row else 0
                            ),
                            "declared_count": (
                                row.method_declared_count if row else 0
                            ),
                            "unsupported_methods": (
                                row.unsupported_methods if row else []
                            ),
                            "manifest_version": (
                                profile.capability_manifest_version
                            ),
                        },
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
                list(evidence.values()), profiles, configuration
            ),
            "providers": provider_support,
            "commands": [
                {
                    "method": row.method,
                    "command_count": row.command_count,
                    "session_count": row.session_count,
                    "first_seen_at": _iso(row.first_seen_at),
                    "last_seen_at": _iso(row.last_seen_at),
                }
                for row in commands
            ],
        }

    async def probes(
        self,
        domain_id: int,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> DomainProbePage | None:
        query = select(SupportProbe).where(SupportProbe.domain_id == domain_id)
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "support-probe-v1")
            query = query.where(
                (SupportProbe.created_at < occurred_at)
                | (
                    (SupportProbe.created_at == occurred_at)
                    & (SupportProbe.id < identifier)
                )
            )
        query = query.order_by(
            SupportProbe.created_at.desc(), SupportProbe.id.desc()
        ).limit(limit + 1)
        async with self._sessions() as database:
            if await database.get(Domain, domain_id) is None:
                return None
            rows = list(await database.scalars(query))
        has_more = len(rows) > limit
        rows = rows[:limit]
        probes = [
            {
                "id": row.id,
                "source_session_id": row.source_session_id,
                "candidate_provider": row.candidate_provider,
                "trigger": row.trigger,
                "state": row.state,
                "outcome": row.outcome,
                "navigation_state": row.navigation_state,
                "status_state": row.status_state,
                "headers_state": row.headers_state,
                "method_coverage_state": row.method_coverage_state,
                "content_state": row.content_state,
                "status_code": row.status_code,
                "reason_codes": row.reason_codes,
                "method_observed_count": row.method_observed_count,
                "method_declared_count": row.method_declared_count,
                "unsupported_methods": row.unsupported_methods,
                "content_facts": row.content_facts,
                "cost_units": row.cost_units,
                "created_at": _iso(row.created_at),
                "finished_at": _iso(row.finished_at),
            }
            for row in rows
        ]
        next_cursor = (
            _encode_cursor("support-probe-v1", rows[-1].created_at, rows[-1].id)
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
                | (
                    (GatewaySession.created_at == occurred_at)
                    & (GatewaySession.id < identifier)
                )
            )
        query = query.order_by(
            GatewaySession.created_at.desc(), GatewaySession.id.desc()
        ).limit(limit + 1)
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
                    "actual_cost_units": sum(
                        attempt.actual_cost_units or 0 for attempt in session_attempts
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
        supported = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderSupport.domain_id))).where(
                    DomainProviderSupport.support_state == "supported"
                )
            )
            or 0
        )
        checking = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderSupport.domain_id))).where(
                    DomainProviderSupport.support_state == "checking"
                )
            )
            or 0
        )
        unsupported = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderSupport.domain_id))).where(
                    DomainProviderSupport.support_state == "unsupported"
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
        return {
            "known_domains": known,
            "supported_domains": supported,
            "checking_domains": checking,
            "unsupported_domains": unsupported,
            "no_evidence_domains": max(0, known - len(
                set(
                    await database.scalars(
                        select(DomainProviderSupport.domain_id).distinct()
                    )
                )
            )),
            "transitioned_domains": transitioned,
        }
