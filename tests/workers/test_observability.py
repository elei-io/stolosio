from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    GatewaySession,
    SessionEventRecord,
)
from backend.debug import HistoricalDebugTimeline
from backend.events import EventType, SessionEvent
from backend.metrics import FleetSnapshotService
from backend.proxy.contracts import ProviderName
from backend.settings import Settings
from backend.workers.maintenance.recorder import EventRecorder
from backend.workers.maintenance.retention import RetentionJob


async def add_session(
    sessions: async_sessionmaker[AsyncSession],
    session_id: UUID,
    *,
    state: str = "open",
    lease_expires_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> None:
    async with sessions.begin() as database:
        database.add(
            GatewaySession(
                id=str(session_id),
                owner_id="test-owner",
                lease_token=str(uuid4()),
                requested_settings={},
                state=state,
                lease_expires_at=lease_expires_at,
                closed_at=closed_at,
            )
        )


async def add_attempt(
    sessions: async_sessionmaker[AsyncSession],
    session_id: UUID,
    *,
    state: str,
    queued_at: datetime | None = None,
) -> None:
    async with sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(uuid4()),
                session_id=str(session_id),
                ordinal=1,
                provider="chromium",
                resolved_settings={"harbor.provider.slug": "chromium"},
                setting_sources={"harbor.provider.slug": "auto"},
                state=state,
                queued_at=queued_at,
            )
        )


@pytest.mark.asyncio
async def test_recorder_is_idempotent_and_projects_domain_evidence(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)
    await add_session(database_sessions, session_id)
    recorder = EventRecorder(database_sessions)
    assert await recorder.known_session_ids({str(session_id), str(uuid4())}) == {str(session_id)}
    connected = SessionEvent.create(
        EventType.ATTEMPT_CONNECTED,
        session_id,
        provider=ProviderName.CHROMIUM,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={"duration_ms": 10},
    )
    started = SessionEvent.create(
        EventType.ATTEMPT_STARTED,
        session_id,
        provider=ProviderName.CHROMIUM,
        attempt_id=attempt_id,
        occurred_at=now - timedelta(seconds=1),
    )
    command = SessionEvent.create(
        EventType.COMMAND_RECEIVED,
        session_id,
        provider=ProviderName.CHROMIUM,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={
            "command_id": 1,
            "method": "Page.navigate",
            "domain": "example.com",
        },
    )

    assert await recorder.record([connected, connected, command]) == 2
    assert await recorder.record([started, command]) == 1

    async with database_sessions() as database:
        assert await database.scalar(select(func.count()).select_from(SessionEventRecord)) == 3
        domain = await database.scalar(select(Domain).where(Domain.hostname == "example.com"))
        stat = await database.scalar(select(DomainCommandStat))
    assert domain is not None and domain.session_count == 1
    assert stat is not None
    assert (stat.command_count, stat.session_count) == (1, 1)

    timeline = await HistoricalDebugTimeline(database_sessions).events(session_id)
    assert timeline[0].event_type == "attempt.started"
    assert {event.event_type for event in timeline[1:]} == {
        "attempt.connected",
        "command.received",
    }
    assert all("recommendation" not in event.payload for event in timeline)


@pytest.mark.asyncio
async def test_fleet_snapshot_includes_every_provider_and_only_live_leases(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    active_session = uuid4()
    queued_session = uuid4()
    expired_session = uuid4()
    await add_session(
        database_sessions, active_session, lease_expires_at=now + timedelta(minutes=1)
    )
    await add_attempt(database_sessions, active_session, state="active")
    await add_session(
        database_sessions, queued_session, lease_expires_at=now + timedelta(minutes=1)
    )
    await add_attempt(
        database_sessions,
        queued_session,
        state="queued",
        queued_at=now - timedelta(seconds=4),
    )
    await add_session(
        database_sessions, expired_session, lease_expires_at=now - timedelta(minutes=1)
    )
    await add_attempt(database_sessions, expired_session, state="active")

    snapshots = await FleetSnapshotService(database_sessions, Settings()).snapshot()

    assert [snapshot.provider for snapshot in snapshots] == list(ProviderName)
    chromium = snapshots[0]
    assert chromium.active_attempts == 1
    assert chromium.queued_attempts == 1
    assert chromium.oldest_queued_attempt_seconds >= 4
    assert all(snapshot.active_attempts == 0 for snapshot in snapshots[1:])


@pytest.mark.asyncio
async def test_retention_keeps_unpublished_outbox_rows(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    session_id = uuid4()
    await add_session(database_sessions, session_id)
    async with database_sessions.begin() as database:
        database.add_all(
            [
                SessionEventRecord(
                    session_id=str(session_id),
                    event_type="session.open",
                    provider="chromium",
                    occurred_at=now - timedelta(days=3),
                    payload={},
                    published_at=now - timedelta(days=3),
                ),
                SessionEventRecord(
                    session_id=str(session_id),
                    event_type="session.closed",
                    provider="chromium",
                    occurred_at=now - timedelta(days=3),
                    payload={},
                    published_at=None,
                ),
            ]
        )
    retention = RetentionJob(
        database_sessions,
        event_days=1,
        terminal_session_days=90,
        domain_days=365,
        batch_size=1,
    )

    deleted = await retention.run_once()

    assert deleted["session_events"] == 1
    async with database_sessions() as database:
        remaining = list(await database.scalars(select(SessionEventRecord)))
    assert len(remaining) == 1
    assert remaining[0].published_at is None
