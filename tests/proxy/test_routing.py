import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    Domain,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainRoutingPreference,
    GatewaySession,
    HealthProbe,
    SessionDomain,
    SessionEventRecord,
)
from backend.events import EventType
from backend.proxy.contracts import PROMOTION_PROVIDERS, ProviderName
from backend.proxy.external_capacity import ExternalCapacityRepository
from backend.proxy.health import (
    HealthProbeResult,
    PromotionRepository,
)
from backend.proxy.routing import RoutingRepository


async def _domain(
    sessions: async_sessionmaker[AsyncSession],
    hostname: str,
    *,
    providers: tuple[str, ...] = ("http", "browserless"),
) -> int:
    now = datetime.now(UTC)
    async with sessions.begin() as database:
        domain = Domain(
            hostname=hostname,
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        database.add_all(
            [
                DomainProviderHealth(
                    domain_id=domain.id,
                    provider=provider,
                    health_state="healthy",
                    successful_probe_count=1,
                    navigation_state="healthy",
                    status_state="healthy",
                    headers_state="healthy",
                    content_state="healthy",
                    health_policy_version=1,
                    provider_contract_version=1,
                )
                for provider in providers
            ]
        )
        return domain.id


async def _session(
    sessions: async_sessionmaker[AsyncSession],
    *,
    created_at: datetime | None = None,
) -> str:
    session_id = str(uuid4())
    async with sessions.begin() as database:
        database.add(
            GatewaySession(
                id=session_id,
                owner_id="test",
                lease_token=str(uuid4()),
                requested_settings={},
                state="closed",
                created_at=created_at or datetime.now(UTC),
                closed_at=datetime.now(UTC),
            )
        )
    return session_id


@pytest.mark.asyncio
async def test_plan_bootstraps_unknown_then_orders_only_healthy_eligible_providers(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await ExternalCapacityRepository(database_sessions).ensure(
        ProviderName.BROWSERBASE,
        enabled=True,
        max_active_sessions=5,
        max_queued_attempts=100,
    )
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    unknown = await routing.plan("unknown.test")
    assert unknown.reason == "configured_default_bootstrap"
    assert unknown.first.provider is ProviderName.BROWSERLESS
    assert [candidate.provider for candidate in unknown.candidates] == [ProviderName.BROWSERLESS]
    paid_fallback = await routing.plan(
        "unknown.test",
        allow_paid_fallback=True,
    )
    assert [candidate.provider for candidate in paid_fallback.candidates] == [
        ProviderName.BROWSERLESS,
        ProviderName.BROWSERBASE,
    ]

    domain_id = await _domain(database_sessions, "known.test")
    async with database_sessions.begin() as database:
        database.add(
            DomainProviderCostStat(
                domain_id=domain_id,
                provider="http",
                observed_attempt_count=2,
                total_cost_units=4,
            )
        )

    plan = await routing.plan("known.test", allow_paid_fallback=True)
    assert plan.reason == "cheapest_eligible"
    assert [candidate.provider for candidate in plan.candidates] == [
        ProviderName.HTTP,
        ProviderName.BROWSERLESS,
        ProviderName.BROWSERBASE,
    ]
    assert plan.first.estimated_cost_units == 2


@pytest.mark.asyncio
async def test_browserbase_is_never_primary_when_local_health_is_unfavorable(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await ExternalCapacityRepository(database_sessions).ensure(
        ProviderName.BROWSERBASE,
        enabled=True,
        max_active_sessions=5,
        max_queued_attempts=100,
    )
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "difficult.test")
    async with database_sessions.begin() as database:
        rows = list(
            await database.scalars(
                select(DomainProviderHealth).where(DomainProviderHealth.domain_id == domain_id)
            )
        )
        for row in rows:
            row.health_state = "unhealthy"

    plan = await routing.plan(
        "difficult.test",
        allow_paid_fallback=True,
    )

    assert [candidate.provider for candidate in plan.candidates] == [
        ProviderName.BROWSERLESS,
        ProviderName.BROWSERBASE,
    ]
    assert plan.reason == "local_correctness_fallback"
    assert plan.first.position == 0
    assert plan.candidates[-1].position == 1


@pytest.mark.asyncio
async def test_disabled_browserbase_is_not_offered_by_automatic_routing(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await ExternalCapacityRepository(database_sessions).ensure(
        ProviderName.BROWSERBASE,
        enabled=False,
        max_active_sessions=5,
        max_queued_attempts=100,
    )
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()

    plan = await routing.plan("unknown.test")

    assert [candidate.provider for candidate in plan.candidates] == [ProviderName.BROWSERLESS]


@pytest.mark.asyncio
async def test_adaptive_preference_promotes_and_demotes_browserless(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "adaptive.test")

    initial = await routing.plan("adaptive.test", exploration_key="initial")
    assert initial.first.provider is ProviderName.HTTP

    await routing.record_routing_evidence("adaptive.test", "browser_required")
    await routing.record_routing_evidence("adaptive.test", "browser_required")
    promoted = await routing.plan("adaptive.test", exploration_key="normal-session")
    assert promoted.first.provider is ProviderName.BROWSERLESS
    assert promoted.reason == "adaptive_browser_required"

    await routing.record_routing_evidence("adaptive.test", "http_sufficient")
    await routing.record_routing_evidence("adaptive.test", "http_sufficient")
    demoted = await routing.plan("adaptive.test", exploration_key="normal-session")
    assert demoted.first.provider is ProviderName.HTTP
    assert demoted.reason == "cheapest_eligible"

    async with database_sessions() as database:
        preference = await database.get(DomainRoutingPreference, domain_id)
    assert preference is not None
    assert preference.preferred_provider == "http"
    assert preference.browser_required_count == 2
    assert preference.http_sufficient_count == 2


@pytest.mark.asyncio
async def test_adaptive_browserless_preference_keeps_deterministic_http_canaries(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    await _domain(database_sessions, "canary.test")
    await routing.record_routing_evidence("canary.test", "browser_required")
    await routing.record_routing_evidence("canary.test", "browser_required")
    exploration_key = next(
        str(index)
        for index in range(100)
        if routing._exploration_bucket("canary.test", str(index)) < 1_000
    )

    plan = await routing.plan(
        "canary.test",
        exploration_key=exploration_key,
    )

    assert plan.first.provider is ProviderName.HTTP
    assert plan.reason == "adaptive_http_exploration"


@pytest.mark.asyncio
async def test_adaptive_evidence_updates_are_concurrency_safe(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "concurrent.test")

    await asyncio.gather(
        *(
            routing.record_routing_evidence(
                "concurrent.test",
                "browser_required",
            )
            for _ in range(10)
        )
    )

    async with database_sessions() as database:
        preference = await database.get(DomainRoutingPreference, domain_id)
    assert preference is not None
    assert preference.browser_required_count == 10
    assert preference.preference_score == 6
    assert preference.preferred_provider == "browserless"


@pytest.mark.asyncio
async def test_browserbase_cannot_be_configured_as_the_automatic_default(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()

    with pytest.raises(ValueError, match="paid fallback"):
        await routing.update_settings(default_provider=ProviderName.BROWSERBASE)


async def _add_probe_source(
    sessions: async_sessionmaker[AsyncSession], domain_id: int, hostname: str
) -> str:
    session_id = await _session(sessions, created_at=datetime.now(UTC) - timedelta(seconds=2))
    now = datetime.now(UTC) - timedelta(seconds=1)
    async with sessions.begin() as database:
        database.add(
            SessionDomain(
                session_id=session_id,
                domain_id=domain_id,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        database.add_all(
            [
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_REQUESTED,
                    provider="browserbase",
                    occurred_at=now,
                    payload={
                        "url": f"https://{hostname}/",
                        "probe_safe": True,
                    },
                ),
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_RESPONSE,
                    provider="browserbase",
                    occurred_at=now,
                    payload={"url": f"https://{hostname}/", "status": 200},
                ),
            ]
        )
    return session_id


@pytest.mark.asyncio
async def test_health_probes_do_not_depend_on_historical_methods(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "probe.test", providers=())
    source = await _add_probe_source(database_sessions, domain_id, "probe.test")
    health = PromotionRepository(database_sessions)
    jobs = await health.schedule(delay_seconds=0)

    assert {job.provider for job in jobs} == set(PROMOTION_PROVIDERS)
    assert ProviderName.BROWSERBASE not in {job.provider for job in jobs}
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(HealthProbe).where(HealthProbe.source_session_id == source)
            )
        )
    assert {probe.trigger for probe in probes} == {"new_domain"}

    owner = "worker"
    claimed = await health.claim(owner, lease_seconds=60)
    assert claimed is not None
    await health.complete(
        claimed.id,
        owner,
        HealthProbeResult(
            navigation_state="healthy",
            status_state="healthy",
            headers_state="healthy",
            content_state="healthy",
            status_code=200,
            reason_codes=(),
            content_facts={"visible_text_chars": 500},
        ),
    )
    assert (await routing.plan("probe.test")).first.provider is claimed.provider


@pytest.mark.asyncio
async def test_refresh_probe_preserves_existing_routing_eligibility(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    await routing.update_settings(existing_domain_probe_rate_basis_points=10_000)
    domain_id = await _domain(database_sessions, "refresh.test")
    async with database_sessions.begin() as database:
        domain = await database.get(Domain, domain_id)
        assert domain is not None
        domain.eligible_acquisition_count = 1
    await _add_probe_source(database_sessions, domain_id, "refresh.test")

    health = PromotionRepository(database_sessions)
    jobs = await health.schedule(delay_seconds=0)

    assert {job.provider for job in jobs} == set(PROMOTION_PROVIDERS)
    async with database_sessions() as database:
        rows = list(
            await database.scalars(
                select(DomainProviderHealth).where(DomainProviderHealth.domain_id == domain_id)
            )
        )
    assert {row.health_state for row in rows} == {"healthy"}
    assert [candidate.provider for candidate in (await routing.plan("refresh.test")).candidates][
        :2
    ] == [ProviderName.HTTP, ProviderName.BROWSERLESS]


@pytest.mark.asyncio
async def test_probe_execution_failure_replaces_stale_healthy_evidence(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "crash.test")
    await _add_probe_source(database_sessions, domain_id, "crash.test")
    health = PromotionRepository(database_sessions)
    await health.schedule(delay_seconds=0)

    owner = "worker"
    claimed = await health.claim(owner, lease_seconds=60)
    assert claimed is not None
    await health.fail(claimed.id, owner)

    async with database_sessions() as database:
        probe = await database.get(HealthProbe, claimed.id)
        profile = await database.get(
            DomainProviderHealth,
            (domain_id, claimed.provider.value),
        )

    assert probe is not None
    assert probe.state == "failed"
    assert probe.outcome == "unhealthy"
    assert probe.navigation_state == "unhealthy"
    assert probe.reason_codes == ["probe_execution_failed"]
    assert profile is not None
    assert profile.health_state == "unhealthy"
    assert profile.navigation_state == "unhealthy"
    assert profile.failure_reason_code == "probe_execution_failed"
    assert profile.last_checked_at is not None
    assert profile.successful_probe_count == 0
    assert profile.failed_probe_count == 1


@pytest.mark.asyncio
async def test_probe_cohort_suppresses_materially_incomplete_http_content(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "relative.test", providers=())
    source = await _add_probe_source(database_sessions, domain_id, "relative.test")
    health = PromotionRepository(database_sessions)
    await health.schedule(delay_seconds=0)

    owner = "cohort-worker"
    jobs = await health.claim_cohort(owner, lease_seconds=60)
    assert {job.provider for job in jobs} == set(PROMOTION_PROVIDERS)
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(HealthProbe).where(HealthProbe.source_session_id == source)
            )
        )
    assert len({probe.cohort_id for probe in probes}) == 1

    sparse = {
        "primary_region_present": True,
        "primary_visible_text_chars": 4,
        "primary_meaningful_elements": 0,
        "primary_link_count": 0,
        "primary_image_count": 0,
    }
    rendered = {
        "primary_region_present": True,
        "primary_visible_text_chars": 1_000,
        "primary_meaningful_elements": 50,
        "primary_link_count": 10,
        "primary_image_count": 30,
    }
    for job in jobs:
        await health.complete(
            job.id,
            owner,
            HealthProbeResult(
                navigation_state="healthy",
                status_state="healthy",
                headers_state="healthy",
                content_state="healthy",
                status_code=200,
                reason_codes=(),
                content_facts=(sparse if job.provider is ProviderName.HTTP else rendered),
            ),
        )

    async with database_sessions() as database:
        http_probe = await database.scalar(
            select(HealthProbe).where(
                HealthProbe.source_session_id == source,
                HealthProbe.candidate_provider == "http",
            )
        )
        http_health = await database.get(DomainProviderHealth, (domain_id, "http"))
        browserless_probe = await database.scalar(
            select(HealthProbe).where(
                HealthProbe.source_session_id == source,
                HealthProbe.candidate_provider == "browserless",
            )
        )

    assert http_probe is not None
    assert http_probe.outcome == "unhealthy"
    assert http_probe.content_state == "unhealthy"
    assert http_probe.comparison_state == "materially_incomplete"
    assert http_probe.reason_codes == ["materially_incomplete"]
    assert http_probe.content_facts["relative_coverage_percent"] == 0
    assert http_health is not None
    assert http_health.health_state == "unhealthy"
    assert http_health.failure_reason_code == "materially_incomplete"
    assert browserless_probe is not None
    assert browserless_probe.comparison_state == "comparable"


@pytest.mark.asyncio
async def test_browserbase_requires_an_explicit_manual_probe(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "terminal.test", providers=())
    await _add_probe_source(database_sessions, domain_id, "terminal.test")
    promotion = PromotionRepository(database_sessions)

    manual = await promotion.schedule_manual(
        domain_id,
        (ProviderName.BROWSERBASE,),
    )
    assert [job.provider for job in manual.scheduled] == [ProviderName.BROWSERBASE]

    claimed = await promotion.claim_cohort("manual-worker", lease_seconds=60)
    assert [job.provider for job in claimed] == [ProviderName.BROWSERBASE]

    jobs = await promotion.schedule(delay_seconds=0)
    assert ProviderName.BROWSERBASE not in {job.provider for job in jobs}
