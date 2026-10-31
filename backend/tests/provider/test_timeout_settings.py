"""B01: saved timeout precedence and history through the real admin API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.http.test_api_settings import clean_all, migrated_test_engine
from x_insight import db
from x_insight.app import app
from x_insight.reasoning.provider_config import runtime_timeout

__all__ = ["clean_all", "migrated_test_engine"]


def test_saved_timeout_overrides_env_and_pins_history(clean_all, monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", "141")
    with clean_all.begin() as connection:
        connection.execute(text("TRUNCATE provider_settings"))
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin", "role": "admin"}
    )
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    body = {
        "base_url": "http://127.0.0.1:9",
        "model": "test",
        "secret_action": "clear",
        "local_endpoint_approved": True,
    }
    first = client.put(
        "/api/v1/api-settings", json={**body, "timeout_seconds": 60}, headers=headers
    )
    assert first.status_code == 200, first.text
    revision = first.json()["revision"]
    assert first.json()["timeout_seconds"] == 60
    assert client.get("/api/v1/ops/status").json()["provider"]["timeout_seconds"] == 60
    checked = client.post("/api/v1/api-settings/test", json={}, headers=headers)
    assert checked.status_code == 200
    assert checked.json()["timeout_seconds"] == 60
    second = client.put(
        "/api/v1/api-settings", json={**body, "timeout_seconds": None}, headers=headers
    )
    assert second.status_code == 200
    assert client.get("/api/v1/ops/status").json()["provider"]["timeout_seconds"] == 141
    assert client.get(f"/api/v1/api-settings/revisions/{revision}").json()["timeout_seconds"] == 60
    with db.session_scope(clean_all) as session:
        assert runtime_timeout(session, revision) == 60
        assert runtime_timeout(session, second.json()["revision"]) == 141
    for value in [True, 0, 9, 901, "garbage"]:
        rejected = client.put(
            "/api/v1/api-settings", json={**body, "timeout_seconds": value}, headers=headers
        )
        assert rejected.status_code == 422, rejected.text
    with clean_all.begin() as connection:
        connection.execute(text("TRUNCATE provider_settings"))


@pytest.mark.parametrize(
    "timeout", [None, 10, 300, 900, 9, 901, True, "60", float("nan"), float("inf")]
)
def test_restore_timeout_validation(timeout):
    from x_insight.operations.restore import _check_provider_approvals

    row = {
        "local_endpoint_approved": False,
        "trusted_proxy_approved": False,
        "timeout_seconds": timeout,
    }
    problem = _check_provider_approvals({"provider_settings": [row]}, "0025")
    assert (problem is None) == (timeout is None or type(timeout) is int and 10 <= timeout <= 900)
