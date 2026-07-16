from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.proxy.contracts import ProviderName

router = APIRouter(prefix="/v1/fleet", tags=["fleet"])


class ProviderFleetResponse(BaseModel):
    provider: ProviderName
    active_attempts: int
    queued_attempts: int
    capacity: int
    oldest_queued_attempt_seconds: float
    desired_instances: int
    observed_instances: int
    ready_instances: int
    draining_instances: int
    unhealthy_instances: int
    total_slots: int
    available_slots: int


class GatewayFleetResponse(BaseModel):
    active_sessions: int
    capacity: int


@router.get("/gateway", response_model=GatewayFleetResponse)
async def gateway(request: Request) -> GatewayFleetResponse:
    try:
        snapshot = await request.app.state.fleet.gateway_snapshot()
    except Exception as error:
        raise HTTPException(status_code=503, detail="fleet snapshot unavailable") from error
    return GatewayFleetResponse(
        active_sessions=snapshot.active_sessions,
        capacity=snapshot.capacity,
    )


@router.get("/providers", response_model=list[ProviderFleetResponse])
async def providers(request: Request) -> list[ProviderFleetResponse]:
    try:
        snapshots = await request.app.state.fleet.snapshot()
    except Exception as error:
        raise HTTPException(status_code=503, detail="fleet snapshot unavailable") from error
    return [
        ProviderFleetResponse(
            provider=snapshot.provider,
            active_attempts=snapshot.active_attempts,
            queued_attempts=snapshot.queued_attempts,
            capacity=snapshot.capacity,
            oldest_queued_attempt_seconds=snapshot.oldest_queued_attempt_seconds,
            desired_instances=snapshot.desired_instances,
            observed_instances=snapshot.observed_instances,
            ready_instances=snapshot.ready_instances,
            draining_instances=snapshot.draining_instances,
            unhealthy_instances=snapshot.unhealthy_instances,
            total_slots=snapshot.total_slots,
            available_slots=snapshot.available_slots,
        )
        for snapshot in snapshots
    ]
