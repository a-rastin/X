"""Physician account administration (S04, seam T1).

Slices (tasks.md S04, plan.md §§2.1, 4.1-4.3, 11) through public HTTP + real
PostgreSQL, backend only (no frontend, no clinical content):
1. Admin creates/edits physician and retrieves safe fields; physician cannot
   list/manage accounts or elevate role; stable actor IDs across rename;
   admin username immutable.
2. Reset/deactivation revokes sessions immediately; inactive cannot auth;
   revision (If-Match/ETag 412) + reactivation.
3. Deactivation explicit retain/discard + empty draft-set revision contract
   (no draft tables; Cases integration incomplete until S51).
4. Idempotency/conflicts, audit safe attributed events, no hash in response.

Handoff: admin_client fixture is the authenticated admin interface; physician
login uses the created account; revocation is credential_revision +
revoked_at with active checks.
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
from x_insight.identity import service


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


def _truncate_identity(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    # Idempotency store (S04 migration 0003) in its own transaction so a
    # missing table (red phase) never rolls back the truncates above.
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE idempotency_records"))
    except Exception:
        pass


@pytest.fixture()
def clean_identity(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate_identity(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate_identity(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)


@pytest.fixture()
def admin_client(clean_identity, monkeypatch):
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


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _login(client: TestClient, username="admin", password="admin", role="admin"):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "role": role}
    )


def _physician_client(clean_identity, monkeypatch, username: str, password: str):
    """Log in as a physician; returns (client, csrf, user)."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    response = _login(client, username=username, password=password, role="physician")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["research_warning"] == service.RESEARCH_WARNING
    return client, body["csrf_token"], body["user"]


def _assert_safe_user(payload: dict) -> None:
    assert set(payload) >= {"id", "username", "role", "active", "revision"}
    uuid.UUID(str(payload["id"]))
    assert payload["role"] in ("admin", "physician")
    blob = contracts.canonical_json(payload).decode().lower()
    for secret in ("password", "pbkdf2", "token_hash", "csrf", "hash"):
        assert secret not in blob, f"safe user leaked {secret}: {blob}"


# --- Slice 1: create/edit/retrieve + authorization ---


def test_admin_creates_and_retrieves_physician_safe_fields(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_jones", "password": "secret123", "role": "physician"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    _assert_safe_user(user)
    assert user["username"] == "dr_jones"
    assert user["role"] == "physician"
    assert user["active"] is True
    # Retrieve via list and single read; both carry safe fields only.
    listed = client.get("/api/v1/physicians")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(item["id"] == user["id"] and item["username"] == "dr_jones" for item in items)
    for item in items:
        _assert_safe_user(item)
    single = client.get(f"/api/v1/physicians/{user['id']}")
    assert single.status_code == 200, single.text
    _assert_safe_user(single.json()["user"])
    assert single.json()["user"]["id"] == user["id"]
    # No hash/token anywhere in these responses.
    for response in (created, listed, single):
        lowered = response.text.lower()
        assert "pbkdf2" not in lowered
        assert "token_hash" not in lowered
        assert "csrf" not in lowered


def test_admin_edits_physician_preserves_stable_id(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_house", "password": "initial"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    original_id = created["id"]
    original_revision = created["revision"]
    etag = f'"{original_revision}"'
    patched = client.patch(
        f"/api/v1/physicians/{original_id}",
        json={"username": "dr_house_renamed"},
        headers={**_auth_headers(csrf), "If-Match": etag},
    )
    assert patched.status_code == 200, patched.text
    renamed = patched.json()["user"]
    _assert_safe_user(renamed)
    assert renamed["id"] == original_id
    assert renamed["username"] == "dr_house_renamed"
    assert renamed["revision"] == original_revision + 1
    # Stable ID resolves under the new name.
    again = client.get(f"/api/v1/physicians/{original_id}")
    assert again.status_code == 200
    assert again.json()["user"]["username"] == "dr_house_renamed"
    assert again.json()["user"]["id"] == original_id


def test_admin_username_immutable(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    admin_id = admin_client["user"]["id"]
    # Username mutation on the admin account is denied.
    denied = client.patch(
        f"/api/v1/physicians/{admin_id}",
        json={"username": "root"},
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 422, denied.text
    assert client.get("/api/v1/me").json()["user"]["username"] == "admin"
    # Role elevation is denied: physician stays physician.
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_noelev", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    elevated = client.patch(
        f"/api/v1/physicians/{created['id']}",
        json={"username": "dr_noelev", "role": "admin"},
        headers=_auth_headers(csrf),
    )
    assert elevated.status_code == 422, elevated.text
    assert client.get(f"/api/v1/physicians/{created['id']}").json()["user"]["role"] == "physician"


def test_physician_cannot_manage_accounts_or_elevate(
    admin_client, clean_identity, monkeypatch
) -> None:
    admin_http = admin_client["client"]
    admin_csrf = admin_client["csrf"]
    admin_http.post(
        "/api/v1/physicians",
        json={"username": "dr_limited", "password": "pw123"},
        headers=_auth_headers(admin_csrf),
    )
    phys_client, phys_csrf, _ = _physician_client(
        clean_identity, monkeypatch, "dr_limited", "pw123"
    )
    assert phys_client.get("/api/v1/physicians").status_code == 403
    assert (
        phys_client.post(
            "/api/v1/physicians",
            json={"username": "dr_other", "password": "x"},
            headers=_auth_headers(phys_csrf),
        ).status_code
        == 403
    )
    # Even with a valid id, physician PATCH is denied (no elevation path).
    listed = admin_http.get("/api/v1/physicians").json()["items"]
    target = next(item for item in listed if item["username"] == "dr_limited")
    assert (
        phys_client.patch(
            f"/api/v1/physicians/{target['id']}",
            json={"username": "dr_hacked"},
            headers=_auth_headers(phys_csrf),
        ).status_code
        == 403
    )
    # Deactivation authorization is covered in Slice 2 (route lands there).
    assert True


def test_unauthenticated_physician_routes_401(clean_identity, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_identity)
    client = TestClient(app)
    assert client.get("/api/v1/physicians").status_code == 401
    assert client.post(
        "/api/v1/physicians", json={"username": "x", "password": "y"}
    ).status_code in (
        401,
        403,
    )
    assert client.patch(
        f"/api/v1/physicians/{uuid.uuid4()}", json={"username": "z"}
    ).status_code in (401, 403)


# --- Slice 2: reset/deactivation revocation + reactivation + revision ---


def test_password_reset_revokes_sessions_immediately(
    admin_client, clean_identity, monkeypatch
) -> None:
    admin_http = admin_client["client"]
    admin_csrf = admin_client["csrf"]
    created = admin_http.post(
        "/api/v1/physicians",
        json={"username": "dr_reset", "password": "old_secret"},
        headers=_auth_headers(admin_csrf),
    ).json()["user"]
    phys_client, phys_csrf, _ = _physician_client(
        clean_identity, monkeypatch, "dr_reset", "old_secret"
    )
    assert phys_client.get("/api/v1/me").status_code == 200
    etag = f'"{created["revision"]}"'
    reset = admin_http.patch(
        f"/api/v1/physicians/{created['id']}",
        json={"password": "brand_new_secret"},
        headers={**_auth_headers(admin_csrf), "If-Match": etag},
    )
    assert reset.status_code == 200, reset.text
    # Prior physician session is revoked immediately.
    assert phys_client.get("/api/v1/me").status_code == 401
    # Old password dead (generic 401), new password works.
    assert (
        _login(
            TestClient(app), username="dr_reset", password="old_secret", role="physician"
        ).status_code
        == 401
    )
    fresh_login = _login(
        TestClient(app), username="dr_reset", password="brand_new_secret", role="physician"
    )
    assert fresh_login.status_code == 200
    assert fresh_login.json()["research_warning"] == service.RESEARCH_WARNING


def test_deactivation_revokes_and_blocks_inactive_reactivation_restores(
    admin_client, clean_identity, monkeypatch
) -> None:
    admin_http = admin_client["client"]
    admin_csrf = admin_client["csrf"]
    created = admin_http.post(
        "/api/v1/physicians",
        json={"username": "dr_sleep", "password": "awake123"},
        headers=_auth_headers(admin_csrf),
    ).json()["user"]
    phys_client, _, _ = _physician_client(clean_identity, monkeypatch, "dr_sleep", "awake123")
    assert phys_client.get("/api/v1/me").status_code == 200
    deactivated = admin_http.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["user"]["active"] is False
    # Existing session revoked immediately; inactive login fails generic 401.
    assert phys_client.get("/api/v1/me").status_code == 401
    failed = _login(TestClient(app), username="dr_sleep", password="awake123", role="physician")
    assert failed.status_code == 401
    assert failed.json()["code"] == "UNAUTHENTICATED"
    assert failed.json()["message"] == "Invalid username, password, or role."
    # Reactivation restores authentication (old sessions stay revoked).
    reactivated = admin_http.post(
        f"/api/v1/physicians/{created['id']}/reactivate",
        headers=_auth_headers(admin_csrf),
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["user"]["active"] is True
    assert phys_client.get("/api/v1/me").status_code == 401
    assert (
        _login(
            TestClient(app), username="dr_sleep", password="awake123", role="physician"
        ).status_code
        == 200
    )


def test_patch_stale_revision_412_and_etag(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_stale", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    revision = created["revision"]
    stale = client.patch(
        f"/api/v1/physicians/{created['id']}",
        json={"username": "dr_stale_v2"},
        headers={**_auth_headers(csrf), "If-Match": f'"{revision + 99}"'},
    )
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    fresh = client.patch(
        f"/api/v1/physicians/{created['id']}",
        json={"username": "dr_stale_v2"},
        headers={**_auth_headers(csrf), "If-Match": f'"{revision}"'},
    )
    assert fresh.status_code == 200
    assert fresh.headers["ETag"] == f'"{revision + 1}"'
    assert fresh.json()["user"]["revision"] == revision + 1


def test_deactivation_requires_admin_role(admin_client, clean_identity, monkeypatch) -> None:
    admin_http = admin_client["client"]
    admin_csrf = admin_client["csrf"]
    created = admin_http.post(
        "/api/v1/physicians",
        json={"username": "dr_guard", "password": "pw"},
        headers=_auth_headers(admin_csrf),
    ).json()["user"]
    phys_client, phys_csrf, _ = _physician_client(clean_identity, monkeypatch, "dr_guard", "pw")
    assert (
        phys_client.post(
            f"/api/v1/physicians/{created['id']}/deactivate",
            json={"draft_action": "retain"},
            headers=_auth_headers(phys_csrf),
        ).status_code
        == 403
    )
    assert (
        phys_client.post(
            f"/api/v1/physicians/{created['id']}/reactivate",
            headers=_auth_headers(phys_csrf),
        ).status_code
        == 403
    )
    assert TestClient(app).post(
        f"/api/v1/physicians/{created['id']}/deactivate", json={"draft_action": "retain"}
    ).status_code in (401, 403)


# --- Slice 3: retain/discard choice + empty draft-set revision contract ---
#
# Until drafts exist (Cases S07/S14), the reviewed set is empty and no draft
# tables are fabricated for this test. Real draft/job race cases land in S51
# (Cases integration explicitly incomplete until then).


def test_deactivation_requires_explicit_retain_or_discard(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_choice", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    # Missing choice → 422; invented choice → 422; no silent default.
    assert (
        client.post(
            f"/api/v1/physicians/{created['id']}/deactivate",
            json={},
            headers=_auth_headers(csrf),
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/physicians/{created['id']}/deactivate",
            json={"draft_action": "maybe"},
            headers=_auth_headers(csrf),
        ).status_code
        == 422
    )
    # Both explicit choices are accepted and echoed with the empty set.
    for action in ("retain", "discard"):
        target = client.post(
            "/api/v1/physicians",
            json={"username": f"dr_{action}_ok", "password": "x"},
            headers=_auth_headers(csrf),
        ).json()["user"]
        response = client.post(
            f"/api/v1/physicians/{target['id']}/deactivate",
            json={"draft_action": action},
            headers=_auth_headers(csrf),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["draft_action"] == action
        assert body["reviewed_drafts"] == []
        assert body["draft_set_revision"] == 0
        assert body["user"]["active"] is False


def test_deactivation_stale_draft_set_revision_conflicts(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_draftrev", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    # Reviewed revision 0 (empty) is current; any other revision is stale.
    ok = client.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "discard", "draft_set_revision": 0},
        headers=_auth_headers(csrf),
    )
    assert ok.status_code == 200
    other = client.post(
        "/api/v1/physicians",
        json={"username": "dr_draftrev2", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    stale = client.post(
        f"/api/v1/physicians/{other['id']}/deactivate",
        json={"draft_action": "discard", "draft_set_revision": 7},
        headers=_auth_headers(csrf),
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "DRAFT_SET_CHANGED"


def test_no_draft_tables_fabricated_for_deactivation(clean_identity) -> None:
    """S04 must not create draft-content tables (S51 owns Cases integration).

    S06 legitimately adds the patient registry (patients + encounters); this
    pins that scope: no notes/runs/jobs/drafts tables beyond S06.
    """
    from sqlalchemy import text

    with clean_identity.connect() as connection:
        tables = sorted(
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
    assert "alembic_version" in tables
    assert "audit_events" in tables
    assert "users" in tables
    assert "sessions" in tables
    assert "patients" in tables
    assert "encounters" in tables
    for fabricated in ("drafts", "notes", "runs", "jobs"):
        assert fabricated not in tables, f"unexpected table {fabricated}"


# --- Slice 4: idempotency/conflicts/audit/no secrets ---


def test_idempotent_create_replays_same_body_conflicts_changed_body(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    headers = {**_auth_headers(csrf), "Idempotency-Key": "create-replay-001"}
    first = client.post(
        "/api/v1/physicians",
        json={"username": "dr_idem", "password": "secret"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["user"]["id"]
    # Same key + same body replays the original result (no duplicate).
    replay = client.post(
        "/api/v1/physicians",
        json={"username": "dr_idem", "password": "secret"},
        headers=headers,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["user"]["id"] == first_id
    # Same key + changed body is 409 (not a silent second account).
    conflict = client.post(
        "/api/v1/physicians",
        json={"username": "dr_idem", "password": "different"},
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] in ("IDEMPOTENCY_CONFLICT", "CONFLICT")


def test_idempotent_deactivate_replay_and_conflict(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_idem_deact", "password": "x"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    headers = {**_auth_headers(csrf), "Idempotency-Key": "deact-replay-001"}
    first = client.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "retain"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "retain"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["user"]["id"] == first.json()["user"]["id"]
    conflict = client.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "discard"},
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text


def test_duplicate_username_conflicts_without_idempotency(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    assert (
        client.post(
            "/api/v1/physicians",
            json={"username": "dr_dup", "password": "a"},
            headers=_auth_headers(csrf),
        ).status_code
        == 201
    )
    duplicate = client.post(
        "/api/v1/physicians",
        json={"username": "DR_DUP", "password": "b"},
        headers=_auth_headers(csrf),
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "CONFLICT"


def test_audit_safe_attributed_events_no_secrets(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    engine = admin_client["engine"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_audited", "password": "s3cret!"},
        headers=_auth_headers(csrf),
    ).json()["user"]
    etag = f'"{created["revision"]}"'
    client.patch(
        f"/api/v1/physicians/{created['id']}",
        json={"username": "dr_audited_renamed"},
        headers={**_auth_headers(csrf), "If-Match": etag},
    )
    client.post(
        f"/api/v1/physicians/{created['id']}/deactivate",
        json={"draft_action": "discard"},
        headers=_auth_headers(csrf),
    )
    client.post(
        f"/api/v1/physicians/{created['id']}/reactivate",
        headers=_auth_headers(csrf),
    )
    with db_module.session_scope(engine) as session:
        rows = (
            session.execute(
                text(
                    "SELECT operation, actor, details FROM audit_events "
                    "WHERE operation LIKE 'physicians.%'"
                )
            )
            .mappings()
            .all()
        )
    operations = [row["operation"] for row in rows]
    for expected in (
        "physicians.create.success",
        "physicians.patch.success",
        "physicians.deactivate.success",
        "physicians.reactivate.success",
    ):
        assert expected in operations, f"missing {expected} in {operations}"
    for row in rows:
        assert row["actor"] == "admin"
        blob = contracts.canonical_json(row["details"]).decode().lower()
        assert "dr_audited" in blob or "retain" in blob or "discard" in blob
        for secret in ("password", "s3cret", "pbkdf2", "token_hash", "csrf", "hash"):
            assert secret not in blob, f"{row['operation']} leaked {secret}: {blob}"


def test_no_hash_or_password_in_any_physician_response(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_clean", "password": "topsecret123"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201
    responses = [
        created,
        client.get("/api/v1/physicians"),
        client.get(f"/api/v1/physicians/{created.json()['user']['id']}"),
        client.patch(
            f"/api/v1/physicians/{created.json()['user']['id']}",
            json={"username": "dr_clean_v2"},
            headers=_auth_headers(csrf),
        ),
    ]
    for response in responses:
        assert response.status_code in (200, 201), response.text
        lowered = response.text.lower()
        assert "password_hash" not in lowered
        assert "pbkdf2" not in lowered
        assert "topsecret123" not in lowered
        assert "token_hash" not in lowered
        assert "csrf" not in lowered


def test_physician_list_pagination_bounds(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    for i in range(3):
        client.post(
            "/api/v1/physicians",
            json={"username": f"dr_page_{i}", "password": "x"},
            headers=_auth_headers(csrf),
        )
    defaulted = client.get("/api/v1/physicians")
    assert defaulted.status_code == 200
    assert defaulted.json()["limit"] == 25
    one = client.get("/api/v1/physicians?limit=1&offset=0")
    assert one.status_code == 200
    assert len(one.json()["items"]) == 1
    assert client.get("/api/v1/physicians?limit=101").status_code == 422
    assert client.get("/api/v1/physicians?limit=0").status_code == 422


# --- Gap cover (S04 verify): validation, CSRF, singleton admin, 404s ---
#
# The 18 tests above exercise all four S04 bullets. The four below pin
# critical plan §2.1/§4.3 gates that had no S04 assertion: empty-credential
# validation on admin create, CSRF on physician mutations, singleton-admin
# preservation (no role=admin create, no admin deactivation), physician
# single-read denial, and unknown-ID 404s. All through public T1 HTTP.


def test_physician_create_rejects_empty_fields_and_admin_role(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    headers = _auth_headers(csrf)
    # Empty password is 422 verbatim (no silent accept; S03 no-trim rule carries).
    empty_pw = client.post(
        "/api/v1/physicians",
        json={"username": "dr_empty_pw", "password": ""},
        headers=headers,
    )
    assert empty_pw.status_code == 422, empty_pw.text
    assert "password" in empty_pw.json()["field_errors"]
    # Blank username is 422.
    blank_user = client.post(
        "/api/v1/physicians",
        json={"username": "   ", "password": "x"},
        headers=headers,
    )
    assert blank_user.status_code == 422, blank_user.text
    # Second admin via role=admin is denied (singleton admin, plan §2.1).
    second_admin = client.post(
        "/api/v1/physicians",
        json={"username": "admin2", "password": "x", "role": "admin"},
        headers=headers,
    )
    assert second_admin.status_code == 422, second_admin.text
    # None of the rejected attempts created an account.
    listed = client.get("/api/v1/physicians").json()["items"]
    names = {item["username"] for item in listed}
    assert "dr_empty_pw" not in names
    assert "admin2" not in names
    assert sum(1 for item in listed if item["role"] == "admin") == 1


def test_physician_mutations_require_csrf(admin_client) -> None:
    client = admin_client["client"]  # holds the admin session cookie
    created = client.post(
        "/api/v1/physicians",
        json={"username": "dr_csrf", "password": "x"},
        headers=_auth_headers(admin_client["csrf"]),
    ).json()["user"]
    # Same session but missing/wrong CSRF → 403 on every physician mutation.
    assert (
        client.post(
            "/api/v1/physicians",
            json={"username": "dr_csrf2", "password": "x"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/physicians/{created['id']}",
            json={"username": "dr_csrf_v2"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/physicians/{created['id']}/deactivate",
            json={"draft_action": "retain"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/physicians/{created['id']}/deactivate",
            json={"draft_action": "retain"},
            headers={"X-CSRF-Token": "bad"},
        ).status_code
        == 403
    )
    # CSRF-denied attempts changed nothing.
    names = {item["username"] for item in client.get("/api/v1/physicians").json()["items"]}
    assert "dr_csrf2" not in names
    assert client.get(f"/api/v1/physicians/{created['id']}").json()["user"]["active"] is True


def test_physician_cannot_read_single_physician(admin_client, clean_identity, monkeypatch) -> None:
    admin_http = admin_client["client"]
    admin_csrf = admin_client["csrf"]
    target = admin_http.post(
        "/api/v1/physicians",
        json={"username": "dr_private", "password": "pw123"},
        headers=_auth_headers(admin_csrf),
    ).json()["user"]
    phys_client, _, _ = _physician_client(clean_identity, monkeypatch, "dr_private", "pw123")
    # Single-read is credential listing: physician denied like the list route.
    assert phys_client.get(f"/api/v1/physicians/{target['id']}").status_code == 403
    assert phys_client.get("/api/v1/physicians?limit=1&offset=0").status_code == 403


def test_admin_cannot_be_deactivated_and_unknown_ids_404(admin_client) -> None:
    client = admin_client["client"]
    csrf = admin_client["csrf"]
    admin_id = admin_client["user"]["id"]
    denied = client.post(
        f"/api/v1/physicians/{admin_id}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 422, denied.text
    assert client.get("/api/v1/me").json()["user"]["username"] == "admin"
    missing = str(uuid.uuid4())
    assert client.get(f"/api/v1/physicians/{missing}").status_code == 404
    assert (
        client.patch(
            f"/api/v1/physicians/{missing}",
            json={"username": "ghost"},
            headers=_auth_headers(csrf),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/physicians/{missing}/deactivate",
            json={"draft_action": "retain"},
            headers=_auth_headers(csrf),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/physicians/{missing}/reactivate",
            headers=_auth_headers(csrf),
        ).status_code
        == 404
    )
