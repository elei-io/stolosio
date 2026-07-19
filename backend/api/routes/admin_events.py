from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from backend.debug import (
    ActivityConsumerTooSlow,
    ActivityEventFamily,
    ActivityEventFilters,
    ActivityEventOutcome,
)
from backend.events import EventType
from backend.proxy.contracts import ProviderName

router = APIRouter(prefix="/v1/admin/events", tags=["admin"])


class ActivityEventPageResponse(BaseModel):
    events: list[dict[str, object]]
    next_cursor: str | None


ProvidersQuery = Annotated[list[ProviderName] | None, Query(alias="provider")]
FamiliesQuery = Annotated[list[ActivityEventFamily] | None, Query(alias="event_family")]
TypesQuery = Annotated[list[EventType] | None, Query(alias="event_type")]
OutcomesQuery = Annotated[list[ActivityEventOutcome] | None, Query(alias="outcome")]


def _filters(
    providers: list[ProviderName] | None,
    families: list[ActivityEventFamily] | None,
    event_types: list[EventType] | None,
    outcomes: list[ActivityEventOutcome] | None,
    session_id: UUID | None,
    attempt_id: UUID | None,
) -> ActivityEventFilters:
    return ActivityEventFilters(
        providers=tuple(providers or ()),
        families=tuple(families or ()),
        event_types=tuple(event_types or ()),
        outcomes=tuple(outcomes or ()),
        session_id=session_id,
        attempt_id=attempt_id,
    )


@router.get("", response_model=ActivityEventPageResponse)
async def list_events(
    request: Request,
    provider: ProvidersQuery = None,
    event_family: FamiliesQuery = None,
    event_type: TypesQuery = None,
    outcome: OutcomesQuery = None,
    session_id: UUID | None = None,
    attempt_id: UUID | None = None,
    before: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ActivityEventPageResponse:
    filters = _filters(
        provider,
        event_family,
        event_type,
        outcome,
        session_id,
        attempt_id,
    )
    try:
        page = await request.app.state.activity_history.events(
            filters,
            before=before,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ActivityEventPageResponse(events=page.events, next_cursor=page.next_cursor)


@router.get("/stream")
async def stream_events(
    request: Request,
    provider: ProvidersQuery = None,
    event_family: FamiliesQuery = None,
    event_type: TypesQuery = None,
    outcome: OutcomesQuery = None,
    session_id: UUID | None = None,
    attempt_id: UUID | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = getattr(request.app.state, "activity_stream", None)
    if service is None:
        raise HTTPException(status_code=503, detail="activity stream unavailable")
    try:
        after_sequence = int(last_event_id) if last_event_id is not None else None
        if after_sequence is not None and after_sequence < 0:
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid Last-Event-ID") from error

    filters = _filters(
        provider,
        event_family,
        event_type,
        outcome,
        session_id,
        attempt_id,
    )

    async def generate():
        try:
            async for event in service.events(filters, after_sequence=after_sequence):
                yield event
        except ActivityConsumerTooSlow:
            yield 'event: stream-error\ndata: {"reason":"consumer_too_slow"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
