import pytest

from backend.fleet.service import FleetService
from backend.proxy.contracts import ProviderName


class FakeRepository:
    def __init__(self) -> None:
        self.values = None

    async def update_configuration(self, provider, values, *, actor):
        self.values = values
        return "updated"


@pytest.mark.asyncio
async def test_provider_capacity_is_operator_controlled() -> None:
    repository = FakeRepository()
    service = FleetService(repository)  # type: ignore[arg-type]

    result = await service.update(
        ProviderName.BROWSERLESS,
        {"session_capacity_per_instance": 37},
        actor="test",
    )

    assert result == "updated"
    assert repository.values == {"session_capacity_per_instance": 37}
