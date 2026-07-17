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

    @property
    def provider(self) -> str:
        return self._fleet.definition.provider.value

    async def reconcile(self) -> ReconciliationResult:
        definition = self._fleet.definition
        provider = definition.provider
        configuration, decision = await self._repository.evaluate(provider)
        instances = await self._runtime.list_instances(self._fleet.deployment)
        current = len(instances)
        direction = None

        if current != configuration.desired_instances:
            next_count = current + 1 if current < configuration.desired_instances else current - 1
            direction = "up" if next_count > current else "down"
            try:
                await self._runtime.scale(self._fleet.deployment, next_count)
            except Exception as error:
                await self._repository.record_reconciliation(
                    provider,
                    status="runtime_scale_failed",
                )
                raise ReconciliationOperationError(direction) from error
            instances = await self._runtime.list_instances(self._fleet.deployment)

        observations: list[ObservedInstance] = []
        replaced = 0
        live_ids = {instance.instance_id for instance in instances}
        for instance_id in set(self._unhealthy_since) - live_ids:
            self._unhealthy_since.pop(instance_id, None)

        for instance in instances:
            ready = await self._runtime.port_open(instance, definition.connection_port)
            now = datetime.now(UTC)
            if ready:
                self._unhealthy_since.pop(instance.instance_id, None)
            else:
                self._unhealthy_since.setdefault(instance.instance_id, now)
            unhealthy_since = self._unhealthy_since.get(instance.instance_id)
            startup_expired = (
                unhealthy_since is not None
                and (now - unhealthy_since).total_seconds() >= self._startup_timeout_seconds
            )
            if startup_expired:
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
                )
            )

        await self._repository.observe_instances(
            provider,
            observations,
            platform=self._runtime.platform,
            observation_ttl_seconds=self._observation_ttl_seconds,
        )
        await self._repository.record_reconciliation(provider, status="ready")
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
        )
