import json
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.routes.debug import router

REFERENCE = UUID("3a10fc5f-1b31-45d4-b95f-3981ea833a91")


class FakeDebugStream:
    def __init__(self) -> None:
        self.references = []

    async def stream(self, websocket, reference) -> bool:
        self.references.append(reference)
        await websocket.send_text(json.dumps({"event_type": "session.open"}))
        return True


def test_debug_route_streams_by_client_reference_and_closes_at_terminal_event() -> None:
    app = FastAPI()
    service = FakeDebugStream()
    app.state.debug_stream = service
    app.include_router(router)

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/v1/debug?stolosio.session.reference={REFERENCE}"
        ) as websocket:
            assert websocket.receive_json() == {"event_type": "session.open"}
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_text()

    assert closed.value.code == 1000
    assert service.references == [REFERENCE]


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?stolosio.session.reference=not-a-uuid",
        (f"?stolosio.session.reference={REFERENCE}&stolosio.session.reference={REFERENCE}"),
    ],
)
def test_debug_route_rejects_invalid_references(query: str) -> None:
    app = FastAPI()
    app.state.debug_stream = FakeDebugStream()
    app.include_router(router)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/v1/debug{query}") as websocket:
                websocket.receive_text()

    assert closed.value.code == 4400


def test_debug_route_reports_unavailable_event_backbone() -> None:
    app = FastAPI()
    app.state.debug_stream = None
    app.include_router(router)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                f"/v1/debug?stolosio.session.reference={REFERENCE}"
            ) as websocket:
                websocket.receive_text()

    assert closed.value.code == 1013
