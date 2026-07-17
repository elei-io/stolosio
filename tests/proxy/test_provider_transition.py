"""Explicit HTTP execution and automatic provider-transition tests."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderTransitionStat,
    GatewaySession,
)
from backend.events.cdp import CdpEventObserver
from backend.events.publisher import NullEventPublisher
from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SessionState,
    SettingSource,
)
from backend.proxy.provider_transition.history import ProviderTransitionRepository
from backend.proxy.provider_transition.materialization import (
    CdpReplayMaterializationStrategy,
)
from backend.proxy.provider_transition.session import (
    ProviderTransitionSession,
    ReplayEntry,
    _http_request_headers,
)
from backend.proxy.routing import NoSupportedProvider, ProviderCandidate, ProviderPlan
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


def transition_session(upstream: FakeProviderSession) -> ProviderTransitionSession:
    session_id = uuid4()
    settings = Settings()
    resolved = ResolvedSessionSettings(
        provider=ProviderSettingSchema(slug=ProviderName.HTTP),
        session=SessionSettingSchema(),
        sources={"harbor.provider.slug": SettingSource.EXPLICIT},
    )
    publisher = NullEventPublisher()
    facade = ProviderTransitionSession(
        HarborSession(str(session_id), "test", "lease", SessionState.OPEN),
        resolved,
        None,  # type: ignore[arg-type]
        CapabilityRegistry({ProviderName.CHROMIUM: frozenset()}),
        None,  # type: ignore[arg-type]
        CdpEventObserver(session_id, None, ProviderName.HTTP, publisher),
        settings,
        automatic=True,
    )
    facade._upstream = upstream
    facade._upstream_messages = upstream.messages().__aiter__()
    return facade


@pytest.mark.asyncio
async def test_disabling_javascript_remains_lazy_and_is_recorded_for_replay() -> None:
    facade = transition_session(FakeProviderSession([]))
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


def test_materialization_strategy_rejects_side_effecting_commands() -> None:
    strategy = CdpReplayMaterializationStrategy()

    assert strategy.can_materialize(["Page.navigate"]) is True
    assert strategy.can_materialize(["Input.dispatchMouseEvent"]) is False


@pytest.mark.asyncio
async def test_exhausted_support_plan_returns_explicit_protocol_error() -> None:
    class EmptyPlan:
        async def plan(self, *args, **kwargs):
            raise NoSupportedProvider("No supported provider remains")

    facade = transition_session(FakeProviderSession([]))
    facade._routing = EmptyPlan()  # type: ignore[assignment]
    facade._domain = "example.test"
    command = {"id": 30, "method": "Page.printToPDF", "params": {}}

    await facade.send(json.dumps(command))
    response = json.loads(await anext(facade.messages()))

    assert response == {
        "id": 30,
        "error": {"code": -32000, "message": "No supported provider remains"},
    }


@pytest.mark.asyncio
async def test_transition_replays_every_acknowledged_command_in_order_with_id_mapping() -> None:
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
    facade = transition_session(upstream)
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
async def test_transition_tries_the_full_plan_before_releasing_the_source(
    monkeypatch,
) -> None:
    timeline: list[str] = []

    class Lease:
        def __init__(self, provider: ProviderName) -> None:
            self.attempt = type(
                "Attempt",
                (),
                {
                    "provider": provider,
                    "attempt_id": str(uuid4()),
                    "endpoint": None,
                },
            )()

        async def activate(self) -> None:
            timeline.append(f"activate:{self.attempt.provider.value}")

        async def release(self, *, failed: bool, reason: str) -> None:
            timeline.append(f"release:{self.attempt.provider.value}:{reason}")

    source = Lease(ProviderName.HTTP)

    class Attempts:
        async def acquire(self, session, resolved):
            assert resolved.provider.slug is not None
            provider = resolved.provider.slug
            timeline.append(f"acquire:{provider.value}")
            return Lease(provider)

    class Adapter:
        def __init__(self, provider: ProviderName) -> None:
            self.provider = provider

        async def acquire(self, session, resolved):
            timeline.append(f"connect:{self.provider.value}")
            if self.provider is ProviderName.LIGHTPANDA:
                raise ConnectionError("first candidate unavailable")
            return FakeProviderSession([])

    monkeypatch.setattr(
        "backend.proxy.provider_transition.session.get_provider_adapter",
        lambda provider, endpoint=None: Adapter(provider),
    )

    class History:
        async def record_transition(self, *args, **kwargs) -> None:
            timeline.append("record:transition")

    session_id = uuid4()
    facade = ProviderTransitionSession(
        HarborSession(str(session_id), "test", "lease", SessionState.OPEN),
        ResolvedSessionSettings(
            provider=ProviderSettingSchema(),
            session=SessionSettingSchema(),
            sources={"harbor.provider.slug": SettingSource.AUTO},
        ),
        Attempts(),  # type: ignore[arg-type]
        CapabilityRegistry(
            {
                ProviderName.LIGHTPANDA: frozenset({"Page.printToPDF"}),
                ProviderName.CHROMIUM: frozenset({"Page.printToPDF"}),
            }
        ),
        History(),  # type: ignore[arg-type]
        CdpEventObserver(
            session_id,
            None,
            None,
            NullEventPublisher(),
        ),
        Settings(),
        automatic=True,
    )
    facade._attempt = source  # type: ignore[assignment]
    facade._attempted_providers.add(ProviderName.HTTP)
    plan = ProviderPlan(
        (
            ProviderCandidate(ProviderName.LIGHTPANDA, 10, 0),
            ProviderCandidate(ProviderName.CHROMIUM, 100, 1),
        ),
        "cheapest_supported",
        1,
        1,
    )

    await facade._transition(
        {"id": 7, "method": "Page.printToPDF"},
        "Page.printToPDF",
        plan=plan,
    )

    assert timeline[:6] == [
        "acquire:lightpanda",
        "connect:lightpanda",
        "release:lightpanda:provider_transition_attempt_failed",
        "acquire:chromium",
        "connect:chromium",
        "activate:chromium",
    ]
    assert timeline.index("activate:chromium") < timeline.index(
        "release:http:provider_transitioned"
    )


@pytest.mark.asyncio
async def test_transition_history_is_factual_and_durable(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    history = ProviderTransitionRepository(database_sessions)
    session_id = str(uuid4())
    attempt_id = str(uuid4())
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="example.test",
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        database.add(
            GatewaySession(
                id=session_id,
                owner_id="owner",
                lease_token="lease",
                requested_settings={},
                state="open",
                created_at=now,
            )
        )
        await database.flush()
        database.add(
            AcquisitionAttempt(
                id=attempt_id,
                session_id=session_id,
                ordinal=1,
                provider="chromium",
                resolved_settings={},
                setting_sources={},
                state="active",
                domain_id=domain.id,
            )
        )

    await history.record_transition(
        session_id,
        attempt_id,
        from_provider="http",
        to_provider=ProviderName.CHROMIUM,
        trigger_method="Runtime.evaluate",
    )

    async with database_sessions() as database:
        row = await database.get(
            DomainProviderTransitionStat,
            (
                domain.id,
                "http",
                "chromium",
                "new_requirement",
            ),
        )
    assert row is not None
    assert row.transition_count == 1
