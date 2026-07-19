from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from backend.proxy.contracts import ProviderName

router = APIRouter(prefix="/v1/admin/command-costs", tags=["admin"])


class CommandCostResponse(BaseModel):
    provider: ProviderName
    method: str
    command_count: int
    failed_count: int
    interrupted_count: int
    total_duration_ms: int
    total_provider_latency_ms: int
    total_harbor_queue_ms: int
    attributed_browser_time_ms: int
    attributed_cost_units: int
    first_seen_at: str
    last_seen_at: str


@router.get("", response_model=list[CommandCostResponse])
async def list_command_costs(
    request: Request,
    provider: ProviderName | None = None,
    include_overhead: bool = True,
    sort_by: Literal["cost", "browser_time", "commands"] = "cost",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CommandCostResponse]:
    rows = await request.app.state.command_costs.command_costs(
        provider=provider,
        include_overhead=include_overhead,
        sort_by=sort_by,
        limit=limit,
    )
    return [CommandCostResponse.model_validate(row) for row in rows]
