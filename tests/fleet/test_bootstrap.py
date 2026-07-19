from backend.fleet.bootstrap import managed_fleet_configurations
from backend.fleet.providers import MANAGED_FLEETS
from backend.proxy.contracts import ProviderName


def test_every_browser_provider_has_a_bootstrap_configuration() -> None:
    configurations = managed_fleet_configurations()

    assert {configuration.provider for configuration in configurations} == {
        ProviderName.BROWSERLESS,
    }
    assert set(MANAGED_FLEETS) == {
        ProviderName.BROWSERLESS,
    }
    assert ProviderName.HTTP not in MANAGED_FLEETS
    assert configurations[0].session_capacity_per_instance == 5
    assert configurations[0].max_queued_attempts == 100
