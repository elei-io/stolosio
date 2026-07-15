import pytest

from backend.proxy.adapters.cdp import (
    DirectCdpAdapter,
    DiscoveredCdpAdapter,
    _discovery_url,
)
from backend.proxy.adapters.registry import get_provider_adapter
from backend.proxy.contracts import ProviderName


@pytest.mark.asyncio
async def test_direct_adapter_returns_configured_endpoint() -> None:
    adapter = DirectCdpAdapter(ProviderName.LIGHTPANDA, "ws://lightpanda:9222")

    connection = await adapter.connect()

    assert connection.provider is ProviderName.LIGHTPANDA
    assert connection.websocket_url == "ws://lightpanda:9222"


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

    connection = await DiscoveredCdpAdapter(
        ProviderName.CHROMIUM,
        "ws://chromium:9222",
    ).connect()

    assert request == {
        "url": "http://chromium:9222/json/version",
        "headers": {"Host": "localhost"},
    }
    assert connection.websocket_url == "ws://localhost/devtools/browser/browser-id"
    assert connection.transport_host == "chromium"
    assert connection.transport_port == 9222


def test_camoufox_requires_protocol_mapping() -> None:
    with pytest.raises(NotImplementedError, match="CDP protocol mapping"):
        get_provider_adapter(ProviderName.CAMOUFOX)
