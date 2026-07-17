from dataclasses import dataclass

from backend.fleet.providers import MANAGED_FLEETS, managed_fleet_definition
from backend.fleet.repository import FleetRepository
from backend.proxy.contracts import ProviderName
from backend.settings import Settings


@dataclass(frozen=True, slots=True)
class FleetBootstrapConfiguration:
    provider: ProviderName
    minimum_instances: int
    maximum_instances: int
    session_capacity_per_instance: int
    scale_down_cooldown_seconds: int


def managed_fleet_configurations(settings: Settings) -> tuple[FleetBootstrapConfiguration, ...]:
    return tuple(
        FleetBootstrapConfiguration(
            provider=provider,
            minimum_instances=getattr(settings, f"{provider.value}_minimum_instances"),
            maximum_instances=getattr(settings, f"{provider.value}_maximum_instances"),
            session_capacity_per_instance=getattr(
                settings,
                f"{provider.value}_session_capacity_per_instance",
            ),
            scale_down_cooldown_seconds=getattr(
                settings,
                f"{provider.value}_scale_down_cooldown_seconds",
            ),
        )
        for provider in MANAGED_FLEETS
    )


async def ensure_managed_fleets(repository: FleetRepository, settings: Settings) -> None:
    for configuration in managed_fleet_configurations(settings):
        definition = managed_fleet_definition(configuration.provider)
        if (
            definition is not None
            and definition.maximum_session_capacity is not None
            and configuration.session_capacity_per_instance
            > definition.maximum_session_capacity
        ):
            raise ValueError(
                f"{configuration.provider.value} supports at most "
                f"{definition.maximum_session_capacity} session per instance"
            )
        await repository.ensure_fleet(
            configuration.provider,
            minimum_instances=configuration.minimum_instances,
            maximum_instances=configuration.maximum_instances,
            session_capacity_per_instance=configuration.session_capacity_per_instance,
            scale_down_cooldown_seconds=configuration.scale_down_cooldown_seconds,
        )
