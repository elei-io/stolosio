from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import AcquisitionAttempt, GatewaySession
from backend.fleet import FleetRepository
from backend.proxy.attempts import provider_capacity
from backend.proxy.contracts import AttemptState, ProviderName, SessionState
from backend.settings import Settings


@dataclass(frozen=True, slots=True)
class GatewayFleetSnapshot:
    active_sessions: int
    capacity: int


@dataclass(frozen=True, slots=True)
class ProviderFleetSnapshot:
    provider: ProviderName
    active_attempts: int
    queued_attempts: int
    capacity: int
    oldest_queued_attempt_seconds: float
    desired_instances: int = 0
    observed_instances: int = 0
    ready_instances: int = 0
    draining_instances: int = 0
    unhealthy_instances: int = 0
    total_slots: int = 0
    available_slots: int = 0


class FleetSnapshotService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._fleets = FleetRepository(sessions)

    async def gateway_snapshot(self) -> GatewayFleetSnapshot:
        now = datetime.now(UTC)
        async with self._sessions() as database:
            active = await database.scalar(
                select(func.count())
                .select_from(GatewaySession)
                .where(
                    GatewaySession.state.in_(
                        (
                            SessionState.ADMITTED.value,
                            SessionState.OPEN.value,
                            SessionState.CLOSING.value,
                        )
                    ),
                    GatewaySession.lease_expires_at > now,
                )
            )
        return GatewayFleetSnapshot(
            active_sessions=int(active or 0),
            capacity=self._settings.harbor_max_active_sessions,
        )

    async def snapshot(self) -> list[ProviderFleetSnapshot]:
        now = datetime.now(UTC)
        live_sessions = (
            SessionState.ADMITTED.value,
            SessionState.OPEN.value,
            SessionState.CLOSING.value,
        )
        async with self._sessions() as database:
            active_rows = dict(
                (
                    await database.execute(
                        select(AcquisitionAttempt.provider, func.count())
                        .join(
                            GatewaySession,
                            GatewaySession.id == AcquisitionAttempt.session_id,
                        )
                        .where(
                            AcquisitionAttempt.state.in_(
                                (AttemptState.ACQUIRING.value, AttemptState.ACTIVE.value)
                            ),
                            GatewaySession.state.in_(live_sessions),
                            GatewaySession.lease_expires_at > now,
                        )
                        .group_by(AcquisitionAttempt.provider)
                    )
                ).all()
            )
            queued_rows = {
                provider: (count, oldest)
                for provider, count, oldest in (
                    await database.execute(
                        select(
                            AcquisitionAttempt.provider,
                            func.count(),
                            func.min(AcquisitionAttempt.queued_at),
                        )
                        .join(
                            GatewaySession,
                            GatewaySession.id == AcquisitionAttempt.session_id,
                        )
                        .where(
                            AcquisitionAttempt.state == AttemptState.QUEUED.value,
                            GatewaySession.state.in_(live_sessions),
                            GatewaySession.lease_expires_at > now,
                        )
                        .group_by(AcquisitionAttempt.provider)
                    )
                ).all()
            }

        snapshots = []
        for provider in ProviderName:
            queued, oldest = queued_rows.get(provider.value, (0, None))
            age = max(0.0, (now - oldest).total_seconds()) if oldest else 0.0
            managed = await self._fleets.snapshot(provider)
            capacity = (
                managed.total_slots
                if managed is not None
                else provider_capacity(self._settings, provider).max_active
            )
            snapshots.append(
                ProviderFleetSnapshot(
                    provider=provider,
                    active_attempts=int(active_rows.get(provider.value, 0)),
                    queued_attempts=int(queued),
                    capacity=capacity,
                    oldest_queued_attempt_seconds=age,
                    desired_instances=(
                        managed.configuration.desired_instances if managed is not None else 0
                    ),
                    observed_instances=(managed.observed_instances if managed is not None else 0),
                    ready_instances=managed.ready_instances if managed is not None else 0,
                    draining_instances=(managed.draining_instances if managed is not None else 0),
                    unhealthy_instances=(managed.unhealthy_instances if managed is not None else 0),
                    total_slots=managed.total_slots if managed is not None else capacity,
                    available_slots=(
                        managed.available_slots
                        if managed is not None
                        else max(0, capacity - int(active_rows.get(provider.value, 0)))
                    ),
                )
            )
        return snapshots
