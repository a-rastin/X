"""B05: provider capability is an explicit saved setting."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.http.test_api_settings import clean_all, migrated_test_engine
from x_insight.app import app
from x_insight.operations.restore import _check_provider_approvals
from x_insight.reasoning import provider, provider_config, worker
from x_insight.reasoning.provider_config import ProviderSettingsWrite, effective_capability_mode

__all__ = ["clean_all", "migrated_test_engine"]


def test_saved_and_pinned_mode_survives_probe_and_omission(clean_all, monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_CAPABILITY", raising=False)
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
    saved = client.put(
        "/api/v1/api-settings", json={**body, "capability_mode": "json"}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    checked = client.post("/api/v1/api-settings/test", json={}, headers=headers)
    assert checked.status_code == 200
    assert checked.json()["capability_mode"] == "json"
    assert checked.json()["detected_capability_mode"] is None
    omitted = client.put("/api/v1/api-settings", json=body, headers=headers)
    assert omitted.json()["capability_mode"] == "json"
    later = client.put(
        "/api/v1/api-settings", json={**body, "capability_mode": "schema"}, headers=headers
    )
    assert later.json()["capability_mode"] == "schema"
    assert (
        client.get(f"/api/v1/api-settings/revisions/{revision}").json()["capability_mode"] == "json"
    )
    monkeypatch.setattr(
        provider_config, "open_runtime", lambda *_: ("https://example.test", "test", "key")
    )
    job = {"diagnostics": {"provider_settings_revision": revision}}
    assert worker.resolve_production_provider(clean_all, job).config.capability == "json"
    monkeypatch.setenv("X_INSIGHT_PROVIDER_CAPABILITY", "schema")
    assert worker.resolve_production_provider(clean_all, job).config.capability == "schema"
    assert client.get("/api/v1/api-settings").json()["capability_override_active"] is True


def test_detected_mode_is_preserved_on_test_revision(clean_all, monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_CAPABILITY", raising=False)
    monkeypatch.setattr(
        provider_config, "_capability_outcome", lambda *_: ("verified", "ok", "schema")
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin", "role": "admin"}
    )
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    saved = client.put(
        "/api/v1/api-settings",
        json={
            "base_url": "http://127.0.0.1:9",
            "model": "test",
            "secret_action": "clear",
            "local_endpoint_approved": True,
            "capability_mode": "json",
        },
        headers=headers,
    )
    assert saved.status_code == 200
    tested = client.post("/api/v1/api-settings/test", json={}, headers=headers)
    assert tested.json()["detected_capability_mode"] == "schema"
    revision = tested.json()["revision"]
    assert client.get("/api/v1/api-settings").json()["detected_capability_mode"] == "schema"
    assert (
        client.get(f"/api/v1/api-settings/revisions/{revision}").json()["detected_capability_mode"]
        == "schema"
    )
    saved_again = client.put(
        "/api/v1/api-settings",
        json={
            "base_url": "http://127.0.0.1:9",
            "model": "test",
            "secret_action": "clear",
            "local_endpoint_approved": True,
            "capability_mode": "json",
        },
        headers=headers,
    )
    assert saved_again.json()["detected_capability_mode"] is None
    assert (
        client.get(f"/api/v1/api-settings/revisions/{revision}").json()["detected_capability_mode"]
        == "schema"
    )


def test_capability_test_rejects_a_stale_revision(clean_all, monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_CAPABILITY", raising=False)
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
        "/api/v1/api-settings", json={**body, "capability_mode": "schema"}, headers=headers
    )
    old_revision = first.json()["revision"]
    second = client.put(
        "/api/v1/api-settings", json={**body, "capability_mode": "json"}, headers=headers
    )
    assert second.status_code == 200
    tested = client.post(
        "/api/v1/api-settings/test", json={}, headers={**headers, "If-Match": f'"{old_revision}"'}
    )
    assert tested.status_code == 412
    assert tested.json()["code"] == "STALE_REVISION"
    assert client.get("/api/v1/api-settings").json()["revision"] == second.json()["revision"]


def test_example_environment_does_not_override_saved_mode():
    example = Path(__file__).resolve().parents[3] / ".env.example"
    assignments = [
        line.partition("=")[2].strip()
        for line in example.read_text().splitlines()
        if line.startswith("X_INSIGHT_PROVIDER_CAPABILITY=")
    ]
    assert assignments in ([], [""])


def test_effective_mode_respects_only_valid_explicit_override(monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_CAPABILITY", raising=False)
    assert effective_capability_mode("json") == ("json", False)
    monkeypatch.setenv("X_INSIGHT_PROVIDER_CAPABILITY", "schema")
    assert effective_capability_mode("json") == ("schema", True)
    monkeypatch.setenv("X_INSIGHT_PROVIDER_CAPABILITY", "invalid")
    assert effective_capability_mode("json") == ("json", False)


def test_restore_validates_capability_mode_for_current_archives():
    row = {"local_endpoint_approved": False, "trusted_proxy_approved": False}
    assert _check_provider_approvals({"provider_settings": [row]}, "0026") is None
    assert _check_provider_approvals({"provider_settings": [row]}, "0027") is not None
    row["capability_mode"] = "json"
    assert _check_provider_approvals({"provider_settings": [row]}, "0027") is None
    row["capability_mode"] = "other"
    assert _check_provider_approvals({"provider_settings": [row]}, "0027") is not None
    row["capability_mode"] = "json"
    assert _check_provider_approvals({"provider_settings": [row]}, "0028") is not None
    row["detected_capability_mode"] = None
    assert _check_provider_approvals({"provider_settings": [row]}, "0028") is None
    row["detected_capability_mode"] = "schema"
    assert _check_provider_approvals({"provider_settings": [row]}, "0028") is None
    assert _check_provider_approvals({"provider_settings": [row]}, "0027") is not None
    row["detected_capability_mode"] = "invalid"
    assert _check_provider_approvals({"provider_settings": [row]}, "0028") is not None


@pytest.mark.parametrize(
    ("schema_status", "error_param", "expected"),
    [
        (200, None, "schema"),
        (400, "response_format", "json"),
        (401, None, None),
        (400, "model", None),
        (500, None, None),
    ],
)
def test_detection_only_falls_back_for_explicit_format_rejection(
    monkeypatch, schema_status, error_param, expected
):
    formats = []

    class Response:
        content = b"{}"

        def __init__(self, status, body):
            self.status_code = status
            self.body = body

        def json(self):
            return self.body

    def post(_url, payload, _headers, _timeout):
        mode = payload["response_format"]["type"]
        formats.append(mode)
        if mode == "json_schema" and schema_status != 200:
            return Response(schema_status, {"error": {"param": error_param}})
        return Response(200, {"choices": [{"message": {"content": '{"ready":true}'}}]})

    monkeypatch.setattr(provider, "post_json", post)
    assert provider.detect_capability_mode("https://example.test", "model", "secret") == expected
    assert formats == (["json_schema", "json_object"] if expected == "json" else ["json_schema"])


def test_capability_mode_is_accepted_and_validated():
    body = {"base_url": "https://example.test/v1", "model": "fictional"}
    assert ProviderSettingsWrite(**body, capability_mode="json").capability_mode == "json"
    assert ProviderSettingsWrite(**body, capability_mode="schema").capability_mode == "schema"
    with pytest.raises(ValidationError):
        ProviderSettingsWrite(**body, capability_mode="other")
