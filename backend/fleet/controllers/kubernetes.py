import argparse
import asyncio
import logging
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

from backend.db.session import engine, session_factory
from backend.fleet import FleetRepository
from backend.fleet.bootstrap import ensure_managed_fleets
from backend.fleet.providers import MANAGED_FLEETS
from backend.fleet.reconciler import (
    FleetReconciler,
    ManagedFleet,
    ReconciliationOperationError,
)
from backend.fleet.runtimes import KubernetesRuntime
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


def _runtime() -> KubernetesRuntime:
    return KubernetesRuntime(
        namespace=settings.kubernetes_namespace,
        workload_config_map=settings.kubernetes_browserless_workload_config_map,
        headless_service=settings.kubernetes_browserless_headless_service,
    )


def _reconcilers(
    repository: FleetRepository,
    runtime: KubernetesRuntime,
) -> list[FleetReconciler]:
    fleets = tuple(
        ManagedFleet(
            definition,
            getattr(settings, f"kubernetes_{definition.provider.value}_statefulset"),
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
    runtime = _runtime()
    reconcilers = _reconcilers(repository, runtime)
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
                    logger.exception("%s Kubernetes fleet reconciliation failed", provider)
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
                    LAST_SUCCESSFUL_RECONCILE.labels(provider).set_to_current_time()
                finally:
                    RECONCILE_DURATION.labels(provider).observe(time.monotonic() - started)
            if once:
                return
            await asyncio.sleep(settings.fleet_reconcile_interval_seconds)
    finally:
        await runtime.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Harbor's Kubernetes browser fleets"
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
