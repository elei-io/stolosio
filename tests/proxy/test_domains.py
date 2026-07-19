from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderTransitionStat,
    ExternalProviderLimit,
    GatewaySession,
    HealthProbe,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
)
from backend.proxy.domains import DomainFilters, DomainQueryService

pytestmark = pytest.mark.asyncio


async def test_domain_queries_expose_health_and_ordered_plan(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add(
            RoutingConfiguration(
                key="global",
                default_provider="browserless",
                existing_domain_probe_rate_basis_points=100,
                required_health_confirmations=1,
                health_policy_version=1,
                configuration_version=2,
                updated_at=now,
            )
        )
        database.add_all(
            [
                ProviderRoutingProfile(
                    provider="http",
                    automatic_enabled=True,
                    cost_units_per_second=4,
                    provider_contract_version=1,
                    updated_at=now,
                ),
                ProviderRoutingProfile(
                    provider="browserless",
                    automatic_enabled=True,
                    cost_units_per_second=12,
                    provider_contract_version=1,
                    updated_at=now,
                ),
                ProviderRoutingProfile(
                    provider="browserbase",
                    automatic_enabled=True,
                    cost_units_per_second=100,
                    provider_contract_version=1,
                    updated_at=now,
                ),
            ]
        )
        database.add(
            ExternalProviderLimit(
                provider="browserbase",
                enabled=True,
                max_active_sessions=5,
                max_queued_attempts=100,
            )
        )
        domain = Domain(
            hostname="example.test",
            first_seen_at=now - timedelta(days=1),
            last_seen_at=now,
            session_count=3,
            eligible_acquisition_count=2,
        )
        database.add(domain)
        await database.flush()
        database.add_all(
            [
                DomainProviderHealth(
                    domain_id=domain.id,
                    provider="browserless",
                    health_state="healthy",
                    successful_probe_count=1,
                    navigation_state="healthy",
                    status_state="healthy",
                    headers_state="healthy",
                    content_state="healthy",
                    last_status_code=200,
                    last_checked_at=now,
                    last_healthy_at=now,
                    health_policy_version=1,
                    provider_contract_version=1,
                ),
                DomainProviderCostStat(
                    domain_id=domain.id,
                    provider="browserless",
                    observed_attempt_count=2,
                    total_cost_units=6,
                ),
                DomainProviderTransitionStat(
                    domain_id=domain.id,
                    from_provider="http",
                    to_provider="browserless",
                    trigger="new_requirement",
                    transition_count=2,
                    last_trigger_method="Runtime.evaluate",
                    first_seen_at=now - timedelta(hours=3),
                    last_seen_at=now,
                ),
            ]
        )
        session = GatewaySession(
            id="00000000-0000-0000-0000-000000000001",
            owner_id="owner",
            lease_token="lease",
            requested_settings={},
            state="closed",
            created_at=now - timedelta(minutes=2),
            closed_at=now - timedelta(minutes=1),
        )
        database.add(session)
        await database.flush()
        database.add_all(
            [
                SessionDomain(
                    session_id=session.id,
                    domain_id=domain.id,
                    first_seen_at=session.created_at,
                    last_seen_at=session.closed_at,
                ),
                AcquisitionAttempt(
                    id="00000000-0000-0000-0000-000000000002",
                    session_id=session.id,
                    ordinal=1,
                    provider="browserless",
                    resolved_settings={},
                    setting_sources={},
                    state="closed",
                    selection_reason="cheapest_eligible",
                    modeled_cost_units=3,
                ),
                HealthProbe(
                    id="00000000-0000-0000-0000-000000000003",
                    domain_id=domain.id,
                    source_session_id=session.id,
                    candidate_provider="browserless",
                    trigger="new_domain",
                    target_url="https://example.test/",
                    state="completed",
                    outcome="healthy",
                    navigation_state="healthy",
                    status_state="healthy",
                    headers_state="healthy",
                    content_state="healthy",
                    status_code=200,
                    reason_codes=[],
                    content_facts={"visible_text_chars": 400},
                    cost_units=3,
                    created_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                ),
            ]
        )
        domain_id = domain.id

    service = DomainQueryService(database_sessions)
    page = await service.domains(DomainFilters(has_transitions=True))
    detail = await service.domain(domain_id)
    probes = await service.probes(domain_id)
    sessions = await service.sessions(domain_id)

    assert page.domains[0]["expected_plan"] == {
        "reason": "cheapest_eligible",
        "candidates": [
            {"provider": "browserless", "estimated_cost_units": 3},
        ],
        "paid_fallback_available": True,
    }
    assert detail is not None
    assert detail["transition_count"] == 2
    browserless = next(
        provider for provider in detail["providers"] if provider["provider"] == "browserless"
    )
    assert browserless["health_state"] == "healthy"
    assert browserless["routing_eligible"] is True
    browserbase = next(
        provider for provider in detail["providers"] if provider["provider"] == "browserbase"
    )
    assert browserbase["health_state"] == "unknown"
    assert browserbase["routing_eligible"] is False
    assert browserbase["paid_fallback_available"] is True
    assert set(browserless["checks"]) == {
        "navigation",
        "status",
        "headers",
        "content",
    }
    assert probes is not None
    assert probes.probes[0]["outcome"] == "healthy"
    assert "target_url" not in probes.probes[0]
    assert "method_coverage_state" not in probes.probes[0]
    assert sessions is not None
    assert sessions.sessions[0]["selection_reason"] == "cheapest_eligible"
