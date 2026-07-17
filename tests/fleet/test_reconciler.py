from datetime import UTC, datetime

import pytest

from backend.fleet import FleetConfiguration, RuntimeInstance
from backend.fleet.policy import ScalingDecision
from backend.fleet.providers import CAMOUFOX_FLEET, CHROMIUM_FLEET, LIGHTPANDA_FLEET
from backend.fleet.reconciler import FleetReconciler, ManagedFleet
from backend.proxy.contracts import ProviderName


class FakeRepository:
    def __init__(
        self,
        provider: ProviderName,
        *,
        desired_instances: int = 1,
    ) -> None:
        self.provider = provider
        self.desired_instances = desired_instances
        self.observations = None
        self.platform = None
        self.statuses: list[str] = []

    async def evaluate(self, provider):
        assert provider is self.provider
        configuration = FleetConfiguration(
            provider=provider,
            minimum_instances=1,
            maximum_instances=4,
            session_capacity_per_instance=1,
            scale_down_cooldown_seconds=30,
            desired_instances=self.desired_instances,
            configuration_version=1,
            enabled=True,
        )
        return configuration, ScalingDecision(self.desired_instances, None, None)

    async def observe_instances(self, provider, observations, *, platform, **kwargs):
        assert provider is self.provider
        self.observations = observations
        self.platform = platform

    async def record_reconciliation(self, provider, *, status):
        assert provider is self.provider
        self.statuses.append(status)


class FakeRuntime:
    platform = "fake-runtime"

    def __init__(self, instances: list[RuntimeInstance], *, ready: bool = True) -> None:
        self.instances = instances
        self.ready = ready
        self.scaled: list[tuple[str, int]] = []
        self.removed: list[tuple[str, str]] = []
        self.probed_ports: list[int] = []

    async def list_instances(self, deployment):
        return self.instances

    async def scale(self, deployment, replicas):
        self.scaled.append((deployment, replicas))

    async def remove(self, deployment, instance_id):
        self.removed.append((deployment, instance_id))

    async def port_open(self, instance, port):
        self.probed_ports.append(port)
        return self.ready


@pytest.mark.asyncio
async def test_reconciler_routes_lightpanda_through_runtime_neutral_contract() -> None:
    repository = FakeRepository(ProviderName.LIGHTPANDA)
    runtime = FakeRuntime(
        [RuntimeInstance("lightpanda-1", "harbor-lightpanda-1", datetime.now(UTC))]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(LIGHTPANDA_FLEET, "lightpanda-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert result.provider == "lightpanda"
    assert runtime.probed_ports == [9222]
    assert repository.platform == "fake-runtime"
    assert repository.observations[0].endpoint == "ws://harbor-lightpanda-1:9222"
    assert repository.observations[0].state.value == "ready"


@pytest.mark.asyncio
async def test_reconciler_preserves_managed_provider_connection_path() -> None:
    repository = FakeRepository(ProviderName.CAMOUFOX)
    runtime = FakeRuntime(
        [RuntimeInstance("camoufox-1", "harbor-camoufox-1", datetime.now(UTC))]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(CAMOUFOX_FLEET, "camoufox-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    await reconciler.reconcile()

    assert runtime.probed_ports == [1234]
    assert repository.observations[0].endpoint == "ws://harbor-camoufox-1:1234/harbor"


@pytest.mark.asyncio
async def test_reconciler_scales_one_runtime_unit_toward_desired_state() -> None:
    repository = FakeRepository(ProviderName.CHROMIUM, desired_instances=3)
    runtime = FakeRuntime(
        [RuntimeInstance("chromium-1", "harbor-chromium-1", datetime.now(UTC))]
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(CHROMIUM_FLEET, "chromium-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=30,
    )

    result = await reconciler.reconcile()

    assert runtime.scaled == [("chromium-deployment", 2)]
    assert result.scale_direction == "up"


@pytest.mark.asyncio
async def test_reconciler_replaces_instance_that_never_becomes_healthy() -> None:
    repository = FakeRepository(ProviderName.CHROMIUM)
    runtime = FakeRuntime(
        [RuntimeInstance("failed-instance", "harbor-chromium-1", datetime.now(UTC))],
        ready=False,
    )
    reconciler = FleetReconciler(
        repository,  # type: ignore[arg-type]
        runtime,
        ManagedFleet(CHROMIUM_FLEET, "chromium-deployment"),
        observation_ttl_seconds=5,
        startup_timeout_seconds=0,
    )

    result = await reconciler.reconcile()

    assert runtime.removed == [("chromium-deployment", "failed-instance")]
    assert repository.observations == []
    assert repository.statuses == ["ready"]
    assert result.replaced_instances == 1
