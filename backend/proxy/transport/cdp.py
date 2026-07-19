import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from backend.events.cdp import CdpEventObserver
from backend.proxy.contracts import ProviderSession
from backend.proxy.errors import (
    InvalidCdpMessage,
    ProviderConnectionLost,
    ProviderTimeout,
)


def _provider_disconnect_error(upstream: ProviderSession) -> Exception:
    if getattr(upstream, "disconnect_reason", None) == ProviderTimeout.reason:
        return ProviderTimeout()
    return ProviderConnectionLost()


async def relay_cdp(
    downstream: WebSocket,
    upstream: ProviderSession,
    observer: CdpEventObserver | None = None,
) -> None:
    downstream_task = asyncio.create_task(
        _downstream_to_upstream(downstream, upstream, observer)
    )
    upstream_task = asyncio.create_task(_upstream_to_downstream(upstream, downstream, observer))
    tasks = {downstream_task, upstream_task}

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    try:
        for task in done:
            error = task.exception()
            if isinstance(error, ConnectionClosed):
                raise _provider_disconnect_error(upstream)
            if error is not None and not isinstance(error, WebSocketDisconnect):
                raise error
    finally:
        if observer is not None:
            await observer.interrupt_pending("session_ended")


async def _downstream_to_upstream(
    downstream: WebSocket,
    upstream: ProviderSession,
    observer: CdpEventObserver | None = None,
) -> None:
    while True:
        message = await downstream.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        text = message.get("text")
        if text is None:
            raise InvalidCdpMessage

        try:
            command = json.loads(text)
            command_id = command["id"]
            method = command["method"]
            if not isinstance(command_id, int) or not isinstance(method, str):
                raise TypeError
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise InvalidCdpMessage from error

        if observer is not None:
            await observer.command_received(command)
        if observer is not None:
            observer.command_forwarded(command)
        await upstream.send(text)


async def _upstream_to_downstream(
    upstream: ProviderSession,
    downstream: WebSocket,
    observer: CdpEventObserver | None = None,
) -> None:
    async for message in upstream.messages():
        if observer is not None:
            await observer.upstream_message(message)
        await downstream.send_text(message)
    if observer is not None:
        await observer.provider_disconnected()
    raise _provider_disconnect_error(upstream)
