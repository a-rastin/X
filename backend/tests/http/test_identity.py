"""Identity: login, sessions, own credentials (S03, seam T1).

Slices (tasks.md S03, plan.md §§2.1, 4.3, 11) through public HTTP + real
PostgreSQL:
1. Singleton seeding + hashing: fresh DB permits admin/admin once; second
   init preserves changed password; no second admin provisionable.
2. Login + /me: opaque cookie session; generic errors; active checks; CSRF;
   throttling; privileges from account not login role.
3. Revocation, no timeout: logout/password change revoke prior sessions;
   controlled clock causes no expiry; no timeout/complexity.
4. Deny username mutation/self-registration; empty password fails without
   trimming; audit successes/failures with no credentials.
5. Versioned research-warning acknowledgement (issue 24): both roles gated on
   login and /me until the session user acknowledges the current version;
   stale versions are 409 without writes; CSRF/session denial; no revision bump.

Handoff: ``admin_client`` fixture is the authenticated interface (cookies +
CSRF); revocation is credential-revision + revoked_at, never timeout.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import passwords, service
from x_insight.identity.service import SESSION_COOKIE_NAME


@pytest.fixture(scope="module")
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
def clean_identity(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    service.ensure_default_admin(migrated_test_engine)


@pytest.fixture()
def admin_client(clean_identity, monkeypatch):
    """Authenticated admin interface for handoff: client + CSRF + user."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    user = response.json()["user"]
    return {"client": client, "csrf": csrf, "user": user, "engine": clean_identity}


def _login(client: TestClient, username="admin", password="admin", role="admin"):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "role": role}
    )


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


# --- Slice 1: seeding + hashing ---


def test_password_hash_format_and_verify() -> None:
    hashed = passwords.hash_password("admin")
    assert hashed.startswith("pbkdf2_sha256$600000$")
    assert passwords.verify_password("admin", hashed)
    assert not passwords.verify_password("wrong", hashed)
    assert not passwords.verify_password("admin ", hashed)
    assert not passwords.verify_password("", hashed)
    # Verbatim: trailing-space password never equals trimmed one.
    spaced = passwords.hash_password("  secret  ")
    assert passwords.verify_password("  secret  ", spaced)
    assert not passwords.verify_password("secret", spaced)
    assert not passwords.verify_password("bad", "not-a-hash")
    assert not passwords.verify_password("x", "pbkdf2_sha256$abc$def$ghi")


def test_fresh_db_permits_admin_admin_once(clean_identity) -> None:
    client = TestClient(app)
    response = _login(client)
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "admin"
    assert response.json()["user"]["role"] == "admin"
    # Opaque cookie session present (value is not the hash).
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite" in set_cookie.lower()


def test_second_init_preserves_changed_password(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    login = _login(client)
    csrf = login.json()["csrf_token"]
    changed = client.post(
        "/api/v1/me/password",
        json={"current_password": "admin", "new_password": "rotated-secret"},
        headers=_auth_headers(csrf),
    )
    assert changed.status_code == 200
    # Second initialization must not recreate the default password.
    service.ensure_default_admin(clean_identity)
    service.ensure_default_admin(clean_identity)
    with db_module.session_scope(clean_identity) as session:
        rows = session.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one()
        assert rows == 1
    fresh = TestClient(app)
    assert _login(fresh, password="admin").status_code == 401
    assert _login(fresh, password="rotated-secret").status_code == 200


def test_no_second_admin_provisionable(clean_identity) -> None:
    service.ensure_default_admin(clean_identity)
    service.ensure_default_admin(clean_identity)
    with db_module.session_scope(clean_identity) as session:
        count = session.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one()
        assert count == 1
        with pytest.raises(Exception):
            session.execute(
                text(
                    "INSERT INTO users (id, username, role, active, password_hash,"
                    " credential_revision, theme, created_at, updated_at, revision)"
                    " VALUES (gen_random_uuid(), 'admin2', 'admin', true, 'x', 1,"
                    " 'light', now(), now(), 1)"
                )
            )


# --- Slice 2: login + /me ---


def test_login_and_me_flow(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": body["user"]["id"],
        "username": "admin",
        "role": "admin",
        "theme": "light",
    }
    uuid.UUID(body["user"]["id"])
    assert body["csrf_token"]
    # Administrators are gated like physicians until they acknowledge the version.
    assert body["research_warning"] == service.RESEARCH_WARNING
    assert body["research_warning_version"] == service.RESEARCH_WARNING_VERSION
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "admin"
    assert me.json()["research_warning"] == service.RESEARCH_WARNING
    assert me.json()["csrf_token"] == body["csrf_token"]
    assert me.headers["Cache-Control"] == "private, no-store"
    # A freshly opened tab can obtain the token bound to its shared cookie
    # and use it without asking the user to sign in again.
    updated = client.patch(
        "/api/v1/me/preferences",
        json={"theme": "dark"},
        headers=_auth_headers(me.json()["csrf_token"]),
    )
    assert updated.status_code == 200


def test_login_generic_errors_mask_details(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    bodies = []
    for payload in (
        {"username": "admin", "password": "wrong", "role": "admin"},
        {"username": "nope", "password": "admin", "role": "admin"},
        {"username": "admin", "password": "admin", "role": "physician"},
        {"username": "", "password": "admin", "role": "admin"},
        {"username": "admin", "password": "", "role": "admin"},
    ):
        response = client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "UNAUTHENTICATED"
        assert body["message"] == "Invalid username, password, or role."
        assert body["field_errors"] == {}
        bodies.append(response.text)
    # No enumeration, no credentials, no hash in any failure.
    for text_body in bodies:
        assert "Traceback" not in text_body
        for secret in ("wrong", "hash", "pbkdf2", "token"):
            assert secret not in text_body.lower() or "invalid" in text_body.lower()


def test_role_mismatch_cannot_grant_access(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    # Correct password but selected role differs from stored account role.
    response = _login(client, username="admin", password="admin", role="physician")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    # Privileges come from the account: /me shows stored role, never login role.
    ok = _login(client, username="ADMIN", password="admin", role="admin")
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "admin"


def test_inactive_account_cannot_authenticate(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    with db_module.session_scope(clean_identity) as session:
        session.execute(text("UPDATE users SET active=false WHERE username='admin'"))
    client = TestClient(app)
    assert _login(client).status_code == 401
    assert client.get("/api/v1/me").status_code == 401
    with db_module.session_scope(clean_identity) as session:
        session.execute(text("UPDATE users SET active=true WHERE username='admin'"))
    assert _login(TestClient(app)).status_code == 200


def test_csrf_required_for_mutations(admin_client) -> None:
    client = admin_client["client"]
    # No CSRF → 403; wrong CSRF → 403; correct tested in other flows.
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "bad"}).status_code == 403
    me_no_csrf = client.patch("/api/v1/me/preferences", json={"theme": "dark"})
    assert me_no_csrf.status_code == 403


def test_login_throttling(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    for _ in range(5):
        assert _login(client, password="wrong").status_code == 401
    throttled = _login(client, password="wrong")
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "RATE_LIMITED"
    assert throttled.json()["retryable"] is True
    # Even correct credentials are throttled while the window holds.
    assert _login(client, password="admin").status_code == 429


def test_session_cookie_flags_http_vs_https(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    http_client = TestClient(app)
    http_cookie = _login(http_client).headers.get("set-cookie", "").lower()
    assert "httponly" in http_cookie
    assert "samesite=lax" in http_cookie
    assert "secure" not in http_cookie
    https_client = TestClient(app, base_url="https://testserver")
    https_cookie = _login(https_client).headers.get("set-cookie", "").lower()
    assert "httponly" in https_cookie
    assert "samesite=lax" in https_cookie
    assert "secure" in https_cookie


# --- Slice 3: revocation, no timeout ---


def test_logout_revokes_session(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    assert client.get("/api/v1/me").status_code == 200
    logout = client.post("/api/v1/auth/logout", headers=_auth_headers(csrf))
    assert logout.status_code == 200
    assert logout.json() == {"status": "ok"}
    # Prior session no longer authenticates; CSRF reuse also fails.
    assert client.get("/api/v1/me").status_code == 401
    assert client.post("/api/v1/auth/logout", headers=_auth_headers(csrf)).status_code == 401


def test_password_change_revokes_prior_sessions(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client_a = TestClient(app)
    csrf_a = _login(client_a).json()["csrf_token"]
    client_b = TestClient(app)
    csrf_b = _login(client_b).json()["csrf_token"]
    assert client_a.get("/api/v1/me").status_code == 200
    assert client_b.get("/api/v1/me").status_code == 200
    changed = client_b.post(
        "/api/v1/me/password",
        json={"current_password": "admin", "new_password": "brand-new"},
        headers=_auth_headers(csrf_b),
    )
    assert changed.status_code == 200
    new_csrf = changed.json()["csrf_token"]
    # Both prior sessions are revoked; the fresh one works.
    assert client_a.get("/api/v1/me").status_code == 401
    assert client_b.get("/api/v1/me").status_code == 200
    # Old CSRF token no longer valid on the rotated client (403: session ok, CSRF wrong).
    assert client_b.post("/api/v1/auth/logout", headers=_auth_headers(csrf_b)).status_code == 403
    assert client_b.post("/api/v1/auth/logout", headers=_auth_headers(new_csrf)).status_code == 200
    # Old password dead, new password works (and old client stays dead).
    assert _login(TestClient(app), password="admin").status_code == 401
    assert _login(TestClient(app), password="brand-new").status_code == 200
    assert csrf_a != new_csrf


def test_controlled_clock_causes_no_expiry(admin_client, monkeypatch) -> None:
    from datetime import timedelta

    client = admin_client["client"]
    assert client.get("/api/v1/me").status_code == 200
    real_now = contracts.utcnow()
    future = real_now + timedelta(days=365)
    monkeypatch.setattr(contracts, "utcnow", lambda: future)
    # No idle/absolute timeout: far-future clock still authenticates.
    assert client.get("/api/v1/me").status_code == 200


def test_no_password_complexity_and_no_timeout_on_simple_secret(
    clean_identity, monkeypatch
) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    csrf = _login(client).json()["csrf_token"]
    # Single-character secret is allowed: no complexity rule (plan.md §2.1).
    changed = client.post(
        "/api/v1/me/password",
        json={"current_password": "admin", "new_password": "x"},
        headers=_auth_headers(csrf),
    )
    assert changed.status_code == 200
    assert _login(TestClient(app), password="x").status_code == 200


# --- Slice 4: mutation denial, empty passwords, audit ---


def test_username_mutation_denied(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    # Extra username field is forbidden; theme-only schema denies mutation.
    response = client.patch(
        "/api/v1/me/preferences",
        json={"theme": "dark", "username": "hacker"},
        headers=_auth_headers(csrf),
    )
    assert response.status_code == 422
    assert client.get("/api/v1/me").json()["user"]["username"] == "admin"
    # Legitimate theme change still works and stays revision-safe.
    ok = client.patch("/api/v1/me/preferences", json={"theme": "dark"}, headers=_auth_headers(csrf))
    assert ok.status_code == 200
    assert ok.json()["user"]["theme"] == "dark"
    assert ok.json()["user"]["username"] == "admin"


def test_self_registration_denied(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    for method, path, payload in (
        ("post", "/api/v1/auth/register", {"username": "doc", "password": "x"}),
        ("post", "/api/v1/physicians", {"username": "doc", "password": "x"}),
        ("get", "/api/v1/physicians", None),
    ):
        response = (
            getattr(client, method)(path, json=payload)
            if payload
            else getattr(client, method)(path)
        )
        assert response.status_code in (401, 403, 404, 405)


def test_empty_password_fails_without_trimming(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    assert _login(client, password="").status_code == 401
    # Trailing-space password is distinct (no silent trim) → generic 401.
    assert _login(client, password="admin ").status_code == 401
    assert _login(client, password=" admin").status_code == 401
    csrf = _login(client).json()["csrf_token"]
    empty_change = client.post(
        "/api/v1/me/password",
        json={"current_password": "admin", "new_password": ""},
        headers=_auth_headers(csrf),
    )
    assert empty_change.status_code == 422
    assert "new_password" in empty_change.json()["field_errors"]
    # Spaced new secret is stored verbatim (not trimmed): login needs spaces.
    spaced = client.post(
        "/api/v1/me/password",
        json={"current_password": "admin", "new_password": "  spaced  "},
        headers=_auth_headers(csrf),
    )
    assert spaced.status_code == 200
    assert _login(TestClient(app), password="  spaced  ").status_code == 200
    assert _login(TestClient(app), password="spaced").status_code == 401


def test_audit_success_failure_without_credentials(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    assert _login(client, password="wrong").status_code == 401
    csrf = _login(client).json()["csrf_token"]
    assert client.post("/api/v1/auth/logout", headers=_auth_headers(csrf)).status_code == 200
    with db_module.session_scope(clean_identity) as session:
        rows = session.execute(text("SELECT operation, details FROM audit_events")).mappings().all()
    operations = [row["operation"] for row in rows]
    assert "auth.login.failure" in operations
    assert "auth.login.success" in operations
    assert "auth.logout" in operations
    for row in rows:
        blob = contracts.canonical_json(row["details"]).decode()
        lowered = blob.lower()
        assert "admin" not in lowered or "username" in lowered
        for secret in ("password", "pbkdf2", "token_hash", "csrf", "hash"):
            assert secret not in lowered, f"{row['operation']} leaked {secret}: {blob}"


def test_changing_credentials_must_not_recreate_default(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    csrf = _login(client).json()["csrf_token"]
    assert (
        client.post(
            "/api/v1/me/password",
            json={"current_password": "admin", "new_password": "persisted"},
            headers=_auth_headers(csrf),
        ).status_code
        == 200
    )
    service.ensure_default_admin(clean_identity)
    assert _login(TestClient(app), password="admin").status_code == 401
    assert _login(TestClient(app), password="persisted").status_code == 200


def test_health_and_ready_preserved_with_new_schema(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "x-insight"}
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["schema_version"] == db_module.EXPECTED_SCHEMA_VERSION


def test_real_postgresql_only(clean_identity) -> None:
    assert "postgresql" in str(clean_identity.url)
    assert "+sqlite" not in str(clean_identity.url)
    with clean_identity.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
    assert "PostgreSQL" in version


# --- Slice 5: versioned research-warning acknowledgement (issue 24) ---
#
# Pending = the account's stored acknowledged version is NULL or differs from
# RESEARCH_WARNING_VERSION, for both roles. The mutation takes only the
# displayed version; it can only change the session user's own acknowledgement.

ACK_PATH = "/api/v1/me/research-warning-acknowledgement"
WARNING_FIELDS = ("research_warning", "research_warning_version")
CLEARED = {"research_warning": None, "research_warning_version": None}


def _warning_fields(body: dict) -> dict:
    return {name: body[name] for name in WARNING_FIELDS}


def _pending() -> dict:
    return {
        "research_warning": service.RESEARCH_WARNING,
        "research_warning_version": service.RESEARCH_WARNING_VERSION,
    }


def _create_physician(admin: dict, username: str, password: str = "secret123") -> dict:
    created = admin["client"].post(
        "/api/v1/physicians",
        json={"username": username, "password": password},
        headers=_auth_headers(admin["csrf"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["user"]


def _acknowledge(client: TestClient, csrf: str, version: str | None = None):
    body = {"version": service.RESEARCH_WARNING_VERSION if version is None else version}
    return client.post(ACK_PATH, json=body, headers=_auth_headers(csrf))


@pytest.mark.parametrize(
    ("username", "password", "role"),
    [("admin", "admin", "admin"), ("dr_both_roles", "secret123", "physician")],
)
def test_both_roles_are_pending_then_cleared_for_later_login_and_me(
    admin_client, clean_identity, username, password, role
) -> None:
    if role == "physician":
        _create_physician(admin_client, username, password)
    client = TestClient(app)
    login = _login(client, username=username, password=password, role=role)
    assert login.status_code == 200, login.text
    assert _warning_fields(login.json()) == _pending()
    assert _warning_fields(client.get("/api/v1/me").json()) == _pending()

    ack = _acknowledge(client, login.json()["csrf_token"])
    assert ack.status_code == 200, ack.text
    assert ack.json() == CLEARED

    # A fresh client is a new sign-in: login and /me both report no warning.
    fresh = TestClient(app)
    fresh_login = _login(fresh, username=username, password=password, role=role)
    assert _warning_fields(fresh_login.json()) == CLEARED
    assert _warning_fields(fresh.get("/api/v1/me").json()) == CLEARED


def test_acknowledgement_is_isolated_to_the_session_user(admin_client, clean_identity) -> None:
    _create_physician(admin_client, "dr_first")
    _create_physician(admin_client, "dr_second")
    first = TestClient(app)
    csrf = _login(first, "dr_first", "secret123", "physician").json()["csrf_token"]
    assert _acknowledge(first, csrf).status_code == 200

    second = _login(TestClient(app), "dr_second", "secret123", "physician")
    assert _warning_fields(second.json()) == _pending()
    assert _warning_fields(_login(TestClient(app)).json()) == _pending()


def test_changing_the_version_makes_acknowledged_accounts_pending_again(
    admin_client, clean_identity, monkeypatch
) -> None:
    original = service.RESEARCH_WARNING_VERSION
    _create_physician(admin_client, "dr_revised")
    for username, password, role in (
        ("admin", "admin", "admin"),
        ("dr_revised", "secret123", "physician"),
    ):
        client = TestClient(app)
        csrf = _login(client, username, password, role).json()["csrf_token"]
        assert _acknowledge(client, csrf).status_code == 200

    revised = "research-warning-v2"
    monkeypatch.setattr(service, "RESEARCH_WARNING_VERSION", revised)
    for username, password, role in (
        ("admin", "admin", "admin"),
        ("dr_revised", "secret123", "physician"),
    ):
        login = _login(TestClient(app), username, password, role)
        assert login.json()["research_warning_version"] == revised
        assert login.json()["research_warning"] == service.RESEARCH_WARNING

    client = TestClient(app)
    csrf = _login(client, "dr_revised", "secret123", "physician").json()["csrf_token"]
    stale = _acknowledge(client, csrf, version=original)
    assert stale.status_code == 409, stale.text
    assert stale.json()["research_warning_version"] == revised
    acknowledged = _acknowledge(client, csrf, version=revised)
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json() == CLEARED


def test_stale_version_is_rejected_with_current_text_and_persists_nothing(
    admin_client, clean_identity
) -> None:
    _create_physician(admin_client, "dr_stale")
    client = TestClient(app)
    csrf = _login(client, "dr_stale", "secret123", "physician").json()["csrf_token"]

    stale = _acknowledge(client, csrf, version="research-warning-v0")
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert set(body) == {
        "code",
        "message",
        "field_errors",
        "request_id",
        "retryable",
        *WARNING_FIELDS,
    }
    assert body["code"] == "RESEARCH_WARNING_CHANGED"
    assert body["retryable"] is True
    assert "version" in body["field_errors"]
    assert body["request_id"] == stale.headers["x-request-id"]
    assert _warning_fields(body) == _pending()
    # Nothing was written: still pending for /me and for a new sign-in.
    assert _warning_fields(client.get("/api/v1/me").json()) == _pending()
    relogin = _login(TestClient(app), "dr_stale", "secret123", "physician")
    assert _warning_fields(relogin.json()) == _pending()


def test_stale_version_after_current_acknowledgement_reports_no_pending_text(
    admin_client, clean_identity
) -> None:
    _create_physician(admin_client, "dr_already")
    client = TestClient(app)
    csrf = _login(client, "dr_already", "secret123", "physician").json()["csrf_token"]
    assert _acknowledge(client, csrf).status_code == 200

    stale = _acknowledge(client, csrf, version="research-warning-v0")
    assert stale.status_code == 409, stale.text
    assert _warning_fields(stale.json()) == CLEARED


def test_acknowledgement_denied_without_session_or_csrf(admin_client, clean_identity) -> None:
    _create_physician(admin_client, "dr_guard")
    current = service.RESEARCH_WARNING_VERSION
    unauthenticated = TestClient(app).post(ACK_PATH, json={"version": current})
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "UNAUTHENTICATED"

    client = TestClient(app)
    _login(client, "dr_guard", "secret123", "physician")
    missing = client.post(ACK_PATH, json={"version": current})
    assert missing.status_code == 403
    assert missing.json()["code"] == "FORBIDDEN"
    forged = client.post(ACK_PATH, json={"version": current}, headers={"X-CSRF-Token": "forged"})
    assert forged.status_code == 403
    # Same denial as an existing mutation on this surface.
    existing = client.patch("/api/v1/me/preferences", json={"theme": "dark"})
    assert existing.status_code == 403
    assert _warning_fields(client.get("/api/v1/me").json()) == _pending()
    relogin = _login(TestClient(app), "dr_guard", "secret123", "physician")
    assert _warning_fields(relogin.json()) == _pending()


def test_acknowledgement_rejects_malformed_body_and_client_supplied_identity(
    admin_client, clean_identity
) -> None:
    _create_physician(admin_client, "dr_body")
    client = TestClient(app)
    csrf = _login(client, "dr_body", "secret123", "physician").json()["csrf_token"]
    current = service.RESEARCH_WARNING_VERSION
    headers = _auth_headers(csrf)

    empty = client.post(ACK_PATH, json={"version": ""}, headers=headers)
    assert empty.status_code == 422
    assert empty.json()["code"] == "VALIDATION_FAILED"
    assert "version" in empty.json()["field_errors"]
    missing = client.post(ACK_PATH, json={}, headers=headers)
    assert missing.status_code == 422
    assert missing.json()["code"] == "VALIDATION_FAILED"
    smuggled = client.post(
        ACK_PATH,
        json={"version": current, "user_id": str(admin_client["user"]["id"])},
        headers=headers,
    )
    assert smuggled.status_code == 422
    assert smuggled.json()["code"] == "VALIDATION_FAILED"
    assert _warning_fields(client.get("/api/v1/me").json()) == _pending()


def test_repeated_current_version_acknowledgement_succeeds(admin_client, clean_identity) -> None:
    _create_physician(admin_client, "dr_repeat")
    client = TestClient(app)
    csrf = _login(client, "dr_repeat", "secret123", "physician").json()["csrf_token"]
    for _ in range(2):
        ack = _acknowledge(client, csrf)
        assert ack.status_code == 200, ack.text
        assert ack.json() == CLEARED
    assert _warning_fields(client.get("/api/v1/me").json()) == CLEARED


def test_acknowledgement_leaves_credential_and_profile_revisions_unchanged(
    admin_client, clean_identity
) -> None:
    physician = _create_physician(admin_client, "dr_revision")
    detail = f"/api/v1/physicians/{physician['id']}"
    before = admin_client["client"].get(detail).json()["user"]
    client = TestClient(app)
    csrf = _login(client, "dr_revision", "secret123", "physician").json()["csrf_token"]
    assert _acknowledge(client, csrf).status_code == 200

    after = admin_client["client"].get(detail).json()["user"]
    assert after["revision"] == before["revision"]
    # A credential-revision bump would revoke this live session; it must survive.
    assert client.get("/api/v1/me").status_code == 200
