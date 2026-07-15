import asyncio
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from backend.proxy.contracts import ProviderConnection


async def proxy_cdp(websocket: WebSocket, connection: ProviderConnection) -> None:
    transport: dict[str, str | int] = {}
    if connection.transport_host is not None:
        transport["host"] = connection.transport_host
    if connection.transport_port is not None:
        transport["port"] = connection.transport_port

    try:
        async with connect(
            connection.websocket_url,
            max_size=None,
            proxy=None,
            **transport,
        ) as upstream:
            await websocket.accept()
            await _relay(websocket, upstream)
    except NotImplementedError:
        raise
    except Exception:
        with suppress(RuntimeError):
            await websocket.close(code=1011, reason="Upstream browser connection failed")
        raise


async def _relay(downstream: WebSocket, upstream: ClientConnection) -> None:
    downstream_task = asyncio.create_task(_downstream_to_upstream(downstream, upstream))
    upstream_task = asyncio.create_task(_upstream_to_downstream(upstream, downstream))
    tasks = {downstream_task, upstream_task}

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        error = task.exception()
        if error is not None and not isinstance(error, (ConnectionClosed, WebSocketDisconnect)):
            raise error


async def _downstream_to_upstream(
    downstream: WebSocket,
    upstream: ClientConnection,
) -> None:
    while True:
        message = await downstream.receive()
        message_type = message["type"]

        if message_type == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if text := message.get("text"):
            await upstream.send(text)
        elif data := message.get("bytes"):
            await upstream.send(data)


async def _upstream_to_downstream(
    upstream: ClientConnection,
    downstream: WebSocket,
) -> None:
    async for message in upstream:
        if isinstance(message, str):
            await downstream.send_text(message)
        else:
            await downstream.send_bytes(message)
