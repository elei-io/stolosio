from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    GatewaySession,
    GatewayState,
    SessionEventRecord,
)
from backend.proxy.contracts import AttemptState, SessionState, StolosioSession
from backend.proxy.postgres.usage import finalize_attempt_usage


class SessionAdmissionStatus(StrEnum):
    ADMITTED = "admitted"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class SessionRepositorySettings:
    lease_seconds: float


_LIVE_SESSION_STATES = (
    SessionState.ADMITTED.value,
    SessionState.OPEN.value,
    SessionState.CLOSING.value,
)
_LIVE_ATTEMPT_STATES = (
    AttemptState.QUEUED.value,
    AttemptState.ACQUIRING.value,
    AttemptState.ACTIVE.value,
)


class PostgresSessionRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: SessionRepositorySettings,
    ) -> None:
        self._sessions = sessions
        self._settings = settings

    async def admit(
        self,
        session: StolosioSession,
        *,
        max_active: int,
        requested_settings: dict[str, object],
        client_reference: str | None,
    ) -> SessionAdmissionStatus:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            await self._lock_gateway(database)
            await self._expire_stale(database, now)

            row = GatewaySession(
                id=session.session_id,
                owner_id=session.owner_id,
                lease_token=session.lease_token,
                client_reference=client_reference,
                requested_settings=requested_settings,
                state=SessionState.REQUESTED.value,
                created_at=now,
            )
            database.add(row)
            await database.flush()

            active = await database.scalar(
                select(func.count())
                .select_from(GatewaySession)
                .where(
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at > now,
                )
            )
            if int(active or 0) >= max_active:
                row.state = SessionState.FAILED.value
                row.closed_at = now
                row.terminal_reason = "gateway_capacity_full"
                self._event(
                    database,
                    row,
                    SessionState.FAILED,
                    now,
                    reason="gateway_capacity_full",
                )
                return SessionAdmissionStatus.FULL

            row.state = SessionState.ADMITTED.value
            row.admitted_at = now
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            return SessionAdmissionStatus.ADMITTED

    async def heartbeat(self, session: StolosioSession) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await self._owned(database, session, for_update=True)
            if (
                row is None
                or row.state not in _LIVE_SESSION_STATES
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                return False
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            return True

    async def open(self, session: StolosioSession) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await self._owned(database, session, for_update=True)
            if (
                row is None
                or row.state != SessionState.ADMITTED.value
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                return False
            row.state = SessionState.OPEN.value
            row.opened_at = now
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            self._event(database, row, SessionState.OPEN, now)
            return True

    async def release(
        self,
        session: StolosioSession,
        *,
        failed: bool,
        reason: str,
    ) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await self._owned(database, session, for_update=True)
            if row is None:
                return False
            already_terminal = row.state in (
                SessionState.CLOSED.value,
                SessionState.FAILED.value,
            )
            cleanup_failed = failed or row.state == SessionState.FAILED.value
            cleanup_reason = row.terminal_reason or reason
            attempts = list(
                await database.scalars(
                    select(AcquisitionAttempt)
                    .where(
                        AcquisitionAttempt.session_id == row.id,
                        AcquisitionAttempt.state.in_(_LIVE_ATTEMPT_STATES),
                    )
                    .order_by(AcquisitionAttempt.id)
                    .with_for_update()
                )
            )
            for attempt in attempts:
                await finalize_attempt_usage(database, attempt, now)
                attempt.state = (
                    AttemptState.FAILED.value if cleanup_failed else AttemptState.COMPLETED.value
                )
                attempt.finished_at = now
                attempt.terminal_reason = cleanup_reason
                self._attempt_event(
                    database,
                    attempt,
                    "attempt.failed" if cleanup_failed else "attempt.closed",
                    now,
                    reason=cleanup_reason if cleanup_failed else None,
                )
            if already_terminal:
                return True
            if not failed and row.state == SessionState.OPEN.value:
                row.state = SessionState.CLOSING.value
                row.closing_at = now

            terminal = SessionState.FAILED if failed else SessionState.CLOSED
            row.state = terminal.value
            row.closed_at = now
            row.lease_expires_at = None
            row.terminal_reason = reason
            self._event(database, row, terminal, now, reason=reason)
            return True

    async def active_count(self) -> int:
        async with self._sessions() as database:
            now = await self._now(database)
            value = await database.scalar(
                select(func.count())
                .select_from(GatewaySession)
                .where(
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at > now,
                )
            )
            return int(value or 0)

    async def read_session(self, session_id: str) -> dict[str, str]:
        async with self._sessions() as database:
            row = await database.get(GatewaySession, session_id)
            if row is None:
                return {}
            values = {
                "session_id": row.id,
                "owner_id": row.owner_id,
                "lease_token": row.lease_token,
                "state": row.state,
                "terminal_reason": row.terminal_reason,
            }
            return {key: value for key, value in values.items() if value is not None}

    async def events(self, session_id: str) -> list[str]:
        async with self._sessions() as database:
            values = await database.scalars(
                select(SessionEventRecord.event_type)
                .where(SessionEventRecord.session_id == session_id)
                .order_by(SessionEventRecord.id)
            )
            return list(values)

    async def _lock_gateway(self, database: AsyncSession) -> None:
        await database.execute(
            insert(GatewayState)
            .values(key="global")
            .on_conflict_do_nothing(index_elements=[GatewayState.key])
        )
        await database.scalar(
            select(GatewayState).where(GatewayState.key == "global").with_for_update()
        )

    async def _expire_stale(self, database: AsyncSession, now: datetime) -> None:
        rows = list(
            await database.scalars(
                select(GatewaySession)
                .where(
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at <= now,
                )
                .with_for_update()
            )
        )
        for row in rows:
            attempts = list(
                await database.scalars(
                    select(AcquisitionAttempt)
                    .where(
                        AcquisitionAttempt.session_id == row.id,
                        AcquisitionAttempt.state.in_(_LIVE_ATTEMPT_STATES),
                    )
                    .with_for_update()
                )
            )
            for attempt in attempts:
                await finalize_attempt_usage(database, attempt, now)
                attempt.state = AttemptState.FAILED.value
                attempt.finished_at = now
                attempt.terminal_reason = "session_lease_expired"
                self._attempt_event(
                    database,
                    attempt,
                    "attempt.failed",
                    now,
                    reason="session_lease_expired",
                )
            row.state = SessionState.FAILED.value
            row.closed_at = now
            row.lease_expires_at = None
            row.terminal_reason = "session_lease_expired"
            self._event(
                database,
                row,
                SessionState.FAILED,
                now,
                reason="session_lease_expired",
            )

    async def _owned(
        self,
        database: AsyncSession,
        session: StolosioSession,
        *,
        for_update: bool,
    ) -> GatewaySession | None:
        statement = select(GatewaySession).where(
            GatewaySession.id == session.session_id,
            GatewaySession.owner_id == session.owner_id,
            GatewaySession.lease_token == session.lease_token,
        )
        if for_update:
            statement = statement.with_for_update()
        return await database.scalar(statement)

    @staticmethod
    async def _now(database: AsyncSession) -> datetime:
        value = await database.scalar(select(func.clock_timestamp()))
        assert isinstance(value, datetime)
        return value

    @staticmethod
    def _event(
        database: AsyncSession,
        row: GatewaySession,
        state: SessionState,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if reason is not None:
            payload["reason"] = reason
        database.add(
            SessionEventRecord(
                session_id=row.id,
                event_type=f"session.{state.value}",
                provider=None,
                reason=reason,
                occurred_at=now,
                payload=payload,
            )
        )

    @staticmethod
    def _attempt_event(
        database: AsyncSession,
        attempt: AcquisitionAttempt,
        event_type: str,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        database.add(
            SessionEventRecord(
                session_id=attempt.session_id,
                attempt_id=attempt.id,
                event_type=event_type,
                provider=attempt.provider,
                reason=reason,
                occurred_at=now,
                payload={"reason": reason} if reason else {},
            )
        )
