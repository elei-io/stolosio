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
    DomainPromotionStat,
    DomainProviderProfile,
    GatewaySession,
    ProviderRoutingProfile,
    QualificationProbe,
    RoutingConfiguration,
    SessionDomain,
)
from backend.proxy.capabilities.manifests import PROVIDER_METHODS
from backend.proxy.contracts import ProviderName


class DomainQualificationState(StrEnum):
    UNQUALIFIED = "unqualified"
    PROBING = "probing"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DomainFilters:
    search: str | None = None
    qualification_state: DomainQualificationState | None = None
    has_active_probes: bool | None = None
    has_promotions: bool | None = None


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
        decoded = json.loads(base64.urlsafe_b64decode(cursor + padding))
        version, occurred_at, identifier = decoded
        value = datetime.fromisoformat(occurred_at)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {kind} cursor") from error
    if version != kind or value.tzinfo is None or not identifier:
        raise ValueError(f"invalid {kind} cursor")
    return value, identifier


def _provider_evidence(
    rows: list[DomainProviderProfile],
    profiles: dict[str, ProviderRoutingProfile],
    default_provider: str,
) -> tuple[dict[str, object], dict[str, int]]:
    candidates: list[tuple[int, str]] = []
    counts = {"qualified": 0, "probing": 0, "rejected": 0}
    for row in rows:
        if row.qualification_state in counts:
            counts[row.qualification_state] += 1
        profile = profiles.get(row.provider)
        if (
            row.qualification_state == DomainQualificationState.QUALIFIED
            and profile is not None
            and profile.automatic_enabled
        ):
            expected_cost = (
                row.total_cost_units // row.observed_session_count
                if row.observed_session_count
                else profile.cost_units_per_second
            )
            candidates.append((expected_cost, row.provider))

    if candidates:
        cost, provider = min(candidates, key=lambda item: (item[0], item[1]))
        return (
            {
                "provider": provider,
                "reason": "cheapest_qualified",
                "estimated_cost_units": cost,
            },
            counts,
        )

    fallback = profiles.get(default_provider)
    return (
        {
            "provider": default_provider,
            "reason": "unqualified_domain_default",
            "estimated_cost_units": fallback.cost_units_per_second if fallback else 0,
        },
        counts,
    )


def _comparison_check(
    value: bool | None,
    *,
    checked_at: datetime | None,
    checking: bool,
) -> dict[str, object]:
    return {
        "state": (
            "matches"
            if value is True
            else "differs"
            if value is False
            else "checking"
            if checking
            else "not_checked"
        ),
        "checked_at": _iso(checked_at) if value is not None else None,
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
        query = select(Domain, DomainPromotionStat).outerjoin(
            DomainPromotionStat,
            DomainPromotionStat.domain_id == Domain.id,
        )
        if filters.search:
            query = query.where(Domain.hostname.ilike(f"%{filters.search.strip()}%"))
        if filters.qualification_state is not None:
            query = query.where(
                exists(
                    select(DomainProviderProfile.domain_id).where(
                        DomainProviderProfile.domain_id == Domain.id,
                        DomainProviderProfile.qualification_state
                        == filters.qualification_state.value,
                    )
                )
            )
        if filters.has_active_probes is not None:
            active = exists(
                select(QualificationProbe.domain_id).where(
                    QualificationProbe.domain_id == Domain.id,
                    QualificationProbe.state.in_(("queued", "running")),
                )
            )
            query = query.where(active if filters.has_active_probes else ~active)
        if filters.has_promotions is not None:
            promoted = func.coalesce(DomainPromotionStat.promotion_count, 0) > 0
            query = query.where(promoted if filters.has_promotions else ~promoted)
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "domain-v1")
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
            rows = list(await database.execute(query))
            has_more = len(rows) > limit
            rows = rows[:limit]
            domain_ids = [domain.id for domain, _ in rows]
            evidence_rows = (
                list(
                    await database.scalars(
                        select(DomainProviderProfile).where(
                            DomainProviderProfile.domain_id.in_(domain_ids)
                        )
                    )
                )
                if domain_ids
                else []
            )
            active_probe_rows = (
                list(
                    await database.execute(
                        select(QualificationProbe.domain_id, func.count())
                        .where(
                            QualificationProbe.domain_id.in_(domain_ids),
                            QualificationProbe.state.in_(("queued", "running")),
                        )
                        .group_by(QualificationProbe.domain_id)
                    )
                )
                if domain_ids
                else []
            )
            profiles = {
                profile.provider: profile
                for profile in await database.scalars(select(ProviderRoutingProfile))
            }
            configuration = await database.get(RoutingConfiguration, "global")
            default_provider = (
                configuration.default_provider if configuration is not None else "camoufox"
            )
            summary = await self._summary(database)

        evidence_by_domain: dict[int, list[DomainProviderProfile]] = {}
        for evidence in evidence_rows:
            evidence_by_domain.setdefault(evidence.domain_id, []).append(evidence)
        active_probes = {domain_id: count for domain_id, count in active_probe_rows}

        result: list[dict[str, object]] = []
        for domain, promotion in rows:
            route, counts = _provider_evidence(
                evidence_by_domain.get(domain.id, []),
                profiles,
                default_provider,
            )
            result.append(
                {
                    "id": domain.id,
                    "hostname": domain.hostname,
                    "first_seen_at": _iso(domain.first_seen_at),
                    "last_seen_at": _iso(domain.last_seen_at),
                    "session_count": domain.session_count,
                    "eligible_acquisition_count": domain.eligible_acquisition_count,
                    "promotion_count": promotion.promotion_count if promotion else 0,
                    "active_probe_count": active_probes.get(domain.id, 0),
                    "qualification_counts": counts,
                    "current_route": route,
                }
            )

        next_cursor = (
            _encode_cursor("domain-v1", rows[-1][0].last_seen_at, rows[-1][0].id)
            if has_more and rows
            else None
        )
        return DomainPage(domains=result, summary=summary, next_cursor=next_cursor)

    async def domain(self, domain_id: int) -> dict[str, object] | None:
        async with self._sessions() as database:
            domain = await database.get(Domain, domain_id)
            if domain is None:
                return None
            promotion = await database.get(DomainPromotionStat, domain_id)
            configuration = await database.get(RoutingConfiguration, "global")
            default_provider = (
                configuration.default_provider if configuration is not None else "camoufox"
            )
            profiles = {
                profile.provider: profile
                for profile in await database.scalars(
                    select(ProviderRoutingProfile).order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            }
            evidence = {
                row.provider: row
                for row in await database.scalars(
                    select(DomainProviderProfile).where(
                        DomainProviderProfile.domain_id == domain_id
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
            probes = list(
                await database.scalars(
                    select(QualificationProbe)
                    .where(QualificationProbe.domain_id == domain_id)
                    .order_by(
                        QualificationProbe.created_at.desc(),
                        QualificationProbe.id.desc(),
                    )
                )
            )
            active_probe_count = int(
                await database.scalar(
                    select(func.count())
                    .select_from(QualificationProbe)
                    .where(
                        QualificationProbe.domain_id == domain_id,
                        QualificationProbe.state.in_(("queued", "running")),
                    )
                )
                or 0
            )

        route, _ = _provider_evidence(list(evidence.values()), profiles, default_provider)
        latest_completed: dict[str, QualificationProbe] = {}
        active_providers: set[str] = set()
        for probe in probes:
            if probe.state in {"queued", "running"}:
                active_providers.add(probe.candidate_provider)
            elif (
                probe.candidate_provider not in latest_completed
                and probe.state in {"completed", "failed"}
            ):
                latest_completed[probe.candidate_provider] = probe
        observed_methods = {command.method for command in commands}
        method_checked_at = max(
            (command.last_seen_at for command in commands),
            default=None,
        )
        provider_evidence: list[dict[str, object]] = []
        for provider, profile in profiles.items():
            row = evidence.get(provider)
            probe = latest_completed.get(provider)
            checking = provider in active_providers
            observed = row.observed_session_count if row else 0
            total_cost = row.total_cost_units if row else 0
            supported_methods = PROVIDER_METHODS.get(
                ProviderName(provider),
                frozenset(),
            )
            unsupported_methods = sorted(observed_methods - supported_methods)
            method_state = (
                "not_checked"
                if not observed_methods
                else "matches"
                if not unsupported_methods
                else "differs"
            )
            finished_at = probe.finished_at if probe else None
            status_matches = (
                probe.candidate_status == probe.baseline_status
                if probe is not None and probe.candidate_status is not None
                else None
            )
            headers_match = (
                probe.candidate_headers == probe.baseline_headers
                if probe is not None and probe.candidate_headers is not None
                else None
            )
            console_matches = (
                probe.candidate_console_errors <= probe.baseline_console_errors
                if probe is not None and probe.candidate_console_errors is not None
                else None
            )
            content_matches = (
                probe.candidate_content_fingerprint == probe.baseline_content_fingerprint
                if probe is not None and probe.candidate_content_fingerprint is not None
                else None
            )
            provider_evidence.append(
                {
                    "provider": provider,
                    "automatic_enabled": profile.automatic_enabled,
                    "is_default": provider == default_provider,
                    "qualification_state": (
                        row.qualification_state if row else "unqualified"
                    ),
                    "successful_probe_count": row.successful_probe_count if row else 0,
                    "failed_probe_count": row.failed_probe_count if row else 0,
                    "observed_session_count": observed,
                    "total_cost_units": total_cost,
                    "average_cost_units": (
                        total_cost // observed
                        if observed
                        else profile.cost_units_per_second
                    ),
                    "cost_is_estimate": observed == 0,
                    "last_status_code": row.last_status_code if row else None,
                    "last_verified_at": _iso(row.last_verified_at) if row else None,
                    "comparison_policy_version": (
                        row.comparison_policy_version if row else None
                    ),
                    "last_probe_at": _iso(finished_at),
                    "probe_state": (
                        "checking"
                        if checking
                        else probe.state
                        if probe is not None
                        else "not_checked"
                    ),
                    "checks": {
                        "status": _comparison_check(
                            status_matches,
                            checked_at=finished_at,
                            checking=checking,
                        ),
                        "headers": _comparison_check(
                            headers_match,
                            checked_at=finished_at,
                            checking=checking,
                        ),
                        "console": _comparison_check(
                            console_matches,
                            checked_at=finished_at,
                            checking=checking,
                        ),
                        "methods": {
                            "state": method_state,
                            "checked_at": _iso(method_checked_at),
                            "observed_count": len(observed_methods),
                            "supported_count": len(observed_methods) - len(unsupported_methods),
                            "unsupported_methods": unsupported_methods,
                            "manifest_version": profile.capability_manifest_version,
                        },
                        "content": _comparison_check(
                            content_matches,
                            checked_at=finished_at,
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
            "active_probe_count": active_probe_count,
            "current_route": route,
            "providers": provider_evidence,
            "promotion": (
                {
                    "promotion_count": promotion.promotion_count,
                    "last_trigger_method": promotion.last_trigger_method,
                    "first_seen_at": _iso(promotion.first_seen_at),
                    "last_seen_at": _iso(promotion.last_seen_at),
                }
                if promotion is not None
                else None
            ),
            "commands": [
                {
                    "method": command.method,
                    "command_count": command.command_count,
                    "session_count": command.session_count,
                    "first_seen_at": _iso(command.first_seen_at),
                    "last_seen_at": _iso(command.last_seen_at),
                }
                for command in commands
            ],
        }

    async def probes(
        self,
        domain_id: int,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> DomainProbePage | None:
        query = select(QualificationProbe).where(QualificationProbe.domain_id == domain_id)
        if before is not None:
            occurred_at, identifier = _decode_cursor(before, "probe-v1")
            query = query.where(
                (QualificationProbe.created_at < occurred_at)
                | (
                    (QualificationProbe.created_at == occurred_at)
                    & (QualificationProbe.id < identifier)
                )
            )
        query = query.order_by(
            QualificationProbe.created_at.desc(),
            QualificationProbe.id.desc(),
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
                "comparison_outcome": row.comparison_outcome,
                "baseline_status": row.baseline_status,
                "candidate_status": row.candidate_status,
                "status_matches": (
                    row.candidate_status == row.baseline_status
                    if row.candidate_status is not None
                    else None
                ),
                "headers_match": (
                    row.candidate_headers == row.baseline_headers
                    if row.candidate_headers is not None
                    else None
                ),
                "baseline_console_errors": row.baseline_console_errors,
                "candidate_console_errors": row.candidate_console_errors,
                "console_errors_acceptable": (
                    row.candidate_console_errors <= row.baseline_console_errors
                    if row.candidate_console_errors is not None
                    else None
                ),
                "content_matches": (
                    row.candidate_content_fingerprint == row.baseline_content_fingerprint
                    if row.candidate_content_fingerprint is not None
                    else None
                ),
                "cost_units": row.cost_units,
                "created_at": _iso(row.created_at),
                "finished_at": _iso(row.finished_at),
            }
            for row in rows
        ]
        next_cursor = (
            _encode_cursor("probe-v1", rows[-1].created_at, rows[-1].id)
            if has_more and rows
            else None
        )
        return DomainProbePage(probes=probes, next_cursor=next_cursor)

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
            GatewaySession.created_at.desc(),
            GatewaySession.id.desc(),
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
                    "routing_reason": next(
                        (
                            attempt.routing_reason
                            for attempt in reversed(session_attempts)
                            if attempt.routing_reason is not None
                        ),
                        None,
                    ),
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
        return DomainSessionPage(sessions=sessions, next_cursor=next_cursor)

    @staticmethod
    async def _summary(database: AsyncSession) -> dict[str, int]:
        known = int(await database.scalar(select(func.count()).select_from(Domain)) or 0)
        qualified = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderProfile.domain_id)))
                .join(
                    ProviderRoutingProfile,
                    ProviderRoutingProfile.provider == DomainProviderProfile.provider,
                )
                .where(
                    DomainProviderProfile.qualification_state == "qualified",
                    ProviderRoutingProfile.automatic_enabled.is_(True),
                )
            )
            or 0
        )
        probing = int(
            await database.scalar(
                select(func.count(func.distinct(DomainProviderProfile.domain_id))).where(
                    DomainProviderProfile.qualification_state == "probing"
                )
            )
            or 0
        )
        promoted = int(
            await database.scalar(
                select(func.count())
                .select_from(DomainPromotionStat)
                .where(DomainPromotionStat.promotion_count > 0)
            )
            or 0
        )
        return {
            "known_domains": known,
            "qualified_domains": qualified,
            "probing_domains": probing,
            "default_only_domains": max(0, known - qualified),
            "promoted_domains": promoted,
        }
