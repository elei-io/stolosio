from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import JSONResponse

router = APIRouter(tags=["proxy"])


@router.websocket("/v1/connect")
@router.websocket("/v1/connect/")
async def connect(websocket: WebSocket) -> None:
    await websocket.app.state.gateway.connect(websocket)


@router.get("/v1/connect/json/version")
@router.get("/v1/connect/json/version/")
async def browser_version() -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.get("/v1/connect/json")
@router.get("/v1/connect/json/list")
async def list_targets() -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.get("/v1/connect/json/protocol")
@router.get("/v1/connect/json/protocol/")
async def protocol() -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.put("/v1/connect/json/new")
async def create_target() -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.get("/v1/connect/json/activate/{target_id}")
async def activate_target(target_id: str) -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.get("/v1/connect/json/close/{target_id}")
async def close_target(target_id: str) -> None:
    raise HTTPException(status_code=501, detail="unsupported_endpoint")


@router.websocket("/v1/connect/devtools/browser/{session_id}")
async def connect_browser_target(websocket: WebSocket, session_id: str) -> None:
    await websocket.send_denial_response(
        JSONResponse({"error": "unsupported_endpoint"}, status_code=501)
    )


@router.websocket("/v1/connect/devtools/page/{target_id}")
async def connect_page_target(websocket: WebSocket, target_id: str) -> None:
    await websocket.send_denial_response(
        JSONResponse({"error": "unsupported_endpoint"}, status_code=501)
    )
