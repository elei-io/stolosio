from fastapi import APIRouter, WebSocket

from backend.proxy.adapters import get_provider_adapter
from backend.proxy.contracts import ProviderName
from backend.proxy.gateway import proxy_cdp

router = APIRouter(prefix="/v1/connect", tags=["proxy"])


@router.websocket("/{provider}")
async def connect(websocket: WebSocket, provider: ProviderName) -> None:
    adapter = get_provider_adapter(provider)
    connection = await adapter.connect()
    await proxy_cdp(websocket, connection)


@router.get("/{provider}/json/version")
@router.get("/{provider}/json/version/")
async def browser_version(provider: ProviderName) -> None:
    raise NotImplementedError


@router.get("/{provider}/json")
@router.get("/{provider}/json/list")
async def list_targets(provider: ProviderName) -> None:
    raise NotImplementedError


@router.get("/{provider}/json/protocol")
@router.get("/{provider}/json/protocol/")
async def protocol(provider: ProviderName) -> None:
    raise NotImplementedError


@router.put("/{provider}/json/new")
async def create_target(provider: ProviderName) -> None:
    raise NotImplementedError


@router.get("/{provider}/json/activate/{target_id}")
async def activate_target(provider: ProviderName, target_id: str) -> None:
    raise NotImplementedError


@router.get("/{provider}/json/close/{target_id}")
async def close_target(provider: ProviderName, target_id: str) -> None:
    raise NotImplementedError


@router.websocket("/{provider}/devtools/browser/{session_id}")
async def connect_browser_target(
    websocket: WebSocket,
    provider: ProviderName,
    session_id: str,
) -> None:
    raise NotImplementedError


@router.websocket("/{provider}/devtools/page/{target_id}")
async def connect_page_target(
    websocket: WebSocket,
    provider: ProviderName,
    target_id: str,
) -> None:
    raise NotImplementedError
