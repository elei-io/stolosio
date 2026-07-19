from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from backend.fleet.contracts import FleetConfiguration


@dataclass(frozen=True, slots=True)
class ScalingDecision:
    desired_instances: int
    direction: str | None
    idle_since: datetime | None
    demand: int


def scaling_decision(
    configuration: FleetConfiguration,
    *,
    demand: int,
    now: datetime,
    provisioned_instances: int | None = None,
    provisioned_capacity: int | None = None,
) -> ScalingDecision:
    current = configuration.desired_instances
    if not configuration.enabled:
        target = 0
    else:
        required = ceil(demand / configuration.session_capacity_per_instance)
        target = min(
            configuration.maximum_instances,
            max(configuration.minimum_instances, required),
        )
        if (
            demand > 0
            and provisioned_instances is not None
            and provisioned_capacity is not None
            and provisioned_instances >= current
            and demand > provisioned_capacity
        ):
            target = max(
                target,
                min(configuration.maximum_instances, current + 1),
            )

    if target > current:
        return ScalingDecision(current + 1, "up", None, demand)

    idle_since = configuration.idle_since
    if demand > 0:
        return ScalingDecision(current, None, None, demand)
    if idle_since is None:
        return ScalingDecision(current, None, now, demand)
    cooldown = timedelta(seconds=configuration.scale_down_cooldown_seconds)
    if target < current and now - idle_since >= cooldown:
        return ScalingDecision(current - 1, "down", idle_since, demand)
    return ScalingDecision(current, None, idle_since, demand)
