from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    Domain,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderRuntimeState,
    GatewaySession,
    HealthProbe,
    SessionDomain,
    SessionDomainProviderCompatibility,
    SessionEventRecord,
)
from backend.events import EventType
from backend.proxy.contracts import ProviderName
from backend.proxy.health import HealthProbeResult, HealthRepository
from backend.proxy.routing import RoutingRepository
from backend.proxy.runtime_compatibility import (
    DomainCommands,
    RuntimeCompatibilityRepository,
)


async def _domain(
    sessions: async_sessionmaker[AsyncSession],
    hostname: str,
    *,
    providers: tuple[str, ...] = ("http", "lightpanda"),
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
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    unknown = await routing.plan("unknown.test")
    assert unknown.reason == "configured_default_bootstrap"
    assert unknown.first.provider is ProviderName.CAMOUFOX

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

    plan = await routing.plan("known.test")
    assert plan.reason == "cheapest_eligible"
    assert [candidate.provider for candidate in plan.candidates] == [
        ProviderName.HTTP,
        ProviderName.LIGHTPANDA,
    ]
    assert plan.first.estimated_cost_units == 2


@pytest.mark.asyncio
async def test_atlas_sequence_suppresses_then_restores_http_without_a_probe(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "atlas.test")
    compatibility = RuntimeCompatibilityRepository(database_sessions)

    assert (await routing.plan("atlas.test")).first.provider is ProviderName.HTTP

    first_session = await _session(database_sessions)
    await compatibility.suppress(
        session_id=first_session,
        hostname="atlas.test",
        provider=ProviderName.HTTP,
        method="Runtime.callFunctionOn",
    )
    assert (
        await routing.plan("atlas.test")
    ).first.provider is ProviderName.LIGHTPANDA

    second_session = await _session(database_sessions)
    await compatibility.record_session(
        second_session,
        {
            "atlas.test": DomainCommands(
                first_seen_at=datetime.now(UTC) + timedelta(milliseconds=1),
                commands=[
                    ("Browser.getVersion", None),
                    ("Page.navigate", {"url": "https://atlas.test/"}),
                ],
            )
        },
    )
    assert (await routing.plan("atlas.test")).first.provider is ProviderName.HTTP
    assert (await routing.plan("atlas.test")).first.provider is ProviderName.HTTP

    async with database_sessions() as database:
        state = await database.get(
            DomainProviderRuntimeState, (domain_id, ProviderName.HTTP.value)
        )
    assert state is not None
    assert state.state == "eligible"
    assert state.restored_session_id == second_session


@pytest.mark.asyncio
async def test_same_or_older_session_cannot_restore_a_suppression(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    await _domain(database_sessions, "concurrent.test")
    compatibility = RuntimeCompatibilityRepository(database_sessions)
    old_first_seen = datetime.now(UTC) - timedelta(minutes=1)
    old_session = await _session(database_sessions, created_at=old_first_seen)

    await compatibility.suppress(
        session_id=old_session,
        hostname="concurrent.test",
        provider=ProviderName.HTTP,
        method="Runtime.callFunctionOn",
    )
    await compatibility.record_session(
        old_session,
        {
            "concurrent.test": DomainCommands(
                first_seen_at=old_first_seen,
                commands=[("Browser.getVersion", None)],
            )
        },
    )

    assert (
        await routing.plan("concurrent.test")
    ).first.provider is ProviderName.LIGHTPANDA


@pytest.mark.asyncio
async def test_session_compatibility_is_idempotent_and_domain_scoped(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    first_domain_id = await _domain(database_sessions, "first.test")
    second_domain_id = await _domain(database_sessions, "second.test")
    compatibility = RuntimeCompatibilityRepository(database_sessions)
    suppressing_session = await _session(database_sessions)
    for hostname in ("first.test", "second.test"):
        await compatibility.suppress(
            session_id=suppressing_session,
            hostname=hostname,
            provider=ProviderName.HTTP,
            method="Runtime.callFunctionOn",
        )

    later_session = await _session(database_sessions)
    observed_at = datetime.now(UTC) + timedelta(milliseconds=1)
    observations = {
        "first.test": DomainCommands(
            first_seen_at=observed_at,
            commands=[("Browser.getVersion", None)],
        ),
        "second.test": DomainCommands(
            first_seen_at=observed_at,
            commands=[
                (
                    "Runtime.evaluate",
                    {"expression": "document.body.textContent"},
                )
            ],
        ),
    }
    await compatibility.record_session(later_session, observations)
    await compatibility.record_session(later_session, observations)

    async with database_sessions() as database:
        first = await database.get(
            DomainProviderRuntimeState,
            (first_domain_id, ProviderName.HTTP.value),
        )
        second = await database.get(
            DomainProviderRuntimeState,
            (second_domain_id, ProviderName.HTTP.value),
        )
        evidence = list(
            await database.scalars(
                select(SessionDomainProviderCompatibility).where(
                    SessionDomainProviderCompatibility.session_id
                    == later_session,
                    SessionDomainProviderCompatibility.provider == "http",
                )
            )
        )
    assert first is not None and first.state == "eligible"
    assert second is not None and second.state == "suppressed"
    assert len(evidence) == 2


async def _add_probe_source(
    sessions: async_sessionmaker[AsyncSession], domain_id: int, hostname: str
) -> str:
    session_id = await _session(
        sessions, created_at=datetime.now(UTC) - timedelta(seconds=2)
    )
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
                    provider="camoufox",
                    occurred_at=now,
                    payload={
                        "url": f"https://{hostname}/",
                        "probe_safe": True,
                    },
                ),
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_RESPONSE,
                    provider="camoufox",
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
    source = await _add_probe_source(
        database_sessions, domain_id, "probe.test"
    )
    health = HealthRepository(database_sessions)
    jobs = await health.schedule(delay_seconds=0)

    assert {job.provider for job in jobs} == set(ProviderName)
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(HealthProbe).where(
                    HealthProbe.source_session_id == source
                )
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
async def test_probe_execution_failure_replaces_stale_healthy_evidence(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    domain_id = await _domain(database_sessions, "crash.test")
    await _add_probe_source(database_sessions, domain_id, "crash.test")
    health = HealthRepository(database_sessions)
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
    domain_id = await _domain(
        database_sessions, "relative.test", providers=()
    )
    source = await _add_probe_source(
        database_sessions, domain_id, "relative.test"
    )
    health = HealthRepository(database_sessions)
    await health.schedule(delay_seconds=0)

    owner = "cohort-worker"
    jobs = await health.claim_cohort(owner, lease_seconds=60)
    assert {job.provider for job in jobs} == set(ProviderName)
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(HealthProbe).where(
                    HealthProbe.source_session_id == source
                )
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
                content_facts=(
                    sparse if job.provider is ProviderName.HTTP else rendered
                ),
            ),
        )

    async with database_sessions() as database:
        http_probe = await database.scalar(
            select(HealthProbe).where(
                HealthProbe.source_session_id == source,
                HealthProbe.candidate_provider == "http",
            )
        )
        http_health = await database.get(
            DomainProviderHealth, (domain_id, "http")
        )
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
