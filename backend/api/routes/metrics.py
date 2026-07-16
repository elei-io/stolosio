from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.metrics.definitions import (
    GATEWAY_ACTIVE,
    GATEWAY_CAPACITY,
    PROVIDER_ACTIVE,
    PROVIDER_CAPACITY,
    PROVIDER_OLDEST_QUEUED,
    PROVIDER_QUEUED,
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
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
