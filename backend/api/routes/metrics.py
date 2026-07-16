from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.metrics.definitions import (
    GATEWAY_ACTIVE,
    GATEWAY_CAPACITY,
    PROVIDER_ACTIVE,
    PROVIDER_AVAILABLE_SLOTS,
    PROVIDER_CAPACITY,
    PROVIDER_DESIRED_INSTANCES,
    PROVIDER_DRAINING_INSTANCES,
    PROVIDER_OBSERVED_INSTANCES,
    PROVIDER_OLDEST_QUEUED,
    PROVIDER_QUEUED,
    PROVIDER_READY_INSTANCES,
    PROVIDER_TOTAL_SLOTS,
    PROVIDER_UNHEALTHY_INSTANCES,
    REGISTRY,
)

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    try:
        gateway = await request.app.state.fleet.gateway_snapshot()
        snapshots = await request.app.state.fleet.snapshot()
    except Exception as error:
        raise HTTPException(status_code=503, detail="fleet metrics unavailable") from error
    GATEWAY_ACTIVE.set(gateway.active_sessions)
    GATEWAY_CAPACITY.set(gateway.capacity)
    for snapshot in snapshots:
        provider = snapshot.provider.value
        PROVIDER_ACTIVE.labels(provider).set(snapshot.active_attempts)
        PROVIDER_QUEUED.labels(provider).set(snapshot.queued_attempts)
        PROVIDER_CAPACITY.labels(provider).set(snapshot.capacity)
        PROVIDER_OLDEST_QUEUED.labels(provider).set(snapshot.oldest_queued_attempt_seconds)
        PROVIDER_DESIRED_INSTANCES.labels(provider).set(snapshot.desired_instances)
        PROVIDER_OBSERVED_INSTANCES.labels(provider).set(snapshot.observed_instances)
        PROVIDER_READY_INSTANCES.labels(provider).set(snapshot.ready_instances)
        PROVIDER_DRAINING_INSTANCES.labels(provider).set(snapshot.draining_instances)
        PROVIDER_UNHEALTHY_INSTANCES.labels(provider).set(snapshot.unhealthy_instances)
        PROVIDER_TOTAL_SLOTS.labels(provider).set(snapshot.total_slots)
        PROVIDER_AVAILABLE_SLOTS.labels(provider).set(snapshot.available_slots)
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
