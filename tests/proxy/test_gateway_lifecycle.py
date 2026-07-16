import asyncio

import pytest
from starlette.datastructures import QueryParams
from starlette.websockets import WebSocketState

from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import (
    AttemptState,
    HarborSession,
    ProviderAttempt,
    ProviderName,
    SessionState,
)
from backend.proxy.gateway import Gateway
from backend.proxy.sessions import SessionLease
from backend.settings import Settings


class FakeWebSocket:
    def __init__(self, query: str = "harbor.provider.slug=chromium") -> None:
        self.query_params = QueryParams(query)
        self.application_state = WebSocketState.CONNECTING
        self.client_state = WebSocketState.CONNECTING
        self.accepted = False
        self.denial_status: int | None = None
        self.closed: tuple[int, str] | None = None
        self._initial = True
        self._disconnected = asyncio.Event()

    async def receive(self):
        if self._initial:
            self._initial = False
            self.client_state = WebSocketState.CONNECTED
            return {"type": "websocket.connect"}
        await self._disconnected.wait()
        self.client_state = WebSocketState.DISCONNECTED
        return {"type": "websocket.disconnect", "code": 1000}

    async def accept(self) -> None:
        self.accepted = True
        self.application_state = WebSocketState.CONNECTED

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        self.application_state = WebSocketState.DISCONNECTED

    async def send_denial_response(self, response) -> None:
        self.denial_status = response.status_code
        self.application_state = WebSocketState.DISCONNECTED

    def disconnect(self) -> None:
        self._disconnected.set()


class FakeSessionLease:
    def __init__(self) -> None:
        self.session = HarborSession(
            session_id="00000000-0000-4000-8000-000000000001",
            owner_id="replica",
            lease_token="token",
            state=SessionState.ADMITTED,
        )
        self.released: list[tuple[bool, str]] = []

    async def open(self) -> None:
        self.session = HarborSession(
            session_id=self.session.session_id,
            owner_id=self.session.owner_id,
            lease_token=self.session.lease_token,
            state=SessionState.OPEN,
        )

    async def wait_lost(self) -> None:
        await asyncio.Future()

    async def release(self, *, failed: bool, reason: str) -> None:
        self.released.append((failed, reason))


class FakeAttemptLease:
    def __init__(self) -> None:
        self.attempt = ProviderAttempt(
            attempt_id="00000000-0000-4000-8000-000000000002",
            session_id="00000000-0000-4000-8000-000000000001",
            ordinal=1,
            provider=ProviderName.CHROMIUM,
            state=AttemptState.ACQUIRING,
        )
        self.released: list[tuple[bool, str]] = []

    async def activate(self) -> None:
        return None

    async def release(self, *, failed: bool, reason: str) -> None:
        self.released.append((failed, reason))


@pytest.mark.asyncio
async def test_provider_acquisition_failure_releases_both_admission_levels(
    monkeypatch,
) -> None:
    session = FakeSessionLease()
    attempt = FakeAttemptLease()

    class Sessions:
        async def admit(self, requested):
            return session

    class Attempts:
        async def acquire(self, harbor_session, resolved):
            return attempt

    class FailingAdapter:
        async def acquire(self, harbor_session, resolved):
            raise ConnectionError("provider unavailable")

    monkeypatch.setattr(
        "backend.proxy.gateway.get_provider_adapter", lambda provider: FailingAdapter()
    )
    websocket = FakeWebSocket()
    gateway = Gateway(
        Sessions(),  # type: ignore[arg-type]
        Attempts(),  # type: ignore[arg-type]
        CapabilityRegistry({}),
        Settings(session_cleanup_timeout_seconds=1),
    )

    await gateway.connect(websocket)  # type: ignore[arg-type]

    assert websocket.denial_status == 503
    assert not websocket.accepted
    assert attempt.released == [(True, "provider_unavailable")]
    assert session.released == [(True, "provider_unavailable")]


@pytest.mark.asyncio
async def test_disconnect_while_queued_cancels_work_without_accepting() -> None:
    session = FakeSessionLease()
    cancelled = asyncio.Event()
    started = asyncio.Event()

    class Sessions:
        async def admit(self, requested):
            return session

    class Attempts:
        async def acquire(self, harbor_session, resolved):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    websocket = FakeWebSocket()
    gateway = Gateway(
        Sessions(),  # type: ignore[arg-type]
        Attempts(),  # type: ignore[arg-type]
        CapabilityRegistry({}),
        Settings(session_cleanup_timeout_seconds=1),
    )
    task = asyncio.create_task(gateway.connect(websocket))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), timeout=1)
    websocket.disconnect()
    await asyncio.wait_for(task, timeout=1)

    assert cancelled.is_set()
    assert not websocket.accepted
    assert websocket.denial_status is None
    assert session.released == [(False, "client_disconnected")]


@pytest.mark.asyncio
async def test_postgres_heartbeat_failure_marks_session_lease_lost() -> None:
    class FailingRepository:
        async def heartbeat(self, session) -> bool:
            raise ConnectionError("postgres unavailable")

        async def open(self, session) -> bool:
            return True

        async def release(self, session, *, failed: bool, reason: str) -> bool:
            return True

    session = HarborSession(
        session_id="session",
        owner_id="replica",
        lease_token="token",
        state=SessionState.ADMITTED,
    )
    lease = SessionLease(
        session,
        FailingRepository(),  # type: ignore[arg-type]
        heartbeat_seconds=0.01,
    )

    await asyncio.wait_for(lease.wait_lost(), timeout=0.1)
    await lease.release(failed=True, reason="session_lease_lost")
