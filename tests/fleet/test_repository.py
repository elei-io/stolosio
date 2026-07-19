import pytest
from sqlalchemy import select

from backend.db.models import FleetConfigurationEvent
from backend.fleet import FleetRepository
from backend.proxy.contracts import ProviderName


@pytest.mark.asyncio
async def test_fleet_configuration_update_is_versioned_and_audited(database_sessions) -> None:
    repository = FleetRepository(database_sessions)
    await repository.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=4,
        session_capacity_per_instance=2,
        scale_down_cooldown_seconds=30,
    )

    updated = await repository.update_configuration(
        ProviderName.BROWSERLESS,
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


@pytest.mark.asyncio
async def test_bootstrap_preserves_database_fleet_limits(database_sessions) -> None:
    repository = FleetRepository(database_sessions)
    await repository.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=4,
        session_capacity_per_instance=2,
        scale_down_cooldown_seconds=30,
    )

    preserved = await repository.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=2,
        maximum_instances=6,
        session_capacity_per_instance=7,
        scale_down_cooldown_seconds=45,
    )

    assert preserved.configuration_version == 1
    assert preserved.minimum_instances == 1
    assert preserved.maximum_instances == 4
    assert preserved.session_capacity_per_instance == 2
    assert preserved.desired_instances == 1
    async with database_sessions() as database:
        event = await database.scalar(
            select(FleetConfigurationEvent).where(
                FleetConfigurationEvent.actor == "settings-bootstrap"
            )
        )
    assert event is None
