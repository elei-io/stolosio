from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import GatewaySession, ProviderState, SessionEventRecord
from backend.proxy.contracts import HarborSession, SessionState


class AdmissionStatus(StrEnum):
    ACQUIRED = "acquiring"
    QUEUED = "queued"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class RepositorySettings:
    lease_seconds: float
    queue_ttl_seconds: float


_ACTIVE_STATES = (
    SessionState.ACQUIRING.value,
    SessionState.CONNECTED.value,
    SessionState.CLOSING.value,
)
_NONTERMINAL_STATES = (
    SessionState.REQUESTED.value,
    SessionState.QUEUED.value,
    *_ACTIVE_STATES,
)


class PostgresSessionRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: RepositorySettings,
    ) -> None:
        self._sessions = sessions
        self._settings = settings

    async def ping(self) -> None:
        async with self._sessions() as database:
            await database.scalar(select(1))

    async def admit(
        self,
        session: HarborSession,
        *,
        max_active: int,
        max_queued: int,
        requested_settings: dict[str, object] | None = None,
        resolved_settings: dict[str, object] | None = None,
        setting_sources: dict[str, object] | None = None,
    ) -> AdmissionStatus:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            await self._lock_provider(database, session.resolved_provider.value)
            await self._expire_stale(database, session.resolved_provider.value, now)

            row = GatewaySession(
                id=session.session_id,
                owner_id=session.owner_id,
                lease_token=session.lease_token,
                requested_provider=session.requested_provider.value,
                resolved_provider=session.resolved_provider.value,
                requested_settings=requested_settings
                or {"harbor.provider.slug": session.requested_provider.value},
                resolved_settings=resolved_settings
                or {"harbor.provider.slug": session.resolved_provider.value},
                setting_sources=setting_sources or {},
                state=SessionState.REQUESTED.value,
                created_at=now,
            )
            database.add(row)
            await database.flush()
            self._event(database, row, SessionState.REQUESTED, now)

            active = await self._count(database, session.resolved_provider.value, _ACTIVE_STATES)
            queued = await self._count(
                database,
                session.resolved_provider.value,
                (SessionState.QUEUED.value,),
            )
            if queued == 0 and active < max_active:
                row.state = SessionState.ACQUIRING.value
                row.acquiring_at = now
                row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
                self._event(database, row, SessionState.ACQUIRING, now)
                return AdmissionStatus.ACQUIRED

            if queued < max_queued:
                row.state = SessionState.QUEUED.value
                row.queued_at = now
                row.lease_expires_at = now + timedelta(seconds=self._settings.queue_ttl_seconds)
                self._event(database, row, SessionState.QUEUED, now)
                return AdmissionStatus.QUEUED

            row.state = SessionState.FAILED.value
            row.closed_at = now
            row.terminal_reason = "provider_queue_full"
            self._event(
                database,
                row,
                SessionState.FAILED,
                now,
                reason="provider_queue_full",
            )
            return AdmissionStatus.FULL

    async def claim(self, session: HarborSession, *, max_active: int) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            provider = session.resolved_provider.value
            await self._lock_provider(database, provider)
            await self._expire_stale(database, provider, now)

            row = await self._owned_session(database, session, for_update=True)
            if row is None or row.state != SessionState.QUEUED.value:
                return False
            head = await database.scalar(
                select(GatewaySession.id)
                .where(
                    GatewaySession.resolved_provider == provider,
                    GatewaySession.state == SessionState.QUEUED.value,
                    GatewaySession.lease_expires_at > now,
                )
                .order_by(GatewaySession.queue_sequence)
                .limit(1)
            )
            if head != session.session_id:
                return False
            if await self._count(database, provider, _ACTIVE_STATES) >= max_active:
                return False

            row.state = SessionState.ACQUIRING.value
            row.acquiring_at = now
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            self._event(database, row, SessionState.ACQUIRING, now)
            return True

    async def heartbeat(self, session: HarborSession) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await self._owned_session(database, session, for_update=True)
            if (
                row is None
                or row.state not in _ACTIVE_STATES
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                return False
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            return True

    async def transition(self, session: HarborSession, state: SessionState) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await self._owned_session(database, session, for_update=True)
            if (
                row is None
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                return False
            valid = (
                row.state == SessionState.ACQUIRING.value
                and state is SessionState.CONNECTED
            ) or (
                row.state == SessionState.CONNECTED.value and state is SessionState.CLOSING
            )
            if not valid:
                return False

            row.state = state.value
            setattr(row, f"{state.value}_at", now)
            row.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            self._event(database, row, state, now)
            return True

    async def release(
        self,
        session: HarborSession,
        *,
        failed: bool,
        reason: str,
    ) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            await self._lock_provider(database, session.resolved_provider.value)
            row = await database.scalar(
                select(GatewaySession)
                .where(GatewaySession.id == session.session_id)
                .with_for_update()
            )
            if row is None:
                return True
            if row.state in (SessionState.CLOSED.value, SessionState.FAILED.value):
                return True
            if row.owner_id != session.owner_id or row.lease_token != session.lease_token:
                return False

            terminal = SessionState.FAILED if failed else SessionState.CLOSED
            row.state = terminal.value
            row.closed_at = now
            row.lease_expires_at = None
            row.terminal_reason = reason
            self._event(database, row, terminal, now, reason=reason)
            return True

    async def read_session(self, session_id: str) -> dict[str, str]:
        async with self._sessions() as database:
            row = await database.get(GatewaySession, session_id)
            if row is None:
                return {}
            values = {
                "session_id": row.id,
                "owner_id": row.owner_id,
                "lease_token": row.lease_token,
                "requested_provider": row.requested_provider,
                "resolved_provider": row.resolved_provider,
                "state": row.state,
                "terminal_reason": row.terminal_reason,
            }
            return {key: value for key, value in values.items() if value is not None}

    async def active_count(self, provider: str) -> int:
        async with self._sessions() as database:
            now = await self._now(database)
            return await self._count(database, provider, _ACTIVE_STATES, now=now)

    async def queue(self, provider: str) -> list[str]:
        async with self._sessions() as database:
            now = await self._now(database)
            values = await database.scalars(
                select(GatewaySession.id)
                .where(
                    GatewaySession.resolved_provider == provider,
                    GatewaySession.state == SessionState.QUEUED.value,
                    GatewaySession.lease_expires_at > now,
                )
                .order_by(GatewaySession.queue_sequence)
            )
            return list(values)

    async def events(self, session_id: str) -> list[str]:
        async with self._sessions() as database:
            values = await database.scalars(
                select(SessionEventRecord.event_type)
                .where(SessionEventRecord.session_id == session_id)
                .order_by(SessionEventRecord.id)
            )
            return list(values)

    async def _lock_provider(self, database: AsyncSession, provider: str) -> None:
        await database.execute(
            insert(ProviderState)
            .values(provider=provider)
            .on_conflict_do_nothing(index_elements=[ProviderState.provider])
        )
        await database.scalar(
            select(ProviderState)
            .where(ProviderState.provider == provider)
            .with_for_update()
        )

    async def _expire_stale(
        self,
        database: AsyncSession,
        provider: str,
        now: datetime,
    ) -> None:
        rows = await database.scalars(
            select(GatewaySession)
            .where(
                GatewaySession.resolved_provider == provider,
                GatewaySession.state.in_(_NONTERMINAL_STATES),
                GatewaySession.lease_expires_at.is_not(None),
                GatewaySession.lease_expires_at <= now,
            )
            .with_for_update()
        )
        for row in rows:
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

    async def _owned_session(
        self,
        database: AsyncSession,
        session: HarborSession,
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

    async def _count(
        self,
        database: AsyncSession,
        provider: str,
        states: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> int:
        statement = select(func.count()).select_from(GatewaySession).where(
            GatewaySession.resolved_provider == provider,
            GatewaySession.state.in_(states),
        )
        if now is not None:
            statement = statement.where(GatewaySession.lease_expires_at > now)
        return int(await database.scalar(statement) or 0)

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
        database.add(
            SessionEventRecord(
                session_id=row.id,
                event_type=f"session.{state.value}",
                provider=row.resolved_provider,
                reason=reason,
                occurred_at=now,
            )
        )
