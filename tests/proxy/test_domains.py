from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    DomainPromotionStat,
    DomainProviderProfile,
    GatewaySession,
    ProviderRoutingProfile,
    QualificationProbe,
    RoutingConfiguration,
    SessionDomain,
)
from backend.proxy.domains import DomainFilters, DomainQueryService

pytestmark = pytest.mark.asyncio


async def test_domain_queries_expose_routing_evidence_without_sensitive_probe_data(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add(
            RoutingConfiguration(
                key="global",
                default_provider="camoufox",
                existing_domain_probe_rate_basis_points=100,
                required_successful_probes=1,
                comparison_policy_version=1,
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
            DomainProviderProfile(
                domain_id=domain.id,
                provider="lightpanda",
                qualification_state="qualified",
                successful_probe_count=1,
                failed_probe_count=0,
                observed_session_count=2,
                total_cost_units=6,
                last_status_code=200,
                last_verified_at=now,
                comparison_policy_version=1,
            )
        )
        database.add(
            DomainPromotionStat(
                domain_id=domain.id,
                promotion_count=2,
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
        database.add(
            DomainCommandStat(
                domain_id=domain.id,
                method="Page.printToPDF",
                command_count=1,
                session_count=1,
                first_seen_at=now,
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
                routing_reason="cheapest_qualified",
                actual_cost_units=3,
            )
        )
        database.add(
            QualificationProbe(
                id="00000000-0000-0000-0000-000000000003",
                domain_id=domain.id,
                source_session_id=session.id,
                candidate_provider="lightpanda",
                trigger="new_domain",
                target_url="https://example.test/",
                baseline_status=200,
                baseline_headers={"content-type": "text/html"},
                baseline_console_errors=1,
                baseline_content_fingerprint="a" * 64,
                state="completed",
                candidate_status=200,
                candidate_headers={"content-type": "text/html"},
                candidate_console_errors=0,
                candidate_content_fingerprint="a" * 64,
                comparison_outcome="matched",
                cost_units=3,
                created_at=now - timedelta(minutes=5),
                finished_at=now - timedelta(minutes=4),
            )
        )
        domain_id = domain.id

    service = DomainQueryService(database_sessions)
    page = await service.domains(DomainFilters(has_promotions=True))
    detail = await service.domain(domain_id)
    probes = await service.probes(domain_id)
    sessions = await service.sessions(domain_id)

    assert page.summary["known_domains"] == 1
    assert page.domains[0]["current_route"] == {
        "provider": "lightpanda",
        "reason": "cheapest_qualified",
        "estimated_cost_units": 3,
    }
    assert detail is not None
    assert detail["promotion"]["last_trigger_method"] == "Runtime.evaluate"
    assert detail["commands"][0]["method"] == "Runtime.evaluate"
    assert detail["providers"][0]["qualification_state"] == "qualified"
    assert detail["providers"][0]["checks"]["status"]["state"] == "matches"
    assert detail["providers"][0]["checks"]["headers"]["state"] == "matches"
    assert detail["providers"][0]["checks"]["console"]["state"] == "matches"
    assert detail["providers"][0]["checks"]["content"]["state"] == "matches"
    assert detail["providers"][0]["checks"]["methods"]["state"] == "differs"
    assert detail["providers"][0]["checks"]["methods"]["unsupported_methods"] == [
        "Page.printToPDF"
    ]
    assert detail["providers"][1]["checks"]["status"]["state"] == "not_checked"
    assert probes is not None
    assert probes.probes[0]["status_matches"] is True
    assert probes.probes[0]["headers_match"] is True
    assert "target_url" not in probes.probes[0]
    assert "baseline_headers" not in probes.probes[0]
    assert sessions is not None
    assert sessions.sessions[0]["providers"] == ["lightpanda"]
    assert sessions.sessions[0]["routing_reason"] == "cheapest_qualified"
