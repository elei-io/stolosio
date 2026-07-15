from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from backend.proxy.errors import ConnectionRejected
from backend.proxy.settings import harbor_settings_resolver

router = APIRouter(tags=["proxy"])


@router.websocket("/v1/connect")
@router.websocket("/v1/connect/")
async def connect(websocket: WebSocket) -> None:
    try:
        requested, resolved = await harbor_settings_resolver.resolve(
            list(websocket.query_params.multi_items())
        )
        await websocket.app.state.gateway.connect(websocket, requested, resolved)
    except ConnectionRejected as error:
        if websocket.application_state is WebSocketState.CONNECTING:
            await websocket.accept()
        if websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close(code=error.close_code, reason=error.reason)


@router.get("/v1/connect/json/version")
@router.get("/v1/connect/json/version/")
async def browser_version() -> None:
    raise NotImplementedError


@router.get("/v1/connect/json")
@router.get("/v1/connect/json/list")
async def list_targets() -> None:
    raise NotImplementedError


@router.get("/v1/connect/json/protocol")
@router.get("/v1/connect/json/protocol/")
async def protocol() -> None:
    raise NotImplementedError


@router.put("/v1/connect/json/new")
async def create_target() -> None:
    raise NotImplementedError


@router.get("/v1/connect/json/activate/{target_id}")
async def activate_target(target_id: str) -> None:
    raise NotImplementedError


@router.get("/v1/connect/json/close/{target_id}")
async def close_target(target_id: str) -> None:
    raise NotImplementedError


@router.websocket("/v1/connect/devtools/browser/{session_id}")
async def connect_browser_target(
    websocket: WebSocket,
    session_id: str,
) -> None:
    raise NotImplementedError


@router.websocket("/v1/connect/devtools/page/{target_id}")
async def connect_page_target(
    websocket: WebSocket,
    target_id: str,
) -> None:
    raise NotImplementedError
