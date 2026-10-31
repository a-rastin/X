"""Saved provider settings drive generation (plan.md §8.3, FR-43).

A queued job is pinned to the settings revision that existed when it was
created. The production worker decrypts that row's key and posts it as a
bearer to that row's endpoint, not to a later unchanged URL and not to the
environment fallback. A format rejection is one extra POST, not a retry.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.worker.test_single_question import (
    _new_physician_batch,
    clean_registry,
    migrated_test_engine,
    valid_cpt,
)
from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import worker as worker_module

__all__ = ["clean_registry", "migrated_test_engine"]

MODEL = "stepfun-ai/step-5-preview"
SENTINEL_KEY = "sk-nvidia-test-do-not-echo"
REPLACEMENT_KEY = "sk-nvidia-test-replaced"
SETTINGS = "/api/v1/api-settings"
DATA_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture()
def saved_registry(clean_registry, monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_DATA_KEY", DATA_KEY)
    monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.delenv(worker_module.PROVIDER_ENDPOINT_ENV_VAR, raising=False)
    monkeypatch.delenv(worker_module.PROVIDER_MODEL_ENV_VAR, raising=False)
    _wipe_settings(clean_registry)
    yield clean_registry
    _wipe_settings(clean_registry)


def _wipe_settings(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE provider_settings"))


def _admin():
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    return client, login.json()["csrf_token"]


def _save(client, csrf, body: dict, revision: int | None = None):
    body = {"local_endpoint_approved": True, **body}
    headers = {"X-CSRF-Token": csrf}
    if revision is not None:
        headers["If-Match"] = contracts.format_etag(revision)
    response = client.put(SETTINGS, json=body, headers=headers)
    assert response.status_code == 200, response.text
    blob = response.text
    assert SENTINEL_KEY not in blob
    assert REPLACEMENT_KEY not in blob
    return response.json()


def _run(engine):
    return worker_module.run_once(
        engine,
        worker_module.ProductionProvider(),
        database_url=db_module.get_test_database_url(),
    )


def test_probe_accepts_json_in_reasoning_content_when_content_empty(monkeypatch) -> None:
    monkeypatch.delenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", raising=False)
    """Step-style reasoning models return the JSON object with content null."""

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            messages = payload.get("messages") or []
            has_tool = any(
                isinstance(item, dict) and item.get("role") == "tool" for item in messages
            )
            if has_tool:
                message = {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": '{"ready":true}',
                }
            else:
                message = {
                    "role": "assistant",
                    "content": "\n",
                    "tool_calls": [
                        {
                            "id": "call_ping",
                            "type": "function",
                            "function": {"name": "ping", "arguments": "{}"},
                        }
                    ],
                }
            body = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
        ok, diagnostic = provider_module.probe_capability(
            provider_module.approved_endpoint(url, True), MODEL, "test-key"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert ok, diagnostic
    assert diagnostic == "Provider supports tools and JSON."


@pytest.mark.parametrize("public", [False, True])
def test_generation_uses_pinned_saved_credential_not_later_url(
    saved_registry, monkeypatch, public
) -> None:
    """Pinned endpoint gets the bearer and model. A later unchanged URL is idle."""
    pinned = provider_module.DeterministicProviderEndpoint()
    later = provider_module.DeterministicProviderEndpoint()
    pinned_url = pinned.start()
    later_url = later.start()
    from contextlib import ExitStack

    from tests.provider.tls_fixture import public_provider_https

    stack = ExitStack()
    public_payloads = []
    public_digest = ""
    if public:
        from x_insight.reasoning import provider_destination as policy

        monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
        pinned_url = "https://provider-one.example/v1/chat/completions"
        later_url = "https://provider-two.example/v1/chat/completions"

        def response_for(payload):
            public_payloads.append(payload)
            if "response_format" in payload:
                return 400, {"error": {"param": "response_format"}}
            return {"choices": [{"message": {"content": json.dumps(valid_cpt(public_digest))}}]}

        public_calls = stack.enter_context(public_provider_https(response_for=response_for))
    try:
        admin, admin_csrf = _admin()
        first = _save(
            admin,
            admin_csrf,
            {
                "api_key": SENTINEL_KEY,
                "base_url": pinned_url,
                "local_endpoint_approved": not public,
                "model": MODEL,
                "timeout_seconds": 60,
                "secret_action": "replace",
            },
        )
        client, _, _, _, package, started = _new_physician_batch(
            saved_registry, monkeypatch, "dr_saved_pin", "0012345601"
        )
        digest = package["manifest"]["network_hash"]
        public_digest = digest
        pinned.script = [
            {
                "type": "status",
                "status": 400,
                "body": {"error": {"param": "response_format", "code": "invalid_request"}},
            },
            {"type": "final", "cpt": valid_cpt(digest)},
        ]
        moved = _save(
            admin,
            admin_csrf,
            {
                "base_url": later_url,
                "model": "other-model",
                "timeout_seconds": 900,
                "secret_action": "unchanged",
                "local_endpoint_approved": not public,
            },
            revision=int(first["revision"]),
        )
        assert moved["revision"] == int(first["revision"]) + 1
        assert moved.get("affected_runs", 0) == 0
        resolved = worker_module.resolve_production_provider(
            saved_registry, {"diagnostics": {"provider_settings_revision": first["revision"]}}
        )
        assert isinstance(resolved, provider_module.BoundedProviderAdapter)
        assert resolved.config.timeout_seconds == 60
        outcome = _run(saved_registry)
        body = client.get(f"/api/v1/generation-batches/{started['batch']['id']}")
    finally:
        stack.close()
        pinned.stop()
        later.stop()

    assert outcome["status"] == "succeeded", outcome
    if public:
        assert len(public_calls) == 2
        assert {call["host"] for call in public_calls} == {"provider-one.example"}
        assert [item["model"] for item in public_payloads] == [MODEL, MODEL]
        assert "response_format" in public_payloads[0]
        assert "response_format" not in public_payloads[1]
    else:
        assert pinned.captured_auth == [f"Bearer {SENTINEL_KEY}", f"Bearer {SENTINEL_KEY}"]
        assert [item["model"] for item in pinned.captured] == [MODEL, MODEL]
        assert "response_format" in pinned.captured[0]
        assert "response_format" not in pinned.captured[1]
    assert later.captured == []
    assert later.captured_auth == []
    assert body.status_code == 200, body.text
    assert body.json()["baseline"]["provider_model"] == MODEL
    assert SENTINEL_KEY not in body.text


@pytest.mark.parametrize("denial", ["legacy", "dns"])
def test_denied_destination_and_unreadable_key_fail_closed(
    saved_registry, monkeypatch, denial
) -> None:
    """A pinned job does not call out when the allowlist or deployment key is gone."""
    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": {"network_hash": "x", "tables": []}}]
    )
    url = endpoint.start()
    try:
        admin, csrf = _admin()
        _save(
            admin,
            csrf,
            {"api_key": SENTINEL_KEY, "base_url": url, "model": MODEL, "secret_action": "replace"},
        )
        _new_physician_batch(saved_registry, monkeypatch, "dr_saved_deny", "0012345602")
        if denial == "legacy":
            monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "other.example")
        else:
            import socket
            from urllib.parse import urlsplit

            original_resolve = socket.getaddrinfo
            endpoint_port = urlsplit(url).port

            def unsafe_resolve(host, port, *args, **kwargs):
                if host == "127.0.0.1" and port == endpoint_port:
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]
                return original_resolve(host, port, *args, **kwargs)

            monkeypatch.setattr(socket, "getaddrinfo", unsafe_resolve)
        denied = _run(saved_registry)
        assert denied["status"] == "failed", denied
        assert denied["error_code"] == "PROVIDER_DESTINATION_DENIED"
        assert endpoint.captured == []

        # New job, same saved row, deployment key no longer opens it.
        monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "127.0.0.1")
        monkeypatch.setenv(
            "X_INSIGHT_PROVIDER_DATA_KEY",
            base64.b64encode(b"\x11" * 32).decode("ascii"),
        )
        _new_physician_batch(saved_registry, monkeypatch, "dr_saved_key", "0012345603")
        unread = _run(saved_registry)
    finally:
        endpoint.stop()

    assert unread["status"] == "failed", unread
    assert unread["error_code"] == "PROVIDER_AUTH"
    assert endpoint.captured == []
    idle = _run(saved_registry)
    assert idle["status"] == "idle", idle


def test_replace_cancels_queued_generation(saved_registry, monkeypatch) -> None:
    """Replace cancels the pinned generation job. The key is not echoed."""
    endpoint = provider_module.DeterministicProviderEndpoint()
    url = endpoint.start()
    try:
        admin, csrf = _admin()
        first = _save(
            admin,
            csrf,
            {"api_key": SENTINEL_KEY, "base_url": url, "model": MODEL, "secret_action": "replace"},
        )
        client, _, _, _, _, started = _new_physician_batch(
            saved_registry, monkeypatch, "dr_saved_cancel", "0012345604"
        )
        batch_id = started["batch"]["id"]
        queued = client.get(f"/api/v1/generation-batches/{batch_id}")
        assert queued.json()["job"]["status"] == "queued"
        replaced = _save(
            admin,
            csrf,
            {
                "api_key": REPLACEMENT_KEY,
                "base_url": url,
                "model": MODEL,
                "secret_action": "replace",
            },
            revision=int(first["revision"]),
        )
        assert replaced["affected_runs"] >= 1
        cancelled = client.get(f"/api/v1/generation-batches/{batch_id}")
    finally:
        endpoint.stop()

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["job"]["status"] == "cancelled"
    assert REPLACEMENT_KEY not in cancelled.text
    assert endpoint.captured == []
