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
    DomainProviderHealth,
    GatewaySession,
    HealthProbe,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
    SessionEventRecord,
)
from backend.events import EventType
from backend.events.normalization import normalize_domain
from backend.proxy.content_comparison import compare_content
from backend.proxy.contracts import PROMOTION_PROVIDERS, ProviderName

_PROMOTION_PROVIDER_VALUES = tuple(
    provider.value for provider in PROMOTION_PROVIDERS
)


@dataclass(frozen=True, slots=True)
class HealthProbeJob:
    id: str
    provider: ProviderName
    target_url: str


@dataclass(frozen=True, slots=True)
class HealthProbeResult:
    navigation_state: str
    status_state: str
    headers_state: str
    content_state: str
    status_code: int | None
    reason_codes: tuple[str, ...]
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
            return "unhealthy"
        if all(state == "healthy" for state in states):
            return "healthy"
        return "inconclusive"


@dataclass(frozen=True, slots=True)
class ManualProbeSchedule:
    scheduled: tuple[HealthProbeJob, ...]
    already_active: tuple[HealthProbeJob, ...]


class ManualProbeUnavailableError(RuntimeError):
    pass


class PromotionRepository:
    """Turns background probe results into routing promotion evidence."""
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def schedule(
        self, *, delay_seconds: float, limit: int = 250
    ) -> list[HealthProbeJob]:
        now = datetime.now(UTC)
        created: list[HealthProbeJob] = []
        async with self._sessions.begin() as database:
            probe_references = select(HealthProbe.id)
            sessions = list(
                await database.scalars(
                    select(GatewaySession)
                    .where(
                        GatewaySession.state == "closed",
                        GatewaySession.closed_at
                        <= now - timedelta(seconds=delay_seconds),
                        GatewaySession.health_evaluated_at.is_(None),
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
                    .where(
                        ProviderRoutingProfile.automatic_enabled.is_(True),
                        ProviderRoutingProfile.provider.in_(
                            _PROMOTION_PROVIDER_VALUES
                        ),
                    )
                    .order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            )
            for session in sessions:
                cohort_id = str(uuid4())
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
                    normalize_domain(target_url)
                    if isinstance(target_url, str)
                    else None
                )
                status = response.payload.get("status") if response else None
                if (
                    hostname is None
                    or not isinstance(status, int)
                    or not 200 <= status < 300
                ):
                    session.health_evaluated_at = now
                    continue
                domain = await database.scalar(
                    select(Domain)
                    .where(Domain.hostname == hostname)
                    .with_for_update()
                )
                if domain is None:
                    session.health_evaluated_at = now
                    continue
                trigger = (
                    "new_domain"
                    if domain.eligible_acquisition_count == 0
                    else "existing_sample"
                )
                bucket = (
                    int.from_bytes(
                        hashlib.sha256(f"{session.id}:{domain.id}".encode()).digest()[
                            :4
                        ],
                        "big",
                    )
                    % 10_000
                )
                domain.eligible_acquisition_count += 1
                session.health_evaluated_at = now
                if (
                    trigger == "existing_sample"
                    and bucket
                    >= configuration.existing_domain_probe_rate_basis_points
                ):
                    continue
                for profile in profiles:
                    probe_id = str(uuid4())
                    inserted = await database.scalar(
                        insert(HealthProbe)
                        .values(
                            id=probe_id,
                            cohort_id=cohort_id,
                            domain_id=domain.id,
                            source_session_id=session.id,
                            candidate_provider=profile.provider,
                            trigger=trigger,
                            sampling_bucket=bucket,
                            target_url=target_url,
                            state="queued",
                            created_at=now,
                        )
                        .on_conflict_do_nothing()
                        .returning(HealthProbe.id)
                    )
                    if inserted is None:
                        continue
                    await database.execute(
                        insert(DomainProviderHealth)
                        .values(
                            domain_id=domain.id,
                            provider=profile.provider,
                            health_state="unknown",
                            health_policy_version=configuration.health_policy_version,
                            provider_contract_version=(
                                profile.provider_contract_version
                            ),
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                DomainProviderHealth.domain_id,
                                DomainProviderHealth.provider,
                            ]
                        )
                    )
                    created.append(
                        HealthProbeJob(
                            probe_id,
                            ProviderName(profile.provider),
                            target_url,
                        )
                    )
        return created

    async def schedule_manual(
        self,
        domain_id: int,
        providers: tuple[ProviderName, ...] | None = None,
    ) -> ManualProbeSchedule:
        now = datetime.now(UTC)
        scheduled: list[HealthProbeJob] = []
        already_active: list[HealthProbeJob] = []
        async with self._sessions.begin() as database:
            domain = await database.scalar(
                select(Domain).where(Domain.id == domain_id).with_for_update()
            )
            if domain is None:
                raise ManualProbeUnavailableError("domain not found")
            requested = await database.scalar(
                select(SessionEventRecord)
                .join(
                    SessionDomain,
                    SessionDomain.session_id == SessionEventRecord.session_id,
                )
                .join(
                    GatewaySession,
                    GatewaySession.id == SessionEventRecord.session_id,
                )
                .where(
                    SessionDomain.domain_id == domain_id,
                    GatewaySession.state == "closed",
                    or_(
                        GatewaySession.client_reference.is_(None),
                        GatewaySession.client_reference.not_in(select(HealthProbe.id)),
                    ),
                    SessionEventRecord.event_type
                    == EventType.NAVIGATION_REQUESTED,
                    SessionEventRecord.payload["probe_safe"]
                    .as_boolean()
                    .is_(True),
                )
                .order_by(
                    SessionEventRecord.occurred_at.desc(),
                    SessionEventRecord.id.desc(),
                )
                .limit(1)
            )
            target_url = requested.payload.get("url") if requested else None
            if (
                not isinstance(target_url, str)
                or normalize_domain(target_url) != domain.hostname
            ):
                raise ManualProbeUnavailableError(
                    "no probe-safe navigation has been recorded for this domain"
                )

            if providers is None:
                # "Probe all" remains cost-safe and covers only providers eligible
                # for automatic promotion. Costly terminal providers require an
                # explicit operator selection.
                profile_query = select(ProviderRoutingProfile).where(
                    ProviderRoutingProfile.provider.in_(
                        _PROMOTION_PROVIDER_VALUES
                    )
                )
            else:
                requested_providers = [
                    provider.value for provider in dict.fromkeys(providers)
                ]
                profile_query = select(ProviderRoutingProfile).where(
                    ProviderRoutingProfile.provider.in_(requested_providers)
                )
            profiles = list(
                await database.scalars(
                    profile_query.order_by(
                        ProviderRoutingProfile.cost_units_per_second,
                        ProviderRoutingProfile.provider,
                    )
                )
            )
            configured = {profile.provider for profile in profiles}
            if providers is not None:
                missing = [
                    provider.value
                    for provider in dict.fromkeys(providers)
                    if provider.value not in configured
                ]
                if missing:
                    raise ManualProbeUnavailableError(
                        f"provider is not configured: {', '.join(missing)}"
                    )
            if not profiles:
                raise ManualProbeUnavailableError(
                    "no providers are configured for health checks"
                )

            provider_names = [profile.provider for profile in profiles]
            active = list(
                await database.scalars(
                    select(HealthProbe).where(
                        HealthProbe.domain_id == domain_id,
                        HealthProbe.candidate_provider.in_(provider_names),
                        HealthProbe.state.in_(("queued", "running")),
                    )
                )
            )
            active_by_provider = {
                probe.candidate_provider: probe for probe in active
            }
            active_cohorts = {probe.cohort_id for probe in active}
            cohort_id = (
                next(iter(active_cohorts))
                if len(active_cohorts) == 1
                else str(uuid4())
            )
            configuration = await database.get(RoutingConfiguration, "global")
            policy_version = (
                configuration.health_policy_version if configuration else 1
            )
            for profile in profiles:
                active_probe = active_by_provider.get(profile.provider)
                if active_probe is not None:
                    already_active.append(
                        HealthProbeJob(
                            active_probe.id,
                            ProviderName(active_probe.candidate_provider),
                            active_probe.target_url,
                        )
                    )
                    continue
                probe = HealthProbe(
                    id=str(uuid4()),
                    cohort_id=cohort_id,
                    domain_id=domain_id,
                    source_session_id=requested.session_id,
                    candidate_provider=profile.provider,
                    trigger="manual",
                    sampling_bucket=None,
                    target_url=target_url,
                    state="queued",
                    created_at=now,
                )
                database.add(probe)
                existing = await database.get(
                    DomainProviderHealth,
                    (domain_id, profile.provider),
                    with_for_update=True,
                )
                if existing is None:
                    database.add(
                        DomainProviderHealth(
                            domain_id=domain_id,
                            provider=profile.provider,
                            health_state="unknown",
                            health_policy_version=policy_version,
                            provider_contract_version=(
                                profile.provider_contract_version
                            ),
                        )
                    )
                scheduled.append(
                    HealthProbeJob(
                        probe.id,
                        ProviderName(profile.provider),
                        target_url,
                    )
                )
        return ManualProbeSchedule(tuple(scheduled), tuple(already_active))

    async def claim(self, owner: str, *, lease_seconds: float) -> HealthProbeJob | None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.scalar(
                select(HealthProbe)
                .where(
                    or_(
                        HealthProbe.candidate_provider.in_(
                            _PROMOTION_PROVIDER_VALUES
                        ),
                        HealthProbe.trigger == "manual",
                    ),
                    (HealthProbe.state == "queued")
                    | (
                        (HealthProbe.state == "running")
                        & (HealthProbe.lease_expires_at <= now)
                    )
                )
                .order_by(HealthProbe.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = "running"
            row.lease_owner = owner
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return HealthProbeJob(
                row.id,
                ProviderName(row.candidate_provider),
                row.target_url,
            )

    async def claim_cohort(
        self, owner: str, *, lease_seconds: float
    ) -> tuple[HealthProbeJob, ...]:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            first = await database.scalar(
                select(HealthProbe)
                .where(
                    or_(
                        HealthProbe.candidate_provider.in_(
                            _PROMOTION_PROVIDER_VALUES
                        ),
                        HealthProbe.trigger == "manual",
                    ),
                    (HealthProbe.state == "queued")
                    | (
                        (HealthProbe.state == "running")
                        & (HealthProbe.lease_expires_at <= now)
                    )
                )
                .order_by(HealthProbe.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if first is None:
                return ()
            rows = list(
                await database.scalars(
                    select(HealthProbe)
                    .where(
                        HealthProbe.cohort_id == first.cohort_id,
                        or_(
                            HealthProbe.candidate_provider.in_(
                                _PROMOTION_PROVIDER_VALUES
                            ),
                            HealthProbe.trigger == "manual",
                        ),
                        (HealthProbe.state == "queued")
                        | (
                            (HealthProbe.state == "running")
                            & (HealthProbe.lease_expires_at <= now)
                        ),
                    )
                    .order_by(HealthProbe.created_at, HealthProbe.candidate_provider)
                    .with_for_update(skip_locked=True)
                )
            )
            expires_at = now + timedelta(seconds=lease_seconds)
            for row in rows:
                row.state = "running"
                row.lease_owner = owner
                row.lease_expires_at = expires_at
            return tuple(
                HealthProbeJob(
                    row.id,
                    ProviderName(row.candidate_provider),
                    row.target_url,
                )
                for row in rows
            )

    async def complete(
        self, probe_id: str, owner: str, result: HealthProbeResult
    ) -> bool:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.get(HealthProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return False
            candidate_session = await database.scalar(
                select(GatewaySession).where(
                    GatewaySession.client_reference == probe_id
                )
            )
            cost = (
                await database.scalar(
                    select(
                        func.coalesce(
                            func.sum(AcquisitionAttempt.modeled_cost_units), 0
                        )
                    ).where(AcquisitionAttempt.session_id == candidate_session.id)
                )
                if candidate_session is not None
                else 0
            )
            row.state = "completed"
            row.outcome = result.outcome
            row.navigation_state = result.navigation_state
            row.status_state = result.status_state
            row.headers_state = result.headers_state
            row.content_state = result.content_state
            row.status_code = result.status_code
            row.reason_codes = list(result.reason_codes)
            row.content_facts = result.content_facts
            row.cost_units = int(cost or 0)
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None

            configuration = await database.get(RoutingConfiguration, "global")
            required = (
                configuration.required_health_confirmations
                if configuration
                else 1
            )
            profile = await database.get(
                DomainProviderHealth,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is None:
                await self._reconcile_cohort(database, row.cohort_id)
                return True
            routing_profile = await database.get(
                ProviderRoutingProfile, row.candidate_provider
            )
            profile.health_policy_version = (
                configuration.health_policy_version if configuration else 1
            )
            if routing_profile is not None:
                profile.provider_contract_version = (
                    routing_profile.provider_contract_version
                )
            profile.navigation_state = result.navigation_state
            profile.status_state = result.status_state
            profile.headers_state = result.headers_state
            profile.content_state = result.content_state
            profile.failure_reason_code = (
                result.reason_codes[0] if result.reason_codes else None
            )
            profile.last_status_code = result.status_code
            profile.last_checked_at = now
            if result.outcome == "healthy":
                profile.successful_probe_count += 1
                if profile.successful_probe_count >= required:
                    profile.health_state = "healthy"
                if profile.health_state == "healthy":
                    profile.last_healthy_at = now
            elif result.outcome == "unhealthy":
                profile.failed_probe_count += 1
                profile.successful_probe_count = 0
                profile.health_state = "unhealthy"
            else:
                profile.inconclusive_probe_count += 1
                profile.health_state = "inconclusive"
            await self._reconcile_cohort(database, row.cohort_id)
            return True

    async def fail(self, probe_id: str, owner: str) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            row = await database.get(HealthProbe, probe_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != owner:
                return
            row.state = "failed"
            row.outcome = "unhealthy"
            row.navigation_state = "unhealthy"
            row.status_state = "inconclusive"
            row.headers_state = "inconclusive"
            row.content_state = "inconclusive"
            row.status_code = None
            row.reason_codes = ["probe_execution_failed"]
            row.content_facts = {}
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            profile = await database.get(
                DomainProviderHealth,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if profile is not None:
                configuration = await database.get(RoutingConfiguration, "global")
                routing_profile = await database.get(
                    ProviderRoutingProfile, row.candidate_provider
                )
                profile.health_policy_version = (
                    configuration.health_policy_version if configuration else 1
                )
                if routing_profile is not None:
                    profile.provider_contract_version = (
                        routing_profile.provider_contract_version
                    )
                profile.navigation_state = "unhealthy"
                profile.status_state = "inconclusive"
                profile.headers_state = "inconclusive"
                profile.content_state = "inconclusive"
                profile.failure_reason_code = "probe_execution_failed"
                profile.last_status_code = None
                profile.last_checked_at = now
                profile.failed_probe_count += 1
                profile.successful_probe_count = 0
                profile.health_state = "unhealthy"
            await self._reconcile_cohort(database, row.cohort_id)

    async def _reconcile_cohort(
        self, database: AsyncSession, cohort_id: str
    ) -> None:
        rows = list(
            await database.scalars(
                select(HealthProbe)
                .where(HealthProbe.cohort_id == cohort_id)
                .order_by(HealthProbe.candidate_provider)
                .with_for_update()
            )
        )
        if not rows or any(row.state in {"queued", "running"} for row in rows):
            return
        if len(rows) == 1:
            rows[0].comparison_state = "not_applicable"
            return

        absolutely_healthy = {
            row.candidate_provider
            for row in rows
            if row.state == "completed" and row.outcome == "healthy"
        }
        comparisons = compare_content(
            {
                row.candidate_provider: row.content_facts
                for row in rows
                if row.state == "completed"
            },
            absolutely_healthy,
        )
        for row in rows:
            if row.state != "completed" or row.outcome != "healthy":
                if row.comparison_state == "pending":
                    row.comparison_state = "not_applicable"
                continue
            comparison = comparisons.get(row.candidate_provider)
            if comparison is None:
                row.comparison_state = "inconclusive"
                continue
            row.comparison_state = comparison.state
            row.content_facts = {
                **row.content_facts,
                "relative_coverage_percent": comparison.coverage_percent,
                "relative_deficient_dimensions": (
                    comparison.deficient_dimensions
                ),
                **{
                    f"reference_{key}": value
                    for key, value in comparison.reference_facts.items()
                },
            }
            if comparison.state != "materially_incomplete":
                continue
            row.outcome = "unhealthy"
            row.content_state = "unhealthy"
            row.reason_codes = list(
                dict.fromkeys([*row.reason_codes, "materially_incomplete"])
            )
            profile = await database.get(
                DomainProviderHealth,
                (row.domain_id, row.candidate_provider),
                with_for_update=True,
            )
            if (
                profile is None
                or profile.last_checked_at is None
                or row.finished_at is None
                or profile.last_checked_at != row.finished_at
            ):
                continue
            profile.content_state = "unhealthy"
            profile.failure_reason_code = "materially_incomplete"
            profile.failed_probe_count += 1
            profile.successful_probe_count = 0
            profile.health_state = "unhealthy"
