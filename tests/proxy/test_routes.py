import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.main import app
from backend.api.routes.proxy import router
from backend.proxy.contracts import ProviderName


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


def test_direct_websocket_route_connects_through_resolved_adapter(monkeypatch) -> None:
    class FakeGateway:
        async def connect(self, websocket, requested, resolved) -> None:
            assert resolved.provider.slug is ProviderName.CHROMIUM
            await websocket.accept()
            await websocket.send_text(await websocket.receive_text())

    with TestClient(app) as client:
        app.state.gateway = FakeGateway()
        with client.websocket_connect(
            "/v1/connect?harbor.provider.slug=chromium"
        ) as websocket:
            websocket.send_text('{"id":1}')
            assert websocket.receive_text() == '{"id":1}'


def test_direct_websocket_route_defaults_to_auto_chromium(monkeypatch) -> None:
    selected: list[ProviderName] = []

    class FakeGateway:
        async def connect(self, websocket, requested, resolved) -> None:
            selected.append(resolved.provider.slug)
            await websocket.accept()
            await websocket.close()

    with TestClient(app) as client:
        app.state.gateway = FakeGateway()
        with client.websocket_connect("/v1/connect"):
            pass

    assert selected == [ProviderName.CHROMIUM]


def test_invalid_harbor_setting_closes_with_stable_code() -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/v1/connect?harbor.unknown=value") as websocket:
                websocket.receive_text()

    assert error.value.code == 4400
    assert error.value.reason == "invalid_harbor_settings"
