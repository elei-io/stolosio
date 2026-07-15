import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.session import Base
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSelection,
    SessionState,
)
from backend.proxy.errors import ProviderQueueFull, SessionQueueTimeout
from backend.proxy.postgres import PostgresSessionRepository
from backend.proxy.postgres.repository import RepositorySettings
from backend.proxy.sessions import SessionManager
from backend.proxy.settings import harbor_settings_resolver
from backend.settings import Settings


@pytest_asyncio.fixture
async def database_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = Settings()
    admin_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    schema = f"harbor_test_{uuid4().hex}"
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception:
        await admin_engine.dispose()
        pytest.skip("Postgres integration service is not available")

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()


@pytest.fixture
def session_settings() -> Settings:
    return Settings(
        session_lease_seconds=2,
        session_heartbeat_seconds=0.1,
        session_queue_poll_ms=10,
        session_queue_timeout_seconds=1,
        chromium_max_active_sessions=1,
        chromium_max_queued_sessions=2,
    )


@pytest.fixture
def repository(
    database_sessions: async_sessionmaker[AsyncSession],
    session_settings: Settings,
) -> PostgresSessionRepository:
    return PostgresSessionRepository(
        database_sessions,
        RepositorySettings(
            lease_seconds=session_settings.session_lease_seconds,
            queue_ttl_seconds=(
                session_settings.session_queue_timeout_seconds
                + session_settings.session_lease_seconds
            ),
        ),
    )


async def requested_and_resolved():
    return await harbor_settings_resolver.resolve(
        [("harbor.provider.slug", "chromium")]
    )


@pytest.mark.asyncio
async def test_admits_below_capacity_and_releases_once(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    manager = SessionManager(repository, session_settings, owner_id="replica-one")

    lease = await manager.admit(requested, resolved)
    assert await repository.active_count("chromium") == 1

    await lease.release()
    await lease.release()

    assert await repository.active_count("chromium") == 0
    stored = await repository.read_session(lease.session.session_id)
    assert stored["state"] == "closed"
    assert await repository.events(lease.session.session_id) == [
        "session.requested",
        "session.acquiring",
        "session.closed",
    ]


@pytest.mark.asyncio
async def test_two_replicas_serve_waiter_after_release(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    first_manager = SessionManager(repository, session_settings, owner_id="replica-one")
    second_manager = SessionManager(repository, session_settings, owner_id="replica-two")
    first = await first_manager.admit(requested, resolved)

    second_task = asyncio.create_task(second_manager.admit(requested, resolved))
    await asyncio.sleep(0.05)
    assert len(await repository.queue("chromium")) == 1

    await first.release()
    second = await asyncio.wait_for(second_task, timeout=1)

    assert await repository.active_count("chromium") == 1
    assert await repository.queue("chromium") == []
    await second.release()


@pytest.mark.asyncio
async def test_stale_token_cannot_heartbeat_or_release(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    manager = SessionManager(repository, session_settings, owner_id="replica-one")
    lease = await manager.admit(requested, resolved)
    stale = lease.session.__class__(
        session_id=lease.session.session_id,
        owner_id=lease.session.owner_id,
        lease_token="stale-token",
        requested_provider=lease.session.requested_provider,
        resolved_provider=lease.session.resolved_provider,
        state=lease.session.state,
    )

    assert not await repository.heartbeat(stale)
    assert not await repository.release(stale, failed=False, reason="stale")
    assert await repository.active_count("chromium") == 1
    await lease.release()


@pytest.mark.asyncio
async def test_rejects_when_provider_queue_is_full(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    settings = session_settings.model_copy(
        update={"chromium_max_active_sessions": 1, "chromium_max_queued_sessions": 0}
    )
    first = await SessionManager(repository, settings, owner_id="one").admit(
        requested, resolved
    )

    with pytest.raises(ProviderQueueFull):
        await SessionManager(repository, settings, owner_id="two").admit(requested, resolved)

    await first.release()


@pytest.mark.asyncio
async def test_queue_timeout_removes_waiter(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    settings = session_settings.model_copy(update={"session_queue_timeout_seconds": 0.05})
    first = await SessionManager(repository, settings, owner_id="one").admit(
        requested, resolved
    )

    with pytest.raises(SessionQueueTimeout):
        await SessionManager(repository, settings, owner_id="two").admit(requested, resolved)

    assert await repository.queue("chromium") == []
    await first.release()


@pytest.mark.asyncio
async def test_three_replicas_are_admitted_in_fifo_order(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    class TrackingNotifier:
        def __init__(self) -> None:
            self.two_waiters = asyncio.Event()

        async def wait(self, provider, wait_seconds: float) -> None:
            if len(await repository.queue(provider.value)) == 2:
                self.two_waiters.set()
            await asyncio.sleep(wait_seconds)

        async def notify(self, provider) -> None:
            return None

        async def close(self) -> None:
            return None

    requested, resolved = await requested_and_resolved()
    notifier = TrackingNotifier()
    first = await SessionManager(
        repository, session_settings, owner_id="one", notifier=notifier
    ).admit(
        requested, resolved
    )
    second_task = asyncio.create_task(
        SessionManager(
            repository, session_settings, owner_id="two", notifier=notifier
        ).admit(
            requested, resolved
        )
    )
    await asyncio.sleep(0.03)
    third_task = asyncio.create_task(
        SessionManager(
            repository, session_settings, owner_id="three", notifier=notifier
        ).admit(
            requested, resolved
        )
    )
    await asyncio.wait_for(notifier.two_waiters.wait(), timeout=1)
    assert len(await repository.queue("chromium")) == 2

    await first.release()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert not third_task.done()

    await second.release()
    third = await asyncio.wait_for(third_task, timeout=1)
    await third.release()


@pytest.mark.asyncio
async def test_cancelling_waiter_removes_it_from_queue(
    repository: PostgresSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    first = await SessionManager(repository, session_settings, owner_id="one").admit(
        requested, resolved
    )
    waiter = asyncio.create_task(
        SessionManager(repository, session_settings, owner_id="two").admit(
            requested, resolved
        )
    )
    await asyncio.sleep(0.03)
    assert len(await repository.queue("chromium")) == 1

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert await repository.queue("chromium") == []
    await first.release()


@pytest.mark.asyncio
async def test_expired_owner_cannot_leak_capacity(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresSessionRepository(
        database_sessions,
        RepositorySettings(lease_seconds=0.05, queue_ttl_seconds=0.1),
    )
    first = HarborSession(
        session_id=str(uuid4()),
        owner_id="dead-replica",
        lease_token="dead-token",
        requested_provider=ProviderSelection.CHROMIUM,
        resolved_provider=ProviderName.CHROMIUM,
        state=SessionState.REQUESTED,
    )
    second = HarborSession(
        session_id=str(uuid4()),
        owner_id="live-replica",
        lease_token="live-token",
        requested_provider=ProviderSelection.CHROMIUM,
        resolved_provider=ProviderName.CHROMIUM,
        state=SessionState.REQUESTED,
    )
    assert (await repository.admit(first, max_active=1, max_queued=1)).value == "acquiring"

    await asyncio.sleep(0.06)

    assert (await repository.admit(second, max_active=1, max_queued=1)).value == "acquiring"
    assert await repository.active_count("chromium") == 1
    assert (await repository.read_session(first.session_id))["state"] == "failed"
    await repository.release(second, failed=False, reason="test_complete")


@pytest.mark.asyncio
async def test_two_database_clients_cannot_claim_the_final_slot(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    settings = RepositorySettings(lease_seconds=1, queue_ttl_seconds=2)
    first_repository = PostgresSessionRepository(database_sessions, settings)
    second_repository = PostgresSessionRepository(database_sessions, settings)

    def candidate(owner_id: str) -> HarborSession:
        return HarborSession(
            session_id=str(uuid4()),
            owner_id=owner_id,
            lease_token=f"{owner_id}-token",
            requested_provider=ProviderSelection.CHROMIUM,
            resolved_provider=ProviderName.CHROMIUM,
            state=SessionState.REQUESTED,
        )

    first = candidate("replica-one")
    second = candidate("replica-two")
    statuses = await asyncio.gather(
        first_repository.admit(first, max_active=1, max_queued=1),
        second_repository.admit(second, max_active=1, max_queued=1),
    )

    assert sorted(status.value for status in statuses) == ["acquiring", "queued"]
    assert await first_repository.active_count("chromium") == 1
    assert len(await first_repository.queue("chromium")) == 1
    await first_repository.release(first, failed=False, reason="test_complete")
    await second_repository.release(second, failed=False, reason="test_complete")
