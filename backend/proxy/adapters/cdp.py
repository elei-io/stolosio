import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
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
        self._owned_contexts: set[str] = set()
        self._pending_context_commands: dict[int, tuple[str, str | None]] = {}
        self._cleanup_command_id = -1

    async def send(self, message: str) -> None:
        self._track_command(message)
        await self._websocket.send(message)

    async def messages(self) -> AsyncIterator[str]:
        async for message in self._websocket:
            if not isinstance(message, str):
                raise RuntimeError("CDP provider sent a binary WebSocket message")
            self._track_response(message)
            yield message

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for context_id in tuple(self._owned_contexts):
            with suppress(Exception):
                await self._dispose_context(context_id)
        await self._websocket.close()

    def _track_command(self, message: str) -> None:
        try:
            command = json.loads(message)
            if not isinstance(command, dict):
                return
            command_id = command["id"]
            method = command["method"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return
        if not isinstance(command_id, int):
            return
        if method == "Target.createBrowserContext":
            self._pending_context_commands[command_id] = ("create", None)
        elif method == "Target.disposeBrowserContext":
            context_id = command.get("params", {}).get("browserContextId")
            if isinstance(context_id, str):
                self._pending_context_commands[command_id] = ("dispose", context_id)

    def _track_response(self, message: str) -> None:
        try:
            response = json.loads(message)
            if not isinstance(response, dict):
                return
            command_id = response.get("id")
        except (json.JSONDecodeError, TypeError):
            return
        pending = self._pending_context_commands.pop(command_id, None)
        if pending is None or "error" in response:
            return
        action, context_id = pending
        if action == "create":
            created = response.get("result", {}).get("browserContextId")
            if isinstance(created, str):
                self._owned_contexts.add(created)
        elif context_id is not None:
            self._owned_contexts.discard(context_id)

    async def _dispose_context(self, context_id: str) -> None:
        command_id = self._cleanup_command_id
        self._cleanup_command_id -= 1
        await self._websocket.send(
            json.dumps(
                {
                    "id": command_id,
                    "method": "Target.disposeBrowserContext",
                    "params": {"browserContextId": context_id},
                }
            )
        )
        async with asyncio.timeout(1):
            while True:
                raw = await self._websocket.recv()
                if not isinstance(raw, str):
                    continue
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(response, dict):
                    continue
                if response.get("id") == command_id:
                    break
        self._owned_contexts.discard(context_id)


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
