from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.proxy.contracts import ProviderName
from backend.proxy.domains import DomainEligibilityState, DomainFilters
from backend.proxy.health import ManualProbeUnavailableError

router = APIRouter(prefix="/v1/admin/domains", tags=["admin"])


class DomainPageResponse(BaseModel):
    domains: list[dict[str, object]]
    summary: dict[str, int]
    next_cursor: str | None


class DomainProbePageResponse(BaseModel):
    probes: list[dict[str, object]]
    next_cursor: str | None


class DomainSessionPageResponse(BaseModel):
    sessions: list[dict[str, object]]
    next_cursor: str | None


class TriggerDomainProbesRequest(BaseModel):
    providers: list[ProviderName] | None = None


class TriggeredProbeResponse(BaseModel):
    id: str
    provider: ProviderName


class TriggerDomainProbesResponse(BaseModel):
    scheduled: list[TriggeredProbeResponse]
    already_active: list[TriggeredProbeResponse]


@router.get("", response_model=DomainPageResponse)
async def list_domains(
    request: Request,
    search: Annotated[str | None, Query(min_length=1, max_length=253)] = None,
    eligibility_state: DomainEligibilityState | None = None,
    has_active_probes: bool | None = None,
    has_transitions: bool | None = None,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DomainPageResponse:
    try:
        page = await request.app.state.domains.domains(
            DomainFilters(
                search=search,
                eligibility_state=eligibility_state,
                has_active_probes=has_active_probes,
                has_transitions=has_transitions,
            ),
            before=before,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return DomainPageResponse(
        domains=page.domains,
        summary=page.summary,
        next_cursor=page.next_cursor,
    )


@router.get("/{domain_id}")
async def get_domain(domain_id: int, request: Request) -> dict[str, object]:
    value = await request.app.state.domains.domain(domain_id)
    if value is None:
        raise HTTPException(status_code=404, detail="domain not found")
    return value


@router.get("/{domain_id}/probes", response_model=DomainProbePageResponse)
async def list_domain_probes(
    domain_id: int,
    request: Request,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DomainProbePageResponse:
    try:
        page = await request.app.state.domains.probes(
            domain_id,
            before=before,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if page is None:
        raise HTTPException(status_code=404, detail="domain not found")
    return DomainProbePageResponse(probes=page.probes, next_cursor=page.next_cursor)


@router.post("/{domain_id}/probes", response_model=TriggerDomainProbesResponse)
async def trigger_domain_probes(
    domain_id: int,
    body: TriggerDomainProbesRequest,
    request: Request,
) -> TriggerDomainProbesResponse:
    try:
        result = await request.app.state.health.schedule_manual(
            domain_id,
            tuple(body.providers) if body.providers is not None else None,
        )
    except ManualProbeUnavailableError as error:
        status_code = 404 if str(error) == "domain not found" else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    return TriggerDomainProbesResponse(
        scheduled=[
            TriggeredProbeResponse(id=probe.id, provider=probe.provider)
            for probe in result.scheduled
        ],
        already_active=[
            TriggeredProbeResponse(id=probe.id, provider=probe.provider)
            for probe in result.already_active
        ],
    )


@router.get("/{domain_id}/sessions", response_model=DomainSessionPageResponse)
async def list_domain_sessions(
    domain_id: int,
    request: Request,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DomainSessionPageResponse:
    try:
        page = await request.app.state.domains.sessions(
            domain_id,
            before=before,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if page is None:
        raise HTTPException(status_code=404, detail="domain not found")
    return DomainSessionPageResponse(sessions=page.sessions, next_cursor=page.next_cursor)
