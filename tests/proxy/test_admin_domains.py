from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_domains import router
from backend.proxy.contracts import ProviderName
from backend.proxy.health import (
    HealthProbeJob,
    ManualProbeSchedule,
    ManualProbeUnavailableError,
)


class FakeDomains:
    def __init__(self) -> None:
        self.filters = None

    async def domains(self, filters, *, before, limit):
        self.filters = filters
        return SimpleNamespace(
            domains=[],
            summary={"known_domains": 0},
            next_cursor=None,
        )

    async def domain(self, domain_id):
        if domain_id == 404:
            return None
        return {"id": domain_id, "hostname": "example.test"}

    async def probes(self, domain_id, *, before, limit):
        if domain_id == 404:
            return None
        return SimpleNamespace(probes=[], next_cursor=None)

    async def sessions(self, domain_id, *, before, limit):
        if domain_id == 404:
            return None
        return SimpleNamespace(sessions=[], next_cursor=None)


class FakeHealth:
    def __init__(self) -> None:
        self.domain_id = None
        self.providers = None

    async def schedule_manual(self, domain_id, providers):
        self.domain_id = domain_id
        self.providers = providers
        if domain_id == 404:
            raise ManualProbeUnavailableError("domain not found")
        if domain_id == 409:
            raise ManualProbeUnavailableError(
                "no probe-safe navigation has been recorded for this domain"
            )
        return ManualProbeSchedule(
            scheduled=(
                HealthProbeJob(
                    "probe-1",
                    ProviderName.HTTP,
                    "https://example.test/",
                ),
            ),
            already_active=(),
        )


def test_domain_routes_parse_filters_and_return_read_models() -> None:
    app = FastAPI()
    domains = FakeDomains()
    health = FakeHealth()
    app.state.domains = domains
    app.state.health = health
    app.include_router(router)
    client = TestClient(app)

    page = client.get(
        "/v1/admin/domains",
        params={
            "search": "example",
            "eligibility_state": "checking",
            "has_active_probes": "true",
            "has_transitions": "false",
            "limit": "25",
        },
    )
    detail = client.get("/v1/admin/domains/7")
    probes = client.get("/v1/admin/domains/7/probes")
    sessions = client.get("/v1/admin/domains/7/sessions")
    triggered = client.post(
        "/v1/admin/domains/7/probes",
        json={"providers": ["http"]},
    )

    assert page.status_code == 200
    assert page.json()["summary"] == {"known_domains": 0}
    assert domains.filters.search == "example"
    assert domains.filters.eligibility_state.value == "checking"
    assert domains.filters.has_active_probes is True
    assert domains.filters.has_transitions is False
    assert detail.json() == {"id": 7, "hostname": "example.test"}
    assert probes.json() == {"probes": [], "next_cursor": None}
    assert sessions.json() == {"sessions": [], "next_cursor": None}
    assert triggered.json() == {
        "scheduled": [{"id": "probe-1", "provider": "http"}],
        "already_active": [],
    }
    assert health.domain_id == 7
    assert health.providers == (ProviderName.HTTP,)


def test_domain_routes_report_missing_domains_and_invalid_filters() -> None:
    app = FastAPI()
    app.state.domains = FakeDomains()
    app.state.health = FakeHealth()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/v1/admin/domains/404").status_code == 404
    assert client.get("/v1/admin/domains/404/probes").status_code == 404
    assert client.get("/v1/admin/domains/404/sessions").status_code == 404
    assert client.post("/v1/admin/domains/404/probes", json={}).status_code == 404
    assert client.post("/v1/admin/domains/409/probes", json={}).status_code == 409
    assert (
        client.get("/v1/admin/domains?eligibility_state=recommended").status_code
        == 422
    )
