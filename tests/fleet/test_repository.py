import pytest
from sqlalchemy import select

from backend.db.models import FleetConfigurationEvent
from backend.fleet import FleetRepository
from backend.proxy.contracts import ProviderName


@pytest.mark.asyncio
async def test_fleet_configuration_update_is_versioned_and_audited(database_sessions) -> None:
    repository = FleetRepository(database_sessions)
    await repository.ensure_fleet(
        ProviderName.CHROMIUM,
        minimum_instances=1,
        maximum_instances=4,
        session_capacity_per_instance=2,
        scale_down_cooldown_seconds=30,
    )

    updated = await repository.update_configuration(
        ProviderName.CHROMIUM,
        {"maximum_instances": 8, "session_capacity_per_instance": 3},
        actor="test-operator",
    )

    assert updated is not None
    assert updated.configuration_version == 2
    async with database_sessions() as database:
        event = await database.scalar(select(FleetConfigurationEvent))
    assert event is not None
    assert event.actor == "test-operator"
    assert event.configuration_version == 2
    assert event.previous_values["maximum_instances"] == 4
    assert event.new_values["maximum_instances"] == 8
