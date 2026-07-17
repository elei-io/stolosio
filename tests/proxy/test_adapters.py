import json

import pytest

from backend.proxy.adapters.cdp import (
    DirectCdpAdapter,
    DiscoveredCdpAdapter,
    WebSocketProviderSession,
    _discovery_url,
)
from backend.proxy.adapters.http import HttpAdapter
from backend.proxy.adapters.lightpanda import (
    LightpandaAdapter,
    LightpandaProviderSession,
)
from backend.proxy.adapters.registry import get_provider_adapter
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SessionState,
)


class FakeWebSocket:
    def __init__(self, messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.messages = messages or []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def recv(self) -> str:
        command = json.loads(self.sent[-1])
        return json.dumps({"id": command["id"], "result": {}})

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_direct_adapter_connects_to_configured_endpoint(monkeypatch) -> None:
    request: dict[str, object] = {}

    async def fake_connect(url: str, **kwargs):
        request["url"] = url
        request["kwargs"] = kwargs
        return FakeWebSocket()

    monkeypatch.setattr("backend.proxy.adapters.cdp.connect", fake_connect)
    adapter = DirectCdpAdapter(ProviderName.LIGHTPANDA, "ws://lightpanda:9222")

    session = await adapter.acquire(None, None)  # type: ignore[arg-type]

    assert session.provider is ProviderName.LIGHTPANDA
    assert request["url"] == "ws://lightpanda:9222"


def test_chromium_discovery_uses_http_endpoint() -> None:
    assert _discovery_url("ws://chromium:9222") == "http://chromium:9222/json/version"


@pytest.mark.asyncio
async def test_discovered_adapter_separates_websocket_and_transport_hosts(monkeypatch) -> None:
    request: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"webSocketDebuggerUrl": "ws://localhost/devtools/browser/browser-id"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            request["url"] = url
            request["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(
        "backend.proxy.adapters.cdp.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    async def fake_connect(url: str, **kwargs):
        request["websocket_url"] = url
        request["websocket_kwargs"] = kwargs
        return FakeWebSocket()

    monkeypatch.setattr("backend.proxy.adapters.cdp.connect", fake_connect)

    session = await DiscoveredCdpAdapter(
        ProviderName.CHROMIUM,
        "ws://chromium:9222",
    ).acquire(None, None)  # type: ignore[arg-type]

    assert request["url"] == "http://chromium:9222/json/version"
    assert request["headers"] == {"Host": "localhost"}
    assert request["websocket_url"] == "ws://localhost/devtools/browser/browser-id"
    assert request["websocket_kwargs"] == {
        "max_size": None,
        "proxy": None,
        "host": "chromium",
        "port": 9222,
    }
    assert session.provider is ProviderName.CHROMIUM


def test_camoufox_uses_mapping_adapter() -> None:
    adapter = get_provider_adapter(
        ProviderName.CAMOUFOX,
        endpoint="ws://harbor-camoufox-2:1234/harbor",
    )

    assert adapter.provider is ProviderName.CAMOUFOX
    assert adapter.endpoint == "ws://harbor-camoufox-2:1234/harbor"


@pytest.mark.asyncio
async def test_explicit_http_is_a_normal_provider_and_never_transitions() -> None:
    adapter = get_provider_adapter(ProviderName.HTTP)
    assert isinstance(adapter, HttpAdapter)
    session = await adapter.acquire(
        HarborSession("session", "owner", "lease", SessionState.OPEN),
        ResolvedSessionSettings(
            provider=ProviderSettingSchema(slug=ProviderName.HTTP),
            session=SessionSettingSchema(),
            sources={},
        ),
    )

    await session.send(json.dumps({"id": 1, "method": "Page.printToPDF"}))

    assert session.provider is ProviderName.HTTP
    assert json.loads(await anext(session.messages())) == {
        "id": 1,
        "error": {
            "code": -32601,
            "message": "Page.printToPDF is not supported by provider http",
        },
    }


def test_lightpanda_uses_assigned_managed_instance_endpoint() -> None:
    adapter = get_provider_adapter(
        ProviderName.LIGHTPANDA,
        endpoint="ws://harbor-lightpanda-2:9222",
    )

    assert isinstance(adapter, LightpandaAdapter)
    assert adapter.endpoint == "ws://harbor-lightpanda-2:9222"


@pytest.mark.asyncio
async def test_lightpanda_maps_enabling_scripts_to_supported_no_op() -> None:
    websocket = FakeWebSocket()
    session = LightpandaProviderSession(  # type: ignore[arg-type]
        ProviderName.LIGHTPANDA,
        websocket,
    )

    await session.send(
        json.dumps(
            {
                "id": 20,
                "method": "Emulation.setScriptExecutionDisabled",
                "params": {"value": False},
                "sessionId": "page-session",
            }
        )
    )

    assert websocket.sent == []
    assert json.loads(await anext(session.messages())) == {
        "id": 20,
        "result": {},
        "sessionId": "page-session",
    }


@pytest.mark.asyncio
async def test_lightpanda_returns_explicit_error_when_disabling_scripts() -> None:
    websocket = FakeWebSocket()
    session = LightpandaProviderSession(  # type: ignore[arg-type]
        ProviderName.LIGHTPANDA,
        websocket,
    )
    command = {
        "id": 21,
        "method": "Emulation.setScriptExecutionDisabled",
        "params": {"value": True},
    }

    await session.send(json.dumps(command))

    assert websocket.sent == []
    assert json.loads(await anext(session.messages())) == {
        "id": 21,
        "error": {
            "code": -32601,
            "message": "Lightpanda cannot disable script execution",
        },
    }


def test_browserless_uses_assigned_managed_instance_endpoint() -> None:
    adapter = get_provider_adapter(
        ProviderName.BROWSERLESS,
        endpoint="ws://harbor-browserless-2:3000",
    )

    assert isinstance(adapter, DirectCdpAdapter)
    assert adapter.endpoint == "ws://harbor-browserless-2:3000"


@pytest.mark.asyncio
async def test_cdp_session_disposes_only_contexts_created_through_that_connection() -> None:
    websocket = FakeWebSocket(
        [json.dumps({"id": 7, "result": {"browserContextId": "owned-context"}})]
    )
    session = WebSocketProviderSession(ProviderName.CHROMIUM, websocket)  # type: ignore[arg-type]
    await session.send(json.dumps({"id": 7, "method": "Target.createBrowserContext"}))
    assert [message async for message in session.messages()]

    await session.close()

    cleanup = json.loads(websocket.sent[-1])
    assert cleanup["method"] == "Target.disposeBrowserContext"
    assert cleanup["params"] == {"browserContextId": "owned-context"}
