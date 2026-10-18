"""S42 provider settings (plan.md §§4.3, 8.3).

Slice 1: admin save/GET masks the key; physician 403; missing session 401.
Slice 2: unchanged/replace/clear must not store the masked placeholder, and a
stale If-Match is 412. Stored bytes are opened with AES-GCM and the fixture
key, not a product decrypt helper.
Slice 3: base URLs outside the operator allowlist are rejected.
Slice 4: a real capability POST verifies the saved endpoint; a save does not.
Slice 5: a prior revision stays readable after replace and clear.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service as identity_service

SENTINEL_KEY = "sk-sentinel-s42-do-not-echo"
REPLACEMENT_KEY = "sk-sentinel-s42-replaced"
BASE_URL = "http://127.0.0.1:9/v1"
MODEL = "s42-slice1"
SETTINGS_PATH = "/api/v1/api-settings"
_SECRETS = (SENTINEL_KEY, REPLACEMENT_KEY)


@pytest.fixture(scope="session")
def migrated_test_engine():
    from alembic import command
    from alembic.config import Config

    url = db_module.get_test_database_url()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = db_module.build_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture()
def clean_all(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    identity_service.ensure_default_admin(migrated_test_engine)
    monkeypatch.setenv(
        "X_INSIGHT_PROVIDER_DATA_KEY",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )
    monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "127.0.0.1")
    yield migrated_test_engine
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    identity_service.ensure_default_admin(migrated_test_engine)


def _login(username="admin", password="admin", role="admin"):
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "role": role}
    )
    assert response.status_code == 200
    return client, response.json()["csrf_token"]


def _physician_client(clean_all):
    admin_client, admin_csrf = _login()
    username = f"doc_{os.getpid()}_{id(clean_all) % 100000}"
    created = admin_client.post(
        "/api/v1/physicians",
        json={"username": username, "password": "secret123"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code == 201, created.text
    physician = TestClient(app)
    login = physician.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret123", "role": "physician"},
    )
    assert login.status_code == 200
    return physician, login.json()["csrf_token"]


def _payload() -> dict[str, str]:
    return {"api_key": SENTINEL_KEY, "base_url": BASE_URL, "model": MODEL}


def _assert_no_secret(response) -> None:
    assert SENTINEL_KEY not in response.text
    assert all(SENTINEL_KEY not in value for value in response.headers.values())


def _assert_masked_settings(response) -> dict:
    assert response.status_code == 200, response.text
    _assert_no_secret(response)
    body = response.json()
    revision = body.get("revision")
    assert type(revision) is int and revision >= 1
    # Status word from the spec. The raw key must not stand in for it.
    assert body.get("api_key") == "configured"
    assert body.get("base_url") == BASE_URL
    assert body.get("model") == MODEL
    return body


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    _assert_no_secret(response)
    body = response.json()
    for key in ("code", "message", "field_errors", "request_id", "retryable"):
        assert key in body, body
    assert body["code"] == code
    assert body["request_id"]


def test_admin_saves_provider_settings_masked_and_unauthorized_denied(clean_all):
    client, csrf = _login()
    payload = _payload()
    saved = client.put(SETTINGS_PATH, json=payload, headers={"X-CSRF-Token": csrf})
    saved_body = _assert_masked_settings(saved)
    fetched = client.get(SETTINGS_PATH)
    fetched_body = _assert_masked_settings(fetched)
    assert fetched_body["revision"] == saved_body["revision"]

    physician, physician_csrf = _physician_client(clean_all)
    _assert_error(physician.get(SETTINGS_PATH), 403, "FORBIDDEN")
    _assert_error(
        physician.put(SETTINGS_PATH, json=payload, headers={"X-CSRF-Token": physician_csrf}),
        403,
        "FORBIDDEN",
    )

    anonymous = TestClient(app)
    _assert_error(anonymous.get(SETTINGS_PATH), 401, "UNAUTHENTICATED")
    _assert_error(anonymous.put(SETTINGS_PATH, json=payload), 401, "UNAUTHENTICATED")


def _put(client, csrf: str, body: dict, revision: int | None = None):
    headers = {"X-CSRF-Token": csrf}
    if revision is not None:
        headers["If-Match"] = contracts.format_etag(revision)
    return client.put(SETTINGS_PATH, json=body, headers=headers)


def _assert_secrets_absent(response) -> None:
    for secret in _SECRETS:
        assert secret not in response.text
        assert all(secret not in value for value in response.headers.values())


def _assert_public(response, *, api_key: str, model: str, revision: int) -> dict:
    assert response.status_code == 200, response.text
    _assert_secrets_absent(response)
    body = response.json()
    assert body.get("revision") == revision
    assert body.get("api_key") == api_key
    assert body.get("base_url") == BASE_URL
    assert body.get("model") == model
    return body


def _decrypt_latest(engine) -> bytes:
    """Open the newest stored key with the fixture key. No product helper."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT key_nonce, key_ciphertext FROM provider_settings "
                "ORDER BY revision DESC LIMIT 1"
            )
        ).one()
    ciphertext = bytes(row.key_ciphertext)
    assert SENTINEL_KEY.encode() not in ciphertext
    assert b"configured" not in ciphertext
    raw_key = base64.b64decode(os.environ["X_INSIGHT_PROVIDER_DATA_KEY"])
    assert len(raw_key) == 32
    return AESGCM(raw_key).decrypt(bytes(row.key_nonce), ciphertext, None)


def _decrypt_revision(engine, revision: int) -> bytes:
    """Open one stored revision with the fixture key. No product helper."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT key_nonce, key_ciphertext FROM provider_settings WHERE revision = :revision"
            ),
            {"revision": revision},
        ).one()
    ciphertext = bytes(row.key_ciphertext)
    assert SENTINEL_KEY.encode() not in ciphertext
    assert b"configured" not in ciphertext
    raw_key = base64.b64decode(os.environ["X_INSIGHT_PROVIDER_DATA_KEY"])
    assert len(raw_key) == 32
    return AESGCM(raw_key).decrypt(bytes(row.key_nonce), ciphertext, None)


def test_secret_action_keeps_placeholder_out_of_storage_and_stale_write_fails(clean_all):
    client, csrf = _login()
    saved = _put(client, csrf, _payload())
    saved_body = _assert_masked_settings(saved)
    revision = saved_body["revision"]
    assert type(revision) is int and revision >= 1

    unchanged = _put(
        client,
        csrf,
        {
            "base_url": BASE_URL,
            "model": "s42-slice2-unchanged",
            "secret_action": "unchanged",
            "api_key": "configured",
        },
        revision,
    )
    unchanged_body = _assert_public(
        unchanged,
        api_key="configured",
        model="s42-slice2-unchanged",
        revision=revision + 1,
    )
    assert _decrypt_latest(clean_all) == SENTINEL_KEY.encode()

    replaced = _put(
        client,
        csrf,
        {
            "base_url": BASE_URL,
            "model": "s42-slice2-replaced",
            "secret_action": "replace",
            "api_key": REPLACEMENT_KEY,
        },
        unchanged_body["revision"],
    )
    replaced_body = _assert_public(
        replaced,
        api_key="configured",
        model="s42-slice2-replaced",
        revision=unchanged_body["revision"] + 1,
    )
    assert _decrypt_latest(clean_all) == REPLACEMENT_KEY.encode()

    cleared = _put(
        client,
        csrf,
        {
            "base_url": BASE_URL,
            "model": "s42-slice2-replaced",
            "secret_action": "clear",
        },
        replaced_body["revision"],
    )
    cleared_body = _assert_public(
        cleared,
        api_key="absent",
        model="s42-slice2-replaced",
        revision=replaced_body["revision"] + 1,
    )

    stale_key = "sk-sentinel-s42-stale"
    stale = _put(
        client,
        csrf,
        {
            "base_url": BASE_URL,
            "model": "s42-slice2-replaced",
            "secret_action": "replace",
            "api_key": stale_key,
        },
        1,
    )
    _assert_error(stale, 412, "STALE_REVISION")
    assert stale_key not in stale.text
    current = client.get(SETTINGS_PATH)
    current_body = _assert_public(
        current,
        api_key="absent",
        model="s42-slice2-replaced",
        revision=cleared_body["revision"],
    )
    assert current_body["revision"] == cleared_body["revision"]

    audit = client.get("/api/v1/audit-events")
    assert audit.status_code == 200, audit.text
    _assert_secrets_absent(audit)
    assert stale_key not in audit.text


def test_fake_ip_proxy_range_is_not_blocked_special_use(monkeypatch):
    """198.18.0.0/15 is fake-IP, not an internal target. RFC1918 and metadata stay blocked."""
    from x_insight.reasoning import provider_config

    monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "api.openai.com")

    def resolve(ip):
        def getaddrinfo(host, port, *args, **kwargs):
            assert host == "api.openai.com"
            return [(None, None, None, None, (ip, port))]

        return getaddrinfo

    url = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(provider_config.socket, "getaddrinfo", resolve("198.18.0.128"))
    assert provider_config._resolved_destination_allowed(url)
    for blocked in ("10.1.2.3", "169.254.169.254", "127.0.0.1"):
        monkeypatch.setattr(provider_config.socket, "getaddrinfo", resolve(blocked))
        assert not provider_config._resolved_destination_allowed(url)
    monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "api.openai.com,10.1.2.3")
    monkeypatch.setattr(provider_config.socket, "getaddrinfo", resolve("10.1.2.3"))
    assert provider_config._resolved_destination_allowed(url)


def test_save_rejects_destinations_outside_the_operator_allowlist(clean_all):
    client, csrf = _login()
    saved = _put(client, csrf, _payload())
    saved_body = _assert_masked_settings(saved)
    revision = saved_body["revision"]

    for base_url in (
        "file:///etc/passwd",
        "http://169.254.169.254/latest",
        "http://user:pass@127.0.0.1/v1",
        "https://example.com/v1",
    ):
        denied = _put(
            client,
            csrf,
            {"base_url": base_url, "model": MODEL, "secret_action": "unchanged"},
            revision,
        )
        _assert_error(denied, 422, "PROVIDER_DESTINATION_DENIED")

    kept = client.get(SETTINGS_PATH)
    assert kept.status_code == 200, kept.text
    kept_body = kept.json()
    assert kept_body["revision"] == revision
    assert kept_body["base_url"] == BASE_URL
    assert kept_body["api_key"] == "configured"
    _assert_no_secret(kept)

    allowed = _put(
        client,
        csrf,
        {"base_url": "http://127.0.0.1:9/v1", "model": MODEL, "secret_action": "unchanged"},
        revision,
    )
    _assert_public(allowed, api_key="configured", model=MODEL, revision=revision + 1)


_REDIRECT_HIT = "REDIRECT-HIT-s42"


def _assert_no_probe_leak(response) -> None:
    for secret in (SENTINEL_KEY, _REDIRECT_HIT):
        assert secret not in response.text
        assert all(secret not in value for value in response.headers.values())


@contextmanager
def _capability_probe():
    """Localhost probe. Server A accepts one capability POST; B counts redirects."""
    counts = {"chat_ok": 0, "b_hits": 0, "steps": {}, "captured": []}
    lock = threading.Lock()
    ports = {"b": 0}

    class _HitAll(BaseHTTPRequestHandler):
        def _hit(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length:
                self.rfile.read(length)
            with lock:
                counts["b_hits"] += 1
            body = _REDIRECT_HIT.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._hit
            raise AttributeError(name)

        def log_message(self, fmt, *args):
            return

    class _Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            path = self.path.split("?", 1)[0]
            if path == "/redirect":
                body = b"no"
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{ports['b']}/secret")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if path == "/echo":
                self._json(200, {"ok": True, "supports_tools": True, "supports_json": True})
                return
            if path == "/auth":
                self._json(401, {"error": {"message": SENTINEL_KEY, "type": "auth"}})
                return
            if path == "/model":
                self._json(404, {"error": {"message": SENTINEL_KEY, "code": "model_not_found"}})
                return
            if path == "/tools":
                self._json(400, {"error": {"message": SENTINEL_KEY, "param": "tools"}})
                return
            if path == "/format" and isinstance(payload, dict) and "response_format" in payload:
                self._json(400, {"error": {"message": SENTINEL_KEY, "param": "response_format"}})
                return
            if path not in ("/v1/chat/completions", "/json", "/format"):
                self._json(404, {"error": {"message": SENTINEL_KEY, "code": "model_not_found"}})
                return
            tools = payload.get("tools") if isinstance(payload, dict) else None
            messages = payload.get("messages") if isinstance(payload, dict) else None
            accepted = (
                isinstance(payload, dict)
                and self.headers.get("Authorization") == f"Bearer {SENTINEL_KEY}"
                and payload.get("model") == MODEL
                and "purpose" not in payload
                and isinstance(tools, list)
                and len(tools) > 0
                and isinstance(messages, list)
                and len(messages) > 0
            )
            if not accepted:
                self._json(400, {"error": {"message": SENTINEL_KEY, "param": "tools"}})
                return
            with lock:
                counts["captured"].append(payload)
                step = counts["steps"].get(path, 0)
                counts["steps"][path] = step + 1
            has_tool_result = any(
                isinstance(item, dict) and item.get("role") == "tool" for item in messages
            )
            if step == 0:
                if has_tool_result:
                    self._json(400, {"error": {"param": "tools"}})
                    return
                self._json(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "call_ping",
                                            "type": "function",
                                            "function": {"name": "ping", "arguments": "{}"},
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )
                return
            if not has_tool_result:
                self._json(400, {"error": {"param": "tools"}})
                return
            with lock:
                counts["chat_ok"] += 1
            if path == "/json":
                self._json(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": f"not-json {SENTINEL_KEY}",
                                }
                            }
                        ]
                    },
                )
                return
            self._json(
                200,
                {"choices": [{"message": {"role": "assistant", "content": '{"ready":true}'}}]},
            )

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server_b = ThreadingHTTPServer(("127.0.0.1", 0), _HitAll)
    server_a = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    ports["b"] = server_b.server_address[1]
    port_a = server_a.server_address[1]
    thread_b = threading.Thread(target=server_b.serve_forever, daemon=True)
    thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
    thread_b.start()
    thread_a.start()
    try:
        yield port_a, counts
    finally:
        server_a.shutdown()
        server_b.shutdown()
        server_a.server_close()
        server_b.server_close()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)


def test_capability_check_uses_the_real_client_and_save_is_not_verification(clean_all, monkeypatch):
    with _capability_probe() as (port_a, counts):
        client, csrf = _login()
        chat_url = f"http://127.0.0.1:{port_a}/v1/chat/completions"
        saved = _put(
            client,
            csrf,
            {"api_key": SENTINEL_KEY, "base_url": chat_url, "model": MODEL},
        )
        assert saved.status_code == 200, saved.text
        saved_body = saved.json()
        assert saved_body["api_key"] == "configured"
        assert saved_body["base_url"] == chat_url
        _assert_no_probe_leak(saved)

        fetched = client.get(SETTINGS_PATH)
        assert fetched.status_code == 200, fetched.text
        fetched_body = fetched.json()
        assert fetched_body.get("capability") == "untested", fetched_body
        _assert_no_probe_leak(fetched)

        checked = client.post(
            "/api/v1/api-settings/test",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        assert checked.status_code == 200, checked.text
        checked_body = checked.json()
        assert checked_body.get("capability") == "verified", checked_body
        assert isinstance(checked_body.get("diagnostic"), str) and checked_body["diagnostic"]
        _assert_no_probe_leak(checked)
        assert counts["chat_ok"] >= 1
        assert counts["captured"], "capability check did not POST a chat body"
        posted = json.dumps(counts["captured"])
        assert "purpose" not in posted
        assert "projection" not in posted
        assert "get_question_patient_inputs" not in posted
        assert SENTINEL_KEY not in posted
        first = counts["captured"][0]
        assert first.get("model") == MODEL
        assert isinstance(first.get("messages"), list) and first["messages"]
        assert isinstance(first.get("tools"), list) and first["tools"]
        assert any(
            isinstance(item, dict) and item.get("role") == "tool"
            for item in counts["captured"][-1].get("messages", [])
        )

        verified = client.get(SETTINGS_PATH)
        assert verified.status_code == 200, verified.text
        assert verified.json().get("capability") == "verified", verified.json()
        _assert_no_probe_leak(verified)

        redirect_url = f"http://127.0.0.1:{port_a}/redirect"
        resaved = _put(
            client,
            csrf,
            {"base_url": redirect_url, "model": MODEL, "secret_action": "unchanged"},
            verified.json()["revision"],
        )
        assert resaved.status_code == 200, resaved.text
        assert resaved.json()["base_url"] == redirect_url
        _assert_no_probe_leak(resaved)
        untested = client.get(SETTINGS_PATH)
        assert untested.status_code == 200, untested.text
        assert untested.json().get("capability") == "untested", untested.json()
        _assert_no_probe_leak(untested)

        failed = client.post(
            "/api/v1/api-settings/test",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        assert failed.status_code == 200, failed.text
        failed_body = failed.json()
        assert failed_body.get("capability") == "failed", failed_body
        assert failed_body.get("diagnostic") == "Provider returned a redirect."
        _assert_no_probe_leak(failed)
        assert counts["b_hits"] == 0

        monkeypatch.setenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "localhost")
        current = client.get(SETTINGS_PATH).json()["revision"]
        chat_before = counts["chat_ok"]
        local_url = f"http://localhost:{port_a}/v1/chat/completions"
        moved = _put(
            client,
            csrf,
            {"base_url": local_url, "model": MODEL, "secret_action": "unchanged"},
            current,
        )
        assert moved.status_code == 200, moved.text
        blocked = client.post(
            "/api/v1/api-settings/test",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        assert blocked.status_code == 200, blocked.text
        assert blocked.json().get("capability") == "failed", blocked.json()
        assert blocked.json().get("diagnostic") == "Resolved address is not an allowlisted IP."
        _assert_no_probe_leak(blocked)
        assert counts["chat_ok"] == chat_before


def test_capability_diagnostics_stay_distinct_and_echo_is_not_verified(clean_all):
    with _capability_probe() as (port, _counts):
        client, csrf = _login()

        def check(path: str) -> dict:
            saved = _put(
                client,
                csrf,
                {
                    "api_key": SENTINEL_KEY,
                    "base_url": f"http://127.0.0.1:{port}{path}",
                    "model": MODEL,
                },
            )
            assert saved.status_code == 200, saved.text
            checked = client.post(
                "/api/v1/api-settings/test",
                json={},
                headers={"X-CSRF-Token": csrf},
            )
            assert checked.status_code == 200, checked.text
            _assert_no_probe_leak(checked)
            return checked.json()

        echo = check("/echo")
        assert echo["capability"] == "failed", echo
        assert echo["diagnostic"] == "Provider does not support tools."

        auth = check("/auth")
        assert auth["capability"] == "failed", auth
        assert auth["diagnostic"] == "Provider rejected the API key."

        missing = check("/model")
        assert missing["capability"] == "failed", missing
        assert missing["diagnostic"] == "Provider model was not found."

        tools = check("/tools")
        assert tools["capability"] == "failed", tools
        assert tools["diagnostic"] == "Provider does not support tools."

        not_json = check("/json")
        assert not_json["capability"] == "failed", not_json
        assert not_json["diagnostic"] == "Provider does not support JSON responses."

        retried = check("/format")
        assert retried["capability"] == "verified", retried
        assert retried["diagnostic"] == "Provider supports tools and JSON."


def test_prior_revision_stays_readable_after_clear(clean_all):
    client, csrf = _login()
    saved = _put(
        client,
        csrf,
        {"api_key": SENTINEL_KEY, "base_url": BASE_URL, "model": "s42-rev-a"},
    )
    assert saved.status_code == 200, saved.text
    rev_a = saved.json()["revision"]
    assert type(rev_a) is int and rev_a >= 1

    replaced = _put(
        client,
        csrf,
        {
            "base_url": BASE_URL,
            "model": "s42-rev-b",
            "secret_action": "replace",
            "api_key": REPLACEMENT_KEY,
        },
        rev_a,
    )
    assert replaced.status_code == 200, replaced.text
    replaced_body = replaced.json()
    assert "affected_runs" in replaced_body, replaced_body
    assert type(replaced_body["affected_runs"]) is int and replaced_body["affected_runs"] >= 0
    rev_b = replaced_body["revision"]
    _assert_secrets_absent(replaced)

    prior = client.get(f"{SETTINGS_PATH}/revisions/{rev_a}")
    assert prior.status_code == 200, prior.text
    prior_body = prior.json()
    assert prior_body["revision"] == rev_a
    assert prior_body["model"] == "s42-rev-a"
    assert prior_body["api_key"] == "configured"
    assert prior_body["base_url"] == BASE_URL
    _assert_secrets_absent(prior)
    assert _decrypt_revision(clean_all, rev_a) == SENTINEL_KEY.encode()

    cleared = _put(
        client,
        csrf,
        {"base_url": BASE_URL, "model": "s42-rev-b", "secret_action": "clear"},
        rev_b,
    )
    assert cleared.status_code == 200, cleared.text
    cleared_body = cleared.json()
    assert cleared_body["api_key"] == "absent"
    assert type(cleared_body.get("affected_runs")) is int
    _assert_secrets_absent(cleared)

    still = client.get(f"{SETTINGS_PATH}/revisions/{rev_a}")
    assert still.status_code == 200, still.text
    still_body = still.json()
    assert still_body["revision"] == rev_a
    assert still_body["model"] == "s42-rev-a"
    assert still_body["api_key"] == "configured"
    _assert_secrets_absent(still)

    latest = client.get(SETTINGS_PATH)
    assert latest.status_code == 200, latest.text
    latest_body = latest.json()
    assert latest_body["api_key"] == "absent"
    assert latest_body["model"] == "s42-rev-b"
    _assert_secrets_absent(latest)

    physician, _physician_csrf = _physician_client(clean_all)
    _assert_error(physician.get(f"{SETTINGS_PATH}/revisions/{rev_a}"), 403, "FORBIDDEN")
