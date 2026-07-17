from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.fleet import router as fleet_router
from backend.api.routes.metrics import router as metrics_router
from backend.metrics import GatewayFleetSnapshot, ProviderFleetSnapshot
from backend.metrics.instrumentation import metric_method, metric_reason, transition_trigger
from backend.proxy.contracts import ProviderName


class FakeFleet:
    async def gateway_snapshot(self):
        return GatewayFleetSnapshot(active_sessions=2, capacity=100)

    async def snapshot(self):
        return [
            ProviderFleetSnapshot(
                provider=provider,
                active_attempts=1 if provider is ProviderName.CHROMIUM else 0,
                queued_attempts=2 if provider is ProviderName.CHROMIUM else 0,
                capacity=3,
                oldest_queued_attempt_seconds=(4.5 if provider is ProviderName.CHROMIUM else 0),
                desired_instances=2,
                observed_instances=2,
                ready_instances=1,
                unhealthy_instances=1,
                total_slots=3,
                available_slots=2,
            )
            for provider in ProviderName
        ]


def test_json_and_prometheus_views_share_the_fleet_snapshot() -> None:
    app = FastAPI()
    app.state.fleet = FakeFleet()
    app.include_router(fleet_router)
    app.include_router(metrics_router)

    with TestClient(app) as client:
        response = client.get("/v1/fleet/providers")
        metrics = client.get("/metrics")

    assert response.status_code == 200
    assert len(response.json()) == 5
    chromium = next(
        snapshot for snapshot in response.json() if snapshot["provider"] == "chromium"
    )
    assert chromium == {
        "provider": "chromium",
        "active_attempts": 1,
        "queued_attempts": 2,
        "capacity": 3,
        "oldest_queued_attempt_seconds": 4.5,
        "desired_instances": 2,
        "observed_instances": 2,
        "ready_instances": 1,
        "draining_instances": 0,
        "unhealthy_instances": 1,
        "total_slots": 3,
        "available_slots": 2,
    }
    assert metrics.status_code == 200
    assert "harbor_gateway_active_sessions 2.0" in metrics.text
    assert "harbor_gateway_capacity 100.0" in metrics.text
    assert 'harbor_provider_active_attempts{provider="chromium"} 1.0' in metrics.text
    assert 'harbor_provider_queued_attempts{provider="chromium"} 2.0' in metrics.text
    assert 'harbor_provider_desired_instances{provider="chromium"} 2.0' in metrics.text
    assert 'harbor_provider_ready_instances{provider="chromium"} 1.0' in metrics.text
    assert 'harbor_provider_unhealthy_instances{provider="chromium"} 1.0' in metrics.text
    assert 'harbor_provider_available_slots{provider="chromium"} 2.0' in metrics.text
    assert "session_id" not in metrics.text


def test_metric_labels_map_unregistered_values_to_other() -> None:
    assert metric_method("Page.navigate") == "Page.navigate"
    assert metric_method("Secret.customCommand") == "other"
    assert metric_reason("provider_unavailable") == "provider_unavailable"
    assert metric_reason("private exception details") == "other"
    assert transition_trigger("Runtime.evaluate") == "new_requirement"
    assert transition_trigger("http_transport_failure") == "http_safety"
    assert transition_trigger("private trigger") == "other"


def test_fleet_routes_fail_closed_when_postgres_is_unavailable() -> None:
    class FailingFleet:
        async def gateway_snapshot(self):
            raise ConnectionError

        async def snapshot(self):
            raise ConnectionError

    app = FastAPI()
    app.state.fleet = FailingFleet()
    app.include_router(fleet_router)
    app.include_router(metrics_router)
    with TestClient(app) as client:
        assert client.get("/v1/fleet/providers").status_code == 503
        assert client.get("/metrics").status_code == 503
