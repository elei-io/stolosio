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
        )
        for snapshot in snapshots
    ]
