import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSelection,
    SessionState,
)
from backend.proxy.errors import ProviderQueueFull, SessionQueueTimeout
from backend.proxy.redis import RedisSessionRepository
from backend.proxy.redis.repository import RepositorySettings
from backend.proxy.sessions import SessionManager
from backend.proxy.settings import harbor_settings_resolver
from backend.settings import Settings


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url("redis://localhost:6379/15", decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis integration service is not available")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def session_settings() -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/15",
        session_lease_seconds=2,
        session_heartbeat_seconds=0.1,
        session_queue_poll_ms=10,
        session_queue_timeout_seconds=1,
        chromium_max_active_sessions=1,
        chromium_max_queued_sessions=2,
    )


@pytest.fixture
def repository(redis_client: Redis, session_settings: Settings) -> RedisSessionRepository:
    return RedisSessionRepository(
        redis_client,
        RepositorySettings(
            lease_ms=session_settings.session_lease_seconds * 1000,
            queue_ttl_ms=(
                session_settings.session_queue_timeout_seconds
                + session_settings.session_lease_seconds
            )
            * 1000,
            terminal_ttl_ms=session_settings.terminal_session_ttl_seconds * 1000,
            event_stream_maxlen=session_settings.session_event_stream_maxlen,
        ),
    )


async def requested_and_resolved():
    return await harbor_settings_resolver.resolve(
        [("harbor.provider.slug", "chromium")]
    )


@pytest.mark.asyncio
async def test_admits_below_capacity_and_releases_once(
    repository: RedisSessionRepository,
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


@pytest.mark.asyncio
async def test_two_replicas_serve_waiter_after_release(
    repository: RedisSessionRepository,
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
    repository: RedisSessionRepository,
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
    repository: RedisSessionRepository,
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
    repository: RedisSessionRepository,
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
    repository: RedisSessionRepository,
    session_settings: Settings,
) -> None:
    requested, resolved = await requested_and_resolved()
    first = await SessionManager(repository, session_settings, owner_id="one").admit(
        requested, resolved
    )
    second_task = asyncio.create_task(
        SessionManager(repository, session_settings, owner_id="two").admit(
            requested, resolved
        )
    )
    await asyncio.sleep(0.03)
    third_task = asyncio.create_task(
        SessionManager(repository, session_settings, owner_id="three").admit(
            requested, resolved
        )
    )
    await asyncio.sleep(0.03)

    queued = await repository.queue("chromium")
    assert len(queued) == 2

    await first.release()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert not third_task.done()

    await second.release()
    third = await asyncio.wait_for(third_task, timeout=1)
    await third.release()


@pytest.mark.asyncio
async def test_cancelling_waiter_removes_it_from_queue(
    repository: RedisSessionRepository,
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
async def test_expired_owner_cannot_leak_capacity(redis_client: Redis) -> None:
    repository = RedisSessionRepository(
        redis_client,
        RepositorySettings(
            lease_ms=50,
            queue_ttl_ms=100,
            terminal_ttl_ms=1_000,
            event_stream_maxlen=100,
        ),
    )
    first = HarborSession(
        session_id="expired-session",
        owner_id="dead-replica",
        lease_token="dead-token",
        requested_provider=ProviderSelection.CHROMIUM,
        resolved_provider=ProviderName.CHROMIUM,
        state=SessionState.REQUESTED,
    )
    second = HarborSession(
        session_id="replacement-session",
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
    await repository.release(second, failed=False, reason="test_complete")


@pytest.mark.asyncio
async def test_two_redis_clients_cannot_claim_the_final_slot(redis_client: Redis) -> None:
    second_client = Redis.from_url("redis://localhost:6379/15", decode_responses=True)
    repository_settings = RepositorySettings(
        lease_ms=1_000,
        queue_ttl_ms=2_000,
        terminal_ttl_ms=1_000,
        event_stream_maxlen=100,
    )
    first_repository = RedisSessionRepository(redis_client, repository_settings)
    second_repository = RedisSessionRepository(second_client, repository_settings)

    def candidate(session_id: str, owner_id: str) -> HarborSession:
        return HarborSession(
            session_id=session_id,
            owner_id=owner_id,
            lease_token=f"{owner_id}-token",
            requested_provider=ProviderSelection.CHROMIUM,
            resolved_provider=ProviderName.CHROMIUM,
            state=SessionState.REQUESTED,
        )

    first = candidate("concurrent-one", "replica-one")
    second = candidate("concurrent-two", "replica-two")
    try:
        statuses = await asyncio.gather(
            first_repository.admit(first, max_active=1, max_queued=1),
            second_repository.admit(second, max_active=1, max_queued=1),
        )

        assert sorted(status.value for status in statuses) == ["acquiring", "queued"]
        assert await first_repository.active_count("chromium") == 1
        assert len(await first_repository.queue("chromium")) == 1
    finally:
        await first_repository.release(first, failed=False, reason="test_complete")
        await second_repository.release(second, failed=False, reason="test_complete")
        await second_client.aclose()
