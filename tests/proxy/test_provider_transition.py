"""Explicit HTTP execution and automatic provider-transition tests."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
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
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SessionState,
    SettingSource,
)
from backend.proxy.errors import DomainBlockingUnavailable
from backend.proxy.provider_transition.history import ProviderTransitionRepository
from backend.proxy.provider_transition.session import (
    ProviderTransitionError,
    ProviderTransitionSession,
    ReplayEntry,
    _http_request_headers,
)
from backend.proxy.routing import NoSupportedProvider, ProviderCandidate, ProviderPlan
from backend.settings import Settings


class FakeProviderSession:
    provider = ProviderName.BROWSERLESS

    def __init__(
        self,
        messages: list[dict],
        provider: ProviderName = ProviderName.BROWSERLESS,
    ) -> None:
        self.provider = provider
        self._messages = messages
        self.sent: list[dict] = []
        self.provider_session_id = None
        self.provider_started_at = datetime.now(UTC)
        self.provider_ended_at = None

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
        None,  # type: ignore[arg-type]
        CdpEventObserver(session_id, None, ProviderName.HTTP, publisher),
        settings,
        automatic=True,
    )
    facade._upstream = upstream
    facade._upstream_messages = upstream.messages().__aiter__()
    return facade


@pytest.mark.asyncio
async def test_http_navigation_honors_the_global_domain_blocklist() -> None:
    facade = transition_session(FakeProviderSession([]))
    facade._upstream = None
    facade._upstream_messages = None
    facade._resolved = ResolvedSessionSettings(
        provider=facade._resolved.provider,
        session=facade._resolved.session,
        sources=facade._resolved.sources,
        blocked_domain_patterns=("*.doubleclick.net",),
        network_policy_version=2,
    )

    await facade.send(
        json.dumps(
            {
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": "https://ads.doubleclick.net/pixel"},
            }
        )
    )

    assert json.loads(await anext(facade.messages())) == {
        "id": 1,
        "error": {
            "code": -32000,
            "message": "Navigation blocked by Harbor network policy",
        },
    }


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


@pytest.mark.asyncio
async def test_exhausted_support_plan_returns_explicit_protocol_error() -> None:
    class EmptyPlan:
        async def plan(self, *args, **kwargs):
            raise NoSupportedProvider("No supported provider remains")

    facade = transition_session(FakeProviderSession([]))
    facade._upstream = None
    facade._upstream_messages = None
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
@pytest.mark.parametrize(
    ("status", "headers", "body", "trigger"),
    [
        (503, {"content-type": "text/html"}, "<main>down</main>", "unhealthy_http_status"),
        (
            200,
            {"content-type": "application/pdf"},
            "pdf",
            "non_html_content_type",
        ),
        (
            200,
            {"content-type": "text/html"},
            "<main>Access denied</main>",
            "error_page",
        ),
    ],
)
async def test_http_validation_failures_escalate_on_the_live_path(
    monkeypatch,
    status: int,
    headers: dict[str, str],
    body: str,
    trigger: str,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status_code = status
            self.content = body.encode()
            self.text = body
            self.url = "https://example.test/"
            self.reason_phrase = "test"
            self.headers = httpx.Headers(headers)

    class Client:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, *args, **kwargs):
            return Response()

    escalations: list[str] = []

    async def escalate(self, pending, reason, **kwargs) -> None:
        escalations.append(reason)

    monkeypatch.setattr(
        "backend.proxy.provider_transition.session.httpx.AsyncClient", Client
    )
    monkeypatch.setattr(ProviderTransitionSession, "_transition", escalate)
    facade = transition_session(FakeProviderSession([]))
    facade._upstream = None
    facade._upstream_messages = None
    facade._attempt = object()  # type: ignore[assignment]

    await facade.send(
        json.dumps(
            {
                "id": 31,
                "method": "Page.navigate",
                "params": {"url": "https://example.test/"},
            }
        )
    )

    assert escalations == [trigger]


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
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 7,
                        "auxData": {
                            "isDefault": True,
                            "frameId": "actual-frame",
                        },
                    }
                },
                "sessionId": "actual-session",
            },
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 8,
                        "auxData": {
                            "isDefault": False,
                            "frameId": "actual-frame",
                        },
                    }
                },
                "sessionId": "actual-session",
            },
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
            events=[
                {
                    "method": "Runtime.executionContextCreated",
                    "params": {
                        "context": {
                            "id": 3,
                            "auxData": {
                                "isDefault": True,
                                "frameId": "synthetic-frame",
                            },
                        }
                    },
                },
                {
                    "method": "Runtime.executionContextCreated",
                    "params": {
                        "context": {
                            "id": 4,
                            "auxData": {
                                "isDefault": False,
                                "frameId": "synthetic-frame",
                            },
                        }
                    },
                },
            ],
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
async def test_transition_stops_waiting_when_replayed_navigation_returns_an_error() -> None:
    upstream = FakeProviderSession(
        [
            {
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": "Navigation failed",
                },
            }
        ]
    )
    facade = transition_session(upstream)
    facade._replay = [
        ReplayEntry(
            command={
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": "https://example.com"},
            },
            response={"id": 1, "result": {"frameId": "synthetic-frame"}},
        )
    ]

    with pytest.raises(
        ProviderTransitionError,
        match="Replay failed for Page.navigate: provider command error",
    ):
        await facade._replay_history()


def test_navigation_replay_ignores_unrelated_execution_contexts() -> None:
    response = {"id": 1, "result": {"frameId": "expected-frame"}}
    expected_events = [
        {
            "method": "Runtime.executionContextCreated",
            "params": {
                "context": {
                    "id": 3,
                    "auxData": {
                        "isDefault": True,
                        "frameId": "synthetic-frame",
                    },
                }
            },
        },
        {
            "method": "Runtime.executionContextCreated",
            "params": {
                "context": {
                    "id": 4,
                    "auxData": {
                        "isDefault": False,
                        "frameId": "synthetic-frame",
                    },
                }
            },
        },
    ]

    assert not ProviderTransitionSession._replay_boundary_reached(
        "Page.navigate",
        response,
        [
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 7,
                        "auxData": {
                            "isDefault": False,
                            "frameId": "expected-frame",
                        },
                    }
                },
            },
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 8,
                        "auxData": {
                            "isDefault": True,
                            "frameId": "other-frame",
                        },
                    }
                },
            },
        ],
        expected_events,
    )
    assert not ProviderTransitionSession._replay_boundary_reached(
        "Page.navigate",
        {"id": 1, "result": {}},
        [
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 9,
                        "auxData": {
                            "isDefault": True,
                            "frameId": "other-frame",
                        },
                    }
                },
            }
        ],
        expected_events,
    )


@pytest.mark.asyncio
async def test_navigation_clears_stale_execution_context_and_object_mappings() -> None:
    upstream = FakeProviderSession(
        [
            {
                "method": "Runtime.executionContextsCleared",
                "params": {},
                "sessionId": "actual-session",
            },
            {
                "method": "Runtime.executionContextCreated",
                "params": {
                    "context": {
                        "id": 3,
                        "uniqueId": "new-default-context",
                    }
                },
                "sessionId": "actual-session",
            },
        ]
    )
    facade = transition_session(upstream)
    facade._forward_ids["session"]["synthetic-session"] = "actual-session"
    facade._reverse_ids["session"]["actual-session"] = "synthetic-session"
    facade._forward_ids["execution"][3] = 2
    facade._reverse_ids["execution"][2] = 3
    facade._forward_ids["object"]["synthetic-object"] = "actual-object"
    facade._reverse_ids["object"]["actual-object"] = "synthetic-object"

    await facade._pump_upstream()
    await facade._forward(
        {
            "id": 24,
            "method": "Runtime.evaluate",
            "params": {"expression": "document.title", "contextId": 3},
            "sessionId": "synthetic-session",
        }
    )

    assert upstream.sent == [
        {
            "id": 24,
            "method": "Runtime.evaluate",
            "params": {"expression": "document.title", "contextId": 3},
            "sessionId": "actual-session",
        }
    ]
    assert "execution" not in facade._forward_ids


@pytest.mark.asyncio
async def test_transition_preserves_provider_timeout_reason() -> None:
    upstream = FakeProviderSession([])
    upstream.disconnect_reason = "provider_timeout"
    facade = transition_session(upstream)

    await facade._pump_upstream()

    assert facade.disconnect_reason == "provider_timeout"
    assert "execution" not in facade._reverse_ids
    assert "object" not in facade._forward_ids
    assert "object" not in facade._reverse_ids


@pytest.mark.asyncio
async def test_transition_preserves_domain_blocking_failure_reason() -> None:
    class BlockedProviderSession(FakeProviderSession):
        disconnect_reason = "domain_blocking_unavailable"

        async def messages(self) -> AsyncIterator[str]:
            raise DomainBlockingUnavailable
            yield  # pragma: no cover

    upstream = BlockedProviderSession([])
    facade = transition_session(upstream)

    await facade._pump_upstream()

    assert facade.disconnect_reason == "domain_blocking_unavailable"


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

        async def release(
            self,
            *,
            failed: bool,
            reason: str,
            command_summary: dict[str, object] | None = None,
        ) -> None:
            timeline.append(f"release:{self.attempt.provider.value}:{reason}")

        async def bind_provider_session(self, **kwargs) -> None:
            return None

        async def record_provider_usage(self, **kwargs) -> None:
            return None

    source = Lease(ProviderName.HTTP)

    class Attempts:
        async def acquire(self, session, resolved, *, replacement_for=None):
            assert resolved.provider.slug is not None
            provider = resolved.provider.slug
            assert replacement_for == source.attempt.attempt_id
            timeline.append(f"acquire:{provider.value}")
            return Lease(provider)

    class Adapter:
        def __init__(self, provider: ProviderName) -> None:
            self.provider = provider

        async def acquire(self, session, resolved):
            timeline.append(f"connect:{self.provider.value}")
            if self.provider is ProviderName.BROWSERLESS:
                raise ConnectionError("first candidate unavailable")
            return FakeProviderSession([], self.provider)

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
            ProviderCandidate(ProviderName.BROWSERLESS, 100, 0),
            ProviderCandidate(ProviderName.BROWSERBASE, 300, 1),
        ),
        "cheapest_eligible",
        1,
        1,
    )

    await facade._transition(
        {"id": 7, "method": "Page.printToPDF"},
        "Page.printToPDF",
        plan=plan,
    )

    assert timeline[:6] == [
        "acquire:browserless",
        "connect:browserless",
        "release:browserless:provider_transition_attempt_failed",
        "acquire:browserbase",
        "connect:browserbase",
        "activate:browserbase",
    ]
    assert timeline.index("activate:browserbase") < timeline.index(
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
                provider="browserbase",
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
        to_provider=ProviderName.BROWSERBASE,
        trigger_method="Runtime.evaluate",
    )

    async with database_sessions() as database:
        row = await database.get(
            DomainProviderTransitionStat,
            (
                domain.id,
                "http",
                "browserbase",
                "new_requirement",
            ),
        )
    assert row is not None
    assert row.transition_count == 1
