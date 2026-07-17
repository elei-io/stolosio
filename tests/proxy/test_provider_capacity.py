import pytest

from backend.proxy.attempts import provider_capacity
from backend.proxy.contracts import ProviderName
from backend.settings import Settings


@pytest.mark.parametrize(
    ("provider", "maximum_instances", "session_capacity", "expected"),
    [
        (ProviderName.CHROMIUM, 3, 4, 12),
        (ProviderName.BROWSERLESS, 3, 5, 15),
        (ProviderName.LIGHTPANDA, 3, 1, 3),
        (ProviderName.CAMOUFOX, 3, 1, 3),
    ],
)
def test_managed_provider_admission_matches_fleet_capacity(
    provider: ProviderName,
    maximum_instances: int,
    session_capacity: int,
    expected: int,
) -> None:
    prefix = provider.value
    settings = Settings(
        **{
            f"{prefix}_maximum_instances": maximum_instances,
            f"{prefix}_session_capacity_per_instance": session_capacity,
        }
    )

    assert provider_capacity(settings, provider).max_active == expected
