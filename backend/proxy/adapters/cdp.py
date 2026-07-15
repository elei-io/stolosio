from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.proxy.contracts import ProviderConnection, ProviderName


@dataclass(frozen=True, slots=True)
class DirectCdpAdapter:
    provider: ProviderName
    endpoint: str

    async def connect(self) -> ProviderConnection:
        return ProviderConnection(provider=self.provider, websocket_url=self.endpoint)


@dataclass(frozen=True, slots=True)
class DiscoveredCdpAdapter:
    provider: ProviderName
    endpoint: str

    async def connect(self) -> ProviderConnection:
        discovery_url = _discovery_url(self.endpoint)
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(discovery_url, headers={"Host": "localhost"})
            response.raise_for_status()
            payload = response.json()

        upstream_url = payload.get("webSocketDebuggerUrl")
        if not isinstance(upstream_url, str):
            raise RuntimeError(f"{self.provider} did not advertise a browser WebSocket")

        return ProviderConnection(
            provider=self.provider,
            websocket_url=upstream_url,
            transport_host=urlsplit(self.endpoint).hostname,
            transport_port=urlsplit(self.endpoint).port,
        )


def _discovery_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = f"{parsed.path.rstrip('/')}/json/version"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))
