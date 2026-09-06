from uuid import UUID

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from backend.debug import DebugConsumerTooSlow, DebugSessionNotFound

router = APIRouter(tags=["debug"])
_REFERENCE_QUERY = "stolosio.session.reference"


@router.websocket("/v1/debug")
async def debug_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    references = websocket.query_params.getlist(_REFERENCE_QUERY)
    try:
        if len(references) != 1:
            raise ValueError
        reference = UUID(references[0])
    except (ValueError, TypeError):
        await websocket.close(code=4400, reason="invalid_session_reference")
        return

    service = getattr(websocket.app.state, "debug_stream", None)
    if service is None:
        await websocket.close(code=1013, reason="debug_stream_unavailable")
        return
    try:
        reached_terminal_event = await service.stream(websocket, reference)
    except DebugSessionNotFound:
        await websocket.close(code=4404, reason="session_reference_not_found")
    except DebugConsumerTooSlow:
        await websocket.close(code=1013, reason="debug_consumer_too_slow")
    except Exception:
        if websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason="debug_stream_failed")
        raise
    else:
        if reached_terminal_event and websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close(code=1000)
