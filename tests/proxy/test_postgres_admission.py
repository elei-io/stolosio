import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import AcquisitionAttempt, GatewaySession, SessionEventRecord
from backend.fleet import FleetInstanceState, FleetRepository, ObservedInstance
from backend.messaging import PollingNotifier
from backend.proxy.attempts import AttemptAdmission
from backend.proxy.contracts import ProviderName, SessionState, StolosioSession
from backend.proxy.errors import (
    GatewayCapacityFull,
    ProviderQueueFull,
    ProviderQueueTimeout,
)
from backend.proxy.external_capacity import ExternalCapacityRepository
from backend.proxy.postgres import (
    PostgresAttemptRepository,
    PostgresSessionRepository,
    SessionRepositorySettings,
)
from backend.proxy.routing import RoutingRepository
from backend.proxy.sessions import SessionAdmission
from backend.proxy.settings import stolosio_settings_resolver
from backend.settings import Settings


@pytest.fixture
def admission_settings() -> Settings:
    return Settings(
        stolosio_max_active_sessions=8,
        session_lease_seconds=2,
        session_heartbeat_seconds=0.1,
        provider_queue_poll_ms=10,
        provider_queue_timeout_seconds=1,
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


@pytest_asyncio.fixture
async def managed_browserless(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    fleets = FleetRepository(database_sessions)
    await fleets.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=1,
        session_capacity_per_instance=1,
        scale_down_cooldown_seconds=1,
        max_queued_attempts=2,
    )
    await fleets.observe_instances(
        ProviderName.BROWSERLESS,
        [
            ObservedInstance(
                instance_id="browserless-test",
                endpoint="ws://browserless-test:3000",
                state=FleetInstanceState.READY,
                session_capacity=1,
            )
        ],
        platform="test",
        observation_ttl_seconds=10,
    )


@pytest_asyncio.fixture(autouse=True)
async def external_provider_capacity(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    capacity = ExternalCapacityRepository(database_sessions)
    await capacity.ensure(
        ProviderName.HTTP,
        enabled=True,
        max_active_sessions=100,
        max_queued_attempts=100,
    )
    await capacity.ensure(
        ProviderName.BROWSERBASE,
        enabled=True,
        max_active_sessions=5,
        max_queued_attempts=100,
    )


async def requested_and_resolved():
    return await stolosio_settings_resolver.resolve([("stolosio.provider.slug", "browserless")])


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
    settings = admission_settings.model_copy(update={"stolosio_max_active_sessions": 2})
    first = await admit(session_repository, settings, "one")
    second = await admit(session_repository, settings, "two")

    with pytest.raises(GatewayCapacityFull):
        await admit(session_repository, settings, "three")

    assert await session_repository.active_count() == 2
    await first.release()
    await second.release()


@pytest.mark.asyncio
async def test_provider_queue_full_releases_no_other_session_capacity(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
    managed_browserless: None,
) -> None:
    settings = admission_settings
    await FleetRepository(database_sessions).update_configuration(
        ProviderName.BROWSERLESS,
        {"max_queued_attempts": 0},
        actor="test",
    )
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
    assert await attempt_repository.active_count(ProviderName.BROWSERLESS) == 1
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
    _, http = await stolosio_settings_resolver.resolve([("stolosio.provider.slug", "http")])
    _, browserbase = await stolosio_settings_resolver.resolve(
        [("stolosio.provider.slug", "browserbase")]
    )
    admissions = attempt_admission(attempt_repository, admission_settings)
    source = await admissions.acquire(session.session, http)
    await source.activate()

    with pytest.raises(
        RuntimeError,
        match="already has a live acquisition attempt",
    ):
        await admissions.acquire(session.session, browserbase)

    replacement = await admissions.acquire(
        session.session,
        browserbase,
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
        ("browserbase", "active"),
    ]

    await replacement.release()
    await source.release()
    await session.release()


@pytest.mark.asyncio
async def test_provider_attempts_are_claimed_in_fifo_order(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
    managed_browserless: None,
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

    assert len(await attempt_repository.queue(ProviderName.BROWSERLESS)) == 2
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
    managed_browserless: None,
) -> None:
    settings = admission_settings.model_copy(update={"provider_queue_timeout_seconds": 0.05})
    _, resolved = await requested_and_resolved()
    first_session = await admit(session_repository, settings, "one")
    waiting_session = await admit(session_repository, settings, "two")
    admissions = attempt_admission(attempt_repository, settings)
    first = await admissions.acquire(first_session.session, resolved)

    with pytest.raises(ProviderQueueTimeout):
        await admissions.acquire(waiting_session.session, resolved)

    assert await attempt_repository.queue(ProviderName.BROWSERLESS) == []
    assert await session_repository.active_count() == 2
    await first.release()
    await first_session.release()
    await waiting_session.release(failed=True, reason="provider_queue_timeout")


@pytest.mark.asyncio
async def test_cancelling_waiter_removes_provider_attempt(
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
    managed_browserless: None,
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

    assert await attempt_repository.queue(ProviderName.BROWSERLESS) == []
    await first.release()
    await first_session.release()
    await waiting_session.release()


@pytest.mark.asyncio
async def test_stale_session_lease_frees_global_and_provider_capacity(
    database_sessions: async_sessionmaker[AsyncSession],
    managed_browserless: None,
) -> None:
    settings = Settings(
        stolosio_max_active_sessions=1,
        session_lease_seconds=2,
        session_heartbeat_seconds=10,
    )
    sessions = PostgresSessionRepository(
        database_sessions, SessionRepositorySettings(lease_seconds=2)
    )
    attempts = PostgresAttemptRepository(database_sessions)
    await RoutingRepository(database_sessions).ensure_defaults()
    first = await admit(sessions, settings, "dead")
    _, resolved = await requested_and_resolved()
    first_attempt = await attempt_admission(attempts, settings).acquire(first.session, resolved)
    provider_started_at = datetime.now(UTC) - timedelta(seconds=5)
    await first_attempt.bind_provider_session(
        provider_session_id="expired-browser",
        provider_started_at=provider_started_at,
    )
    await first_attempt.activate()
    first._heartbeat_task.cancel()
    await asyncio.gather(first._heartbeat_task, return_exceptions=True)
    async with database_sessions.begin() as database:
        row = await database.get(GatewaySession, first.session.session_id)
        assert row is not None
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    second = await admit(sessions, settings, "live")
    second_attempt = await attempt_admission(attempts, settings).acquire(second.session, resolved)

    assert await sessions.active_count() == 1
    assert await attempts.active_count(ProviderName.BROWSERLESS) == 1
    assert (await sessions.read_session(first.session.session_id))["state"] == "failed"
    async with database_sessions() as database:
        expired = await database.get(
            AcquisitionAttempt,
            first_attempt.attempt.attempt_id,
        )
    assert expired is not None
    assert expired.state == "failed"
    assert expired.capacity_occupied_ms is not None
    assert expired.browser_connected_ms is not None
    assert expired.provider_ended_at is not None
    assert expired.provider_reported_ms is not None
    assert expired.modeled_cost_units is not None
    await first_attempt.release(failed=True, reason="session_lease_expired")
    await second_attempt.release()
    await second.release()


@pytest.mark.asyncio
async def test_stale_token_cannot_heartbeat_or_release(
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    lease = await admit(session_repository, admission_settings, "owner")
    stale = StolosioSession(
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
    managed_browserless: None,
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
        "attempt.connected",
        "session.open",
        "attempt.closed",
        "session.closed",
    ]
    assert all(row.published_at is None for row in rows)


@pytest.mark.asyncio
async def test_session_release_finishes_a_live_attempt_as_cleanup_backstop(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
    managed_browserless: None,
) -> None:
    session = await admit(session_repository, admission_settings, "cleanup")
    _, resolved = await requested_and_resolved()
    attempt = await attempt_admission(attempt_repository, admission_settings).acquire(
        session.session, resolved
    )
    await attempt.activate()
    await session.open()

    await session.release()

    async with database_sessions() as database:
        row = await database.get(
            AcquisitionAttempt,
            attempt.attempt.attempt_id,
        )
        events = list(
            await database.scalars(
                select(SessionEventRecord.event_type)
                .where(SessionEventRecord.session_id == session.session.session_id)
                .order_by(SessionEventRecord.id)
            )
        )
    assert row is not None
    assert row.state == "completed"
    assert row.finished_at is not None
    assert row.terminal_reason == "client_disconnected"
    assert row.capacity_occupied_ms is not None
    assert events[-2:] == [
        "attempt.closed",
        "session.closed",
    ]


@pytest.mark.asyncio
async def test_retried_release_repairs_live_attempt_on_closed_session(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    session = await admit(session_repository, admission_settings, "repair")
    await session.open()
    await session.release()
    attempt_id = str(uuid4())
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=attempt_id,
                session_id=session.session.session_id,
                ordinal=1,
                provider="browserbase",
                resolved_settings={"stolosio.provider.slug": "browserbase"},
                setting_sources={"stolosio.provider.slug": "auto"},
                state="active",
                created_at=datetime.now(UTC) - timedelta(seconds=2),
                active_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )

    assert await session_repository.release(
        session.session,
        failed=False,
        reason="client_disconnected",
    )

    async with database_sessions() as database:
        row = await database.get(AcquisitionAttempt, attempt_id)
    assert row is not None
    assert row.state == "completed"
    assert row.finished_at is not None
    assert row.terminal_reason == "client_disconnected"


@pytest.mark.asyncio
async def test_client_reference_is_optional_session_metadata(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    admission_settings: Settings,
) -> None:
    reference = uuid4()
    requested, _ = await stolosio_settings_resolver.resolve(
        [("stolosio.session.reference", str(reference))]
    )
    lease = await SessionAdmission(session_repository, admission_settings).admit(requested)

    async with database_sessions() as database:
        row = await database.get(GatewaySession, lease.session.session_id)
    assert row is not None
    assert row.client_reference == str(reference)
    assert row.requested_settings["stolosio.session.reference"] == str(reference)
    await lease.release()


@pytest.mark.asyncio
async def test_attempt_rows_hold_provider_state_not_gateway_sessions(
    database_sessions: async_sessionmaker[AsyncSession],
    session_repository: PostgresSessionRepository,
    attempt_repository: PostgresAttemptRepository,
    admission_settings: Settings,
    managed_browserless: None,
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
    assert attempt_row is not None and attempt_row.provider == "browserless"
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
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=2,
        session_capacity_per_instance=2,
        scale_down_cooldown_seconds=1,
    )
    first_observation = ObservedInstance(
        instance_id="browserless-one",
        endpoint="ws://browserless-one:3000",
        state=FleetInstanceState.READY,
    )
    await fleets.observe_instances(
        ProviderName.BROWSERLESS,
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
    assert first.attempt.provider_instance_id == "browserless-one"
    assert second.attempt.provider_instance_id == "browserless-one"

    third_task = asyncio.create_task(admissions.acquire(sessions[2].session, resolved))
    await asyncio.sleep(0.03)
    assert not third_task.done()
    await fleets.observe_instances(
        ProviderName.BROWSERLESS,
        [
            first_observation,
            ObservedInstance(
                instance_id="browserless-two",
                endpoint="ws://browserless-two:3000",
                state=FleetInstanceState.READY,
            ),
        ],
        platform="test",
        observation_ttl_seconds=10,
    )
    third = await asyncio.wait_for(third_task, timeout=1)
    assert third.attempt.provider_instance_id == "browserless-two"
    assert third.attempt.endpoint == "ws://browserless-two:3000"

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
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=1,
        session_capacity_per_instance=1,
        scale_down_cooldown_seconds=1,
    )
    await fleets.observe_instances(
        ProviderName.BROWSERLESS,
        [
            ObservedInstance(
                instance_id="disabled-browserless",
                endpoint="ws://disabled-browserless:3000",
                state=FleetInstanceState.READY,
            )
        ],
        platform="test",
        observation_ttl_seconds=10,
    )
    await fleets.update_configuration(
        ProviderName.BROWSERLESS,
        {"enabled": False},
        actor="test",
    )
    session = await admit(session_repository, admission_settings, "disabled")
    _, resolved = await requested_and_resolved()
    with pytest.raises(ProviderQueueFull):
        await attempt_admission(attempt_repository, admission_settings).acquire(
            session.session,
            resolved,
        )

    assert await attempt_repository.active_count(ProviderName.BROWSERLESS) == 0
    await session.release(failed=True, reason="provider_queue_timeout")
