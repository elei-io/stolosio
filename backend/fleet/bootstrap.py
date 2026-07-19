from dataclasses import dataclass

from backend.fleet.repository import FleetRepository
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class FleetBootstrapConfiguration:
    provider: ProviderName
    minimum_instances: int
    maximum_instances: int
    session_capacity_per_instance: int
    scale_down_cooldown_seconds: int
    max_queued_attempts: int


def managed_fleet_configurations() -> tuple[FleetBootstrapConfiguration, ...]:
    return (
        FleetBootstrapConfiguration(
            provider=ProviderName.BROWSERLESS,
            minimum_instances=1,
            maximum_instances=4,
            session_capacity_per_instance=5,
            scale_down_cooldown_seconds=30,
            max_queued_attempts=100,
        ),
    )


async def ensure_managed_fleets(repository: FleetRepository) -> None:
    for configuration in managed_fleet_configurations():
        await repository.ensure_fleet(
            configuration.provider,
            minimum_instances=configuration.minimum_instances,
            maximum_instances=configuration.maximum_instances,
            session_capacity_per_instance=configuration.session_capacity_per_instance,
            scale_down_cooldown_seconds=configuration.scale_down_cooldown_seconds,
            max_queued_attempts=configuration.max_queued_attempts,
        )
