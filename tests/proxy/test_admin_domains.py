from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_domains import router


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


def test_domain_routes_parse_filters_and_return_read_models() -> None:
    app = FastAPI()
    domains = FakeDomains()
    app.state.domains = domains
    app.include_router(router)
    client = TestClient(app)

    page = client.get(
        "/v1/admin/domains",
        params={
            "search": "example",
            "support_state": "checking",
            "has_active_probes": "true",
            "has_transitions": "false",
            "limit": "25",
        },
    )
    detail = client.get("/v1/admin/domains/7")
    probes = client.get("/v1/admin/domains/7/probes")
    sessions = client.get("/v1/admin/domains/7/sessions")

    assert page.status_code == 200
    assert page.json()["summary"] == {"known_domains": 0}
    assert domains.filters.search == "example"
    assert domains.filters.support_state.value == "checking"
    assert domains.filters.has_active_probes is True
    assert domains.filters.has_transitions is False
    assert detail.json() == {"id": 7, "hostname": "example.test"}
    assert probes.json() == {"probes": [], "next_cursor": None}
    assert sessions.json() == {"sessions": [], "next_cursor": None}


def test_domain_routes_report_missing_domains_and_invalid_filters() -> None:
    app = FastAPI()
    app.state.domains = FakeDomains()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/v1/admin/domains/404").status_code == 404
    assert client.get("/v1/admin/domains/404/probes").status_code == 404
    assert client.get("/v1/admin/domains/404/sessions").status_code == 404
    assert (
        client.get("/v1/admin/domains?support_state=recommended").status_code
        == 422
    )
