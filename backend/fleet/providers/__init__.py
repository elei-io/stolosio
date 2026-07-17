from backend.fleet.providers.chromium import CHROMIUM_FLEET
from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.fleet.providers.lightpanda import LIGHTPANDA_FLEET
from backend.proxy.contracts import ProviderName

MANAGED_FLEETS = {
    definition.provider: definition
    for definition in (CHROMIUM_FLEET, LIGHTPANDA_FLEET)
}


def managed_fleet_definition(provider: ProviderName) -> ProviderFleetDefinition | None:
    return MANAGED_FLEETS.get(provider)

__all__ = [
    "CHROMIUM_FLEET",
    "LIGHTPANDA_FLEET",
    "MANAGED_FLEETS",
    "ProviderFleetDefinition",
    "managed_fleet_definition",
]
