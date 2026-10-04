import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from backend.api.main import app
from backend.api.routes.proxy import router


def test_proxy_http_routes_are_registered() -> None:
    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/v1/connect/json/version", "GET") in routes
    assert ("/v1/connect/json/version/", "GET") in routes
    assert ("/v1/connect/json", "GET") in routes
    assert ("/v1/connect/json/list", "GET") in routes
    assert ("/v1/connect/json/protocol", "GET") in routes
    assert ("/v1/connect/json/protocol/", "GET") in routes
    assert ("/v1/connect/json/new", "PUT") in routes
    assert ("/v1/connect/json/activate/{target_id}", "GET") in routes
    assert ("/v1/connect/json/close/{target_id}", "GET") in routes


def test_proxy_websocket_routes_are_registered() -> None:
    routes = {route.path for route in router.routes if isinstance(route, APIWebSocketRoute)}

    assert routes >= {
        "/v1/connect",
        "/v1/connect/",
        "/v1/connect/devtools/browser/{session_id}",
        "/v1/connect/devtools/page/{target_id}",
    }


@pytest.mark.parametrize(
    "route",
    [
        "/v1/connect/json/version",
        "/v1/connect/json/list",
        "/v1/connect/json/protocol",
        "/v1/connect/json/activate/target",
        "/v1/connect/json/close/target",
    ],
)
def test_unsupported_discovery_routes_return_explicit_errors(route):
    with TestClient(app) as client:
        response = client.get(route)
    assert response.status_code == 501
    assert response.json() == {"detail": "unsupported_endpoint"}


def test_unsupported_create_target_returns_explicit_error():
    with TestClient(app) as client:
        response = client.put("/v1/connect/json/new")
    assert response.status_code == 501
    assert response.json() == {"detail": "unsupported_endpoint"}


@pytest.mark.parametrize(
    "route",
    [
        "/v1/connect/devtools/browser/session",
        "/v1/connect/devtools/page/target",
    ],
)
def test_unsupported_target_websockets_are_denied_before_acceptance(route):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDenialResponse) as error:
            with client.websocket_connect(route):
                pass
    assert error.value.status_code == 501
    assert error.value.json() == {"error": "unsupported_endpoint"}


def test_direct_websocket_route_connects_through_resolved_adapter(monkeypatch) -> None:
    class FakeGateway:
        async def connect(self, websocket) -> None:
            assert websocket.query_params["stolosio.provider.slug"] == "browserless"
            await websocket.accept()
            await websocket.send_text(await websocket.receive_text())

    with TestClient(app) as client:
        app.state.gateway = FakeGateway()
        with client.websocket_connect(
            "/v1/connect?stolosio.provider.slug=browserless"
        ) as websocket:
            websocket.send_text('{"id":1}')
            assert websocket.receive_text() == '{"id":1}'


def test_direct_websocket_route_leaves_automatic_provider_unbound(monkeypatch) -> None:
    automatic: list[bool] = []

    class FakeGateway:
        async def connect(self, websocket) -> None:
            automatic.append("stolosio.provider.slug" not in websocket.query_params)
            await websocket.accept()
            await websocket.close()

    with TestClient(app) as client:
        app.state.gateway = FakeGateway()
        with client.websocket_connect("/v1/connect"):
            pass

    assert automatic == [True]


def test_invalid_stolosio_setting_closes_with_stable_code() -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDenialResponse) as error:
            with client.websocket_connect("/v1/connect?stolosio.unknown=value"):
                pass

    assert error.value.status_code == 400
