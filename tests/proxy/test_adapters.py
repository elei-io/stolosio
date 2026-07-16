import pytest

from backend.proxy.adapters.cdp import (
    DirectCdpAdapter,
    DiscoveredCdpAdapter,
    _discovery_url,
)
from backend.proxy.adapters.registry import get_provider_adapter
from backend.proxy.contracts import ProviderName


class FakeWebSocket:
    async def send(self, message: str) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

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
    adapter = get_provider_adapter(ProviderName.CAMOUFOX)

    assert adapter.provider is ProviderName.CAMOUFOX
