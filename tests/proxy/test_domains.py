from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    DomainProviderSupport,
    DomainProviderTransitionStat,
    GatewaySession,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
    SupportProbe,
)
from backend.proxy.domains import DomainFilters, DomainQueryService

pytestmark = pytest.mark.asyncio


async def test_domain_queries_expose_absolute_support_and_ordered_plan(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add(
            RoutingConfiguration(
                key="global",
                default_provider="camoufox",
                existing_domain_probe_rate_basis_points=100,
                required_support_confirmations=1,
                support_policy_version=1,
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
                    capability_manifest_version=1,
                    updated_at=now,
                ),
                ProviderRoutingProfile(
                    provider="camoufox",
                    automatic_enabled=True,
                    cost_units_per_second=12,
                    capability_manifest_version=1,
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
        database.add(
            DomainProviderSupport(
                domain_id=domain.id,
                provider="lightpanda",
                support_state="supported",
                successful_probe_count=1,
                observed_session_count=2,
                total_cost_units=6,
                navigation_state="healthy",
                status_state="healthy",
                headers_state="healthy",
                method_coverage_state="declared",
                content_state="healthy",
                method_observed_count=1,
                method_declared_count=1,
                last_status_code=200,
                last_checked_at=now,
                last_supported_at=now,
                support_policy_version=1,
                capability_manifest_version=1,
            )
        )
        database.add(
            DomainProviderTransitionStat(
                domain_id=domain.id,
                from_provider="http",
                to_provider="lightpanda",
                trigger="new_requirement",
                transition_count=2,
                last_trigger_method="Runtime.evaluate",
                first_seen_at=now - timedelta(hours=3),
                last_seen_at=now,
            )
        )
        database.add(
            DomainCommandStat(
                domain_id=domain.id,
                method="Runtime.evaluate",
                command_count=4,
                session_count=2,
                first_seen_at=now - timedelta(hours=3),
                last_seen_at=now,
            )
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
        database.add(
            SessionDomain(
                session_id=session.id,
                domain_id=domain.id,
                first_seen_at=session.created_at,
                last_seen_at=session.closed_at,
            )
        )
        database.add(
            AcquisitionAttempt(
                id="00000000-0000-0000-0000-000000000002",
                session_id=session.id,
                ordinal=1,
                provider="lightpanda",
                resolved_settings={},
                setting_sources={},
                state="closed",
                selection_reason="cheapest_supported",
                actual_cost_units=3,
            )
        )
        database.add(
            SupportProbe(
                id="00000000-0000-0000-0000-000000000003",
                domain_id=domain.id,
                source_session_id=session.id,
                candidate_provider="lightpanda",
                trigger="new_domain",
                target_url="https://example.test/",
                required_methods=["Runtime.evaluate"],
                state="completed",
                outcome="supported",
                navigation_state="healthy",
                status_state="healthy",
                headers_state="healthy",
                    method_coverage_state="declared",
                content_state="healthy",
                status_code=200,
                reason_codes=[],
                method_observed_count=1,
                method_declared_count=1,
                unsupported_methods=[],
                content_facts={"visible_text_chars": 400},
                cost_units=3,
                created_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
            )
        )
        domain_id = domain.id

    service = DomainQueryService(database_sessions)
    page = await service.domains(DomainFilters(has_transitions=True))
    detail = await service.domain(domain_id)
    probes = await service.probes(domain_id)
    sessions = await service.sessions(domain_id)

    assert page.domains[0]["expected_plan"] == {
        "reason": "cheapest_supported",
        "candidates": [
            {"provider": "lightpanda", "estimated_cost_units": 3},
            {"provider": "camoufox", "estimated_cost_units": 12},
        ],
    }
    assert detail is not None
    assert detail["transition_count"] == 2
    assert detail["providers"][0]["support_state"] == "supported"
    assert detail["providers"][0]["checks"]["content"]["state"] == "healthy"
    assert "console" not in detail["providers"][0]["checks"]
    assert probes is not None
    assert probes.probes[0]["outcome"] == "supported"
    assert "target_url" not in probes.probes[0]
    assert sessions is not None
    assert sessions.sessions[0]["selection_reason"] == "cheapest_supported"
