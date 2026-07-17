from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    Domain,
    DomainCommandStat,
    DomainProviderSupport,
    GatewaySession,
    SessionEventRecord,
    SupportProbe,
)
from backend.events import EventType
from backend.proxy.contracts import ProviderName
from backend.proxy.routing import RoutingRepository
from backend.proxy.support import SupportProbeResult, SupportRepository


@pytest.mark.asyncio
async def test_plan_uses_configured_default_then_orders_supported_with_default_last(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()

    unknown = await routing.plan("unknown.test")
    assert unknown.reason == "configured_default"
    assert unknown.first.provider is ProviderName.CAMOUFOX

    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="known.test",
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        database.add_all(
            [
                DomainProviderSupport(
                    domain_id=domain.id,
                    provider="http",
                    support_state="supported",
                    successful_probe_count=1,
                    observed_session_count=2,
                    total_cost_units=4,
                    support_policy_version=1,
                    capability_manifest_version=1,
                ),
                DomainProviderSupport(
                    domain_id=domain.id,
                    provider="lightpanda",
                    support_state="supported",
                    successful_probe_count=1,
                    support_policy_version=1,
                    capability_manifest_version=1,
                ),
            ]
        )

    plan = await routing.plan("known.test")
    assert plan.reason == "cheapest_supported"
    assert [candidate.provider for candidate in plan.candidates] == [
        ProviderName.HTTP,
        ProviderName.LIGHTPANDA,
        ProviderName.CAMOUFOX,
    ]
    assert plan.first.estimated_cost_units == 2


@pytest.mark.asyncio
async def test_unsupported_conclusion_still_uses_configured_default(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="unsupported.test",
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        database.add(
            DomainProviderSupport(
                domain_id=domain.id,
                provider="http",
                support_state="unsupported",
                failed_probe_count=1,
                support_policy_version=1,
                capability_manifest_version=1,
            )
        )

    plan = await routing.plan("unsupported.test")

    assert plan.reason == "configured_default"
    assert [candidate.provider for candidate in plan.candidates] == [
        ProviderName.CAMOUFOX
    ]


async def _add_eligible_session(
    sessions: async_sessionmaker[AsyncSession], domain: Domain
) -> str:
    session_id = str(uuid4())
    now = datetime.now(UTC)
    async with sessions.begin() as database:
        database.add(
            GatewaySession(
                id=session_id,
                owner_id="test",
                lease_token=str(uuid4()),
                requested_settings={},
                state="closed",
                created_at=now - timedelta(seconds=2),
                closed_at=now - timedelta(seconds=1),
            )
        )
        database.add(
            DomainCommandStat(
                domain_id=domain.id,
                method="Runtime.evaluate",
                command_count=1,
                session_count=1,
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
                        "url": f"https://{domain.hostname}/",
                        "probe_safe": True,
                    },
                ),
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_RESPONSE,
                    provider="camoufox",
                    occurred_at=now,
                    payload={"url": f"https://{domain.hostname}/", "status": 200},
                ),
            ]
        )
    return session_id


@pytest.mark.asyncio
async def test_support_checks_cover_cheap_and_expensive_enabled_providers(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="probe.test",
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        domain_id = domain.id
    source = await _add_eligible_session(
        database_sessions,
        Domain(id=domain_id, hostname="probe.test"),
    )
    support = SupportRepository(database_sessions)
    jobs = await support.schedule(delay_seconds=0)

    assert {job.provider for job in jobs} == set(ProviderName)
    assert all(job.required_methods == ("Runtime.evaluate",) for job in jobs)
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(SupportProbe).where(SupportProbe.source_session_id == source)
            )
        )
    assert {probe.trigger for probe in probes} == {"new_domain"}

    owner = "worker"
    claimed = await support.claim(owner, lease_seconds=60)
    assert claimed is not None
    await support.complete(
        claimed.id,
        owner,
        SupportProbeResult(
            navigation_state="healthy",
            status_state="healthy",
            headers_state="healthy",
            method_coverage_state="declared",
            content_state="healthy",
            status_code=200,
            reason_codes=(),
            method_observed_count=1,
            method_declared_count=1,
            unsupported_methods=(),
            content_facts={"visible_text_chars": 500},
        ),
    )
    plan = await routing.plan("probe.test")
    assert plan.first.provider is claimed.provider
    assert plan.candidates[-1].provider is ProviderName.CAMOUFOX
