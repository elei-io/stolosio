from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.admin_events import router


class FakeHistory:
    def __init__(self) -> None:
        self.filters = None

    async def events(
        self,
        filters,
        *,
        before,
        occurred_after,
        occurred_before,
        limit,
    ):
        self.filters = filters
        self.occurred_after = occurred_after
        self.occurred_before = occurred_before
        return SimpleNamespace(events=[], next_cursor=None)


def test_activity_history_route_parses_repeated_filters() -> None:
    app = FastAPI()
    history = FakeHistory()
    app.state.activity_history = history
    app.include_router(router)

    response = TestClient(app).get(
        "/v1/admin/events",
        params=[
            ("provider", "browserless"),
            ("provider", "browserbase"),
            ("event_family", "attempt"),
            ("outcome", "failure"),
            ("occurred_after", "2026-07-18T01:00:00Z"),
            ("occurred_before", "2026-07-18T02:00:00Z"),
            ("limit", "25"),
        ],
    )

    assert response.status_code == 200
    assert response.json() == {"events": [], "next_cursor": None}
    assert [provider.value for provider in history.filters.providers] == [
        "browserless",
        "browserbase",
    ]
    assert [family.value for family in history.filters.families] == ["attempt"]
    assert [outcome.value for outcome in history.filters.outcomes] == ["failure"]
    assert history.occurred_after.isoformat() == "2026-07-18T01:00:00+00:00"
    assert history.occurred_before.isoformat() == "2026-07-18T02:00:00+00:00"


def test_activity_routes_reject_unknown_filters_and_unavailable_stream() -> None:
    app = FastAPI()
    app.state.activity_history = FakeHistory()
    app.state.activity_stream = None
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/v1/admin/events?event_type=made.up").status_code == 422
    response = client.get("/v1/admin/events/stream")
    assert response.status_code == 503
    assert response.json()["detail"] == "activity stream unavailable"
