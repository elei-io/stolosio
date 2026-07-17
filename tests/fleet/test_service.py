import pytest

from backend.fleet.service import FleetService
from backend.proxy.contracts import ProviderName


class FakeRepository:
    async def update_configuration(self, provider, values, *, actor):
        raise AssertionError("invalid capacity must not reach the repository")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "capacity", "maximum"),
    [
        (ProviderName.BROWSERLESS, 6, 5),
        (ProviderName.LIGHTPANDA, 2, 1),
        (ProviderName.CAMOUFOX, 2, 1),
    ],
)
async def test_provider_capacity_cannot_exceed_process_limit(
    provider: ProviderName,
    capacity: int,
    maximum: int,
) -> None:
    service = FleetService(FakeRepository())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=rf"at most {maximum} session"):
        await service.update(
            provider,
            {"session_capacity_per_instance": capacity},
            actor="test",
        )
