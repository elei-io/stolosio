import json
from collections.abc import AsyncIterator

import pytest

from backend.proxy.contracts import ProviderName
from backend.proxy.errors import DomainBlockingUnavailable
from backend.proxy.transport.domain_blocking import DomainBlockingProviderSession


class FakeProviderSession:
    provider = ProviderName.BROWSERLESS
    disconnect_reason = None

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def messages(self) -> AsyncIterator[str]:
        for message in self._messages:
            yield json.dumps(message)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_blocklist_is_injected_before_an_attached_page_is_exposed() -> None:
    attached = {
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": "target-session",
            "targetInfo": {"type": "page"},
            "waitingForDebugger": True,
        },
    }
    unrelated = {"method": "Target.targetInfoChanged", "params": {}}
    upstream = FakeProviderSession(
        [
            attached,
            unrelated,
            {"id": -1, "sessionId": "target-session", "result": {}},
            {"id": 7, "result": {"ok": True}},
        ]
    )
    session = DomainBlockingProviderSession(
        upstream,
        ("*.doubleclick.net", "ads.example"),
    )

    messages = [json.loads(message) async for message in session.messages()]

    assert messages == [attached, unrelated, {"id": 7, "result": {"ok": True}}]
    assert upstream.sent == [
        {
            "id": -1,
            "method": "Network.setBlockedURLs",
            "params": {
                "urls": [
                    "*://*.doubleclick.net/*",
                    "*://ads.example/*",
                ]
            },
            "sessionId": "target-session",
        }
    ]


@pytest.mark.asyncio
async def test_non_network_targets_are_not_modified() -> None:
    browser = {
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": "browser-session",
            "targetInfo": {"type": "browser"},
        },
    }
    upstream = FakeProviderSession([browser])
    session = DomainBlockingProviderSession(upstream, ("ads.example",))

    assert [json.loads(message) async for message in session.messages()] == [browser]
    assert upstream.sent == []


@pytest.mark.asyncio
async def test_provider_rejection_is_an_explicit_domain_blocking_error() -> None:
    upstream = FakeProviderSession(
        [
            {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": "target-session",
                    "targetInfo": {"type": "page"},
                },
            },
            {
                "id": -1,
                "sessionId": "target-session",
                "error": {"code": -32601, "message": "not supported"},
            },
        ]
    )
    session = DomainBlockingProviderSession(upstream, ("ads.example",))

    with pytest.raises(DomainBlockingUnavailable):
        await anext(session.messages())
