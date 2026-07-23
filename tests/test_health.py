from fastapi.testclient import TestClient

from backend.api.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["postgres"] == "ok"
    assert response.json()["nats"] in {"ok", "degraded"}
    assert response.json()["jetstream"] in {"ok", "degraded"}
