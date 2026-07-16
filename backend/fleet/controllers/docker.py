import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

from backend.db.session import engine, session_factory
from backend.fleet import FleetInstanceState, FleetRepository, ObservedInstance
from backend.proxy.contracts import ProviderName
from backend.settings import settings

logger = logging.getLogger(__name__)

CONTROLLER_REGISTRY = CollectorRegistry(auto_describe=True)
SCALING_ACTIONS = Counter(
    "harbor_provider_scaling_actions",
    "Fleet scaling actions by direction and stable outcome.",
    ("provider", "direction", "outcome"),
    registry=CONTROLLER_REGISTRY,
)
RECONCILE_ERRORS = Counter(
    "harbor_provider_reconcile_errors",
    "Fleet reconciliation errors by stable reason.",
    ("provider", "reason"),
    registry=CONTROLLER_REGISTRY,
)
LAST_SUCCESSFUL_RECONCILE = Gauge(
    "harbor_provider_last_successful_reconcile_timestamp",
    "Unix timestamp of the last successful fleet reconciliation.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)
RECONCILE_DURATION = Histogram(
    "harbor_provider_reconcile_duration_seconds",
    "Fleet reconciliation duration by provider.",
    ("provider",),
    registry=CONTROLLER_REGISTRY,
)


class DockerCommandError(RuntimeError):
    pass


class DockerFleetController:
    def __init__(
        self,
        repository: FleetRepository,
        *,
        workdir: Path,
        project_name: str,
        service: str,
        observation_ttl_seconds: float,
        startup_timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._workdir = workdir
        self._project_name = project_name
        self._service = service
        self._observation_ttl_seconds = observation_ttl_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._unhealthy_since: dict[str, datetime] = {}

    async def reconcile(self) -> None:
        provider = ProviderName.CHROMIUM
        configuration, decision = await self._repository.evaluate(provider)
        containers = await self._containers()
        current = len(containers)
        if current != configuration.desired_instances:
            next_count = current + 1 if current < configuration.desired_instances else current - 1
            direction = "up" if next_count > current else "down"
            try:
                await self._scale(next_count)
            except DockerCommandError:
                SCALING_ACTIONS.labels(provider.value, direction, "failed").inc()
                await self._repository.record_reconciliation(
                    provider,
                    status="docker_scale_failed",
                )
                raise
            SCALING_ACTIONS.labels(provider.value, direction, "succeeded").inc()
            containers = await self._containers()

        observations = []
        for container in containers:
            ready = await self._healthy(container["id"])
            started_at = datetime.fromisoformat(container["started_at"].replace("Z", "+00:00"))
            now = datetime.now(UTC)
            if ready:
                self._unhealthy_since.pop(container["id"], None)
            else:
                self._unhealthy_since.setdefault(container["id"], now)
            unhealthy_since = self._unhealthy_since.get(container["id"])
            startup_expired = (
                unhealthy_since is not None
                and (now - unhealthy_since).total_seconds() >= self._startup_timeout_seconds
            )
            if startup_expired:
                try:
                    await self._remove(container["id"])
                except DockerCommandError:
                    SCALING_ACTIONS.labels(provider.value, "replace", "failed").inc()
                    await self._repository.record_reconciliation(
                        provider,
                        status="docker_replace_failed",
                    )
                    raise
                self._unhealthy_since.pop(container["id"], None)
                SCALING_ACTIONS.labels(provider.value, "replace", "succeeded").inc()
                continue
            observations.append(
                ObservedInstance(
                    instance_id=container["id"],
                    endpoint=f"ws://{container['name']}:9222",
                    state=(FleetInstanceState.READY if ready else FleetInstanceState.STARTING),
                    started_at=started_at,
                )
            )
        await self._repository.observe_instances(
            provider,
            observations,
            platform="docker-compose",
            observation_ttl_seconds=self._observation_ttl_seconds,
        )
        await self._repository.record_reconciliation(provider, status="ready")
        LAST_SUCCESSFUL_RECONCILE.labels(provider.value).set_to_current_time()
        if decision.direction is not None:
            logger.info(
                "Chromium fleet policy requested scale %s to %s instances",
                decision.direction,
                configuration.desired_instances,
            )

    async def _containers(self) -> list[dict[str, str]]:
        output = await self._run(
            "docker",
            "compose",
            "-p",
            self._project_name,
            "ps",
            "-q",
            self._service,
        )
        ids = [value for value in output.splitlines() if value]
        containers = []
        for container_id in ids:
            raw = await self._run("docker", "inspect", container_id)
            payload = json.loads(raw)
            if not isinstance(payload, list) or not payload:
                continue
            container = payload[0]
            if not container.get("State", {}).get("Running"):
                continue
            name = str(container.get("Name", "")).removeprefix("/")
            if name:
                containers.append(
                    {
                        "id": container_id,
                        "name": name,
                        "started_at": str(container.get("State", {}).get("StartedAt")),
                    }
                )
        return sorted(containers, key=lambda container: container["name"])

    async def _healthy(self, container_id: str) -> bool:
        try:
            await self._run(
                "docker",
                "exec",
                container_id,
                "/usr/bin/bash",
                "-c",
                "exec 3<>/dev/tcp/127.0.0.1/9222",
            )
        except DockerCommandError:
            return False
        return True

    async def _scale(self, replicas: int) -> None:
        await self._run(
            "docker",
            "compose",
            "-p",
            self._project_name,
            "up",
            "-d",
            "--scale",
            f"{self._service}={replicas}",
            "--no-recreate",
            self._service,
        )

    async def _remove(self, container_id: str) -> None:
        await self._run("docker", "rm", "--force", container_id)

    async def _run(self, *command: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise DockerCommandError(message or f"Command failed: {command[0]}")
        return stdout.decode()


async def run(*, once: bool) -> None:
    repository = FleetRepository(session_factory)
    await repository.ensure_fleet(
        ProviderName.CHROMIUM,
        minimum_instances=settings.chromium_minimum_instances,
        maximum_instances=settings.chromium_maximum_instances,
        session_capacity_per_instance=settings.chromium_session_capacity_per_instance,
        scale_down_cooldown_seconds=settings.chromium_scale_down_cooldown_seconds,
    )
    controller = DockerFleetController(
        repository,
        workdir=Path(settings.fleet_compose_workdir),
        project_name=settings.fleet_compose_project_name,
        service=settings.fleet_compose_service,
        observation_ttl_seconds=settings.fleet_observation_ttl_seconds,
        startup_timeout_seconds=settings.fleet_instance_startup_timeout_seconds,
    )
    try:
        while True:
            started = time.monotonic()
            try:
                await controller.reconcile()
            except Exception:
                RECONCILE_ERRORS.labels("chromium", "reconcile_failed").inc()
                logger.exception("Chromium fleet reconciliation failed")
                if once:
                    raise
                await asyncio.sleep(settings.fleet_controller_backoff_seconds)
            finally:
                RECONCILE_DURATION.labels("chromium").observe(time.monotonic() - started)
            if once:
                return
            await asyncio.sleep(settings.fleet_reconcile_interval_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile Harbor's local Chromium fleet")
    parser.add_argument("--once", action="store_true", help="Run one reconciliation pass")
    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Do not expose the controller Prometheus endpoint",
    )
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not arguments.no_metrics:
        start_http_server(settings.fleet_controller_metrics_port, registry=CONTROLLER_REGISTRY)
    try:
        asyncio.run(run(once=arguments.once))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
