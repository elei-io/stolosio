import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.events.cdp import CdpEventObserver
from backend.events.publisher import NullEventPublisher
from backend.proxy.capabilities import CapabilityRegistry, capability_registry
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SessionState,
    SettingSource,
)
from backend.proxy.no_browser.history import PromotionHistoryRepository
from backend.proxy.no_browser.session import (
    AdaptiveCdpSession,
    ReplayEntry,
    _http_request_headers,
)
from backend.settings import Settings


class FakeProviderSession:
    provider = ProviderName.CHROMIUM

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def messages(self) -> AsyncIterator[str]:
        for message in self._messages:
            yield json.dumps(message)

    async def close(self) -> None:
        return None


def test_http_request_headers_are_descriptive_and_truthful() -> None:
    headers = _http_request_headers(Settings())

    assert headers == {
        "User-Agent": "HarborBot/0.1 (https://github.com/ekkuleivonen/harbor)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    assert "Mozilla" not in headers["User-Agent"]


def adaptive_session(upstream: FakeProviderSession) -> AdaptiveCdpSession:
    session_id = uuid4()
    settings = Settings()
    resolved = ResolvedSessionSettings(
        provider=ProviderSettingSchema(slug=ProviderName.HTTP),
        session=SessionSettingSchema(),
        sources={"harbor.provider.slug": SettingSource.EXPLICIT},
    )
    publisher = NullEventPublisher()
    facade = AdaptiveCdpSession(
        HarborSession(str(session_id), "test", "lease", SessionState.OPEN),
        resolved,
        None,  # type: ignore[arg-type]
        CapabilityRegistry({ProviderName.CHROMIUM: frozenset()}),
        None,  # type: ignore[arg-type]
        CdpEventObserver(session_id, None, ProviderName.HTTP, publisher),
        settings,
        force_http=True,
    )
    facade._upstream = upstream
    facade._upstream_messages = upstream.messages().__aiter__()
    return facade


@pytest.mark.asyncio
async def test_disabling_javascript_remains_lazy_and_is_recorded_for_replay() -> None:
    facade = adaptive_session(FakeProviderSession([]))
    facade._upstream = None
    facade._upstream_messages = None
    command = {
        "id": 20,
        "method": "Emulation.setScriptExecutionDisabled",
        "params": {"value": True},
    }

    await facade.send(json.dumps(command))
    response = json.loads(await anext(facade.messages()))

    assert response == {"id": 20, "result": {}}
    assert facade._attempt is None
    assert facade._upstream is None
    assert [entry.command for entry in facade._replay] == [command]


@pytest.mark.asyncio
async def test_promotion_skips_provider_that_cannot_replay_disabled_javascript() -> None:
    facade = adaptive_session(FakeProviderSession([]))
    facade._capabilities = capability_registry
    facade._routing = SimpleNamespace(
        provider_profiles=lambda: _profiles(ProviderName.CHROMIUM)
    )
    facade._replay = [
        ReplayEntry(
            command={
                "id": 20,
                "method": "Emulation.setScriptExecutionDisabled",
                "params": {"value": True},
            },
            response={"id": 20, "result": {}},
        )
    ]

    target = await facade._compatible_promotion_target(
        ProviderName.LIGHTPANDA,
        {"id": 21, "method": "Runtime.evaluate", "params": {}},
    )

    assert target is ProviderName.CHROMIUM


async def _profiles(provider: ProviderName) -> list[SimpleNamespace]:
    return [SimpleNamespace(provider=provider.value, automatic_enabled=True)]


@pytest.mark.asyncio
async def test_promotion_replays_every_acknowledged_command_in_order_with_id_mapping() -> None:
    upstream = FakeProviderSession(
        [
            {"id": 1, "result": {"browserContextId": "actual-context"}},
            {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": "actual-session",
                    "targetInfo": {"targetId": "actual-target"},
                    "waitingForDebugger": True,
                },
            },
            {"id": 2, "result": {"targetId": "actual-target"}},
            {"id": 3, "result": {"frameId": "actual-frame"}},
            {"method": "Page.loadEventFired", "params": {"timestamp": 1.0}},
        ]
    )
    facade = adaptive_session(upstream)
    facade._replay = [
        ReplayEntry(
            command={"id": 1, "method": "Target.createBrowserContext"},
            response={"id": 1, "result": {"browserContextId": "synthetic-context"}},
        ),
        ReplayEntry(
            command={
                "id": 2,
                "method": "Target.createTarget",
                "params": {
                    "url": "about:blank",
                    "browserContextId": "synthetic-context",
                },
            },
            response={"id": 2, "result": {"targetId": "synthetic-target"}},
            events=[
                {
                    "method": "Target.attachedToTarget",
                    "params": {
                        "sessionId": "synthetic-session",
                        "targetInfo": {"targetId": "synthetic-target"},
                        "waitingForDebugger": True,
                    },
                }
            ],
        ),
        ReplayEntry(
            command={
                "id": 3,
                "method": "Page.navigate",
                "sessionId": "synthetic-session",
                "params": {"url": "https://example.com"},
            },
            response={"id": 3, "result": {"frameId": "synthetic-frame"}},
        ),
    ]

    await facade._replay_history()

    assert [command["method"] for command in upstream.sent] == [
        "Target.createBrowserContext",
        "Target.createTarget",
        "Page.navigate",
    ]
    assert upstream.sent[1]["params"]["browserContextId"] == "actual-context"
    assert upstream.sent[2]["sessionId"] == "actual-session"


@pytest.mark.asyncio
async def test_promotion_history_is_durable_per_domain(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    history = PromotionHistoryRepository(database_sessions)

    assert not await history.requires_browser("example.test")
    await history.record("example.test", "Runtime.evaluate")
    await history.record("example.test", "Input.dispatchMouseEvent")

    assert await history.requires_browser("example.test")
    assert not await history.requires_browser("other.test")
