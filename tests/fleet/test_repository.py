from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.db.models import AcquisitionAttempt, FleetConfigurationEvent, GatewaySession
from backend.fleet import FleetInstanceState, FleetRepository, ObservedInstance
from backend.proxy.contracts import AttemptState, ProviderName, SessionState


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


@pytest.mark.asyncio
async def test_evaluate_scales_from_observed_capacity_during_transition(
    database_sessions,
) -> None:
    repository = FleetRepository(database_sessions)
    await repository.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=4,
        session_capacity_per_instance=5,
        scale_down_cooldown_seconds=30,
    )
    await repository.observe_instances(
        ProviderName.BROWSERLESS,
        [
            ObservedInstance(
                instance_id="browserless-0",
                endpoint="ws://browserless-0:3000",
                state=FleetInstanceState.READY,
                session_capacity=5,
            )
        ],
        platform="test",
        observation_ttl_seconds=60,
    )
    await repository.update_configuration(
        ProviderName.BROWSERLESS,
        {"session_capacity_per_instance": 10},
        actor="test-operator",
    )
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        session_ids = [str(uuid4()) for _ in range(8)]
        for session_id in session_ids:
            database.add(
                GatewaySession(
                    id=session_id,
                    owner_id=str(uuid4()),
                    lease_token=str(uuid4()),
                    requested_settings={},
                    state=SessionState.OPEN.value,
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
        await database.flush()
        for session_id in session_ids:
            database.add(
                AcquisitionAttempt(
                    id=str(uuid4()),
                    session_id=session_id,
                    ordinal=1,
                    provider=ProviderName.BROWSERLESS.value,
                    resolved_settings={},
                    setting_sources={},
                    state=AttemptState.QUEUED.value,
                )
            )

    configuration, decision = await repository.evaluate(ProviderName.BROWSERLESS)

    assert configuration.desired_instances == 2
    assert decision.direction == "up"

    configuration, decision = await repository.evaluate(ProviderName.BROWSERLESS)

    assert configuration.desired_instances == 2
    assert decision.direction is None
