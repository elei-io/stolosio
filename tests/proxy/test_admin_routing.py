from dataclasses import replace
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_routing import router
from backend.proxy.contracts import ProviderName
from backend.proxy.routing import RoutingSettings


class FakeRouting:
    def __init__(self) -> None:
        self.value = RoutingSettings(ProviderName.CAMOUFOX, 100, 1, 1, 1)
        self.providers = [
            SimpleNamespace(
                provider="http",
                automatic_enabled=True,
                cost_units_per_second=1,
                capability_manifest_version=1,
            )
        ]

    async def settings(self):
        return self.value

    async def update_settings(self, **values):
        self.value = replace(self.value, **values, configuration_version=2)
        return self.value

    async def provider_profiles(self):
        return self.providers

    async def update_provider(self, provider, **values):
        for name, value in values.items():
            setattr(self.providers[0], name, value)
        return self.providers[0]


def test_routing_configuration_and_costs_are_operator_configurable() -> None:
    app = FastAPI()
    app.state.routing = FakeRouting()
    app.state.environment = "development"
    app.include_router(router)

    with TestClient(app) as client:
        routing = client.patch(
            "/v1/admin/routing",
            json={
                "default_provider": "http",
                "existing_domain_probe_rate_basis_points": 250,
            },
        )
        cost = client.patch(
            "/v1/admin/routing/providers/http",
            json={"cost_units_per_second": 2},
        )

    assert routing.status_code == 200
    assert routing.json()["default_provider"] == "http"
    assert routing.json()["existing_domain_probe_rate_basis_points"] == 250
    assert routing.json()["configuration_version"] == 2
    assert cost.status_code == 200
    assert cost.json()["cost_units_per_second"] == 2
