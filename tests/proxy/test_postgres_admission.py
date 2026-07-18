import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import AcquisitionAttempt, GatewaySession, SessionEventRecord
from backend.fleet import FleetInstanceState, FleetRepository, ObservedInstance
from backend.messaging import PollingNotifier
from backend.proxy.attempts import AttemptAdmission
from backend.proxy.contracts import HarborSession, ProviderName, SessionState
from backend.proxy.errors import (
    GatewayCapacityFull,
    ProviderQueueFull,
    ProviderQueueTimeout,
)
from backend.proxy.postgres import (
    PostgresAttemptRepository,
    PostgresSessionRepository,
    SessionRepositorySettings,
)
from backend.proxy.sessions import SessionAdmission
from backend.proxy.settings import harbor_settings_resolver
from backend.settings import Settings


@pytest.fixture
def admission_settings() -> Settings:
    return Settings(
        harbor_max_active_sessions=8,
        session_lease_seconds=2,
        session_heartbeat_seconds=0.1,
        provider_queue_poll_ms=10,
        provider_queue_timeout_seconds=1,
        chromium_minimum_instances=1,
        chromium_maximum_instances=1,
        chromium_session_capacity_per_instance=1,
        chromium_max_queued_attempts=2,
    )


@pytest.fixture
def session_repository(
    database_sessions: async_sessionmaker[AsyncSession],
    admission_settings: Settings,
) -> PostgresSessionRepository:
    return PostgresSessionRepository(
        database_sessions,
        SessionRepositorySettings(lease_seconds=admission_settings.session_lease_seconds),
    )


@pytest.fixture
def attempt_repository(
    database_sessions: async_sessionmaker[AsyncSession],
) -> PostgresAttemptRepository:
    return PostgresAttemptRepository(database_sessions)


async def requested_and_resolved():
    return await harbor_settings_resolver.resolve([("harbor.provider.slug", "chromium")])


async def admit(
    repository: PostgresSessionRepository,
    settings: Settings,
    owner: str,
):
    requested, _ = await requested_and_resolved()
    return await SessionAdmission(repository, settings, owner_id=owner).admit(requested)


def attempt_admission(
    repository: PostgresAttemptRepository,
    settings: Settings,
) -> AttemptAdmission:
    return AttemptAdmission(repository, settings, notifier=PollingNotifier())


@pytest.mark.asyncio
async def test_global_admission_is_independent_of_provider_capacity(
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    settings = admission_settings.model_copy(update={"harbor_max_active_sessions": 2})
    first = await admit(session_repository, settings, "one")
    second = await admit(session_repository, settings, "two")

    with pytest.raises(GatewayCapacityFull):
        await admit(session_repository, settings, "three")

    assert await session_repository.active_count() == 2
    await first.release()
    await second.release()


@pytest.mark.asyncio
async def test_provider_queue_full_releases_no_other_session_capacity(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    settings = admission_settings.model_copy(update={"chromium_max_queued_attempts": 0})
    _, resolved = await requested_and_resolved()
    first_session = await admit(session_repository, settings, "one")
    second_session = await admit(session_repository, settings, "two")
    first = await attempt_admission(attempt_repository, settings).acquire(
        first_session.session, resolved
    )

    with pytest.raises(ProviderQueueFull):
        await attempt_admission(attempt_repository, settings).acquire(
            second_session.session, resolved
        )

    assert await session_repository.active_count() == 2
    assert await attempt_repository.active_count(ProviderName.CHROMIUM) == 1
    await first.release()
    await first_session.release()
    await second_session.release(failed=True, reason="provider_queue_full")


@pytest.mark.asyncio
async def test_transition_replacement_can_overlap_one_active_source_attempt(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    session = await admit(session_repository, admission_settings, "transition")
    _, http = await harbor_settings_resolver.resolve(
        [("harbor.provider.slug", "http")]
    )
    _, lightpanda = await harbor_settings_resolver.resolve(
        [("harbor.provider.slug", "lightpanda")]
    )
    admissions = attempt_admission(attempt_repository, admission_settings)
    source = await admissions.acquire(session.session, http)
    await source.activate()

    with pytest.raises(
        RuntimeError,
        match="already has a live acquisition attempt",
    ):
        await admissions.acquire(session.session, lightpanda)

    replacement = await admissions.acquire(
        session.session,
        lightpanda,
        replacement_for=source.attempt.attempt_id,
    )
    await replacement.activate()

    async with database_sessions() as database:
        rows = list(
            await database.scalars(
                select(AcquisitionAttempt)
                .where(AcquisitionAttempt.session_id == session.session.session_id)
                .order_by(AcquisitionAttempt.ordinal)
            )
        )
    assert [(row.provider, row.state) for row in rows] == [
        ("http", "active"),
        ("lightpanda", "active"),
    ]

    await replacement.release()
    await source.release()
    await session.release()


@pytest.mark.asyncio
async def test_provider_attempts_are_claimed_in_fifo_order(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    class TrackingNotifier:
        def __init__(self) -> None:
            self.one_queued = asyncio.Event()
            self.two_queued = asyncio.Event()

        async def wait(self, provider, wait_seconds: float) -> None:
            depth = len(await attempt_repository.queue(provider))
            if depth >= 1:
                self.one_queued.set()
            if depth >= 2:
                self.two_queued.set()
            await asyncio.sleep(wait_seconds)

        async def notify(self, provider) -> None:
            return None

        async def close(self) -> None:
            return None

    _, resolved = await requested_and_resolved()
    sessions = [
        await admit(session_repository, admission_settings, owner)
        for owner in ("one", "two", "three")
    ]
    notifier = TrackingNotifier()
    admissions = AttemptAdmission(
        attempt_repository,
        admission_settings,
        notifier=notifier,
    )
    first = await admissions.acquire(sessions[0].session, resolved)
    second_task = asyncio.create_task(admissions.acquire(sessions[1].session, resolved))
    await asyncio.wait_for(notifier.one_queued.wait(), timeout=1)
    third_task = asyncio.create_task(admissions.acquire(sessions[2].session, resolved))
    await asyncio.wait_for(notifier.two_queued.wait(), timeout=1)

    assert len(await attempt_repository.queue(ProviderName.CHROMIUM)) == 2
    await first.release()
    second = await asyncio.wait_for(second_task, timeout=1)
    assert not third_task.done()
    await second.release()
    third = await asyncio.wait_for(third_task, timeout=1)
    await third.release()
    for session in sessions:
        await session.release()


@pytest.mark.asyncio
async def test_provider_queue_timeout_removes_attempt_only(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    settings = admission_settings.model_copy(update={"provider_queue_timeout_seconds": 0.05})
    _, resolved = await requested_and_resolved()
    first_session = await admit(session_repository, settings, "one")
    waiting_session = await admit(session_repository, settings, "two")
    admissions = attempt_admission(attempt_repository, settings)
    first = await admissions.acquire(first_session.session, resolved)

    with pytest.raises(ProviderQueueTimeout):
        await admissions.acquire(waiting_session.session, resolved)

    assert await attempt_repository.queue(ProviderName.CHROMIUM) == []
    assert await session_repository.active_count() == 2
    await first.release()
    await first_session.release()
    await waiting_session.release(failed=True, reason="provider_queue_timeout")


@pytest.mark.asyncio
async def test_cancelling_waiter_removes_provider_attempt(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    _, resolved = await requested_and_resolved()
    first_session = await admit(session_repository, admission_settings, "one")
    waiting_session = await admit(session_repository, admission_settings, "two")
    admissions = attempt_admission(attempt_repository, admission_settings)
    first = await admissions.acquire(first_session.session, resolved)
    waiter = asyncio.create_task(admissions.acquire(waiting_session.session, resolved))
    await asyncio.sleep(0.03)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert await attempt_repository.queue(ProviderName.CHROMIUM) == []
    await first.release()
    await first_session.release()
    await waiting_session.release()


@pytest.mark.asyncio
async def test_stale_session_lease_frees_global_and_provider_capacity(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(
        harbor_max_active_sessions=1,
        session_lease_seconds=2,
        session_heartbeat_seconds=10,
        chromium_minimum_instances=1,
        chromium_maximum_instances=1,
        chromium_session_capacity_per_instance=1,
        chromium_max_queued_attempts=1,
    )
    sessions = PostgresSessionRepository(
        database_sessions, SessionRepositorySettings(lease_seconds=2)
    )
    attempts = PostgresAttemptRepository(database_sessions)
    first = await admit(sessions, settings, "dead")
    _, resolved = await requested_and_resolved()
    first_attempt = await attempt_admission(attempts, settings).acquire(first.session, resolved)
    first._heartbeat_task.cancel()
    await asyncio.gather(first._heartbeat_task, return_exceptions=True)
    async with database_sessions.begin() as database:
        row = await database.get(GatewaySession, first.session.session_id)
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = await admit(sessions, settings, "live")
    second_attempt = await attempt_admission(attempts, settings).acquire(second.session, resolved)

    assert await sessions.active_count() == 1
    assert await attempts.active_count(ProviderName.CHROMIUM) == 1
    assert (await sessions.read_session(first.session.session_id))["state"] == "failed"
    await first_attempt.release(failed=True, reason="session_lease_expired")
    await second_attempt.release()
    await second.release()


@pytest.mark.asyncio
async def test_stale_token_cannot_heartbeat_or_release(
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    lease = await admit(session_repository, admission_settings, "owner")
    stale = HarborSession(
        session_id=lease.session.session_id,
        owner_id=lease.session.owner_id,
        lease_token="stale",
        state=SessionState.ADMITTED,
    )

    assert not await session_repository.heartbeat(stale)
    assert not await session_repository.release(stale, failed=False, reason="stale")
    assert await session_repository.active_count() == 1
    await lease.release()


@pytest.mark.asyncio
async def test_lifecycle_and_attempt_events_are_transactional_unpublished_outbox_rows(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    session = await admit(session_repository, admission_settings, "events")
    _, resolved = await requested_and_resolved()
    attempt = await attempt_admission(attempt_repository, admission_settings).acquire(
        session.session, resolved
    )
    await attempt.activate()
    await session.open()
    await attempt.release()
    await session.release()

    async with database_sessions() as database:
        rows = list(
            await database.scalars(select(SessionEventRecord).order_by(SessionEventRecord.id))
        )
    assert [row.event_type for row in rows] == [
        "session.requested",
        "session.admitted",
        "attempt.started",
        "attempt.acquiring",
        "attempt.connected",
        "session.open",
        "attempt.closed",
        "session.closing",
        "session.closed",
    ]
    assert all(row.published_at is None for row in rows)


@pytest.mark.asyncio
async def test_client_reference_is_optional_session_metadata(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    reference = uuid4()
    requested, _ = await harbor_settings_resolver.resolve(
        [("harbor.session.reference", str(reference))]
    )
    lease = await SessionAdmission(session_repository, admission_settings).admit(requested)

    async with database_sessions() as database:
        row = await database.get(GatewaySession, lease.session.session_id)
    assert row is not None
    assert row.client_reference == str(reference)
    assert row.requested_settings["harbor.session.reference"] == str(reference)
    await lease.release()


@pytest.mark.asyncio
async def test_attempt_rows_hold_provider_state_not_gateway_sessions(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    session = await admit(session_repository, admission_settings, "shape")
    _, resolved = await requested_and_resolved()
    attempt = await attempt_admission(attempt_repository, admission_settings).acquire(
        session.session, resolved
    )

    async with database_sessions() as database:
        session_row = await database.get(GatewaySession, session.session.session_id)
        attempt_row = await database.get(AcquisitionAttempt, attempt.attempt.attempt_id)
    assert session_row is not None and not hasattr(session_row, "resolved_provider")
    assert attempt_row is not None and attempt_row.provider == "chromium"
    await attempt.release()
    await session.release()


@pytest.mark.asyncio
async def test_managed_fleet_packs_slots_then_claims_queue_on_a_new_instance(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    fleets = FleetRepository(database_sessions)
    await fleets.ensure_fleet(
        ProviderName.CHROMIUM,
        minimum_instances=1,
        maximum_instances=2,
        session_capacity_per_instance=2,
        scale_down_cooldown_seconds=1,
    )
    first_observation = ObservedInstance(
        instance_id="chromium-one",
        endpoint="ws://chromium-one:9222",
        state=FleetInstanceState.READY,
    )
    await fleets.observe_instances(
        ProviderName.CHROMIUM,
        [first_observation],
        platform="test",
        observation_ttl_seconds=10,
    )
    sessions = [
        await admit(session_repository, admission_settings, owner)
        for owner in ("one", "two", "three")
    ]
    _, resolved = await requested_and_resolved()
    admissions = attempt_admission(attempt_repository, admission_settings)

    first = await admissions.acquire(sessions[0].session, resolved)
    second = await admissions.acquire(sessions[1].session, resolved)
    assert first.attempt.provider_instance_id == "chromium-one"
    assert second.attempt.provider_instance_id == "chromium-one"

    third_task = asyncio.create_task(admissions.acquire(sessions[2].session, resolved))
    await asyncio.sleep(0.03)
    assert not third_task.done()
    await fleets.observe_instances(
        ProviderName.CHROMIUM,
        [
            first_observation,
            ObservedInstance(
                instance_id="chromium-two",
                endpoint="ws://chromium-two:9222",
                state=FleetInstanceState.READY,
            ),
        ],
        platform="test",
        observation_ttl_seconds=10,
    )
    third = await asyncio.wait_for(third_task, timeout=1)
    assert third.attempt.provider_instance_id == "chromium-two"
    assert third.attempt.endpoint == "ws://chromium-two:9222"

    for attempt in (first, second, third):
        await attempt.release()
    for session in sessions:
        await session.release()


@pytest.mark.asyncio
async def test_disabled_managed_fleet_does_not_assign_observed_instance(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
) -> None:
    fleets = FleetRepository(database_sessions)
    await fleets.ensure_fleet(
        ProviderName.CHROMIUM,
        minimum_instances=1,
        maximum_instances=1,
        session_capacity_per_instance=1,
        scale_down_cooldown_seconds=1,
    )
    await fleets.observe_instances(
        ProviderName.CHROMIUM,
        [
            ObservedInstance(
                instance_id="disabled-chromium",
                endpoint="ws://disabled-chromium:9222",
                state=FleetInstanceState.READY,
            )
        ],
        platform="test",
        observation_ttl_seconds=10,
    )
    await fleets.update_configuration(
        ProviderName.CHROMIUM,
        {"enabled": False},
        actor="test",
    )
    session = await admit(session_repository, admission_settings, "disabled")
    _, resolved = await requested_and_resolved()
    settings = admission_settings.model_copy(
        update={"provider_queue_timeout_seconds": 0.05}
    )

    with pytest.raises(ProviderQueueTimeout):
        await attempt_admission(attempt_repository, settings).acquire(
            session.session,
            resolved,
        )

    assert await attempt_repository.active_count(ProviderName.CHROMIUM) == 0
    await session.release(failed=True, reason="provider_queue_timeout")
