from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect

from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ResolvedSessionSettings,
)


class WebSocketProviderSession:
    def __init__(self, provider: ProviderName, websocket: ClientConnection) -> None:
        self.provider = provider
        self._websocket = websocket
        self._closed = False

    async def send(self, message: str) -> None:
        await self._websocket.send(message)

    async def messages(self) -> AsyncIterator[str]:
        async for message in self._websocket:
            if not isinstance(message, str):
                raise RuntimeError("CDP provider sent a binary WebSocket message")
            yield message

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._websocket.close()


@dataclass(frozen=True, slots=True)
class DirectCdpAdapter:
    provider: ProviderName
    endpoint: str

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ) -> WebSocketProviderSession:
        websocket = await connect(self.endpoint, max_size=None, proxy=None)
        return WebSocketProviderSession(self.provider, websocket)


@dataclass(frozen=True, slots=True)
class DiscoveredCdpAdapter:
    provider: ProviderName
    endpoint: str

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ) -> WebSocketProviderSession:
        discovery_url = _discovery_url(self.endpoint)
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(discovery_url, headers={"Host": "localhost"})
            response.raise_for_status()
            payload = response.json()

        upstream_url = payload.get("webSocketDebuggerUrl")
        if not isinstance(upstream_url, str):
            raise RuntimeError(f"{self.provider} did not advertise a browser WebSocket")

        websocket = await connect(
            upstream_url,
            max_size=None,
            proxy=None,
            host=urlsplit(self.endpoint).hostname,
            port=urlsplit(self.endpoint).port,
        )
        return WebSocketProviderSession(self.provider, websocket)


def _discovery_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = f"{parsed.path.rstrip('/')}/json/version"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))
