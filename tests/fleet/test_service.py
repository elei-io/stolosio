import pytest

from backend.fleet.service import FleetService
from backend.proxy.contracts import ProviderName


class FakeRepository:
    async def update_configuration(self, provider, values, *, actor):
        raise AssertionError("invalid capacity must not reach the repository")


@pytest.mark.asyncio
async def test_lightpanda_capacity_cannot_exceed_provider_limit() -> None:
    service = FleetService(FakeRepository())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="at most 1 session"):
        await service.update(
            ProviderName.LIGHTPANDA,
            {"session_capacity_per_instance": 2},
            actor="test",
        )
