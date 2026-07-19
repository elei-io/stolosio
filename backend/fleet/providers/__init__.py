from backend.fleet.providers.browserless import BROWSERLESS_FLEET
from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

MANAGED_FLEETS = {BROWSERLESS_FLEET.provider: BROWSERLESS_FLEET}


def managed_fleet_definition(provider: ProviderName) -> ProviderFleetDefinition | None:
    return MANAGED_FLEETS.get(provider)

__all__ = [
    "BROWSERLESS_FLEET",
    "MANAGED_FLEETS",
    "ProviderFleetDefinition",
    "managed_fleet_definition",
]
