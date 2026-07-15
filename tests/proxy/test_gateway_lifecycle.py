import asyncio

import pytest

from backend.messaging import PollingNotifier
from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSelection,
    SessionState,
)
from backend.proxy.errors import ProviderUnavailable
from backend.proxy.gateway import Gateway
from backend.proxy.sessions import SessionLease
from backend.proxy.settings import harbor_settings_resolver


async def connection_settings():
    return await harbor_settings_resolver.resolve(
        [("harbor.provider.slug", "chromium")]
    )


@pytest.mark.asyncio
async def test_postgres_admission_failure_fails_closed() -> None:
    class FailingSessions:
        async def admit(self, requested, resolved):
            raise ConnectionError("postgres unavailable")

    requested, resolved = await connection_settings()
    gateway = Gateway(
        FailingSessions(),  # type: ignore[arg-type]
        CapabilityRegistry({}),
        acquisition_timeout_seconds=1,
    )

    with pytest.raises(ProviderUnavailable):
        await gateway.connect(None, requested, resolved)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_provider_acquisition_failure_releases_capacity(monkeypatch) -> None:
    released: list[tuple[bool, str]] = []

    class FakeLease:
        session = object()

        async def release(self, *, failed: bool, reason: str) -> None:
            released.append((failed, reason))

    class FakeSessions:
        async def admit(self, requested, resolved):
            return FakeLease()

    class FailingAdapter:
        async def acquire(self, session, settings):
            raise ConnectionError("provider unavailable")

    monkeypatch.setattr(
        "backend.proxy.gateway.get_provider_adapter",
        lambda provider: FailingAdapter(),
    )
    requested, resolved = await connection_settings()
    gateway = Gateway(
        FakeSessions(),  # type: ignore[arg-type]
        CapabilityRegistry({ProviderName.CHROMIUM: frozenset()}),
        acquisition_timeout_seconds=1,
    )

    with pytest.raises(ProviderUnavailable):
        await gateway.connect(None, requested, resolved)  # type: ignore[arg-type]

    assert released == [(True, "provider_unavailable")]


@pytest.mark.asyncio
async def test_postgres_heartbeat_failure_marks_lease_lost() -> None:
    class FailingRepository:
        async def heartbeat(self, session) -> bool:
            raise ConnectionError("postgres unavailable")

        async def transition(self, session, state) -> bool:
            return True

        async def release(self, session, *, failed: bool, reason: str) -> bool:
            return True

    session = HarborSession(
        session_id="session",
        owner_id="replica",
        lease_token="token",
        requested_provider=ProviderSelection.CHROMIUM,
        resolved_provider=ProviderName.CHROMIUM,
        state=SessionState.ACQUIRING,
    )
    lease = SessionLease(
        session,
        FailingRepository(),  # type: ignore[arg-type]
        PollingNotifier(),
        heartbeat_seconds=0.01,
    )

    await asyncio.wait_for(lease.wait_lost(), timeout=0.1)
    await lease.release(failed=True, reason="session_lease_lost")
