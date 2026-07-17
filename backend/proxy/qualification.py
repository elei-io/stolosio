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
    DomainProviderProfile,
    GatewaySession,
    ProviderRoutingProfile,
    QualificationProbe,
    RoutingConfiguration,
    SessionEventRecord,
)
from backend.events import EventType
from backend.events.normalization import normalize_domain
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class ProbeJob:
    id: str
    provider: ProviderName
    target_url: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    status: int
    headers: dict
    console_errors: int
    content_fingerprint: str


class QualificationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def provider_for_reference(self, reference: str | None) -> ProviderName | None:
        if reference is None:
            return None
        async with self._sessions() as database:
            provider = await database.scalar(
                select(QualificationProbe.candidate_provider).where(
                    QualificationProbe.id == reference
                )
            )
        return ProviderName(provider) if provider is not None else None

    async def schedule(self, *, delay_seconds: float, limit: int = 250) -> list[ProbeJob]:
        now = datetime.now(UTC)
        created: list[ProbeJob] = []
        async with self._sessions.begin() as database:
            probe_references = select(QualificationProbe.id)
            sessions = list(
                await database.scalars(
                    select(GatewaySession)
                    .where(
                        GatewaySession.state == "closed",
                        GatewaySession.closed_at <= now - timedelta(seconds=delay_seconds),
                        GatewaySession.qualification_evaluated_at.is_(None),
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
                    select(ProviderRoutingProfile).where(
                        ProviderRoutingProfile.automatic_enabled.is_(True)
                    )
                )
            )
            costs = {profile.provider: profile.cost_units_per_second for profile in profiles}
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
                content = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type == EventType.PAGE_CONTENT_OBSERVED
                    ),
                    None,
                )
                if requested is None or response is None or content is None:
                    session.qualification_evaluated_at = now
                    continue
                target_url = requested.payload.get("url")
                hostname = normalize_domain(target_url) if isinstance(target_url, str) else None
                status = response.payload.get("status")
                fingerprint = content.payload.get("content_fingerprint")
                if (
                    hostname is None
                    or not isinstance(status, int)
                    or not isinstance(fingerprint, str)
                    or response.provider not in costs
                ):
                    session.qualification_evaluated_at = now
                    continue
                domain = await database.scalar(
                    select(Domain).where(Domain.hostname == hostname).with_for_update()
                )
                if domain is None:
                    continue
                trigger = (
                    "new_domain" if domain.eligible_acquisition_count == 0 else "existing_sample"
                )
                bucket = (
                    int.from_bytes(
                        hashlib.sha256(f"{session.id}:{domain.id}".encode()).digest()[:4],
                        "big",
                    )
                    % 10_000
                )
                domain.eligible_acquisition_count += 1
                session.qualification_evaluated_at = now
                if (
                    trigger == "existing_sample"
                    and bucket >= configuration.existing_domain_probe_rate_basis_points
                ):
                    continue
                baseline_cost = costs[response.provider]
                console_errors = sum(
                    event.event_type == EventType.JAVASCRIPT_EXCEPTION
                    or (
                        event.event_type == EventType.CONSOLE_MESSAGE
                        and event.payload.get("level") == "error"
                    )
                    for event in events
                )
                for profile in profiles:
                    if profile.cost_units_per_second >= baseline_cost:
                        continue
                    probe_id = str(uuid4())
                    inserted = await database.scalar(
                        insert(QualificationProbe)
                        .values(
                            id=probe_id,
                            domain_id=domain.id,
                            source_session_id=session.id,
                            candidate_provider=profile.provider,
                            trigger=trigger,
                            sampling_bucket=bucket,
                            target_url=target_url,
                            baseline_status=status,
                            baseline_headers=response.payload.get("selected_headers", {}),
                            baseline_console_errors=console_errors,
                            baseline_content_fingerprint=fingerprint,
                            state="queued",
                            created_at=now,
                        )
                        .on_conflict_do_nothing()
                        .returning(QualificationProbe.id)
                    )
                    if inserted is None:
                        continue
                    await database.execute(
                        insert(DomainProviderProfile)
                        .values(
                            domain_id=domain.id,
                            provider=profile.provider,
                            qualification_state="probing",
                            comparison_policy_version=(configuration.comparison_policy_version),
                        )
                        .on_conflict_do_nothing()
                    )
                    created.append(ProbeJob(probe_id, ProviderName(profile.provider), target_url))
        return created

    async def claim(self, owner: str, *, lease_seconds: float) -> ProbeJob | None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.scalar(
                select(QualificationProbe)
                .where(
                    (QualificationProbe.state == "queued")
                    | (
                        (QualificationProbe.state == "running")
                        & (QualificationProbe.lease_expires_at <= now)
                    )
                )
                .order_by(QualificationProbe.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = "running"
            row.lease_owner = owner
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return ProbeJob(row.id, ProviderName(row.candidate_provider), row.target_url)

    async def complete(self, probe_id: str, owner: str, result: ProbeResult) -> bool:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.get(QualificationProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return False
            safe = (
                result.status == row.baseline_status
                and result.headers == row.baseline_headers
                and result.console_errors <= row.baseline_console_errors
                and result.content_fingerprint == row.baseline_content_fingerprint
            )
            candidate_session = await database.scalar(
                select(GatewaySession).where(GatewaySession.client_reference == probe_id)
            )
            cost = (
                await database.scalar(
                    select(func.coalesce(func.sum(AcquisitionAttempt.actual_cost_units), 0)).where(
                        AcquisitionAttempt.session_id == candidate_session.id
                    )
                )
                if candidate_session is not None
                else 0
            )
            row.state = "completed"
            row.candidate_status = result.status
            row.candidate_headers = result.headers
            row.candidate_console_errors = result.console_errors
            row.candidate_content_fingerprint = result.content_fingerprint
            row.comparison_outcome = "matched" if safe else "mismatched"
            row.cost_units = int(cost or 0)
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None

            configuration = await database.get(RoutingConfiguration, "global")
            required = configuration.required_successful_probes if configuration else 1
            profile = await database.get(
                DomainProviderProfile,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is None:
                return True
            if safe:
                profile.successful_probe_count += 1
                profile.qualification_state = (
                    "qualified" if profile.successful_probe_count >= required else "probing"
                )
                profile.last_verified_at = now
            else:
                profile.failed_probe_count += 1
                profile.qualification_state = "rejected"
            profile.last_status_code = result.status
            return True

    async def fail(self, probe_id: str, owner: str) -> None:
        async with self._sessions.begin() as database:
            row = await database.get(QualificationProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return
            row.state = "failed"
            row.comparison_outcome = "execution_failed"
            row.finished_at = datetime.now(UTC)
            row.lease_owner = None
            row.lease_expires_at = None
            profile = await database.get(
                DomainProviderProfile,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is not None:
                profile.failed_probe_count += 1
                profile.qualification_state = "rejected"
