from datetime import UTC, datetime

import pytest

from backend.fleet import FleetConfiguration
from backend.fleet.controllers.docker import DockerFleetController
from backend.fleet.policy import ScalingDecision
from backend.proxy.contracts import ProviderName


class FakeRepository:
    def __init__(self) -> None:
        self.observations = None
        self.statuses: list[str] = []

    async def evaluate(self, provider):
        configuration = FleetConfiguration(
            provider=ProviderName.CHROMIUM,
            minimum_instances=1,
            maximum_instances=2,
            session_capacity_per_instance=2,
            scale_down_cooldown_seconds=30,
            desired_instances=1,
            configuration_version=1,
            enabled=True,
        )
        return configuration, ScalingDecision(1, None, None)

    async def observe_instances(self, provider, observations, **kwargs):
        self.observations = observations

    async def record_reconciliation(self, provider, *, status):
        self.statuses.append(status)


@pytest.mark.asyncio
async def test_controller_removes_instance_that_never_becomes_healthy(tmp_path) -> None:
    repository = FakeRepository()
    controller = DockerFleetController(
        repository,  # type: ignore[arg-type]
        workdir=tmp_path,
        project_name="harbor",
        service="chromium",
        observation_ttl_seconds=5,
        startup_timeout_seconds=0,
    )
    removed: list[str] = []

    async def containers():
        return [
            {
                "id": "failed-instance",
                "name": "harbor-chromium-1",
                "started_at": datetime.now(UTC).isoformat(),
            }
        ]

    async def unhealthy(container_id):
        return False

    async def remove(container_id):
        removed.append(container_id)

    controller._containers = containers  # type: ignore[method-assign]
    controller._healthy = unhealthy  # type: ignore[method-assign]
    controller._remove = remove  # type: ignore[method-assign]

    await controller.reconcile()

    assert removed == ["failed-instance"]
    assert repository.observations == []
    assert repository.statuses == ["ready"]
