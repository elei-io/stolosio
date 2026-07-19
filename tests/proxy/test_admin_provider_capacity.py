from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_provider_capacity import router
from backend.proxy.contracts import ProviderName
from backend.proxy.external_capacity import (
    ExternalCapacity,
    ExternalCapacityEnablementError,
)


class FakeExternalCapacity:
    def __init__(self) -> None:
        self.value = ExternalCapacity(
            provider=ProviderName.HTTP,
            enabled=True,
            max_active_sessions=100,
            max_queued_attempts=100,
            configuration_version=1,
        )
        self.actor: str | None = None

    async def update(self, provider, values, *, actor):
        if provider is not ProviderName.HTTP:
            return None
        self.actor = actor
        self.value = replace(
            self.value,
            **values,
            configuration_version=self.value.configuration_version + 1,
        )
        return self.value


def test_admin_can_update_http_capacity() -> None:
    app = FastAPI()
    capacity = FakeExternalCapacity()
    app.state.external_capacity = capacity
    app.include_router(router)

    with TestClient(app) as client:
        response = client.patch(
            "/v1/admin/providers/http/capacity",
            json={"max_active_sessions": 12, "max_queued_attempts": 24},
            headers={"X-Harbor-Actor": "test-operator"},
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "http"
    assert response.json()["max_active_sessions"] == 12
    assert response.json()["max_queued_attempts"] == 24
    assert capacity.actor == "test-operator"


def test_missing_browserbase_api_key_is_returned_as_a_clear_conflict() -> None:
    class MissingCredentialsCapacity(FakeExternalCapacity):
        async def update(self, provider, values, *, actor):
            raise ExternalCapacityEnablementError(
                "Cannot enable Browserbase because its API key is not configured. "
                "Configure browserbase_api_key and restart Harbor."
            )

    app = FastAPI()
    app.state.external_capacity = MissingCredentialsCapacity()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.patch(
            "/v1/admin/providers/browserbase/capacity",
            json={"enabled": True},
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Cannot enable Browserbase because its API key is not configured. "
            "Configure browserbase_api_key and restart Harbor."
        )
    }
