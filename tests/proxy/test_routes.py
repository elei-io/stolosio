from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routes.proxy import router
from backend.proxy.contracts import ProviderConnection, ProviderName


def test_proxy_http_routes_are_registered() -> None:
    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/v1/connect/{provider}/json/version", "GET") in routes
    assert ("/v1/connect/{provider}/json/version/", "GET") in routes
    assert ("/v1/connect/{provider}/json", "GET") in routes
    assert ("/v1/connect/{provider}/json/list", "GET") in routes
    assert ("/v1/connect/{provider}/json/protocol", "GET") in routes
    assert ("/v1/connect/{provider}/json/protocol/", "GET") in routes
    assert ("/v1/connect/{provider}/json/new", "PUT") in routes
    assert ("/v1/connect/{provider}/json/activate/{target_id}", "GET") in routes
    assert ("/v1/connect/{provider}/json/close/{target_id}", "GET") in routes


def test_proxy_websocket_routes_are_registered() -> None:
    routes = {route.path for route in router.routes if isinstance(route, APIWebSocketRoute)}

    assert routes >= {
        "/v1/connect/{provider}",
        "/v1/connect/{provider}/devtools/browser/{session_id}",
        "/v1/connect/{provider}/devtools/page/{target_id}",
    }


def test_direct_websocket_route_connects_through_adapter(monkeypatch) -> None:
    class FakeAdapter:
        async def connect(self) -> ProviderConnection:
            return ProviderConnection(ProviderName.CHROMIUM, "ws://upstream")

    async def fake_proxy(websocket, connection: ProviderConnection) -> None:
        assert connection.websocket_url == "ws://upstream"
        await websocket.accept()
        await websocket.send_text(await websocket.receive_text())

    monkeypatch.setattr(
        "backend.api.routes.proxy.get_provider_adapter",
        lambda provider: FakeAdapter(),
    )
    monkeypatch.setattr("backend.api.routes.proxy.proxy_cdp", fake_proxy)

    with (
        TestClient(app) as client,
        client.websocket_connect("/v1/connect/chromium") as websocket,
    ):
        websocket.send_text('{"id":1}')
        assert websocket.receive_text() == '{"id":1}'
