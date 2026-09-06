import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderCostStat,
    GatewaySession,
    HealthProbe,
    ProviderCommandCostStat,
    SessionEventRecord,
)
from backend.debug import HistoricalDebugTimeline
from backend.events import EventType, SessionEvent
from backend.metrics import FleetSnapshotService
from backend.proxy.contracts import AttemptState, ProviderAttempt, ProviderName
from backend.proxy.postgres import PostgresAttemptRepository
from backend.settings import Settings
from backend.workers.maintenance import recorder as recorder_module
from backend.workers.maintenance.recorder import EventRecorder
from backend.workers.maintenance.retention import RetentionJob


async def add_session(
    sessions: async_sessionmaker[AsyncSession],
    session_id: UUID,
    *,
    state: str = "open",
    lease_expires_at: datetime | None = None,
    closed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> None:
    async with sessions.begin() as database:
        database.add(
            GatewaySession(
                id=str(session_id),
                owner_id="test-owner",
                lease_token=str(uuid4()),
                requested_settings={},
                state=state,
                **({"created_at": created_at} if created_at is not None else {}),
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
                provider="browserless",
                resolved_settings={"stolosio.provider.slug": "browserless"},
                setting_sources={"stolosio.provider.slug": "auto"},
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
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(attempt_id),
                session_id=str(session_id),
                ordinal=1,
                provider="browserless",
                resolved_settings={"stolosio.provider.slug": "browserless"},
                setting_sources={"stolosio.provider.slug": "auto"},
                state="closed",
                finished_at=now,
                browser_connected_ms=100,
                chargeable_time_ms=100,
                modeled_cost_units=20,
                command_summary={
                    "methods": {
                        "Page.navigate": {
                            "count": 1,
                            "failed_count": 0,
                            "duration_ms": 90,
                            "provider_latency_ms": 80,
                            "stolosio_queue_ms": 10,
                        }
                    }
                },
            )
        )
    recorder = EventRecorder(database_sessions)
    assert await recorder.known_session_ids({str(session_id), str(uuid4())}) == {str(session_id)}
    navigation = SessionEvent.create(
        EventType.NAVIGATION_RESPONSE,
        session_id,
        provider=ProviderName.BROWSERLESS,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={"url": "https://example.com/", "status": 200},
    )
    summary = SessionEvent.create(
        EventType.COMMAND_SUMMARY,
        session_id,
        provider=ProviderName.BROWSERLESS,
        attempt_id=attempt_id,
        occurred_at=now + timedelta(milliseconds=25),
        payload={
            "methods": {
                "Page.navigate": {
                    "count": 1,
                    "failed_count": 0,
                    "duration_ms": 90,
                    "provider_latency_ms": 80,
                    "stolosio_queue_ms": 10,
                }
            }
        },
    )

    assert await recorder.record([summary, navigation, navigation]) == 2
    assert await recorder.record([summary, navigation]) == 0

    async with database_sessions() as database:
        assert await database.scalar(select(func.count()).select_from(SessionEventRecord)) == 2
        domain = await database.scalar(select(Domain).where(Domain.hostname == "example.com"))
        stats = list(
            await database.scalars(
                select(ProviderCommandCostStat).order_by(ProviderCommandCostStat.method)
            )
        )
        domain_cost = await database.scalar(select(DomainProviderCostStat))
        attempt = await database.get(AcquisitionAttempt, str(attempt_id))
        summary_is_sql_null = await database.scalar(
            select(AcquisitionAttempt.command_summary.is_(None)).where(
                AcquisitionAttempt.id == str(attempt_id)
            )
        )
    assert domain is not None and domain.session_count == 1
    assert [(stat.method, stat.attributed_browser_time_ms) for stat in stats] == [
        ("Page.navigate", 80),
        ("__session_overhead__", 20),
    ]
    command_stat = stats[0]
    assert command_stat.command_count == 1
    assert command_stat.total_duration_ms == 90
    assert command_stat.total_provider_latency_ms == 80
    assert command_stat.total_stolosio_queue_ms == 10
    assert command_stat.attributed_cost_units == 16
    assert domain_cost is not None
    assert attempt is not None
    assert attempt.command_cost_projected is True
    assert attempt.command_summary is None
    assert summary_is_sql_null is True
    assert (domain_cost.observed_attempt_count, domain_cost.total_cost_units) == (
        1,
        20,
    )

    timeline = await HistoricalDebugTimeline(database_sessions).events(session_id)
    assert {event.event_type for event in timeline} == {
        "navigation.response",
        "command.summary",
    }
    assert all("recommendation" not in event.payload for event in timeline)


@pytest.mark.asyncio
async def test_http_command_summary_never_attributes_browser_time(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)
    await add_session(database_sessions, session_id)
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(attempt_id),
                session_id=str(session_id),
                ordinal=1,
                provider="http",
                resolved_settings={},
                setting_sources={},
                state="closed",
                finished_at=now,
                browser_connected_ms=100,
                chargeable_time_ms=100,
                modeled_cost_units=2,
                command_summary={
                    "methods": {
                        "Page.navigate": {
                            "count": 1,
                            "failed_count": 0,
                            "duration_ms": 80,
                            "provider_latency_ms": 75,
                            "stolosio_queue_ms": 5,
                        }
                    }
                },
            )
        )
    summary = SessionEvent.create(
        EventType.COMMAND_SUMMARY,
        session_id,
        provider=ProviderName.HTTP,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={
            "methods": {
                "Page.navigate": {
                    "count": 1,
                    "failed_count": 0,
                    "duration_ms": 80,
                    "provider_latency_ms": 75,
                    "stolosio_queue_ms": 5,
                }
            }
        },
    )

    assert await EventRecorder(database_sessions).record([summary]) == 1

    async with database_sessions() as database:
        rows = list(
            await database.scalars(
                select(ProviderCommandCostStat).order_by(ProviderCommandCostStat.method)
            )
        )
    assert [(row.method, row.attributed_browser_time_ms) for row in rows] == [
        ("Page.navigate", 0),
        ("__session_overhead__", 0),
    ]
    assert rows[0].attributed_cost_units == 0
    assert rows[1].attributed_cost_units == 2


@pytest.mark.asyncio
async def test_terminal_outbox_redelivery_projects_stored_command_summary(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)
    await add_session(database_sessions, session_id)
    terminal = SessionEvent.create(
        EventType.ATTEMPT_CLOSED,
        session_id,
        provider=ProviderName.BROWSERLESS,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={},
    )
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(attempt_id),
                session_id=str(session_id),
                ordinal=1,
                provider="browserless",
                resolved_settings={},
                setting_sources={},
                state="completed",
                finished_at=now,
                browser_connected_ms=1_000,
                chargeable_time_ms=2_000,
                modeled_cost_units=4,
                command_summary={
                    "methods": {
                        "Runtime.evaluate": {
                            "count": 1,
                            "failed_count": 0,
                            "duration_ms": 500,
                            "provider_latency_ms": 500,
                            "stolosio_queue_ms": 0,
                        }
                    }
                },
            )
        )
        database.add(
            SessionEventRecord(
                event_id=terminal.event_id,
                schema_version=terminal.schema_version,
                session_id=str(session_id),
                attempt_id=str(attempt_id),
                event_type=terminal.event_type,
                provider=ProviderName.BROWSERLESS.value,
                occurred_at=now,
                payload={},
            )
        )

    assert await EventRecorder(database_sessions).record([terminal]) == 0

    async with database_sessions() as database:
        attempt = await database.get(AcquisitionAttempt, str(attempt_id))
        rows = {
            row.method: row
            for row in await database.scalars(select(ProviderCommandCostStat))
        }
    assert attempt is not None and attempt.command_cost_projected
    assert rows["Runtime.evaluate"].attributed_cost_units == 1
    assert rows["__session_overhead__"].attributed_cost_units == 3


@pytest.mark.asyncio
async def test_command_cost_projection_bounds_method_identity_and_tracks_interruptions(
    database_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recorder_module,
        "_MAX_RETAINED_METHODS_PER_PROVIDER",
        3,
    )
    session_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)
    await add_session(database_sessions, session_id)
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(attempt_id),
                session_id=str(session_id),
                ordinal=1,
                provider="browserless",
                resolved_settings={},
                setting_sources={},
                state="closed",
                finished_at=now,
                browser_connected_ms=40,
                chargeable_time_ms=40,
                modeled_cost_units=4,
                command_summary={
                    "methods": {
                        method: {
                            "count": 1,
                            "failed_count": int(method == "Domain.third"),
                            "interrupted_count": int(method == "Domain.fourth"),
                            "duration_ms": 10,
                            "provider_latency_ms": 10,
                            "stolosio_queue_ms": 0,
                        }
                        for method in (
                            "Domain.first",
                            "Domain.second",
                            "Domain.third",
                            "Domain.fourth",
                        )
                    }
                },
            )
        )
    summary = SessionEvent.create(
        EventType.COMMAND_SUMMARY,
        session_id,
        provider=ProviderName.BROWSERLESS,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={
            "methods": {
                method: {
                    "count": 1,
                    "failed_count": int(method == "Domain.third"),
                    "interrupted_count": int(method == "Domain.fourth"),
                    "duration_ms": 10,
                    "provider_latency_ms": 10,
                    "stolosio_queue_ms": 0,
                }
                for method in (
                    "Domain.first",
                    "Domain.second",
                    "Domain.third",
                    "Domain.fourth",
                )
            }
        },
    )

    assert await EventRecorder(database_sessions).record([summary]) == 1

    async with database_sessions() as database:
        rows = {row.method: row for row in await database.scalars(select(ProviderCommandCostStat))}
    assert len(set(rows) - {"__other__"}) == 2
    assert "__other__" in rows
    assert rows["__other__"].command_count == 2
    assert sum(row.failed_count for row in rows.values()) == 1
    assert sum(row.interrupted_count for row in rows.values()) == 1
    assert rows["__other__"].attributed_browser_time_ms == 20


@pytest.mark.asyncio
async def test_recorder_commits_raw_events_before_starting_projections(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    await add_session(database_sessions, session_id)
    event = SessionEvent.create(
        EventType.NAVIGATION_RESPONSE,
        session_id,
        provider=ProviderName.HTTP,
        payload={"url": "https://example.com/", "status": 200},
    )
    observed_persisted_event = False

    class TrackingRecorder(EventRecorder):
        async def _project_events(self, events: list[SessionEvent]) -> None:
            nonlocal observed_persisted_event
            async with database_sessions() as database:
                observed_persisted_event = (
                    await database.scalar(
                        select(func.count())
                        .select_from(SessionEventRecord)
                        .where(SessionEventRecord.event_id == event.event_id)
                    )
                    == 1
                )
            await super()._project_events(events)

    assert await TrackingRecorder(database_sessions).record([event]) == 1
    assert observed_persisted_event
    async with database_sessions() as database:
        domain = await database.scalar(
            select(Domain).where(Domain.hostname == "example.com")
        )
    assert domain is not None


@pytest.mark.asyncio
async def test_recorder_redelivery_recovers_after_projection_phase_failure(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    attempt_id = uuid4()
    now = datetime.now(UTC)
    await add_session(database_sessions, session_id)
    async with database_sessions.begin() as database:
        database.add(
            AcquisitionAttempt(
                id=str(attempt_id),
                session_id=str(session_id),
                ordinal=1,
                provider="http",
                resolved_settings={},
                setting_sources={},
                state="completed",
                finished_at=now,
                chargeable_time_ms=100,
                modeled_cost_units=2,
                command_summary={"methods": {}},
            )
        )
    navigation = SessionEvent.create(
        EventType.NAVIGATION_RESPONSE,
        session_id,
        provider=ProviderName.HTTP,
        attempt_id=attempt_id,
        occurred_at=now,
        payload={"url": "https://example.com/", "status": 200},
    )
    terminal = SessionEvent.create(
        EventType.ATTEMPT_CLOSED,
        session_id,
        provider=ProviderName.HTTP,
        attempt_id=attempt_id,
        occurred_at=now,
    )

    class FailingRecorder(EventRecorder):
        async def _project_attempts(
            self,
            attempt_domains: dict[str, int],
            summary_attempt_ids: set[str],
        ) -> None:
            raise RuntimeError("projection interrupted")

    with pytest.raises(RuntimeError, match="projection interrupted"):
        await FailingRecorder(database_sessions).record([navigation, terminal])

    async with database_sessions() as database:
        assert (
            await database.scalar(select(func.count()).select_from(SessionEventRecord))
            == 2
        )
        domain = await database.scalar(
            select(Domain).where(Domain.hostname == "example.com")
        )
        attempt = await database.get(AcquisitionAttempt, str(attempt_id))
    assert domain is not None and domain.session_count == 1
    assert attempt is not None and attempt.domain_id is None

    assert await EventRecorder(database_sessions).record([navigation, terminal]) == 0

    async with database_sessions() as database:
        attempt = await database.get(AcquisitionAttempt, str(attempt_id))
        domain_cost = await database.scalar(select(DomainProviderCostStat))
    assert attempt is not None
    assert attempt.domain_id == domain.id
    assert attempt.cost_projected
    assert attempt.command_cost_projected
    assert attempt.command_summary is None
    assert domain_cost is not None
    assert domain_cost.observed_attempt_count == 1
    assert domain_cost.total_cost_units == 2


@pytest.mark.asyncio
async def test_same_domain_projection_does_not_deadlock_attempt_finalization(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    attempts: list[ProviderAttempt] = []
    navigation_events: list[SessionEvent] = []
    async with database_sessions.begin() as database:
        attempt_rows: list[AcquisitionAttempt] = []
        for ordinal in range(32):
            session_id = uuid4()
            attempt_id = uuid4()
            database.add(
                GatewaySession(
                    id=str(session_id),
                    owner_id="test-owner",
                    lease_token=str(uuid4()),
                    requested_settings={},
                    state="open",
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            attempt_rows.append(
                AcquisitionAttempt(
                    id=str(attempt_id),
                    session_id=str(session_id),
                    ordinal=1,
                    provider="http",
                    resolved_settings={},
                    setting_sources={},
                    state="active",
                    acquiring_at=now - timedelta(milliseconds=100),
                    active_at=now - timedelta(milliseconds=90),
                )
            )
            attempts.append(
                ProviderAttempt(
                    attempt_id=str(attempt_id),
                    session_id=str(session_id),
                    ordinal=ordinal + 1,
                    provider=ProviderName.HTTP,
                    state=AttemptState.ACTIVE,
                )
            )
            navigation_events.append(
                SessionEvent.create(
                    EventType.NAVIGATION_RESPONSE,
                    session_id,
                    provider=ProviderName.HTTP,
                    attempt_id=attempt_id,
                    occurred_at=now,
                    payload={
                        "url": f"https://same-domain.test/page/{ordinal}",
                        "status": 200,
                    },
                )
            )
        await database.flush()
        database.add_all(attempt_rows)

    recorder = EventRecorder(database_sessions)
    repository = PostgresAttemptRepository(database_sessions)
    async with asyncio.timeout(10):
        recorded, *released = await asyncio.gather(
            recorder.record(navigation_events),
            *(
                repository.finish(
                    attempt,
                    failed=False,
                    reason="client_disconnected",
                    command_summary={"methods": {}},
                )
                for attempt in attempts
            ),
        )

    assert recorded == len(navigation_events)
    assert all(released)
    async with database_sessions() as database:
        domain = await database.scalar(
            select(Domain).where(Domain.hostname == "same-domain.test")
        )
        assert domain is not None
        linked_attempts = await database.scalar(
            select(func.count())
            .select_from(AcquisitionAttempt)
            .where(
                AcquisitionAttempt.domain_id == domain.id,
                AcquisitionAttempt.state == AttemptState.COMPLETED,
            )
        )
    assert domain.session_count == len(attempts)
    assert linked_attempts == len(attempts)


@pytest.mark.asyncio
async def test_recorder_keeps_preselection_events_providerless(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    await add_session(database_sessions, session_id)
    event = SessionEvent.create(EventType.SESSION_OPEN, session_id)

    assert await EventRecorder(database_sessions).record([event]) == 1

    async with database_sessions() as database:
        row = await database.scalar(select(SessionEventRecord))
    assert row is not None
    assert row.provider is None

    timeline = await HistoricalDebugTimeline(database_sessions).events(session_id)
    assert timeline[0].provider is None


@pytest.mark.asyncio
async def test_recorder_keeps_probe_sessions_out_of_domain_projections(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    source_session_id = uuid4()
    probe_session_id = uuid4()
    probe_id = str(uuid4())
    now = datetime.now(UTC)
    await add_session(database_sessions, source_session_id)
    await add_session(database_sessions, probe_session_id)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="source.test",
            first_seen_at=now,
            last_seen_at=now,
            session_count=1,
        )
        database.add(domain)
        await database.flush()
        candidate = await database.get(
            GatewaySession,
            str(probe_session_id),
        )
        assert candidate is not None
        candidate.client_reference = probe_id
        database.add(
            HealthProbe(
                id=probe_id,
                domain_id=domain.id,
                source_session_id=str(source_session_id),
                candidate_provider="http",
                trigger="manual",
                target_url="https://candidate.test/",
                state="running",
                created_at=now,
            )
        )

    recorder = EventRecorder(database_sessions)
    navigation = SessionEvent.create(
        EventType.NAVIGATION_RESPONSE,
        probe_session_id,
        provider=ProviderName.HTTP,
        occurred_at=now,
        payload={"url": "https://candidate.test/", "status": 200},
    )
    command = SessionEvent.create(
        EventType.COMMAND_SUMMARY,
        probe_session_id,
        provider=ProviderName.HTTP,
        attempt_id=uuid4(),
        occurred_at=now,
        payload={
            "methods": {
                "Runtime.callFunctionOn": {
                    "count": 1,
                    "failed_count": 0,
                    "duration_ms": 1,
                    "provider_latency_ms": 1,
                    "stolosio_queue_ms": 0,
                }
            }
        },
    )

    assert await recorder.record([navigation, command]) == 2

    async with database_sessions() as database:
        candidate_domain = await database.scalar(
            select(Domain).where(Domain.hostname == "candidate.test")
        )
        command_cost_count = await database.scalar(
            select(func.count()).select_from(ProviderCommandCostStat)
        )
        event_count = await database.scalar(
            select(func.count())
            .select_from(SessionEventRecord)
            .where(SessionEventRecord.session_id == str(probe_session_id))
        )
    assert candidate_domain is None
    assert command_cost_count == 0
    assert event_count == 2


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

    assert [snapshot.provider.value for snapshot in snapshots] == [
        "http",
        "browserless",
        "browserbase",
    ]
    browserless = next(
        snapshot for snapshot in snapshots if snapshot.provider is ProviderName.BROWSERLESS
    )
    assert browserless.active_attempts == 1
    assert browserless.queued_attempts == 1
    assert browserless.oldest_queued_attempt_seconds >= 4
    assert all(
        snapshot.active_attempts == 0
        for snapshot in snapshots
        if snapshot.provider is not ProviderName.BROWSERLESS
    )


@pytest.mark.asyncio
async def test_gateway_snapshot_reports_recent_and_active_sessions(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    await add_session(
        database_sessions,
        uuid4(),
        lease_expires_at=now + timedelta(minutes=1),
    )
    await add_session(
        database_sessions,
        uuid4(),
        state="closed",
        closed_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
    )
    await add_session(
        database_sessions,
        uuid4(),
        state="closed",
        closed_at=now - timedelta(days=2),
        created_at=now - timedelta(days=2),
    )

    snapshot = await FleetSnapshotService(database_sessions, Settings()).gateway_snapshot()

    assert snapshot.active_sessions == 1
    assert snapshot.sessions_last_24h == 2


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
                    provider="browserless",
                    occurred_at=now - timedelta(days=3),
                    payload={},
                    published_at=now - timedelta(days=3),
                ),
                SessionEventRecord(
                    session_id=str(session_id),
                    event_type="session.closed",
                    provider="browserless",
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
