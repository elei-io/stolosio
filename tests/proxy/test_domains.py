from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderRuntimeState,
    DomainProviderTransitionStat,
    GatewaySession,
    HealthProbe,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
)
from backend.proxy.domains import DomainFilters, DomainQueryService

pytestmark = pytest.mark.asyncio


async def test_domain_queries_expose_health_runtime_state_and_ordered_plan(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add(
            RoutingConfiguration(
                key="global",
                default_provider="camoufox",
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
                    provider="lightpanda",
                    automatic_enabled=True,
                    cost_units_per_second=4,
                    provider_contract_version=1,
                    updated_at=now,
                ),
                ProviderRoutingProfile(
                    provider="camoufox",
                    automatic_enabled=True,
                    cost_units_per_second=12,
                    provider_contract_version=1,
                    updated_at=now,
                ),
            ]
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
                    provider="lightpanda",
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
                DomainProviderRuntimeState(
                    domain_id=domain.id,
                    provider="lightpanda",
                    state="eligible",
                    last_evidence_at=now,
                    provider_contract_version=1,
                ),
                DomainProviderCostStat(
                    domain_id=domain.id,
                    provider="lightpanda",
                    observed_attempt_count=2,
                    total_cost_units=6,
                ),
                DomainProviderTransitionStat(
                    domain_id=domain.id,
                    from_provider="http",
                    to_provider="lightpanda",
                    trigger="new_requirement",
                    transition_count=2,
                    last_trigger_method="Runtime.evaluate",
                    first_seen_at=now - timedelta(hours=3),
                    last_seen_at=now,
                ),
                DomainCommandStat(
                    domain_id=domain.id,
                    method="Runtime.evaluate",
                    command_count=4,
                    session_count=2,
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
                    provider="lightpanda",
                    resolved_settings={},
                    setting_sources={},
                    state="closed",
                    selection_reason="cheapest_eligible",
                    actual_cost_units=3,
                ),
                HealthProbe(
                    id="00000000-0000-0000-0000-000000000003",
                    domain_id=domain.id,
                    source_session_id=session.id,
                    candidate_provider="lightpanda",
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
            {"provider": "lightpanda", "estimated_cost_units": 3},
        ],
    }
    assert detail is not None
    assert detail["transition_count"] == 2
    assert detail["providers"][0]["health_state"] == "healthy"
    assert detail["providers"][0]["runtime_state"] == "eligible"
    assert detail["providers"][0]["routing_eligible"] is True
    assert set(detail["providers"][0]["checks"]) == {
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
