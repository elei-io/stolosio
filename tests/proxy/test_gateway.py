from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from backend.proxy.gateway import _downstream_to_upstream, _upstream_to_downstream


class FakeDownstream:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages = iter(messages or [])
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []

    async def receive(self) -> dict[str, Any]:
        return next(self.messages)

    async def send_text(self, message: str) -> None:
        self.sent_text.append(message)

    async def send_bytes(self, message: bytes) -> None:
        self.sent_bytes.append(message)


class FakeUpstream:
    def __init__(self, messages: list[str | bytes] | None = None) -> None:
        self.messages = messages or []
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[str | bytes]:
        for message in self.messages:
            yield message


@pytest.mark.asyncio
async def test_forwards_downstream_messages_to_provider() -> None:
    downstream = FakeDownstream(
        [
            {"type": "websocket.receive", "text": '{"id":1}'},
            {"type": "websocket.receive", "bytes": b"binary"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    upstream = FakeUpstream()

    with pytest.raises(WebSocketDisconnect):
        await _downstream_to_upstream(downstream, upstream)  # type: ignore[arg-type]

    assert upstream.sent == ['{"id":1}', b"binary"]


@pytest.mark.asyncio
async def test_forwards_provider_messages_to_downstream() -> None:
    upstream = FakeUpstream(['{"id":1,"result":{}}', b"binary"])
    downstream = FakeDownstream()

    await _upstream_to_downstream(upstream, downstream)  # type: ignore[arg-type]

    assert downstream.sent_text == ['{"id":1,"result":{}}']
    assert downstream.sent_bytes == [b"binary"]
