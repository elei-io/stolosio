from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import ProviderName
from backend.proxy.errors import InvalidCdpMessage, ProviderConnectionLost
from backend.proxy.transport.cdp import _downstream_to_upstream, _upstream_to_downstream


class FakeDownstream:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages = iter(messages or [])
        self.sent_text: list[str] = []
        self.sent_json: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        return next(self.messages)

    async def send_text(self, message: str) -> None:
        self.sent_text.append(message)

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent_json.append(message)


class FakeUpstream:
    provider = ProviderName.CHROMIUM

    def __init__(self, messages: list[str] | None = None) -> None:
        self._messages = messages or []
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def messages(self) -> AsyncIterator[str]:
        for message in self._messages:
            yield message

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_forwards_supported_downstream_messages_to_provider() -> None:
    downstream = FakeDownstream(
        [
            {"type": "websocket.receive", "text": '{"id":1,"method":"Page.navigate"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    upstream = FakeUpstream()
    registry = CapabilityRegistry({ProviderName.CHROMIUM: frozenset({"Page.navigate"})})

    with pytest.raises(WebSocketDisconnect):
        await _downstream_to_upstream(  # type: ignore[arg-type]
            downstream,
            upstream,
            ProviderName.CHROMIUM,
            registry,
        )

    assert upstream.sent == ['{"id":1,"method":"Page.navigate"}']


@pytest.mark.asyncio
async def test_returns_not_supported_without_forwarding_or_disconnecting() -> None:
    downstream = FakeDownstream(
        [
            {"type": "websocket.receive", "text": '{"id":7,"method":"Page.printToPDF"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    upstream = FakeUpstream()
    registry = CapabilityRegistry({ProviderName.CHROMIUM: frozenset()})

    with pytest.raises(WebSocketDisconnect):
        await _downstream_to_upstream(  # type: ignore[arg-type]
            downstream,
            upstream,
            ProviderName.CHROMIUM,
            registry,
        )

    assert upstream.sent == []
    assert downstream.sent_json == [
        {
            "id": 7,
            "error": {
                "code": -32601,
                "message": "Page.printToPDF is not supported by provider chromium",
            },
        }
    ]


@pytest.mark.asyncio
async def test_explicit_http_uses_the_same_capability_boundary() -> None:
    downstream = FakeDownstream(
        [
            {"type": "websocket.receive", "text": '{"id":8,"method":"Page.printToPDF"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    upstream = FakeUpstream()
    registry = CapabilityRegistry({ProviderName.HTTP: frozenset()})

    with pytest.raises(WebSocketDisconnect):
        await _downstream_to_upstream(  # type: ignore[arg-type]
            downstream,
            upstream,
            ProviderName.HTTP,
            registry,
        )

    assert upstream.sent == []
    assert downstream.sent_json[0]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_rejects_malformed_and_binary_downstream_messages() -> None:
    registry = CapabilityRegistry({ProviderName.CHROMIUM: frozenset()})
    for message in (
        {"type": "websocket.receive", "text": "not-json"},
        {"type": "websocket.receive", "bytes": b"binary"},
    ):
        with pytest.raises(InvalidCdpMessage):
            await _downstream_to_upstream(  # type: ignore[arg-type]
                FakeDownstream([message]),
                FakeUpstream(),
                ProviderName.CHROMIUM,
                registry,
            )


@pytest.mark.asyncio
async def test_forwards_provider_messages_to_downstream() -> None:
    upstream = FakeUpstream(['{"id":1,"result":{}}'])
    downstream = FakeDownstream()

    with pytest.raises(ProviderConnectionLost):
        await _upstream_to_downstream(upstream, downstream)  # type: ignore[arg-type]

    assert downstream.sent_text == ['{"id":1,"result":{}}']
