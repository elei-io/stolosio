import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass

from websockets.asyncio.client import connect

from backend.proxy.adapters.cdp import WebSocketProviderSession
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ResolvedSessionSettings,
)


class LightpandaProviderSession(WebSocketProviderSession):
    def __init__(self, provider: ProviderName, websocket) -> None:
        super().__init__(provider, websocket)
        self._mapped_responses: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        command = json.loads(message)
        params = command.get("params")
        if command.get("method") == "Emulation.setScriptExecutionDisabled":
            value = params.get("value") if isinstance(params, dict) else None
            if value is False:
                response = {"id": command["id"], "result": {}}
            elif value is True:
                response = {
                    "id": command["id"],
                    "error": {
                        "code": -32601,
                        "message": "Lightpanda cannot disable script execution",
                    },
                }
            else:
                response = {
                    "id": command["id"],
                    "error": {
                        "code": -32602,
                        "message": "value must be a boolean",
                    },
                }
            if isinstance(command.get("sessionId"), str):
                response["sessionId"] = command["sessionId"]
            await self._mapped_responses.put(json.dumps(response, separators=(",", ":")))
            return
        await super().send(message)

    async def messages(self) -> AsyncIterator[str]:
        upstream = self._websocket.__aiter__()
        upstream_task = asyncio.create_task(anext(upstream))
        mapped_task = asyncio.create_task(self._mapped_responses.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {upstream_task, mapped_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if mapped_task in done:
                    yield mapped_task.result()
                    mapped_task = asyncio.create_task(self._mapped_responses.get())
                if upstream_task in done:
                    try:
                        yield upstream_task.result()
                    except StopAsyncIteration:
                        return
                    upstream_task = asyncio.create_task(anext(upstream))
        finally:
            upstream_task.cancel()
            mapped_task.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await upstream_task
            with suppress(asyncio.CancelledError):
                await mapped_task


@dataclass(frozen=True, slots=True)
class LightpandaAdapter:
    provider = ProviderName.LIGHTPANDA
    endpoint: str

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ) -> LightpandaProviderSession:
        websocket = await connect(self.endpoint, max_size=None, proxy=None)
        return LightpandaProviderSession(self.provider, websocket)
