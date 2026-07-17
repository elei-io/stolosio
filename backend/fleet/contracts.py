from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from backend.proxy.contracts import ProviderName


class FleetInstanceState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class FleetConfiguration:
    provider: ProviderName
    minimum_instances: int
    maximum_instances: int
    session_capacity_per_instance: int
    scale_down_cooldown_seconds: int
    desired_instances: int
    configuration_version: int
    enabled: bool
    idle_since: datetime | None = None
    last_reconciled_at: datetime | None = None
    controller_status: str | None = None


@dataclass(frozen=True, slots=True)
class FleetInstance:
    instance_id: str
    provider: ProviderName
    platform: str
    endpoint: str
    state: FleetInstanceState
    capacity: int
    observed_at: datetime
    observation_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ObservedInstance:
    instance_id: str
    endpoint: str
    state: FleetInstanceState
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuntimeInstance:
    """A provider process as seen by an infrastructure runtime."""

    instance_id: str
    address: str
    started_at: datetime | None = None


class FleetRuntime(Protocol):
    """Infrastructure operations required by the fleet reconciler."""

    @property
    def platform(self) -> str: ...

    async def list_instances(self, deployment: str) -> list[RuntimeInstance]: ...

    async def scale(self, deployment: str, replicas: int) -> None: ...

    async def remove(self, deployment: str, instance_id: str) -> None: ...

    async def port_open(self, instance: RuntimeInstance, port: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    configuration: FleetConfiguration
    observed_instances: int
    ready_instances: int
    draining_instances: int
    unhealthy_instances: int
    total_slots: int
    occupied_slots: int
    available_slots: int
    active_attempts: int
    queued_attempts: int
