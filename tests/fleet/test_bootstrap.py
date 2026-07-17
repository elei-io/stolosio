from backend.fleet.bootstrap import managed_fleet_configurations
from backend.fleet.providers import MANAGED_FLEETS
from backend.proxy.contracts import ProviderName
from backend.settings import Settings


def test_every_browser_provider_has_a_bootstrap_configuration() -> None:
    configurations = managed_fleet_configurations(Settings())

    assert {configuration.provider for configuration in configurations} == {
        ProviderName.CHROMIUM,
        ProviderName.BROWSERLESS,
        ProviderName.LIGHTPANDA,
        ProviderName.CAMOUFOX,
    }
    assert set(MANAGED_FLEETS) == {
        ProviderName.CHROMIUM,
        ProviderName.BROWSERLESS,
        ProviderName.LIGHTPANDA,
        ProviderName.CAMOUFOX,
    }
    assert ProviderName.HTTP not in MANAGED_FLEETS
