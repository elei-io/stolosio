from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    Domain,
    DomainProviderProfile,
    GatewaySession,
    QualificationProbe,
    SessionEventRecord,
)
from backend.events import EventType
from backend.proxy.contracts import ProviderName
from backend.proxy.qualification import ProbeResult, QualificationRepository
from backend.proxy.routing import RoutingRepository


@pytest.mark.asyncio
async def test_unknown_domains_use_default_and_qualified_domains_use_cheapest_provider(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    routing = RoutingRepository(database_sessions)
    await routing.ensure_defaults()

    unknown = await routing.choose("unknown.test")
    assert unknown.provider is ProviderName.CAMOUFOX
    assert unknown.reason == "unknown_domain_default"

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
        database.add(
            DomainProviderProfile(
                domain_id=domain.id,
                provider=ProviderName.HTTP.value,
                qualification_state="qualified",
                successful_probe_count=1,
                failed_probe_count=0,
                observed_session_count=2,
                total_cost_units=4,
                comparison_policy_version=1,
            )
        )

    choice = await routing.choose("known.test")
    assert choice.provider is ProviderName.HTTP
    assert choice.reason == "cheapest_qualified"
    assert choice.estimated_cost_units == 2


async def add_eligible_session(
    sessions: async_sessionmaker[AsyncSession],
    domain: Domain,
    *,
    suffix: str,
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
        database.add_all(
            [
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_REQUESTED,
                    provider=ProviderName.CHROMIUM.value,
                    occurred_at=now,
                    payload={
                        "url": f"https://{domain.hostname}/{suffix}",
                        "probe_safe": True,
                    },
                ),
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.NAVIGATION_RESPONSE,
                    provider=ProviderName.CHROMIUM.value,
                    occurred_at=now,
                    payload={
                        "url": f"https://{domain.hostname}/{suffix}",
                        "status": 200,
                        "selected_headers": {"content-type": "text/html"},
                    },
                ),
                SessionEventRecord(
                    session_id=session_id,
                    event_type=EventType.PAGE_CONTENT_OBSERVED,
                    provider=ProviderName.CHROMIUM.value,
                    occurred_at=now,
                    payload={
                        "content_fingerprint": "a" * 64,
                        "content_length": 100,
                    },
                ),
            ]
        )
    return session_id


@pytest.mark.asyncio
async def test_new_domains_are_probed_and_existing_sampling_is_configurable(
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

    source = await add_eligible_session(
        database_sessions,
        Domain(id=domain_id, hostname="probe.test"),
        suffix="first",
    )
    qualifications = QualificationRepository(database_sessions)
    jobs = await qualifications.schedule(delay_seconds=0)

    assert {job.provider for job in jobs} == {
        ProviderName.HTTP,
        ProviderName.LIGHTPANDA,
    }
    async with database_sessions() as database:
        probes = list(
            await database.scalars(
                select(QualificationProbe).where(QualificationProbe.source_session_id == source)
            )
        )
    assert {probe.trigger for probe in probes} == {"new_domain"}

    http = next(job for job in jobs if job.provider is ProviderName.HTTP)
    owner = "worker"
    claimed = await qualifications.claim(owner, lease_seconds=60)
    while claimed is not None and claimed.id != http.id:
        await qualifications.fail(claimed.id, owner)
        claimed = await qualifications.claim(owner, lease_seconds=60)
    assert claimed is not None
    await qualifications.complete(
        claimed.id,
        owner,
        ProbeResult(
            status=200,
            headers={"content-type": "text/html"},
            console_errors=0,
            content_fingerprint="a" * 64,
        ),
    )
    assert (await routing.choose("probe.test")).provider is ProviderName.HTTP

    await routing.update_settings(existing_domain_probe_rate_basis_points=0)
    await add_eligible_session(
        database_sessions,
        Domain(id=domain_id, hostname="probe.test"),
        suffix="second",
    )
    assert await qualifications.schedule(delay_seconds=0) == []
