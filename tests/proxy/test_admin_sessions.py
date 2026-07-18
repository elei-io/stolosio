from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_sessions import router


class FakeSessionQueries:
    def __init__(self) -> None:
        self.filters = None

    async def sessions(self, filters, *, before, limit):
        self.filters = filters
        if before == "bad":
            raise ValueError("invalid sessions cursor")
        return SimpleNamespace(sessions=[], next_cursor=None)

    async def session(self, session_id):
        if session_id == "missing":
            return None
        return {"id": session_id, "state": "closed"}


def test_session_routes_parse_filters_and_return_read_models() -> None:
    app = FastAPI()
    queries = FakeSessionQueries()
    app.state.session_queries = queries
    app.include_router(router)
    client = TestClient(app)

    page = client.get(
        "/v1/admin/sessions",
        params={"search": "abc", "state": "closed", "provider": "http"},
    )
    detail = client.get("/v1/admin/sessions/session-1")

    assert page.status_code == 200
    assert page.json() == {"sessions": [], "next_cursor": None}
    assert queries.filters.search == "abc"
    assert queries.filters.state == "closed"
    assert queries.filters.provider == "http"
    assert detail.json() == {"id": "session-1", "state": "closed"}


def test_session_routes_report_missing_sessions_and_invalid_cursors() -> None:
    app = FastAPI()
    app.state.session_queries = FakeSessionQueries()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/v1/admin/sessions/missing").status_code == 404
    response = client.get("/v1/admin/sessions", params={"before": "bad"})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid sessions cursor"
