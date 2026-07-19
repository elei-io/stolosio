from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    FleetConfigurationEvent,
    GatewaySession,
    ProviderFleet,
    ProviderInstance,
    ProviderState,
)
from backend.fleet.contracts import (
    FleetConfiguration,
    FleetInstance,
    FleetInstanceState,
    FleetSnapshot,
    ObservedInstance,
)
from backend.fleet.policy import ScalingDecision, scaling_decision
from backend.proxy.contracts import AttemptState, ProviderName, SessionState

_ACTIVE_ATTEMPT_STATES = (AttemptState.ACQUIRING.value, AttemptState.ACTIVE.value)
_LIVE_ATTEMPT_STATES = (AttemptState.QUEUED.value, *_ACTIVE_ATTEMPT_STATES)
_LIVE_SESSION_STATES = (
    SessionState.ADMITTED.value,
    SessionState.OPEN.value,
    SessionState.CLOSING.value,
)
_CONFIGURATION_FIELDS = (
    "minimum_instances",
    "maximum_instances",
    "session_capacity_per_instance",
    "scale_down_cooldown_seconds",
    "max_queued_attempts",
    "enabled",
)


class FleetRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_fleet(
        self,
        provider: ProviderName,
        *,
        minimum_instances: int,
        maximum_instances: int,
        session_capacity_per_instance: int,
        scale_down_cooldown_seconds: int,
        max_queued_attempts: int = 100,
    ) -> FleetConfiguration:
        self._validate(
            minimum_instances=minimum_instances,
            maximum_instances=maximum_instances,
            session_capacity_per_instance=session_capacity_per_instance,
            scale_down_cooldown_seconds=scale_down_cooldown_seconds,
            max_queued_attempts=max_queued_attempts,
        )
        async with self._sessions.begin() as database:
            await database.execute(
                insert(ProviderFleet)
                .values(
                    provider=provider.value,
                    minimum_instances=minimum_instances,
                    maximum_instances=maximum_instances,
                    session_capacity_per_instance=session_capacity_per_instance,
                    scale_down_cooldown_seconds=scale_down_cooldown_seconds,
                    max_queued_attempts=max_queued_attempts,
                    desired_instances=minimum_instances,
                    configuration_version=1,
                    enabled=True,
                )
                .on_conflict_do_nothing(index_elements=[ProviderFleet.provider])
            )
            row = await database.get(ProviderFleet, provider.value)
            assert row is not None
            return self._configuration(row)

    async def list_configurations(self) -> list[FleetConfiguration]:
        async with self._sessions() as database:
            rows = list(
                await database.scalars(select(ProviderFleet).order_by(ProviderFleet.provider))
            )
            return [self._configuration(row) for row in rows]

    async def configuration(self, provider: ProviderName) -> FleetConfiguration | None:
        async with self._sessions() as database:
            row = await database.get(ProviderFleet, provider.value)
            return self._configuration(row) if row is not None else None

    async def update_configuration(
        self,
        provider: ProviderName,
        values: Mapping[str, Any],
        *,
        actor: str,
    ) -> FleetConfiguration | None:
        unknown = set(values) - set(_CONFIGURATION_FIELDS)
        if unknown:
            raise ValueError(f"Unknown fleet configuration fields: {sorted(unknown)}")
        async with self._sessions.begin() as database:
            row = await database.get(ProviderFleet, provider.value, with_for_update=True)
            if row is None:
                return None
            previous = self._configuration_values(row)
            updated = {**previous, **values}
            self._validate(**updated)
            for field in _CONFIGURATION_FIELDS:
                setattr(row, field, updated[field])
            row.configuration_version += 1
            row.updated_at = await self._now(database)
            database.add(
                FleetConfigurationEvent(
                    provider=row.provider,
                    configuration_version=row.configuration_version,
                    previous_values=previous,
                    new_values=updated,
                    actor=actor,
                )
            )
            return self._configuration(row)

    async def evaluate(self, provider: ProviderName) -> tuple[FleetConfiguration, ScalingDecision]:
        async with self._sessions.begin() as database:
            row = await database.get(ProviderFleet, provider.value, with_for_update=True)
            if row is None:
                raise LookupError(f"No managed fleet for {provider.value}")
            now = await self._now(database)
            demand = await self._attempt_count(database, provider.value, _LIVE_ATTEMPT_STATES, now)
            configuration = self._configuration(row)
            decision = scaling_decision(configuration, demand=demand, now=now)
            if decision.desired_instances != row.desired_instances:
                if decision.direction == "up":
                    row.last_scale_up_at = now
                elif decision.direction == "down":
                    row.last_scale_down_at = now
                row.desired_instances = decision.desired_instances
            row.idle_since = decision.idle_since
            row.updated_at = now
            return self._configuration(row), decision

    async def observe_instances(
        self,
        provider: ProviderName,
        observations: Sequence[ObservedInstance],
        *,
        platform: str,
        observation_ttl_seconds: float,
    ) -> None:
        async with self._sessions.begin() as database:
            fleet = await database.get(ProviderFleet, provider.value, with_for_update=True)
            if fleet is None:
                raise LookupError(f"No managed fleet for {provider.value}")
            now = await self._now(database)
            expires = now + timedelta(seconds=observation_ttl_seconds)
            seen = {observation.instance_id for observation in observations}
            for observation in observations:
                row = await database.get(
                    ProviderInstance,
                    observation.instance_id,
                    with_for_update=True,
                )
                previous_state = row.state if row is not None else None
                if row is None:
                    row = ProviderInstance(
                        id=observation.instance_id,
                        provider=provider.value,
                        platform=platform,
                        endpoint=observation.endpoint,
                        state=observation.state.value,
                        capacity=(
                            observation.session_capacity
                            or fleet.session_capacity_per_instance
                        ),
                        observed_at=now,
                        observation_expires_at=expires,
                        started_at=observation.started_at or now,
                    )
                    database.add(row)
                else:
                    row.platform = platform
                    row.endpoint = observation.endpoint
                    if not (
                        row.state == FleetInstanceState.DRAINING.value
                        and observation.state is FleetInstanceState.READY
                    ):
                        row.state = observation.state.value
                    row.capacity = (
                        observation.session_capacity
                        or fleet.session_capacity_per_instance
                    )
                    row.observed_at = now
                    row.observation_expires_at = expires
                    row.stopped_at = None
                    if observation.started_at is not None:
                        row.started_at = observation.started_at
                if observation.state is FleetInstanceState.READY and previous_state != "ready":
                    row.ready_at = now
                if (
                    observation.state is FleetInstanceState.DRAINING
                    and previous_state != "draining"
                ):
                    row.draining_at = now

            existing = list(
                await database.scalars(
                    select(ProviderInstance)
                    .where(
                        ProviderInstance.provider == provider.value,
                        ProviderInstance.state != FleetInstanceState.STOPPED.value,
                    )
                    .with_for_update()
                )
            )
            for row in existing:
                if row.id not in seen:
                    row.state = FleetInstanceState.STOPPED.value
                    row.stopped_at = now
                    row.observation_expires_at = now

    async def prepare_scale_down(
        self,
        provider: ProviderName,
        instance_id: str,
    ) -> bool:
        """Drain one instance and atomically verify it has no live assignments."""
        async with self._sessions.begin() as database:
            await self._lock_provider(database, provider.value)
            instance = await database.get(
                ProviderInstance,
                instance_id,
                with_for_update=True,
            )
            if instance is None or instance.provider != provider.value:
                return False
            now = await self._now(database)
            instance.state = FleetInstanceState.DRAINING.value
            instance.draining_at = instance.draining_at or now
            active = await database.scalar(
                select(func.count())
                .select_from(AcquisitionAttempt)
                .join(GatewaySession, GatewaySession.id == AcquisitionAttempt.session_id)
                .where(
                    AcquisitionAttempt.provider_instance_id == instance_id,
                    AcquisitionAttempt.state.in_(_ACTIVE_ATTEMPT_STATES),
                    GatewaySession.state.in_(_LIVE_SESSION_STATES),
                    GatewaySession.lease_expires_at > now,
                )
            )
            return int(active or 0) == 0

    async def cancel_scale_down(self, provider: ProviderName) -> None:
        """Make previously draining instances require a fresh ready observation."""
        async with self._sessions.begin() as database:
            await self._lock_provider(database, provider.value)
            rows = list(
                await database.scalars(
                    select(ProviderInstance)
                    .where(
                        ProviderInstance.provider == provider.value,
                        ProviderInstance.state == FleetInstanceState.DRAINING.value,
                    )
                    .with_for_update()
                )
            )
            for row in rows:
                row.state = FleetInstanceState.STARTING.value
                row.draining_at = None

    async def record_reconciliation(
        self,
        provider: ProviderName,
        *,
        status: str,
    ) -> None:
        async with self._sessions.begin() as database:
            row = await database.get(ProviderFleet, provider.value, with_for_update=True)
            if row is None:
                return
            now = await self._now(database)
            row.controller_status = status
            row.last_reconciled_at = now
            row.updated_at = now

    async def instances(self, provider: ProviderName) -> list[FleetInstance]:
        async with self._sessions() as database:
            rows = list(
                await database.scalars(
                    select(ProviderInstance)
                    .where(ProviderInstance.provider == provider.value)
                    .order_by(ProviderInstance.id)
                )
            )
            return [self._instance(row) for row in rows]

    async def snapshot(self, provider: ProviderName) -> FleetSnapshot | None:
        async with self._sessions() as database:
            row = await database.get(ProviderFleet, provider.value)
            if row is None:
                return None
            now = await self._now(database)
            instance_rows = list(
                await database.scalars(
                    select(ProviderInstance).where(ProviderInstance.provider == provider.value)
                )
            )
            active = await self._attempt_count(
                database, provider.value, _ACTIVE_ATTEMPT_STATES, now
            )
            queued = await self._attempt_count(
                database, provider.value, (AttemptState.QUEUED.value,), now
            )
            ready = [
                instance
                for instance in instance_rows
                if instance.state == FleetInstanceState.READY.value
                and instance.observation_expires_at > now
            ]
            total_slots = sum(instance.capacity for instance in ready)
            return FleetSnapshot(
                configuration=self._configuration(row),
                observed_instances=sum(
                    instance.state != FleetInstanceState.STOPPED.value for instance in instance_rows
                ),
                ready_instances=len(ready),
                draining_instances=sum(
                    instance.state == FleetInstanceState.DRAINING.value
                    for instance in instance_rows
                ),
                unhealthy_instances=sum(
                    instance.state == FleetInstanceState.UNHEALTHY.value
                    for instance in instance_rows
                ),
                total_slots=total_slots,
                occupied_slots=active,
                available_slots=max(0, total_slots - active),
                active_attempts=active,
                queued_attempts=queued,
            )

    async def _attempt_count(
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

    @staticmethod
    async def _now(database: AsyncSession) -> datetime:
        value = await database.scalar(select(func.clock_timestamp()))
        assert isinstance(value, datetime)
        return value

    @staticmethod
    async def _lock_provider(database: AsyncSession, provider: str) -> None:
        await database.execute(
            insert(ProviderState)
            .values(provider=provider)
            .on_conflict_do_nothing(index_elements=[ProviderState.provider])
        )
        await database.scalar(
            select(ProviderState).where(ProviderState.provider == provider).with_for_update()
        )

    @staticmethod
    def _configuration(row: ProviderFleet) -> FleetConfiguration:
        return FleetConfiguration(
            provider=ProviderName(row.provider),
            minimum_instances=row.minimum_instances,
            maximum_instances=row.maximum_instances,
            session_capacity_per_instance=row.session_capacity_per_instance,
            scale_down_cooldown_seconds=row.scale_down_cooldown_seconds,
            max_queued_attempts=row.max_queued_attempts,
            desired_instances=row.desired_instances,
            configuration_version=row.configuration_version,
            enabled=row.enabled,
            idle_since=row.idle_since,
            last_reconciled_at=row.last_reconciled_at,
            controller_status=row.controller_status,
        )

    @staticmethod
    def _instance(row: ProviderInstance) -> FleetInstance:
        return FleetInstance(
            instance_id=row.id,
            provider=ProviderName(row.provider),
            platform=row.platform,
            endpoint=row.endpoint,
            state=FleetInstanceState(row.state),
            capacity=row.capacity,
            observed_at=row.observed_at,
            observation_expires_at=row.observation_expires_at,
        )

    @staticmethod
    def _configuration_values(row: ProviderFleet) -> dict[str, Any]:
        return {field: getattr(row, field) for field in _CONFIGURATION_FIELDS}

    @staticmethod
    def _validate(
        *,
        minimum_instances: int,
        maximum_instances: int,
        session_capacity_per_instance: int,
        scale_down_cooldown_seconds: int,
        max_queued_attempts: int,
        enabled: bool = True,
    ) -> None:
        if minimum_instances < 0 or maximum_instances < minimum_instances:
            raise ValueError("Fleet instance limits are invalid")
        if maximum_instances > 100:
            raise ValueError("Fleet maximum_instances cannot exceed 100")
        if session_capacity_per_instance < 1:
            raise ValueError("Fleet session capacity must be positive")
        if scale_down_cooldown_seconds < 1:
            raise ValueError("Fleet scale-down cooldown must be positive")
        if max_queued_attempts < 0:
            raise ValueError("Fleet queue limit must be non-negative")
