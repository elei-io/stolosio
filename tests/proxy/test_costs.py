from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import AcquisitionAttempt, GatewaySession
from backend.proxy.costs import CostQueryService


@pytest.mark.asyncio
async def test_cost_overview_uses_finalized_attempts_and_provider_time_bases(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add_all(
            [
                GatewaySession(
                    id="00000000-0000-0000-0000-000000000001",
                    owner_id="owner",
                    lease_token="lease-1",
                    requested_settings={},
                    state="closed",
                    created_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                ),
                GatewaySession(
                    id="00000000-0000-0000-0000-000000000002",
                    owner_id="owner",
                    lease_token="lease-2",
                    requested_settings={},
                    state="failed",
                    created_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(minutes=30),
                ),
            ]
        )
        await database.flush()
        database.add_all(
            [
                AcquisitionAttempt(
                    id="00000000-0000-0000-0000-000000000011",
                    session_id="00000000-0000-0000-0000-000000000001",
                    ordinal=1,
                    provider="browserless",
                    resolved_settings={},
                    setting_sources={},
                    state="completed",
                    finished_at=now - timedelta(hours=1),
                    capacity_occupied_ms=12_000,
                    browser_connected_ms=10_000,
                    chargeable_time_ms=12_000,
                    modeled_cost_units=24,
                ),
                AcquisitionAttempt(
                    id="00000000-0000-0000-0000-000000000012",
                    session_id="00000000-0000-0000-0000-000000000002",
                    ordinal=1,
                    provider="browserbase",
                    resolved_settings={},
                    setting_sources={},
                    state="failed",
                    finished_at=now - timedelta(minutes=30),
                    browser_connected_ms=5_000,
                    estimated_billable_ms=60_000,
                    chargeable_time_ms=60_000,
                    modeled_cost_units=120,
                ),
            ]
        )

    value = await CostQueryService(database_sessions).overview("24h")

    assert value["totals"] == {
        "session_count": 2,
        "attempt_count": 2,
        "failed_attempt_count": 1,
        "modeled_cost_units": 144,
        "chargeable_time_ms": 72_000,
        "browser_connected_time_ms": 15_000,
        "browserless_slot_time_ms": 12_000,
        "browserbase_billable_time_ms": 60_000,
    }
    assert [row["provider"] for row in value["providers"]] == [
        "browserbase",
        "browserless",
    ]
    assert value["recent_sessions"][0]["modeled_cost_units"] == 120
    assert value["buckets"]


@pytest.mark.asyncio
async def test_cost_overview_rejects_unknown_window(database_sessions) -> None:
    with pytest.raises(ValueError, match="invalid cost window"):
        await CostQueryService(database_sessions).overview("forever")
