import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from backend.proxy.contracts import ProviderName
from backend.proxy.errors import DomainBlockingUnavailable
from backend.proxy.transport import domain_blocking
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
async def test_blocklist_is_injected_without_holding_unrelated_messages() -> None:
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

    assert messages == [unrelated, attached, {"id": 7, "result": {"ok": True}}]
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

    assert session.disconnect_reason == "domain_blocking_unavailable"


@pytest.mark.asyncio
async def test_slow_worker_policy_does_not_block_an_existing_page_response() -> None:
    class CoordinatedProviderSession(FakeProviderSession):
        def __init__(self) -> None:
            super().__init__([])
            self.queue: asyncio.Queue[dict] = asyncio.Queue()
            self.policy_sent = asyncio.Event()

        async def send(self, message: str) -> None:
            command = json.loads(message)
            self.sent.append(command)
            if command.get("method") == "Network.setBlockedURLs":
                self.policy_sent.set()
            elif command.get("method") == "Runtime.evaluate":
                await self.queue.put(
                    {
                        "id": command["id"],
                        "sessionId": command["sessionId"],
                        "result": {"result": {"type": "number", "value": 2}},
                    }
                )

        async def messages(self) -> AsyncIterator[str]:
            while True:
                yield json.dumps(await self.queue.get())

    upstream = CoordinatedProviderSession()
    session = DomainBlockingProviderSession(upstream, ("ads.example",))
    messages = session.messages().__aiter__()
    await upstream.queue.put(
        {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "slow-worker",
                "targetInfo": {
                    "targetId": "worker-target",
                    "type": "service_worker",
                },
            },
        }
    )

    next_message = asyncio.create_task(anext(messages))
    await upstream.policy_sent.wait()
    policy_command = upstream.sent[0]
    await session.send(
        json.dumps(
            {
                "id": 77,
                "method": "Runtime.evaluate",
                "sessionId": "existing-page",
                "params": {"expression": "1 + 1"},
            }
        )
    )

    assert json.loads(await asyncio.wait_for(next_message, timeout=0.1)) == {
        "id": 77,
        "sessionId": "existing-page",
        "result": {"result": {"type": "number", "value": 2}},
    }

    await upstream.queue.put(
        {
            "id": policy_command["id"],
            "sessionId": "slow-worker",
            "result": {},
        }
    )
    attached = json.loads(await asyncio.wait_for(anext(messages), timeout=0.1))

    assert attached["params"]["sessionId"] == "slow-worker"
    await session.close()


@pytest.mark.asyncio
async def test_new_targets_are_configured_concurrently() -> None:
    class CoordinatedProviderSession(FakeProviderSession):
        def __init__(self) -> None:
            super().__init__([])
            self.queue: asyncio.Queue[dict] = asyncio.Queue()
            self.two_policies_sent = asyncio.Event()

        async def send(self, message: str) -> None:
            command = json.loads(message)
            self.sent.append(command)
            policies = [
                sent
                for sent in self.sent
                if sent.get("method") == "Network.setBlockedURLs"
            ]
            if len(policies) == 2:
                self.two_policies_sent.set()

        async def messages(self) -> AsyncIterator[str]:
            while True:
                yield json.dumps(await self.queue.get())

    def attached(session_id: str) -> dict:
        return {
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": session_id,
                "targetInfo": {
                    "targetId": f"{session_id}-target",
                    "type": "worker",
                },
            },
        }

    upstream = CoordinatedProviderSession()
    session = DomainBlockingProviderSession(upstream, ("ads.example",))
    messages = session.messages().__aiter__()
    first_message = asyncio.create_task(anext(messages))
    await upstream.queue.put(attached("worker-one"))
    await upstream.queue.put(attached("worker-two"))

    await asyncio.wait_for(upstream.two_policies_sent.wait(), timeout=0.1)
    policies = {
        command["sessionId"]: command
        for command in upstream.sent
        if command.get("method") == "Network.setBlockedURLs"
    }
    await upstream.queue.put(
        {
            "id": policies["worker-two"]["id"],
            "sessionId": "worker-two",
            "result": {},
        }
    )

    second_attachment = json.loads(
        await asyncio.wait_for(first_message, timeout=0.1)
    )
    assert second_attachment["params"]["sessionId"] == "worker-two"

    await upstream.queue.put(
        {
            "id": policies["worker-one"]["id"],
            "sessionId": "worker-one",
            "result": {},
        }
    )
    first_attachment = json.loads(
        await asyncio.wait_for(anext(messages), timeout=0.1)
    )
    assert first_attachment["params"]["sessionId"] == "worker-one"
    await session.close()


@pytest.mark.asyncio
async def test_policy_bootstrap_timeout_is_an_explicit_domain_blocking_error(
    monkeypatch,
) -> None:
    class StalledProviderSession(FakeProviderSession):
        async def messages(self) -> AsyncIterator[str]:
            yield json.dumps(
                {
                    "method": "Target.attachedToTarget",
                    "params": {
                        "sessionId": "target-session",
                        "targetInfo": {"type": "page"},
                    },
                }
            )
            await asyncio.Future()

    monkeypatch.setattr(
        domain_blocking,
        "_BOOTSTRAP_TIMEOUT_SECONDS",
        0.01,
    )
    session = DomainBlockingProviderSession(
        StalledProviderSession([]),
        ("ads.example",),
    )

    with pytest.raises(DomainBlockingUnavailable):
        await anext(session.messages())

    assert session.disconnect_reason == "domain_blocking_unavailable"
