import math
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    GatewaySession,
    ProviderFleet,
    ProviderInstance,
    ProviderRoutingProfile,
    ProviderState,
    SessionEventRecord,
)
from backend.proxy.contracts import (
    AttemptState,
    HarborSession,
    ProviderAttempt,
    ProviderName,
    SessionState,
)


class AttemptAdmissionStatus(StrEnum):
    ACQUIRING = "acquiring"
    QUEUED = "queued"
    FULL = "full"


_ACTIVE_ATTEMPT_STATES = (AttemptState.ACQUIRING.value, AttemptState.ACTIVE.value)
_LIVE_ATTEMPT_STATES = (
    AttemptState.QUEUED.value,
    AttemptState.ACQUIRING.value,
    AttemptState.ACTIVE.value,
)
_LIVE_SESSION_STATES = (SessionState.ADMITTED.value, SessionState.OPEN.value)


class PostgresAttemptRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def enqueue(
        self,
        session: HarborSession,
        attempt_id: str,
        provider: ProviderName,
        *,
        max_active: int,
        max_queued: int,
        resolved_settings: dict[str, object],
        setting_sources: dict[str, object],
    ) -> tuple[AttemptAdmissionStatus, ProviderAttempt]:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            session_row = await self._owned_session(database, session, now)
            if session_row is None:
                raise RuntimeError("Harbor session lease is not active")
            await self._lock_provider(database, provider.value)
            await self._expire_stale(database, provider.value, now)

            live_for_session = await database.scalar(
                select(func.count())
                .select_from(AcquisitionAttempt)
                .where(
                    AcquisitionAttempt.session_id == session.session_id,
                    AcquisitionAttempt.state.in_(_LIVE_ATTEMPT_STATES),
                )
            )
            if int(live_for_session or 0):
                raise RuntimeError("Harbor session already has a live acquisition attempt")
            ordinal = (
                int(
                    await database.scalar(
                        select(func.coalesce(func.max(AcquisitionAttempt.ordinal), 0)).where(
                            AcquisitionAttempt.session_id == session.session_id
                        )
                    )
                    or 0
                )
                + 1
            )
            row = AcquisitionAttempt(
                id=attempt_id,
                session_id=session.session_id,
                ordinal=ordinal,
                provider=provider.value,
                resolved_settings=resolved_settings,
                setting_sources=setting_sources,
                state=AttemptState.REQUESTED.value,
                created_at=now,
            )
            database.add(row)
            await database.flush()
            self._event(database, row, "attempt.started", now)

            queued = await self._count(
                database,
                provider.value,
                (AttemptState.QUEUED.value,),
                now,
            )
            managed = await database.get(ProviderFleet, provider.value)
            instance = (
                await self._select_instance(database, provider.value, now)
                if managed is not None and managed.enabled and queued == 0
                else None
            )
            active = await self._count(database, provider.value, _ACTIVE_ATTEMPT_STATES, now)
            can_acquire = queued == 0 and (
                instance is not None
                if managed is not None
                else active < max_active
            )
            if can_acquire:
                row.state = AttemptState.ACQUIRING.value
                row.acquiring_at = now
                row.provider_instance_id = instance.id if instance is not None else None
                self._event(database, row, "attempt.acquiring", now)
                status = AttemptAdmissionStatus.ACQUIRING
            elif queued < max_queued:
                row.state = AttemptState.QUEUED.value
                row.queued_at = now
                self._event(database, row, "attempt.queued", now)
                status = AttemptAdmissionStatus.QUEUED
            else:
                row.state = AttemptState.FAILED.value
                row.finished_at = now
                row.terminal_reason = "provider_queue_full"
                self._event(
                    database,
                    row,
                    "attempt.failed",
                    now,
                    reason="provider_queue_full",
                )
                status = AttemptAdmissionStatus.FULL
            return status, self._contract(row, instance.endpoint if instance is not None else None)

    async def claim(
        self,
        session: HarborSession,
        attempt: ProviderAttempt,
        *,
        max_active: int,
    ) -> ProviderAttempt | None:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            if await self._owned_session(database, session, now) is None:
                return None
            await self._lock_provider(database, attempt.provider.value)
            await self._expire_stale(database, attempt.provider.value, now)
            row = await database.scalar(
                select(AcquisitionAttempt)
                .where(
                    AcquisitionAttempt.id == attempt.attempt_id,
                    AcquisitionAttempt.session_id == session.session_id,
                )
                .with_for_update()
            )
            if row is None or row.state != AttemptState.QUEUED.value:
                return None
            head = await database.scalar(
                select(AcquisitionAttempt.id)
                .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
                .where(
                    AcquisitionAttempt.provider == attempt.provider.value,
                    AcquisitionAttempt.state == AttemptState.QUEUED.value,
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at > now,
                )
                .order_by(AcquisitionAttempt.queue_sequence)
                .limit(1)
            )
            if head != attempt.attempt_id:
                return None
            managed = await database.get(ProviderFleet, attempt.provider.value)
            instance = (
                await self._select_instance(database, attempt.provider.value, now)
                if managed is not None and managed.enabled
                else None
            )
            if managed is not None:
                if not managed.enabled or instance is None:
                    return None
            elif (
                await self._count(
                    database,
                    attempt.provider.value,
                    _ACTIVE_ATTEMPT_STATES,
                    now,
                )
                >= max_active
            ):
                return None
            row.state = AttemptState.ACQUIRING.value
            row.acquiring_at = now
            row.provider_instance_id = instance.id if instance is not None else None
            self._event(database, row, "attempt.acquiring", now)
            return self._contract(row, instance.endpoint if instance is not None else None)

    async def activate(self, attempt: ProviderAttempt) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            row = await database.get(AcquisitionAttempt, attempt.attempt_id, with_for_update=True)
            if row is None or row.state != AttemptState.ACQUIRING.value:
                return False
            row.state = AttemptState.ACTIVE.value
            row.active_at = now
            self._event(database, row, "attempt.connected", now)
            return True

    async def finish(
        self,
        attempt: ProviderAttempt,
        *,
        failed: bool,
        reason: str,
    ) -> bool:
        async with self._sessions.begin() as database:
            now = await self._now(database)
            await self._lock_provider(database, attempt.provider.value)
            row = await database.get(AcquisitionAttempt, attempt.attempt_id, with_for_update=True)
            if row is None:
                return False
            if row.state in (AttemptState.COMPLETED.value, AttemptState.FAILED.value):
                return True
            row.state = AttemptState.FAILED.value if failed else AttemptState.COMPLETED.value
            row.finished_at = now
            row.terminal_reason = reason
            start = row.active_at or row.acquiring_at
            if start is not None:
                profile = await database.get(ProviderRoutingProfile, row.provider)
                if profile is not None:
                    seconds = max(0.0, (now - start).total_seconds())
                    row.actual_cost_units = math.ceil(seconds * profile.cost_units_per_second)
            self._event(
                database,
                row,
                "attempt.failed" if failed else "attempt.closed",
                now,
                reason=reason if failed else None,
            )
            return True

    async def active_count(self, provider: ProviderName) -> int:
        async with self._sessions() as database:
            now = await self._now(database)
            return await self._count(database, provider.value, _ACTIVE_ATTEMPT_STATES, now)

    async def queue(self, provider: ProviderName) -> list[str]:
        async with self._sessions() as database:
            now = await self._now(database)
            values = await database.scalars(
                select(AcquisitionAttempt.id)
                .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
                .where(
                    AcquisitionAttempt.provider == provider.value,
                    AcquisitionAttempt.state == AttemptState.QUEUED.value,
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at > now,
                )
                .order_by(AcquisitionAttempt.queue_sequence)
            )
            return list(values)

    async def _owned_session(
        self,
        database: AsyncSession,
        session: HarborSession,
        now: datetime,
    ) -> GatewaySession | None:
        return await database.scalar(
            select(GatewaySession)
            .where(
                GatewaySession.id == session.session_id,
                GatewaySession.owner_id == session.owner_id,
                GatewaySession.lease_token == session.lease_token,
                GatewaySession.state.in_(_LIVE_SESSION_STATES),
                GatewaySession.lease_expires_at > now,
            )
            .with_for_update()
        )

    async def _lock_provider(self, database: AsyncSession, provider: str) -> None:
        await database.execute(
            insert(ProviderState)
            .values(provider=provider)
            .on_conflict_do_nothing(index_elements=[ProviderState.provider])
        )
        await database.scalar(
            select(ProviderState).where(ProviderState.provider == provider).with_for_update()
        )

    async def _expire_stale(
        self,
        database: AsyncSession,
        provider: str,
        now: datetime,
    ) -> None:
        rows = list(
            await database.scalars(
                select(AcquisitionAttempt)
                .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
                .where(
                    AcquisitionAttempt.provider == provider,
                    AcquisitionAttempt.state.in_(_LIVE_ATTEMPT_STATES),
                    or_(
                        GatewaySession.state.not_in(_LIVE_SESSION_STATES),
                        GatewaySession.lease_expires_at.is_(None),
                        GatewaySession.lease_expires_at <= now,
                    ),
                )
                .with_for_update()
            )
        )
        for row in rows:
            row.state = AttemptState.FAILED.value
            row.finished_at = now
            row.terminal_reason = "session_lease_expired"
            self._event(
                database,
                row,
                "attempt.failed",
                now,
                reason="session_lease_expired",
            )

    async def _count(
        self,
        database: AsyncSession,
        provider: str,
        states: tuple[str, ...],
        now: datetime,
    ) -> int:
        value = await database.scalar(
            select(func.count())
            .select_from(AcquisitionAttempt)
            .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
            .where(
                AcquisitionAttempt.provider == provider,
                AcquisitionAttempt.state.in_(states),
                GatewaySession.state.in_(_LIVE_SESSION_STATES),
                GatewaySession.lease_expires_at > now,
            )
        )
        return int(value or 0)

    async def _select_instance(
        self,
        database: AsyncSession,
        provider: str,
        now: datetime,
    ) -> ProviderInstance | None:
        instances = list(
            await database.scalars(
                select(ProviderInstance)
                .where(
                    ProviderInstance.provider == provider,
                    ProviderInstance.state == "ready",
                    ProviderInstance.observation_expires_at > now,
                )
                .order_by(ProviderInstance.id)
                .with_for_update()
            )
        )
        candidates: list[tuple[int, ProviderInstance]] = []
        for instance in instances:
            occupied = int(
                await database.scalar(
                    select(func.count())
                    .select_from(AcquisitionAttempt)
                    .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
                    .where(
                        AcquisitionAttempt.provider_instance_id == instance.id,
                        AcquisitionAttempt.state.in_(_ACTIVE_ATTEMPT_STATES),
                        GatewaySession.state.in_(_LIVE_SESSION_STATES),
                        GatewaySession.lease_expires_at > now,
                    )
                )
                or 0
            )
            if occupied < instance.capacity:
                candidates.append((occupied, instance))
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: (candidate[0], candidate[1].id))[1]

    @staticmethod
    async def _now(database: AsyncSession) -> datetime:
        value = await database.scalar(select(func.clock_timestamp()))
        assert isinstance(value, datetime)
        return value

    @staticmethod
    def _contract(row: AcquisitionAttempt, endpoint: str | None = None) -> ProviderAttempt:
        return ProviderAttempt(
            attempt_id=row.id,
            session_id=row.session_id,
            ordinal=row.ordinal,
            provider=ProviderName(row.provider),
            state=AttemptState(row.state),
            provider_instance_id=row.provider_instance_id,
            endpoint=endpoint,
        )

    @staticmethod
    def _event(
        database: AsyncSession,
        row: AcquisitionAttempt,
        event_type: str,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if event_type == "attempt.started":
            payload = {
                "resolved_settings": row.resolved_settings,
                "setting_sources": row.setting_sources,
            }
        if reason is not None:
            payload["reason"] = reason
        if row.actual_cost_units is not None:
            payload["cost_units"] = row.actual_cost_units
        database.add(
            SessionEventRecord(
                session_id=row.session_id,
                attempt_id=row.id,
                event_type=event_type,
                provider=row.provider,
                reason=reason,
                occurred_at=now,
                payload=payload,
            )
        )
