from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_network import router
from backend.proxy.network_policy import NetworkPolicy, normalize_domain_patterns


class FakeNetworkPolicy:
    def __init__(self) -> None:
        self.value = NetworkPolicy((), 1)

    async def settings(self) -> NetworkPolicy:
        return self.value

    async def update(self, patterns: list[str]) -> NetworkPolicy:
        self.value = NetworkPolicy(
            normalize_domain_patterns(patterns),
            self.value.configuration_version + 1,
        )
        return self.value


def test_network_policy_routes_replace_the_global_blocklist() -> None:
    app = FastAPI()
    app.state.network_policy = FakeNetworkPolicy()
    app.include_router(router)
    client = TestClient(app)

    initial = client.get("/v1/admin/network")
    updated = client.patch(
        "/v1/admin/network",
        json={"blocked_domain_patterns": ["ads.example", "*.DoubleClick.NET"]},
    )

    assert initial.json() == {
        "blocked_domain_patterns": [],
        "configuration_version": 1,
    }
    assert updated.status_code == 200
    assert updated.json() == {
        "blocked_domain_patterns": ["*.doubleclick.net", "ads.example"],
        "configuration_version": 2,
    }


def test_network_policy_routes_reject_invalid_patterns_and_payloads() -> None:
    app = FastAPI()
    app.state.network_policy = FakeNetworkPolicy()
    app.include_router(router)
    client = TestClient(app)

    invalid = client.patch(
        "/v1/admin/network",
        json={"blocked_domain_patterns": ["https://ads.example"]},
    )
    missing = client.patch("/v1/admin/network", json={})
    extra = client.patch(
        "/v1/admin/network",
        json={"blocked_domain_patterns": [], "enabled": True},
    )

    assert invalid.status_code == 422
    assert missing.status_code == 422
    assert extra.status_code == 422
