from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from backend.db.session import session_factory

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: Literal["ok"]
    nats: Literal["ok", "degraded"]
    jetstream: Literal["ok", "degraded"]


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    async with session_factory() as database:
        await database.execute(text("SELECT 1"))
    nats_ready = bool(getattr(request.app.state, "nats_connected", False))
    topology_ready = bool(getattr(request.app.state, "nats_topology_ready", False))
    return HealthResponse(
        status="ok" if nats_ready and topology_ready else "degraded",
        postgres="ok",
        nats="ok" if nats_ready else "degraded",
        jetstream="ok" if topology_ready else "degraded",
    )
