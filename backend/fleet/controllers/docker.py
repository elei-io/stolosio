import argparse
import asyncio
import logging
import time
from pathlib import Path

from prometheus_client import start_http_server

from backend.db.session import engine, session_factory
from backend.fleet import FleetRepository
from backend.fleet.bootstrap import ensure_managed_fleets
from backend.fleet.controller_metrics import (
    CONTROLLER_REGISTRY,
    LAST_SUCCESSFUL_RECONCILE,
    RECONCILE_DURATION,
    RECONCILE_ERRORS,
    SCALE_REQUEST_TO_FIRST_ASSIGNMENT,
    SCALE_REQUEST_TO_READY,
    SCALE_REQUEST_UNASSIGNED,
    SCALING_ACTIONS,
)
from backend.fleet.providers import MANAGED_FLEETS
from backend.fleet.reconciler import (
    FleetReconciler,
    ManagedFleet,
    ReconciliationOperationError,
)
from backend.fleet.runtimes import DockerComposeRuntime
from backend.settings import settings

logger = logging.getLogger(__name__)


def _reconcilers(repository: FleetRepository) -> list[FleetReconciler]:
    runtime = DockerComposeRuntime(
        workdir=Path(settings.fleet_compose_workdir),
        project_name=settings.fleet_compose_project_name,
    )
    fleets = tuple(
        ManagedFleet(
            definition,
            getattr(settings, f"{definition.provider.value}_fleet_compose_service"),
        )
        for definition in MANAGED_FLEETS.values()
    )
    return [
        FleetReconciler(
            repository,
            runtime,
            fleet,
            observation_ttl_seconds=settings.fleet_observation_ttl_seconds,
            startup_timeout_seconds=settings.fleet_instance_startup_timeout_seconds,
        )
        for fleet in fleets
    ]


async def run(*, once: bool) -> None:
    repository = FleetRepository(session_factory)
    await ensure_managed_fleets(repository)
    reconcilers = _reconcilers(repository)
    try:
        while True:
            for reconciler in reconcilers:
                provider = reconciler.provider
                started = time.monotonic()
                try:
                    result = await reconciler.reconcile()
                except Exception as error:
                    if isinstance(error, ReconciliationOperationError):
                        SCALING_ACTIONS.labels(
                            provider,
                            error.direction,
                            "failed",
                        ).inc()
                    RECONCILE_ERRORS.labels(provider, "reconcile_failed").inc()
                    logger.exception("%s fleet reconciliation failed", provider)
                    if once:
                        raise
                    await asyncio.sleep(settings.fleet_controller_backoff_seconds)
                else:
                    if result.scale_direction is not None:
                        SCALING_ACTIONS.labels(
                            provider,
                            result.scale_direction,
                            "succeeded",
                        ).inc()
                    if result.replaced_instances:
                        SCALING_ACTIONS.labels(provider, "replace", "succeeded").inc(
                            result.replaced_instances
                        )
                    for latency in result.ready_latencies_seconds:
                        SCALE_REQUEST_TO_READY.labels(provider).observe(latency)
                    for latency in result.first_assignment_latencies_seconds:
                        SCALE_REQUEST_TO_FIRST_ASSIGNMENT.labels(provider).observe(latency)
                    if result.unassigned_scale_requests:
                        SCALE_REQUEST_UNASSIGNED.labels(provider).inc(
                            result.unassigned_scale_requests
                        )
                    LAST_SUCCESSFUL_RECONCILE.labels(provider).set_to_current_time()
                finally:
                    RECONCILE_DURATION.labels(provider).observe(time.monotonic() - started)
            if once:
                return
            await asyncio.sleep(settings.fleet_reconcile_interval_seconds)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Stolosio's local Docker Compose browser fleets"
    )
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
