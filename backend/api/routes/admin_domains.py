from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.proxy.domains import DomainFilters, DomainQualificationState

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


@router.get("", response_model=DomainPageResponse)
async def list_domains(
    request: Request,
    search: Annotated[str | None, Query(min_length=1, max_length=253)] = None,
    qualification_state: DomainQualificationState | None = None,
    has_active_probes: bool | None = None,
    has_promotions: bool | None = None,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DomainPageResponse:
    try:
        page = await request.app.state.domains.domains(
            DomainFilters(
                search=search,
                qualification_state=qualification_state,
                has_active_probes=has_active_probes,
                has_promotions=has_promotions,
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
