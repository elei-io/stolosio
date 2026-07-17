import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    DomainProviderSupport,
    GatewaySession,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionEventRecord,
    SupportProbe,
)
from backend.events import EventType
from backend.events.normalization import normalize_domain
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class SupportProbeJob:
    id: str
    provider: ProviderName
    target_url: str
    required_methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupportProbeResult:
    navigation_state: str
    status_state: str
    headers_state: str
    method_coverage_state: str
    content_state: str
    status_code: int | None
    reason_codes: tuple[str, ...]
    method_observed_count: int
    method_declared_count: int
    unsupported_methods: tuple[str, ...]
    content_facts: dict

    @property
    def outcome(self) -> str:
        states = (
            self.navigation_state,
            self.status_state,
            self.headers_state,
            self.content_state,
        )
        if "unhealthy" in states:
            return "unsupported"
        if self.method_coverage_state == "missing":
            return "unsupported"
        if (
            all(state == "healthy" for state in states)
            and self.method_coverage_state == "declared"
        ):
            return "supported"
        return "inconclusive"


class SupportRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def schedule(
        self, *, delay_seconds: float, limit: int = 250
    ) -> list[SupportProbeJob]:
        now = datetime.now(UTC)
        created: list[SupportProbeJob] = []
        async with self._sessions.begin() as database:
            probe_references = select(SupportProbe.id)
            sessions = list(
                await database.scalars(
                    select(GatewaySession)
                    .where(
                        GatewaySession.state == "closed",
                        GatewaySession.closed_at <= now - timedelta(seconds=delay_seconds),
                        GatewaySession.support_evaluated_at.is_(None),
                        or_(
                            GatewaySession.client_reference.is_(None),
                            GatewaySession.client_reference.not_in(probe_references),
                        ),
                    )
                    .order_by(GatewaySession.closed_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            configuration = await database.get(RoutingConfiguration, "global")
            if configuration is None:
                return []
            profiles = list(
                await database.scalars(
                    select(ProviderRoutingProfile)
                    .where(ProviderRoutingProfile.automatic_enabled.is_(True))
                    .order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            )
            for session in sessions:
                events = list(
                    await database.scalars(
                        select(SessionEventRecord)
                        .where(SessionEventRecord.session_id == session.id)
                        .order_by(SessionEventRecord.id)
                    )
                )
                requested = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type == EventType.NAVIGATION_REQUESTED
                        and event.payload.get("probe_safe") is True
                    ),
                    None,
                )
                response = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type == EventType.NAVIGATION_RESPONSE
                    ),
                    None,
                )
                target_url = requested.payload.get("url") if requested else None
                hostname = (
                    normalize_domain(target_url) if isinstance(target_url, str) else None
                )
                status = response.payload.get("status") if response else None
                if hostname is None or not isinstance(status, int) or not 200 <= status < 300:
                    session.support_evaluated_at = now
                    continue
                domain = await database.scalar(
                    select(Domain).where(Domain.hostname == hostname).with_for_update()
                )
                if domain is None:
                    session.support_evaluated_at = now
                    continue
                trigger = (
                    "new_domain"
                    if domain.eligible_acquisition_count == 0
                    else "existing_sample"
                )
                bucket = (
                    int.from_bytes(
                        hashlib.sha256(f"{session.id}:{domain.id}".encode()).digest()[:4],
                        "big",
                    )
                    % 10_000
                )
                domain.eligible_acquisition_count += 1
                session.support_evaluated_at = now
                if (
                    trigger == "existing_sample"
                    and bucket >= configuration.existing_domain_probe_rate_basis_points
                ):
                    continue
                methods = tuple(
                    await database.scalars(
                        select(DomainCommandStat.method).where(
                            DomainCommandStat.domain_id == domain.id
                        )
                    )
                )
                for profile in profiles:
                    probe_id = str(uuid4())
                    inserted = await database.scalar(
                        insert(SupportProbe)
                        .values(
                            id=probe_id,
                            domain_id=domain.id,
                            source_session_id=session.id,
                            candidate_provider=profile.provider,
                            trigger=trigger,
                            sampling_bucket=bucket,
                            target_url=target_url,
                            required_methods=list(methods),
                            state="queued",
                            created_at=now,
                        )
                        .on_conflict_do_nothing()
                        .returning(SupportProbe.id)
                    )
                    if inserted is None:
                        continue
                    await database.execute(
                        insert(DomainProviderSupport)
                        .values(
                            domain_id=domain.id,
                            provider=profile.provider,
                            support_state="checking",
                            support_policy_version=configuration.support_policy_version,
                            capability_manifest_version=(
                                profile.capability_manifest_version
                            ),
                        )
                        .on_conflict_do_update(
                            index_elements=[
                                DomainProviderSupport.domain_id,
                                DomainProviderSupport.provider,
                            ],
                            set_={
                                "support_state": "checking",
                                "support_policy_version": (
                                    configuration.support_policy_version
                                ),
                                "capability_manifest_version": (
                                    profile.capability_manifest_version
                                ),
                            },
                        )
                    )
                    created.append(
                        SupportProbeJob(
                            probe_id,
                            ProviderName(profile.provider),
                            target_url,
                            methods,
                        )
                    )
        return created

    async def claim(self, owner: str, *, lease_seconds: float) -> SupportProbeJob | None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.scalar(
                select(SupportProbe)
                .where(
                    (SupportProbe.state == "queued")
                    | (
                        (SupportProbe.state == "running")
                        & (SupportProbe.lease_expires_at <= now)
                    )
                )
                .order_by(SupportProbe.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = "running"
            row.lease_owner = owner
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return SupportProbeJob(
                row.id,
                ProviderName(row.candidate_provider),
                row.target_url,
                tuple(row.required_methods),
            )

    async def complete(
        self, probe_id: str, owner: str, result: SupportProbeResult
    ) -> bool:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.get(SupportProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return False
            candidate_session = await database.scalar(
                select(GatewaySession).where(GatewaySession.client_reference == probe_id)
            )
            cost = (
                await database.scalar(
                    select(func.coalesce(func.sum(AcquisitionAttempt.actual_cost_units), 0))
                    .where(AcquisitionAttempt.session_id == candidate_session.id)
                )
                if candidate_session is not None
                else 0
            )
            row.state = "completed"
            row.outcome = result.outcome
            row.navigation_state = result.navigation_state
            row.status_state = result.status_state
            row.headers_state = result.headers_state
            row.method_coverage_state = result.method_coverage_state
            row.content_state = result.content_state
            row.status_code = result.status_code
            row.reason_codes = list(result.reason_codes)
            row.method_observed_count = result.method_observed_count
            row.method_declared_count = result.method_declared_count
            row.unsupported_methods = list(result.unsupported_methods)
            row.content_facts = result.content_facts
            row.cost_units = int(cost or 0)
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None

            configuration = await database.get(RoutingConfiguration, "global")
            required = (
                configuration.required_support_confirmations if configuration else 1
            )
            profile = await database.get(
                DomainProviderSupport,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is None:
                return True
            profile.navigation_state = result.navigation_state
            profile.status_state = result.status_state
            profile.headers_state = result.headers_state
            profile.method_coverage_state = result.method_coverage_state
            profile.content_state = result.content_state
            profile.method_observed_count = result.method_observed_count
            profile.method_declared_count = result.method_declared_count
            profile.unsupported_methods = list(result.unsupported_methods)
            profile.failure_reason_code = (
                result.reason_codes[0] if result.reason_codes else None
            )
            profile.last_status_code = result.status_code
            profile.last_checked_at = now
            if result.outcome == "supported":
                profile.successful_probe_count += 1
                profile.support_state = (
                    "supported"
                    if profile.successful_probe_count >= required
                    else "checking"
                )
                if profile.support_state == "supported":
                    profile.last_supported_at = now
            elif result.outcome == "unsupported":
                profile.failed_probe_count += 1
                profile.successful_probe_count = 0
                profile.support_state = "unsupported"
            else:
                profile.inconclusive_probe_count += 1
                profile.support_state = "checking"
            return True

    async def fail(self, probe_id: str, owner: str) -> None:
        async with self._sessions.begin() as database:
            row = await database.get(SupportProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return
            row.state = "failed"
            row.outcome = "inconclusive"
            row.reason_codes = ["probe_execution_failed"]
            row.finished_at = datetime.now(UTC)
            row.lease_owner = None
            row.lease_expires_at = None
            profile = await database.get(
                DomainProviderSupport,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is not None:
                profile.inconclusive_probe_count += 1
                profile.support_state = "checking"
