from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from backend.proxy.contracts import ProviderName

router = APIRouter(prefix="/v1/admin/costs", tags=["admin"])


class CostTotalsResponse(BaseModel):
    session_count: int
    attempt_count: int
    failed_attempt_count: int
    modeled_cost_units: int
    chargeable_time_ms: int
    browser_connected_time_ms: int
    browserless_slot_time_ms: int
    browserbase_billable_time_ms: int


class ProviderCostResponse(BaseModel):
    provider: ProviderName
    attempt_count: int
    session_count: int
    failed_attempt_count: int
    modeled_cost_units: int
    chargeable_time_ms: int
    browser_connected_time_ms: int
    capacity_occupied_time_ms: int
    estimated_billable_time_ms: int


class CostBucketResponse(BaseModel):
    started_at: str
    provider: ProviderName
    attempt_count: int
    modeled_cost_units: int
    chargeable_time_ms: int


class CostSessionResponse(BaseModel):
    session_id: str
    client_reference: str | None
    closed_at: str | None
    providers: list[ProviderName]
    modeled_cost_units: int
    chargeable_time_ms: int


class CostOverviewResponse(BaseModel):
    window: Literal["24h", "7d", "30d", "90d"]
    starts_at: str
    ends_at: str
    finalized_through: str | None
    totals: CostTotalsResponse
    providers: list[ProviderCostResponse]
    buckets: list[CostBucketResponse]
    recent_sessions: list[CostSessionResponse]


@router.get("/overview", response_model=CostOverviewResponse)
async def cost_overview(
    request: Request,
    window: Literal["24h", "7d", "30d", "90d"] = Query(default="7d"),
) -> CostOverviewResponse:
    value = await request.app.state.costs.overview(window)
    return CostOverviewResponse.model_validate(value)
