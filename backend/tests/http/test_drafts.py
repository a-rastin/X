"""Author-owned draft persistence, concurrency, and slot release (S07, T1).

Backend only (tasks.md S07, plan.md §§2.3, 4; FR-16, FR-22, NFR-04) through
public HTTP + real PostgreSQL; no frontend, no assessment content:
1. Author saves/retrieves a private draft after restart; strangers (other
   physicians, admin) cannot read its clinical body or derived artifacts
   through ordinary routes, nor mutate it. Patient directory/demographics
   never carry draft content (transitive privacy).
2. Stale-tab PATCH fails 412; If-Match is required (absent/wildcard/malformed
   is 422). ETag tracks the revision.
3. Failed saves never look saved: 412/422 leave revision + body untouched,
   so the client reconciles against GET server truth.
4. One open draft per patient: sequential and simultaneous creates (including
   by the same author) yield one success and generic 409 OPEN_DRAFT_EXISTS
   with no content leak. Discard needs confirmation + current revision,
   releases the slot without deleting the patient, and calls the (currently
   no-op) job-cancellation hook.

Handoff: every wizard page autosaves through PATCH with the GET revision as
If-Match and a fresh Idempotency-Key per attempt (same key replays the same
attempt). Only 2xx is durable.
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
from x_insight.cases import encounters as encounters_service
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


def _truncate_cases(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE encounters, patients"))
    except Exception:
        pass
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE idempotency_records"))
    except Exception:
        pass


@pytest.fixture()
def clean_registry(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate_cases(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate_cases(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)


@pytest.fixture()
def admin_client(clean_registry, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    user = response.json()["user"]
    return {"client": client, "csrf": csrf, "user": user, "engine": clean_registry}


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    client = admin_client["client"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": username, "password": password},
        headers=_auth_headers(admin_client["csrf"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["user"]


def _physician_client(clean_registry, monkeypatch, username: str, password: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "role": "physician"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return client, body["csrf_token"], body["user"]


def _valid_payload(identifier: str = "0012345678", **overrides):
    payload = {
        "identifier": identifier,
        "given_name": "Anna",
        "family_name": "Novak",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "+43 699 123456",
    }
    payload.update(overrides)
    return payload


def _create_patient(client, csrf, identifier="0012345678"):
    response = client.post(
        "/api/v1/patients", json=_valid_payload(identifier), headers=_auth_headers(csrf)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _save(client, csrf, encounter_id, draft_data, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(
        f"/api/v1/encounters/{encounter_id}", json={"draft_data": draft_data}, headers=headers
    )


# --- Slice 1: private save/resume after restart ---


def test_author_saves_and_resumes_private_draft_after_restart(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_resume")
    client, csrf, user = _physician_client(clean_registry, monkeypatch, "dr_resume", "pw123")
    created = _create_patient(client, csrf)
    encounter_id = created["draft"]["id"]
    assert created["draft"]["revision"] == 1

    # Fresh registration drafts start with an empty object body.
    initial = client.get(f"/api/v1/encounters/{encounter_id}")
    assert initial.status_code == 200, initial.text
    assert initial.json()["draft_data"] == {}
    assert initial.json()["revision"] == 1
    assert initial.headers["etag"] == '"1"'

    body = {"demographics_note": "lives alone", "answers": {"q1": 2}, "schema_version": 1}
    saved = _save(client, csrf, encounter_id, body, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_data"] == body
    assert saved.json()["revision"] == 2
    assert saved.headers["etag"] == '"2"'

    # Restart: a brand-new HTTP client re-authenticates and reads server truth.
    fresh_client = TestClient(app)
    login = fresh_client.post(
        "/api/v1/auth/login",
        json={"username": "dr_resume", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh_client.get(f"/api/v1/encounters/{encounter_id}")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["draft_data"] == body
    assert resumed.json()["revision"] == 2
    assert resumed.headers["etag"] == '"2"'
    assert resumed.json()["encounter"]["author_id"] == user["id"]


def test_private_draft_hidden_from_other_physician_and_admin(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_owner")
    _make_physician(admin_client, "dr_stranger")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_owner", "pw123")
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_stranger", "pw123"
    )
    created = _create_patient(owner, owner_csrf)
    encounter_id = created["draft"]["id"]
    secret = {"private": "owner-only clinical content"}
    assert _save(owner, owner_csrf, encounter_id, secret, 1).status_code == 200

    # Stranger reads/mutations are denied without leaking the body.
    for denied in (
        stranger.get(f"/api/v1/encounters/{encounter_id}"),
        stranger.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {"hijack": True}},
            headers={**_auth_headers(stranger_csrf), "If-Match": '"2"'},
        ),
        stranger.post(
            f"/api/v1/encounters/{encounter_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(stranger_csrf), "If-Match": '"2"'},
        ),
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}"),
        admin_client["client"].patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {"hijack": True}},
            headers={**_auth_headers(admin_client["csrf"]), "If-Match": '"2"'},
        ),
    ):
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"
        assert "owner-only clinical content" not in denied.text
        assert "draft_data" not in denied.json()

    # Failed stranger writes changed nothing: author truth is intact.
    truth = owner.get(f"/api/v1/encounters/{encounter_id}")
    assert truth.status_code == 200
    assert truth.json()["draft_data"] == secret
    assert truth.json()["revision"] == 2

    # Transitive privacy: shared patient routes never carry draft content.
    patient_id = created["patient"]["id"]
    for reader in (
        stranger.get(f"/api/v1/patients/{patient_id}"),
        admin_client["client"].get(f"/api/v1/patients/{patient_id}"),
        stranger.get("/api/v1/patients"),
    ):
        assert reader.status_code == 200, reader.text
        assert "owner-only clinical content" not in reader.text
        assert "draft_data" not in reader.json()
        if "items" in reader.json():
            for item in reader.json()["items"]:
                assert "draft_data" not in item
        else:
            assert "draft_data" not in reader.json()["patient"]


# --- Slice 2: stale-tab 412 + ETag/If-Match contract ---


def test_stale_tab_patch_fails_412_and_keeps_server_truth(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_stale")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_stale", "pw123")
    encounter_id = _create_patient(client, csrf)["draft"]["id"]

    first_tab = _save(client, csrf, encounter_id, {"tab": "A"}, 1)
    assert first_tab.status_code == 200
    assert first_tab.json()["revision"] == 2

    # Stale second tab still on revision 1: 412 with the plan §4.3 body.
    stale = _save(client, csrf, encounter_id, {"tab": "B-stale"}, 1)
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    assert stale.json()["request_id"]

    # Current tab retries on the fresh revision and wins.
    retried = _save(client, csrf, encounter_id, {"tab": "B-reconciled"}, 2)
    assert retried.status_code == 200, retried.text
    assert retried.json()["revision"] == 3
    assert retried.headers["etag"] == '"3"'
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["draft_data"] == {
        "tab": "B-reconciled"
    }


def test_patch_requires_explicit_if_match(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_match")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_match", "pw123")
    encounter_id = _create_patient(client, csrf)["draft"]["id"]

    no_header = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {}},
        headers=_auth_headers(csrf),
    )
    assert no_header.status_code == 422, no_header.text
    assert no_header.json()["code"] == "INVALID_IF_MATCH"

    wildcard = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {}},
        headers={**_auth_headers(csrf), "If-Match": "*"},
    )
    assert wildcard.status_code == 422, wildcard.text

    malformed = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {}},
        headers={**_auth_headers(csrf), "If-Match": "not-a-revision"},
    )
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["code"] == "INVALID_IF_MATCH"

    # None of the rejected preconditions wrote anything.
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 1


# --- Slice 3: failed saves never look saved ---


def test_failed_validation_save_leaves_server_truth_unchanged(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_truth")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_truth", "pw123")
    encounter_id = _create_patient(client, csrf)["draft"]["id"]
    assert _save(client, csrf, encounter_id, {"kept": "first"}, 1).status_code == 200

    # Non-object body is rejected by the schema (422 with field errors).
    not_object = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": ["not", "an", "object"]},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert not_object.status_code == 422, not_object.text

    # Oversized object is rejected by the semantic contract.
    too_big = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {f"k{i}": i for i in range(101)}},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert too_big.status_code == 422, too_big.text
    assert too_big.json()["field_errors"]

    # Server truth still shows the acknowledged write only.
    truth = client.get(f"/api/v1/encounters/{encounter_id}")
    assert truth.json() == {
        "encounter": truth.json()["encounter"],
        "draft_data": {"kept": "first"},
        "revision": 2,
    }
    assert truth.headers["etag"] == '"2"'


def test_patch_idempotency_replays_without_double_bump(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_idem_draft")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_idem_draft", "pw123")
    encounter_id = _create_patient(client, csrf)["draft"]["id"]

    first = _save(client, csrf, encounter_id, {"v": 1}, 1, key="draft-save-001")
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 2

    # Same key + same body + same base revision replays the stored result.
    replay = _save(client, csrf, encounter_id, {"v": 1}, 1, key="draft-save-001")
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"] == 2
    assert replay.json()["draft_data"] == {"v": 1}

    # Same key + changed body is a key conflict and writes nothing.
    conflict = _save(client, csrf, encounter_id, {"v": 2}, 2, key="draft-save-001")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 2


# --- Slice 4: single-slot creation race + confirmed discard ---


def test_sequential_second_create_conflicts_generically(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_slot_a")
    _make_physician(admin_client, "dr_slot_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_slot_a", "pw123")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_slot_b", "pw123")
    created = _create_patient(client_a, csrf_a)
    patient_id = created["patient"]["id"]
    assert _save(client_a, csrf_a, created["draft"]["id"], {"secret": 1}, 1).status_code == 200

    # Same author and another author both get the same generic conflict.
    for contender, csrf in ((client_a, csrf_a), (client_b, csrf_b)):
        denied = contender.post(
            f"/api/v1/patients/{patient_id}/encounters",
            json={"kind": "follow_up"},
            headers=_auth_headers(csrf),
        )
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "OPEN_DRAFT_EXISTS"
        assert "secret" not in denied.text
        assert "draft_data" not in denied.json()
        assert "author_id" not in denied.json()
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE lifecycle = 'draft'")
        ).scalar_one()
    assert drafts == 1


def test_concurrent_creates_allow_one_draft_only(admin_client, clean_registry, monkeypatch) -> None:
    import threading

    _make_physician(admin_client, "dr_race_owner")
    _make_physician(admin_client, "dr_race_other")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_race_owner", "pw123")
    created = _create_patient(owner, owner_csrf, "0111111111")
    draft_id = created["draft"]["id"]
    patient_id = created["patient"]["id"]
    # Free the slot so the race starts from an empty slot (discard path below
    # proves release; here it sets up a genuine simultaneous-create race).
    discard = owner.post(
        f"/api/v1/encounters/{draft_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(owner_csrf), "If-Match": '"1"'},
    )
    assert discard.status_code == 200, discard.text

    barrier = threading.Barrier(7)
    outcomes: list[int] = []
    bodies: list[tuple[int, str]] = []

    def _attempt(username: str) -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        response = thread_client.post(
            f"/api/v1/patients/{patient_id}/encounters",
            json={"kind": "follow_up"},
            headers=_auth_headers(csrf),
        )
        outcomes.append(response.status_code)
        bodies.append((response.status_code, response.text))

    threads = [
        threading.Thread(target=_attempt, args=(name,))
        for name in (
            "dr_race_owner",
            "dr_race_owner",
            "dr_race_owner",
            "dr_race_other",
            "dr_race_other",
            "dr_race_other",
            "dr_race_other",
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == [201] + [409] * 6, outcomes
    # Losers get the generic conflict with no content/author oracle; the one
    # winner sees only its own fresh empty body.
    for status, body in bodies:
        if status == 409:
            assert '"OPEN_DRAFT_EXISTS"' in body
            assert "draft_data" not in body
            assert "author_id" not in body
        else:
            assert '"draft_data":{}' in body.replace(" ", "")
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE patient_id = :pid AND lifecycle = 'draft'"),
            {"pid": patient_id},
        ).scalar_one()
    assert drafts == 1


def test_discard_requires_confirmation_and_current_revision(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_discard")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_discard", "pw123")
    created = _create_patient(client, csrf)
    encounter_id = created["draft"]["id"]
    patient_id = created["patient"]["id"]
    assert _save(client, csrf, encounter_id, {"k": "v"}, 1).status_code == 200

    unconfirmed = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": False},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert unconfirmed.status_code == 422, unconfirmed.text

    stale = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"

    # Both failures kept the slot: the draft is still readable and occupied.
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 200
    occupied = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert occupied.status_code == 409

    # The job-cancellation hook is wired even though no jobs exist yet.
    calls: list[str] = []
    real_hook = encounters_service.cancel_draft_jobs
    monkeypatch.setattr(encounters_service, "cancel_draft_jobs", lambda eid: calls.append(str(eid)))
    try:
        done = client.post(
            f"/api/v1/encounters/{encounter_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"2"'},
        )
    finally:
        monkeypatch.setattr(encounters_service, "cancel_draft_jobs", real_hook)
    assert done.status_code == 200, done.text
    assert calls == [encounter_id]
    assert done.json()["encounter"]["lifecycle"] == "discarded"
    assert done.json()["revision"] == 3
    assert "draft_data" not in done.json()

    # Released rows are not readable through ordinary draft routes, the slot
    # is free for a new draft, and the patient was never deleted.
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 404
    assert client.get(f"/api/v1/patients/{patient_id}").status_code == 200
    freed = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert freed.status_code == 201, freed.text
    assert freed.json()["encounter"]["id"] != encounter_id
    assert freed.json()["draft_data"] == {}


def test_draft_storage_column_grants_and_object_check(clean_registry) -> None:
    with clean_registry.connect() as connection:

        def _can(role: str, privilege: str, table: str) -> bool:
            return bool(
                connection.execute(
                    text(f"SELECT has_table_privilege('{role}', '{table}', '{privilege}')")
                ).scalar()
            )

        assert _can("x_insight_app", "SELECT", "encounters")
        assert _can("x_insight_app", "INSERT", "encounters")
        assert _can("x_insight_app", "UPDATE", "encounters")
        assert not _can("x_insight_app", "DELETE", "encounters")
        assert _can("x_insight_readonly", "SELECT", "encounters")
        assert not _can("x_insight_readonly", "INSERT", "encounters")
        assert _can("x_insight_migrate", "UPDATE", "encounters")
    with clean_registry.begin() as connection:
        nullable = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'encounters' AND column_name = 'draft_data'"
            )
        ).scalar_one()
        assert nullable == "NO"
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_encounters_draft_data_object'"
            )
        ).scalar_one()
        assert "jsonb_typeof" in definition
        assert "object" in definition


def test_draft_audit_events_are_safe_and_attributed(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_audit_draft")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_audit_draft", "pw123")
    created = _create_patient(client, csrf)
    encounter_id = created["draft"]["id"]
    assert (
        _save(client, csrf, encounter_id, {"secret_inside": "must-not-audit"}, 1).status_code == 200
    )  # noqa: E501
    discard = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert discard.status_code == 200
    # Slot creation through the S07 route emits its own audit event.
    patient_id = created["patient"]["id"]
    recreated = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert recreated.status_code == 201, recreated.text
    with db_module.session_scope(clean_registry) as session:
        rows = (
            session.execute(
                text(
                    "SELECT operation, actor, details FROM audit_events "
                    "WHERE operation LIKE 'encounters.%' ORDER BY occurred_at"
                )
            )
            .mappings()
            .all()
        )
    # Registration (S06) creates its draft via patients.create, not the S07
    # slot route — so the S07 trail here is save, discard, then re-create.
    assert [row["operation"] for row in rows] == [
        "encounters.patch.success",
        "encounters.discard.success",
        "encounters.create.success",
    ]
    assert {row["actor"] for row in rows} == {"dr_audit_draft"}
    blob = contracts.canonical_json([dict(row["details"]) for row in rows]).decode()
    assert "must-not-audit" not in blob
    assert "secret_inside" not in blob
    for secret in ("password", "pbkdf2", "token_hash", "csrf"):
        assert secret not in blob.lower()


def test_unknown_encounter_is_not_found(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_missing")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_missing", "pw123")
    missing = uuid.uuid4()
    assert client.get(f"/api/v1/encounters/{missing}").status_code == 404
    assert (
        client.patch(
            f"/api/v1/encounters/{missing}",
            json={"draft_data": {}},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/encounters/{missing}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/patients/{missing}/encounters",
            json={"kind": "follow_up"},
            headers=_auth_headers(csrf),
        ).status_code
        == 404
    )
    assert TestClient(app).get(f"/api/v1/encounters/{missing}").status_code == 401
