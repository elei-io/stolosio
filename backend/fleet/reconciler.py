import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.fleet.contracts import (
    FleetInstanceState,
    FleetRuntime,
    ObservedInstance,
)
from backend.fleet.providers import ProviderFleetDefinition
from backend.fleet.repository import FleetRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManagedFleet:
    definition: ProviderFleetDefinition
    deployment: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    provider: str
    scale_direction: str | None = None
    replaced_instances: int = 0
    ready_latencies_seconds: tuple[float, ...] = ()
    first_assignment_latencies_seconds: tuple[float, ...] = ()
    unassigned_scale_requests: int = 0


@dataclass(slots=True)
class _ScaleRequest:
    requested_at: datetime
    ready_observed: bool = False
    assignment_observed: bool = False


class ReconciliationOperationError(RuntimeError):
    def __init__(self, direction: str) -> None:
        super().__init__(f"fleet runtime {direction} operation failed")
        self.direction = direction


class FleetReconciler:
    """Reconcile Harbor's durable desired state through any fleet runtime."""

    def __init__(
        self,
        repository: FleetRepository,
        runtime: FleetRuntime,
        fleet: ManagedFleet,
        *,
        observation_ttl_seconds: float,
        startup_timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._fleet = fleet
        self._observation_ttl_seconds = observation_ttl_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._unhealthy_since: dict[str, datetime] = {}
        self._known_instances: set[str] = set()
        self._unbound_scale_requests: list[_ScaleRequest] = []
        self._scale_requests: dict[str, _ScaleRequest] = {}

    @property
    def provider(self) -> str:
        return self._fleet.definition.provider.value

    async def reconcile(self) -> ReconciliationResult:
        definition = self._fleet.definition
        provider = definition.provider
        configuration, decision = await self._repository.evaluate(provider)
        instances = await self._runtime.list_instances(self._fleet.deployment)
        self._bind_scale_requests(instances)
        current = len(instances)
        direction = None
        controller_status = "ready"

        capacity_changed = any(
            instance.configuration_stale
            or instance.session_capacity != configuration.session_capacity_per_instance
            for instance in instances
        )
        current_capacity = next(
            (
                instance.session_capacity
                for instance in instances
                if instance.session_capacity is not None
            ),
            configuration.session_capacity_per_instance,
        )
        reconfigure = capacity_changed and decision.demand == 0
        if capacity_changed and decision.demand > 0:
            controller_status = "waiting_for_zero_demand"
        if current != configuration.desired_instances or reconfigure:
            next_count = current + 1 if current < configuration.desired_instances else current - 1
            if current == configuration.desired_instances:
                next_count = current
                direction = "configure"
            else:
                direction = "up" if next_count > current else "down"
            if direction == "down":
                candidate = self._runtime.scale_down_candidate(instances)
                if candidate is None or not await self._repository.prepare_scale_down(
                    provider,
                    candidate.instance_id,
                ):
                    controller_status = "waiting_for_scale_down_drain"
                    direction = None
                    next_count = current
            else:
                await self._repository.cancel_scale_down(provider)
            try:
                if direction is not None:
                    scale_requested_at = datetime.now(UTC)
                    await self._runtime.scale(
                        self._fleet.deployment,
                        next_count,
                        session_capacity=(
                            configuration.session_capacity_per_instance
                            if decision.demand == 0
                            else current_capacity
                        ),
                    )
                    if direction == "up":
                        self._unbound_scale_requests.append(_ScaleRequest(scale_requested_at))
            except Exception as error:
                await self._repository.record_reconciliation(
                    provider,
                    status="runtime_scale_failed",
                )
                raise ReconciliationOperationError(direction) from error
            instances = await self._runtime.list_instances(self._fleet.deployment)
            self._bind_scale_requests(instances)

        observations: list[ObservedInstance] = []
        ready_instance_ids: set[str] = set()
        replaced = 0
        live_ids = {instance.instance_id for instance in instances}
        for instance_id in set(self._unhealthy_since) - live_ids:
            self._unhealthy_since.pop(instance_id, None)

        for instance in instances:
            ready = await self._runtime.port_open(instance, definition.connection_port)
            now = datetime.now(UTC)
            if ready:
                self._unhealthy_since.pop(instance.instance_id, None)
                ready_instance_ids.add(instance.instance_id)
            else:
                self._unhealthy_since.setdefault(instance.instance_id, now)
            unhealthy_since = self._unhealthy_since.get(instance.instance_id)
            startup_expired = (
                unhealthy_since is not None
                and (now - unhealthy_since).total_seconds() >= self._startup_timeout_seconds
            )
            if startup_expired:
                can_replace = await self._repository.prepare_scale_down(
                    provider,
                    instance.instance_id,
                )
                if not can_replace:
                    observations.append(
                        ObservedInstance(
                            instance_id=instance.instance_id,
                            endpoint=definition.endpoint(instance),
                            state=FleetInstanceState.UNHEALTHY,
                            started_at=instance.started_at,
                            session_capacity=instance.session_capacity,
                        )
                    )
                    controller_status = "waiting_for_unhealthy_instance_sessions"
                    continue
                try:
                    await self._runtime.remove(self._fleet.deployment, instance.instance_id)
                except Exception as error:
                    await self._repository.record_reconciliation(
                        provider,
                        status="runtime_replace_failed",
                    )
                    raise ReconciliationOperationError("replace") from error
                self._unhealthy_since.pop(instance.instance_id, None)
                replaced += 1
                continue
            observations.append(
                ObservedInstance(
                    instance_id=instance.instance_id,
                    endpoint=definition.endpoint(instance),
                    state=(FleetInstanceState.READY if ready else FleetInstanceState.STARTING),
                    started_at=instance.started_at,
                    session_capacity=instance.session_capacity,
                )
            )

        await self._repository.observe_instances(
            provider,
            observations,
            platform=self._runtime.platform,
            observation_ttl_seconds=self._observation_ttl_seconds,
        )
        (
            ready_latencies,
            first_assignment_latencies,
            unassigned_scale_requests,
        ) = await self._lifecycle_latencies(
            provider,
            {instance.instance_id for instance in instances},
            ready_instance_ids,
        )
        await self._repository.record_reconciliation(provider, status=controller_status)
        if decision.direction is not None:
            logger.info(
                "%s fleet policy requested scale %s to %s instances",
                provider.value,
                decision.direction,
                configuration.desired_instances,
            )
        return ReconciliationResult(
            provider=provider.value,
            scale_direction=direction,
            replaced_instances=replaced,
            ready_latencies_seconds=ready_latencies,
            first_assignment_latencies_seconds=first_assignment_latencies,
            unassigned_scale_requests=unassigned_scale_requests,
        )

    def _bind_scale_requests(self, instances: list) -> None:
        live_ids = {instance.instance_id for instance in instances}
        new_ids = sorted(live_ids - self._known_instances)
        for instance_id in new_ids:
            if not self._unbound_scale_requests:
                break
            self._scale_requests[instance_id] = self._unbound_scale_requests.pop(0)
        self._known_instances = live_ids

    async def _lifecycle_latencies(
        self,
        provider,
        live_ids: set[str],
        ready_ids: set[str],
    ) -> tuple[tuple[float, ...], tuple[float, ...], int]:
        if not self._scale_requests:
            return (), (), 0
        ready_latencies: list[float] = []
        assignment_latencies: list[float] = []
        unassigned = 0
        assignment_times = await self._repository.first_assignment_times(
            provider,
            set(self._scale_requests),
        )
        for instance_id, request in list(self._scale_requests.items()):
            if instance_id in ready_ids and not request.ready_observed:
                ready_latencies.append(
                    max(
                        0.0,
                        (datetime.now(UTC) - request.requested_at).total_seconds(),
                    )
                )
                request.ready_observed = True
            assigned_at = assignment_times.get(instance_id)
            if assigned_at is not None and not request.assignment_observed:
                assignment_latencies.append(
                    max(0.0, (assigned_at - request.requested_at).total_seconds())
                )
                request.assignment_observed = True
            if instance_id not in live_ids:
                if not request.assignment_observed:
                    unassigned += 1
                self._scale_requests.pop(instance_id, None)
            elif request.assignment_observed:
                self._scale_requests.pop(instance_id, None)
        return tuple(ready_latencies), tuple(assignment_latencies), unassigned
