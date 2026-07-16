from datetime import UTC, datetime, timedelta

from backend.fleet import FleetConfiguration, scaling_decision
from backend.proxy.contracts import ProviderName


def configuration(**changes) -> FleetConfiguration:
    values = {
        "provider": ProviderName.CHROMIUM,
        "minimum_instances": 1,
        "maximum_instances": 4,
        "session_capacity_per_instance": 2,
        "scale_down_cooldown_seconds": 30,
        "desired_instances": 1,
        "configuration_version": 1,
        "enabled": True,
    }
    values.update(changes)
    return FleetConfiguration(**values)


def test_policy_scales_one_instance_at_a_time_for_slot_demand() -> None:
    now = datetime.now(UTC)

    assert scaling_decision(configuration(), demand=2, now=now).desired_instances == 1
    decision = scaling_decision(configuration(), demand=3, now=now)
    assert decision.desired_instances == 2
    assert decision.direction == "up"


def test_policy_scales_down_only_after_the_whole_fleet_is_idle_for_cooldown() -> None:
    now = datetime.now(UTC)
    idle_since = now - timedelta(seconds=31)
    configured = configuration(desired_instances=3, idle_since=idle_since)

    assert scaling_decision(configured, demand=1, now=now).desired_instances == 3
    decision = scaling_decision(configured, demand=0, now=now)
    assert decision.desired_instances == 2
    assert decision.direction == "down"


def test_disabled_fleet_converges_to_zero_after_cooldown() -> None:
    now = datetime.now(UTC)
    configured = configuration(
        desired_instances=1,
        enabled=False,
        idle_since=now - timedelta(seconds=31),
    )

    assert scaling_decision(configured, demand=0, now=now).desired_instances == 0
