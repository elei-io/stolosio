import base64
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    GatewaySession,
    SessionDomain,
)
from backend.proxy.workload_facts import (
    WINDOWS,
    CapturePath,
    capture_outcome_expression,
    capture_path_expression,
)


@dataclass(frozen=True, slots=True)
class SessionFilters:
    search: str | None = None
    state: str | None = None
    provider: str | None = None
    workload: Literal["automation", "capture"] | None = None
    outcome: str | None = None
    path: CapturePath | None = None
    since: datetime | None = None
    window: Literal["24h", "7d", "30d"] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SessionPage:
    sessions: list[dict[str, object]]
    next_cursor: str | None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _encode_cursor(created_at: datetime, session_id: str) -> str:
    payload = json.dumps(
        ["sessions-v1", created_at.astimezone(UTC).isoformat(), session_id],
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        version, created_at, session_id = json.loads(base64.urlsafe_b64decode(cursor + padding))
        value = datetime.fromisoformat(created_at)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid sessions cursor") from error
    if version != "sessions-v1" or value.tzinfo is None or not session_id:
        raise ValueError("invalid sessions cursor")
    return value, session_id


def _duration_seconds(session: GatewaySession) -> float | None:
    end = session.closed_at or session.closing_at
    if end is None:
        return None
    return max(0, (end - session.created_at).total_seconds())


def capture_path(summary: dict | None) -> str | None:
    if summary is None:
        return None
    tiers = summary["tiers"]
    if "challenge_resolution" in tiers:
        return "challenge_resolution"
    return "managed" if "managed" in tiers else "http"


def capture_outcome(session: GatewaySession) -> str:
    if session.capture_summary is not None:
        return session.capture_summary["outcome"]
    if session.state not in ("closed", "failed"):
        return "in_progress"
    if session.terminal_reason in (
        "gateway_capacity_full",
        "provider_queue_full",
        "provider_queue_timeout",
    ):
        return "rejected"
    return "interrupted" if session.state == "failed" else "unknown"


class SessionQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def sessions(
        self,
        filters: SessionFilters,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> SessionPage:
        if (filters.outcome or filters.path) and filters.workload != "capture":
            raise ValueError("Capture outcome and path filters require workload=capture")
        query = select(GatewaySession)
        if filters.workload:
            query = query.where(GatewaySession.workload == filters.workload)
        if filters.reason:
            query = query.where(
                func.coalesce(
                    GatewaySession.capture_summary["failure_code"].as_string(),
                    GatewaySession.terminal_reason,
                )
                == filters.reason
            )
        since = datetime.now(UTC) - WINDOWS[filters.window] if filters.window else filters.since
        if since:
            query = query.where(
                func.coalesce(GatewaySession.closed_at, GatewaySession.created_at) >= since
            )
        if filters.outcome:
            query = query.where(capture_outcome_expression() == filters.outcome)
        if filters.path:
            query = query.where(capture_path_expression() == filters.path)
        if filters.search:
            value = f"%{filters.search.strip()}%"
            query = query.where(
                or_(
                    GatewaySession.id.ilike(value),
                    GatewaySession.client_reference.ilike(value),
                    GatewaySession.capture_hostname.ilike(value),
                )
            )
        if filters.state:
            query = query.where(GatewaySession.state == filters.state)
        if filters.provider:
            query = query.where(
                select(AcquisitionAttempt.id)
                .where(
                    AcquisitionAttempt.session_id == GatewaySession.id,
                    AcquisitionAttempt.provider == filters.provider,
                )
                .exists()
            )
        if before:
            created_at, session_id = _decode_cursor(before)
            query = query.where(
                or_(
                    GatewaySession.created_at < created_at,
                    ((GatewaySession.created_at == created_at) & (GatewaySession.id < session_id)),
                )
            )
        query = query.order_by(GatewaySession.created_at.desc(), GatewaySession.id.desc()).limit(
            limit + 1
        )

        async with self._sessions() as database:
            rows = list(await database.scalars(query))
            has_more = len(rows) > limit
            rows = rows[:limit]
            session_ids = [row.id for row in rows]
            attempts_by_session: dict[str, list[AcquisitionAttempt]] = defaultdict(list)
            domains_by_session: dict[str, list[tuple[int, str]]] = defaultdict(list)
            if session_ids:
                for attempt in await database.scalars(
                    select(AcquisitionAttempt)
                    .where(AcquisitionAttempt.session_id.in_(session_ids))
                    .order_by(AcquisitionAttempt.session_id, AcquisitionAttempt.ordinal)
                ):
                    attempts_by_session[attempt.session_id].append(attempt)
                for row in await database.execute(
                    select(SessionDomain.session_id, Domain.id, Domain.hostname)
                    .join(Domain, SessionDomain.domain_id == Domain.id)
                    .where(SessionDomain.session_id.in_(session_ids))
                    .order_by(
                        SessionDomain.session_id,
                        SessionDomain.first_seen_at,
                        Domain.hostname,
                    )
                ):
                    domains_by_session[row[0]].append((row[1], row[2]))
            result = [
                self._summary(
                    row,
                    attempts_by_session[row.id],
                    domains_by_session[row.id],
                )
                for row in rows
            ]

        next_cursor = (
            _encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
        )
        return SessionPage(result, next_cursor)

    async def session(self, session_id: str) -> dict[str, object] | None:
        async with self._sessions() as database:
            session = await database.get(GatewaySession, session_id)
            if session is None:
                return None
            attempts = list(
                await database.scalars(
                    select(AcquisitionAttempt)
                    .where(AcquisitionAttempt.session_id == session_id)
                    .order_by(AcquisitionAttempt.ordinal)
                )
            )
            domains = [
                (row[0], row[1])
                for row in await database.execute(
                    select(Domain.id, Domain.hostname)
                    .join(SessionDomain, SessionDomain.domain_id == Domain.id)
                    .where(SessionDomain.session_id == session_id)
                    .order_by(SessionDomain.first_seen_at, Domain.hostname)
                )
            ]
            summary = self._summary(session, attempts, domains)
        return {
            **summary,
            "requested_setting_keys": sorted(session.requested_settings),
            "admitted_at": _iso(session.admitted_at),
            "opened_at": _iso(session.opened_at),
            "closing_at": _iso(session.closing_at),
            "lease_expires_at": _iso(session.lease_expires_at),
            "attempts": [
                {
                    "id": attempt.id,
                    "ordinal": attempt.ordinal,
                    "provider": attempt.provider,
                    "provider_instance_id": attempt.provider_instance_id,
                    "provider_session_id": attempt.provider_session_id,
                    "state": attempt.state,
                    "modeled_cost_units": attempt.modeled_cost_units,
                    "chargeable_time_ms": attempt.chargeable_time_ms,
                    "cost_basis": attempt.cost_basis,
                    "cost_rate_units_per_second": (attempt.cost_rate_units_per_second),
                    "resolved_setting_keys": sorted(attempt.resolved_settings),
                    "setting_sources": attempt.setting_sources,
                    "created_at": _iso(attempt.created_at),
                    "queued_at": _iso(attempt.queued_at),
                    "acquiring_at": _iso(attempt.acquiring_at),
                    "active_at": _iso(attempt.active_at),
                    "finished_at": _iso(attempt.finished_at),
                    "provider_started_at": _iso(attempt.provider_started_at),
                    "provider_ended_at": _iso(attempt.provider_ended_at),
                    "capacity_occupied_ms": attempt.capacity_occupied_ms,
                    "browser_connected_ms": attempt.browser_connected_ms,
                    "provider_reported_ms": attempt.provider_reported_ms,
                    "phase_summary": attempt.phase_summary,
                    "terminal_reason": attempt.terminal_reason,
                }
                for attempt in attempts
            ],
        }

    def _summary(
        self,
        session: GatewaySession,
        attempts: list[AcquisitionAttempt],
        domains: list[tuple[int, str]],
    ) -> dict[str, object]:
        return {
            "id": session.id,
            "client_reference": session.client_reference,
            "state": session.state,
            "workload": session.workload,
            "capture_hostname": session.capture_hostname,
            "capture": session.capture_summary,
            "capture_outcome": capture_outcome(session) if session.workload == "capture" else None,
            "capture_path": capture_path(session.capture_summary),
            "created_at": _iso(session.created_at),
            "closed_at": _iso(session.closed_at),
            "duration_seconds": _duration_seconds(session),
            "terminal_reason": session.terminal_reason,
            "providers": [attempt.provider for attempt in attempts],
            "modeled_cost_units": sum(attempt.modeled_cost_units or 0 for attempt in attempts),
            "total_browser_time_ms": sum(
                attempt.provider_reported_ms
                if attempt.provider_reported_ms is not None
                else attempt.browser_connected_ms or 0
                for attempt in attempts
            ),
            "total_capacity_occupied_ms": sum(
                attempt.capacity_occupied_ms or 0 for attempt in attempts
            ),
            "domains": [{"id": domain_id, "hostname": hostname} for domain_id, hostname in domains],
        }
