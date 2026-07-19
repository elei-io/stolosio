from datetime import UTC, datetime

import pytest

from backend.fleet import FleetConfiguration, RuntimeInstance
from backend.fleet.policy import ScalingDecision
from backend.fleet.providers import BROWSERLESS_FLEET
from backend.fleet.reconciler import FleetReconciler, ManagedFleet
from backend.proxy.contracts import ProviderName


class FakeRepository:
    def __init__(
        self,
        provider: ProviderName,
        *,
        desired_instances: int = 1,
        demand: int = 0,
    ) -> None:
        self.provider = provider
        self.desired_instances = desired_instances
        self.demand = demand
        self.observations = None
        self.platform = None
        self.statuses: list[str] = []
        self.scale_down_ready = True
        self.drained: list[str] = []
        self.cancelled_scale_down = 0

    async def evaluate(self, provider):
        assert provider is self.provider
        configuration = FleetConfiguration(
            provider=provider,
            minimum_instances=1,
            maximum_instances=4,
            session_capacity_per_instance=1,
            scale_down_cooldown_seconds=30,
            max_queued_attempts=100,
            desired_instances=self.desired_instances,
            configuration_version=1,
            enabled=True,
        )
        return configuration, ScalingDecision(
            self.desired_instances,
            None,
            None,
            self.demand,
        )

    async def observe_instances(self, provider, observations, *, platform, **kwargs):
        assert provider is self.provider
        self.observations = observations
        self.platform = platform

    async def record_reconciliation(self, provider, *, status):
        assert provider is self.provider
        self.statuses.append(status)

    async def prepare_scale_down(self, provider, instance_id):
        assert provider is self.provider
        self.drained.append(instance_id)
        return self.scale_down_ready

    async def cancel_scale_down(self, provider):
        assert provider is self.provider
        self.cancelled_scale_down += 1


class FakeRuntime:
    platform = "fake-runtime"

    def __init__(self, instances: list[RuntimeInstance], *, ready: bool = True) -> None:
        self.instances = instances
        self.ready = ready
        self.scaled: list[tuple[str, int, int]] = []
        self.removed: list[tuple[str, str]] = []
        self.probed_ports: list[int] = []

    async def list_instances(self, deployment):
        return self.instances

    async def scale(self, deployment, replicas, *, session_capacity):
        self.scaled.append((deployment, replicas, session_capacity))

    def scale_down_candidate(self, instances):
        return instances[-1] if instances else None

    async def remove(self, deployment, instance_id):
        self.removed.append((deployment, instance_id))

    async def port_open(self, instance, port):
        self.probed_ports.append(port)
        return self.ready


@pytest.mark.asyncio
async def test_reconciler_routes_browserless_through_runtime_neutral_contract() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS)
    runtime = FakeRuntime(
        [
            RuntimeInstance(
                "browserless-1",
                "harbor-browserless-1",
                datetime.now(UTC),
                session_capacity=1,
            )
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert result.provider == "browserless"
    assert runtime.probed_ports == [3000]
    assert repository.platform == "fake-runtime"
    assert repository.observations[0].endpoint == "ws://harbor-browserless-1:3000"
    assert repository.observations[0].state.value == "ready"


@pytest.mark.asyncio
async def test_reconciler_scales_one_runtime_unit_toward_desired_state() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS, desired_instances=3)
    runtime = FakeRuntime(
        [
            RuntimeInstance(
                "browserless-1",
                "harbor-browserless-1",
                datetime.now(UTC),
                session_capacity=1,
            )
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert runtime.scaled == [("browserless-deployment", 2, 1)]
    assert result.scale_direction == "up"


@pytest.mark.asyncio
async def test_reconciler_replaces_instance_that_never_becomes_healthy() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS)
    runtime = FakeRuntime(
        [
            RuntimeInstance(
                "failed-instance",
                "harbor-browserless-1",
                datetime.now(UTC),
                session_capacity=1,
            )
        ],
        ready=False,
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=0,
    )

    result = await reconciler.reconcile()

    assert runtime.removed == [("browserless-deployment", "failed-instance")]
    assert repository.observations == []
    assert repository.statuses == ["ready"]
    assert result.replaced_instances == 1


@pytest.mark.asyncio
async def test_reconciler_reconfigures_existing_workers_when_capacity_changes() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS)
    runtime = FakeRuntime(
        [
            RuntimeInstance(
                "browserless-1",
                "harbor-browserless-1",
                datetime.now(UTC),
                session_capacity=5,
            )
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert runtime.scaled == [("browserless-deployment", 1, 1)]
    assert result.scale_direction == "configure"


@pytest.mark.asyncio
async def test_reconciler_waits_for_active_sessions_before_reconfiguring() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS, demand=1)
    runtime = FakeRuntime(
        [
            RuntimeInstance(
                "browserless-1",
                "harbor-browserless-1",
                datetime.now(UTC),
                session_capacity=5,
            )
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert runtime.scaled == []
    assert result.scale_direction is None


@pytest.mark.asyncio
async def test_reconciler_drains_runtime_selected_instance_before_scale_down() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS, desired_instances=1)
    runtime = FakeRuntime(
        [
            RuntimeInstance("browserless-0", "harbor-browserless-0", session_capacity=1),
            RuntimeInstance("browserless-1", "harbor-browserless-1", session_capacity=1),
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert repository.drained == ["browserless-1"]
    assert runtime.scaled == [("browserless-deployment", 1, 1)]
    assert result.scale_direction == "down"


@pytest.mark.asyncio
async def test_reconciler_waits_until_scale_down_candidate_is_idle() -> None:
    repository = FakeRepository(ProviderName.BROWSERLESS, desired_instances=1)
    repository.scale_down_ready = False
    runtime = FakeRuntime(
        [
            RuntimeInstance("browserless-0", "harbor-browserless-0", session_capacity=1),
            RuntimeInstance("browserless-1", "harbor-browserless-1", session_capacity=1),
        ]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(BROWSERLESS_FLEET, "browserless-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert runtime.scaled == []
    assert "waiting_for_scale_down_drain" in repository.statuses
    assert result.scale_direction is None
