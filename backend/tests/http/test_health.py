"""Public health-check smoke (S01, seam T1)."""

from fastapi.testclient import TestClient

from x_insight.app import app


def test_health_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "x-insight"}
