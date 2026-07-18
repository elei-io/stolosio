from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.proxy.contracts import ProviderName
from backend.proxy.session_queries import SessionFilters

router = APIRouter(prefix="/v1/admin/sessions", tags=["admin"])


class SessionPageResponse(BaseModel):
    sessions: list[dict[str, object]]
    next_cursor: str | None


@router.get("", response_model=SessionPageResponse)
async def list_sessions(
    request: Request,
    search: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    state: Annotated[str | None, Query(max_length=32)] = None,
    provider: ProviderName | None = None,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SessionPageResponse:
    try:
        page = await request.app.state.session_queries.sessions(
            SessionFilters(search=search, state=state, provider=provider),
            before=before,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SessionPageResponse(
        sessions=page.sessions,
        next_cursor=page.next_cursor,
    )


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, object]:
    value = await request.app.state.session_queries.session(session_id)
    if value is None:
        raise HTTPException(status_code=404, detail="session not found")
    return value
