from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_fleets import router
from backend.fleet import FleetConfiguration
from backend.proxy.contracts import ProviderName


class FakeFleetAdmin:
    def __init__(self) -> None:
        self.value = FleetConfiguration(
            provider=ProviderName.CHROMIUM,
            minimum_instances=1,
            maximum_instances=4,
            session_capacity_per_instance=2,
            scale_down_cooldown_seconds=30,
            desired_instances=1,
            configuration_version=1,
            enabled=True,
        )
        self.actor = None

    async def configurations(self):
        return [self.value]

    async def configuration(self, provider):
        return self.value if provider is ProviderName.CHROMIUM else None

    async def update(self, provider, values, *, actor):
        self.actor = actor
        self.value = replace(self.value, **values, configuration_version=2)
        return self.value


def test_development_admin_can_update_versioned_fleet_configuration() -> None:
    app = FastAPI()
    service = FakeFleetAdmin()
    app.state.fleet_admin = service
    app.state.environment = "development"
    app.include_router(router)

    with TestClient(app) as client:
        response = client.patch(
            "/v1/admin/fleets/chromium",
            json={"maximum_instances": 8, "session_capacity_per_instance": 3},
            headers={"X-Harbor-Actor": "test-operator"},
        )

    assert response.status_code == 200
    assert response.json()["maximum_instances"] == 8
    assert response.json()["session_capacity_per_instance"] == 3
    assert response.json()["configuration_version"] == 2
    assert service.actor == "test-operator"


def test_admin_mutation_is_hidden_outside_development() -> None:
    app = FastAPI()
    app.state.fleet_admin = FakeFleetAdmin()
    app.state.environment = "production"
    app.include_router(router)

    with TestClient(app) as client:
        response = client.patch(
            "/v1/admin/fleets/chromium",
            json={"maximum_instances": 8},
        )

    assert response.status_code == 404
