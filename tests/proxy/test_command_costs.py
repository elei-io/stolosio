from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_command_costs import router
from backend.db.models import ProviderCommandCostStat
from backend.proxy.command_costs import CommandCostQueryService
from backend.proxy.contracts import ProviderName


@pytest.mark.asyncio
async def test_command_cost_query_orders_browser_time_and_filters_overhead(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add_all(
            [
                ProviderCommandCostStat(
                    provider="browserless",
                    method="Runtime.evaluate",
                    command_count=3,
                    failed_count=1,
                    interrupted_count=0,
                    total_duration_ms=150,
                    total_provider_latency_ms=120,
                    total_harbor_queue_ms=30,
                    attributed_browser_time_ms=100,
                    attributed_cost_units=4,
                    first_seen_at=now - timedelta(hours=1),
                    last_seen_at=now,
                ),
                ProviderCommandCostStat(
                    provider="browserless",
                    method="__session_overhead__",
                    command_count=0,
                    failed_count=0,
                    interrupted_count=0,
                    total_duration_ms=0,
                    total_provider_latency_ms=0,
                    total_harbor_queue_ms=0,
                    attributed_browser_time_ms=200,
                    attributed_cost_units=8,
                    first_seen_at=now,
                    last_seen_at=now,
                ),
                ProviderCommandCostStat(
                    provider="browserbase",
                    method="Page.navigate",
                    command_count=2,
                    failed_count=0,
                    interrupted_count=1,
                    total_duration_ms=90,
                    total_provider_latency_ms=80,
                    total_harbor_queue_ms=10,
                    attributed_browser_time_ms=75,
                    attributed_cost_units=9,
                    first_seen_at=now,
                    last_seen_at=now,
                ),
            ]
        )

    service = CommandCostQueryService(database_sessions)
    all_rows = await service.command_costs(limit=10)
    browserless = await service.command_costs(
        provider=ProviderName.BROWSERLESS,
        include_overhead=False,
    )

    assert [row["method"] for row in all_rows] == [
        "Page.navigate",
        "__session_overhead__",
        "Runtime.evaluate",
    ]
    assert [row["method"] for row in browserless] == ["Runtime.evaluate"]
    assert browserless[0]["failed_count"] == 1


def test_command_cost_route_validates_filters() -> None:
    class FakeCommandCosts:
        async def command_costs(self, **values):
            assert values == {
                "provider": ProviderName.BROWSERLESS,
                "include_overhead": False,
                "sort_by": "cost",
                "limit": 5,
            }
            return [
                {
                    "provider": "browserless",
                    "method": "Runtime.evaluate",
                    "command_count": 1,
                    "failed_count": 0,
                    "interrupted_count": 0,
                    "total_duration_ms": 10,
                    "total_provider_latency_ms": 8,
                    "total_harbor_queue_ms": 2,
                    "attributed_browser_time_ms": 8,
                    "attributed_cost_units": 1,
                    "first_seen_at": "2026-07-18T00:00:00Z",
                    "last_seen_at": "2026-07-18T00:00:00Z",
                }
            ]

    app = FastAPI()
    app.state.command_costs = FakeCommandCosts()
    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/v1/admin/command-costs?provider=browserless&include_overhead=false&limit=5"
    )

    assert response.status_code == 200
    assert response.json()[0]["method"] == "Runtime.evaluate"
    assert client.get("/v1/admin/command-costs?limit=0").status_code == 422
