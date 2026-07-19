from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from backend.proxy.contracts import ProviderName
from backend.proxy.errors import (
    DomainBlockingUnavailable,
    InvalidCdpMessage,
    ProviderConnectionLost,
    ProviderTimeout,
)
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
    provider = ProviderName.BROWSERLESS

    def __init__(self, messages: list[str] | None = None) -> None:
        self._messages = messages or []
        self.sent: list[str] = []
        self.disconnect_reason: str | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def messages(self) -> AsyncIterator[str]:
        for message in self._messages:
            yield message

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_forwards_arbitrary_downstream_messages_to_provider() -> None:
    downstream = FakeDownstream(
        [
            {
                "type": "websocket.receive",
                "text": '{"id":1,"method":"Future.unknown","params":{"new":true}}',
            },
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    upstream = FakeUpstream()
    with pytest.raises(WebSocketDisconnect):
        await _downstream_to_upstream(  # type: ignore[arg-type]
            downstream,
            upstream,
        )

    assert upstream.sent == [
        '{"id":1,"method":"Future.unknown","params":{"new":true}}'
    ]


@pytest.mark.asyncio
async def test_rejects_malformed_and_binary_downstream_messages() -> None:
    for message in (
        {"type": "websocket.receive", "text": "not-json"},
        {"type": "websocket.receive", "bytes": b"binary"},
    ):
        with pytest.raises(InvalidCdpMessage):
            await _downstream_to_upstream(  # type: ignore[arg-type]
                FakeDownstream([message]),
                FakeUpstream(),
            )


@pytest.mark.asyncio
async def test_forwards_provider_messages_to_downstream() -> None:
    upstream = FakeUpstream(['{"id":1,"result":{}}'])
    downstream = FakeDownstream()

    with pytest.raises(ProviderConnectionLost):
        await _upstream_to_downstream(upstream, downstream)  # type: ignore[arg-type]

    assert downstream.sent_text == ['{"id":1,"result":{}}']


@pytest.mark.asyncio
async def test_classifies_provider_session_deadline_as_timeout() -> None:
    upstream = FakeUpstream()
    upstream.disconnect_reason = "provider_timeout"

    with pytest.raises(ProviderTimeout):
        await _upstream_to_downstream(  # type: ignore[arg-type]
            upstream,
            FakeDownstream(),
        )


@pytest.mark.asyncio
async def test_preserves_domain_blocking_failure_reason() -> None:
    upstream = FakeUpstream()
    upstream.disconnect_reason = "domain_blocking_unavailable"

    with pytest.raises(DomainBlockingUnavailable):
        await _upstream_to_downstream(  # type: ignore[arg-type]
            upstream,
            FakeDownstream(),
        )
