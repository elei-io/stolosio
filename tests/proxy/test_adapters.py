import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.proxy.adapters.browserbase import BrowserbaseAdapter
from backend.proxy.adapters.cdp import DirectCdpAdapter, WebSocketProviderSession
from backend.proxy.adapters.http import HttpAdapter
from backend.proxy.adapters.registry import get_provider_adapter
from backend.proxy.contracts import (
    ProviderName,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SessionState,
    StolosioSession,
)


class FakeWebSocket:
    def __init__(self, messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.messages = messages or []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, object]:
        return self.body


class FakeHttpClient:
    requests: list[tuple[str, str, dict[str, object]]] = []

    def __init__(self, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append(("POST", url, kwargs))
        return FakeResponse(
            {
                "id": "bb-session",
                "connectUrl": "wss://connect.browserbase.example",
                "startedAt": "2026-07-18T01:02:03Z",
            }
        )

    async def patch(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append(("PATCH", url, kwargs))
        return FakeResponse({})

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.requests.append(("GET", url, kwargs))
        return FakeResponse({"endedAt": "2026-07-18T01:03:03Z"})


@pytest.mark.asyncio
async def test_direct_adapter_connects_to_assigned_browserless_worker(monkeypatch) -> None:
    request: dict[str, object] = {}

    async def fake_connect(url: str, **kwargs):
        request["url"] = url
        request["kwargs"] = kwargs
        return FakeWebSocket()

    monkeypatch.setattr("backend.proxy.adapters.cdp.connect", fake_connect)
    adapter = DirectCdpAdapter(
        ProviderName.BROWSERLESS,
        "ws://stolosio-browserless-2:3000",
    )

    session = await adapter.acquire(None, None)  # type: ignore[arg-type]

    assert session.provider is ProviderName.BROWSERLESS
    assert request == {
        "url": "ws://stolosio-browserless-2:3000",
        "kwargs": {"max_size": None, "proxy": None},
    }


@pytest.mark.asyncio
async def test_native_cdp_session_forwards_unknown_methods_without_interpreting_them() -> None:
    websocket = FakeWebSocket(
        ['{"id":7,"result":{"futureField":true},"sessionId":"page"}']
    )
    session = WebSocketProviderSession(
        ProviderName.BROWSERLESS,
        websocket,  # type: ignore[arg-type]
    )
    command = '{"id":7,"method":"Future.experimentalMethod","params":{"x":1}}'

    await session.send(command)
    messages = [message async for message in session.messages()]

    assert websocket.sent == [command]
    assert messages == ['{"id":7,"result":{"futureField":true},"sessionId":"page"}']


@pytest.mark.asyncio
async def test_native_session_close_does_not_manage_client_contexts() -> None:
    websocket = FakeWebSocket()
    session = WebSocketProviderSession(
        ProviderName.BROWSERLESS,
        websocket,  # type: ignore[arg-type]
    )

    await session.send('{"id":1,"method":"Target.createBrowserContext"}')
    await session.close()

    assert websocket.sent == ['{"id":1,"method":"Target.createBrowserContext"}']
    assert websocket.closed
    assert session.provider_ended_at is not None
    assert session.provider_started_at <= datetime.now(UTC)


def test_native_session_reports_provider_deadline_expiration() -> None:
    session = WebSocketProviderSession(
        ProviderName.BROWSERLESS,
        FakeWebSocket(),  # type: ignore[arg-type]
        timeout_started_at=datetime.now(UTC) - timedelta(seconds=10),
        session_timeout_seconds=5,
    )

    assert session.disconnect_reason == "provider_timeout"


@pytest.mark.asyncio
async def test_browserbase_adapter_owns_remote_session_lifecycle(monkeypatch) -> None:
    websocket = FakeWebSocket()
    FakeHttpClient.requests = []

    async def fake_connect(url: str, **kwargs):
        assert url == "wss://connect.browserbase.example"
        assert kwargs == {"max_size": None, "proxy": None}
        return websocket

    monkeypatch.setattr(
        "backend.proxy.adapters.browserbase.httpx.AsyncClient", FakeHttpClient
    )
    monkeypatch.setattr("backend.proxy.adapters.browserbase.connect", fake_connect)
    adapter = BrowserbaseAdapter(
        api_url="https://api.browserbase.example/v1",
        api_key="secret",
        project_id="project",
        timeout_seconds=600,
    )

    provider_session = await adapter.acquire(
        StolosioSession("stolosio-session", "owner", "lease", SessionState.OPEN),
        ResolvedSessionSettings(
            provider=ProviderSettingSchema(slug=ProviderName.BROWSERBASE),
            session=SessionSettingSchema(),
            sources={},
        ),
    )
    await provider_session.close()

    assert provider_session.provider_session_id == "bb-session"
    assert provider_session.provider_started_at is not None
    assert provider_session.provider_ended_at is not None
    assert (
        provider_session.provider_ended_at - provider_session.provider_started_at
    ).total_seconds() == 60
    assert websocket.closed
    assert [(method, url) for method, url, _ in FakeHttpClient.requests] == [
        ("POST", "https://api.browserbase.example/v1/sessions"),
        ("PATCH", "https://api.browserbase.example/v1/sessions/bb-session"),
        ("GET", "https://api.browserbase.example/v1/sessions/bb-session"),
    ]
    assert FakeHttpClient.requests[0][2]["json"] == {
        "keepAlive": False,
        "timeout": 600,
        "userMetadata": {"stolosioSessionId": "stolosio-session"},
        "projectId": "project",
    }


@pytest.mark.asyncio
async def test_browserbase_releases_session_when_connect_url_is_missing(
    monkeypatch,
) -> None:
    class MissingConnectUrlClient(FakeHttpClient):
        async def post(self, url: str, **kwargs) -> FakeResponse:
            self.requests.append(("POST", url, kwargs))
            return FakeResponse(
                {
                    "id": "orphaned-session",
                    "startedAt": "2026-07-18T01:02:03Z",
                }
            )

    FakeHttpClient.requests = []
    monkeypatch.setattr(
        "backend.proxy.adapters.browserbase.httpx.AsyncClient",
        MissingConnectUrlClient,
    )
    adapter = BrowserbaseAdapter(
        api_url="https://api.browserbase.example/v1",
        api_key="secret",
        project_id=None,
        timeout_seconds=600,
    )

    with pytest.raises(RuntimeError, match="invalid session"):
        await adapter.acquire(
            StolosioSession("stolosio-session", "owner", "lease", SessionState.OPEN),
            ResolvedSessionSettings(
                provider=ProviderSettingSchema(slug=ProviderName.BROWSERBASE),
                session=SessionSettingSchema(),
                sources={},
            ),
        )

    assert [(method, url) for method, url, _ in FakeHttpClient.requests] == [
        ("POST", "https://api.browserbase.example/v1/sessions"),
        (
            "PATCH",
            "https://api.browserbase.example/v1/sessions/orphaned-session",
        ),
        ("GET", "https://api.browserbase.example/v1/sessions/orphaned-session"),
    ]


def test_registry_has_only_http_browserless_and_browserbase_adapters(monkeypatch) -> None:
    from backend.proxy.adapters import registry
    from backend.proxy.errors import ProviderUnavailable

    assert isinstance(get_provider_adapter(ProviderName.HTTP), HttpAdapter)
    browserless = get_provider_adapter(
        ProviderName.BROWSERLESS,
        endpoint="ws://browserless-worker:3000",
    )
    assert isinstance(browserless, DirectCdpAdapter)
    assert browserless.session_timeout_seconds == 600
    monkeypatch.setattr(registry.settings, "browserbase_network_isolation_verified", False)
    with pytest.raises(ProviderUnavailable, match="network isolation"):
        get_provider_adapter(ProviderName.BROWSERBASE)
    monkeypatch.setattr(registry.settings, "browserbase_network_isolation_verified", True)
    assert isinstance(get_provider_adapter(ProviderName.BROWSERBASE), BrowserbaseAdapter)


@pytest.mark.asyncio
async def test_explicit_http_is_bounded_and_never_transitions() -> None:
    adapter = get_provider_adapter(ProviderName.HTTP)
    session = await adapter.acquire(
        StolosioSession("session", "owner", "lease", SessionState.OPEN),
        ResolvedSessionSettings(
            provider=ProviderSettingSchema(slug=ProviderName.HTTP),
            session=SessionSettingSchema(),
            sources={},
        ),
    )

    await session.send(json.dumps({"id": 1, "method": "Page.printToPDF"}))

    assert json.loads(await anext(session.messages())) == {
        "id": 1,
        "error": {
            "code": -32601,
            "message": "Page.printToPDF is not supported by provider http",
        },
    }
