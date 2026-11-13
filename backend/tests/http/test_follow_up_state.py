"""Package C: follow-up-v1 through public HTTP + real PostgreSQL.

Verifies config, gated creation with typed defaults, author-only reads, strict
autosave (forged metadata, cross-workflow state, stale revision), read-only
preview/validate-step, legacy sibling-route guards, and that the activation
gate off keeps legacy follow-up behavior unchanged. Latest signed source
ordering is checked against real SQL with synthetic signed snapshots.

Requires a disposable migrated PostgreSQL (TEST_DATABASE_URL); no application
database is touched.
"""

from __future__ import annotations

import os
import uuid
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.cases import encounters as encounters_service
from x_insight.identity import service


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


def _truncate_cases(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE signed_encounter_snapshots, question_runs, generation_batches, "
                "notes, encounters, patients CASCADE"
            )
        )
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE idempotency_records"))


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
    assert response.status_code == 200, response.text
    return {"client": client, "csrf": response.json()["csrf_token"], "engine": clean_registry}


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    created = admin_client["client"].post(
        "/api/v1/physicians",
        json={"name_en": "Test physician", "username": username, "password": password},
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
    return client, response.json()["csrf_token"]


def _create_patient(client, csrf, identifier="0012345678"):
    response = client.post(
        "/api/v1/patients",
        json={
            "identifier": identifier,
            "given_name": "Anna",
            "family_name": "Novak",
            "sex": "F",
            "age": 30,
            "clinical_status": "established",
        },
        headers=_auth_headers(csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _discard(client, csrf, encounter_id, revision):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _save(client, csrf, encounter_id, draft_data, revision):
    return client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": draft_data},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _new_follow_up(client, csrf, patient_id, **extra):
    return client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up", **extra},
        headers=_auth_headers(csrf),
    )


def _activate(monkeypatch):
    monkeypatch.setattr(encounters_service, "FOLLOWUP_V1_ACTIVE", True)


def _open_follow_up(
    clean_registry,
    monkeypatch,
    admin_client,
    *,
    username="dr_fu",
    active=True,
    identifier="0012345678",
):
    monkeypatch.setattr(encounters_service, "FOLLOWUP_V1_ACTIVE", active)
    _make_physician(admin_client, username)
    client, csrf = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_patient(client, csrf, identifier=identifier)
    patient_id = created["patient"]["id"]
    registration = created["draft"]
    assert _discard(client, csrf, registration["id"], registration["revision"]).status_code == 200
    response = _new_follow_up(client, csrf, patient_id)
    assert response.status_code == 201, response.text
    return client, csrf, patient_id, response.json()


# --- Config (physician read, no credentials) ---


def test_config_requires_physician_and_exposes_versioned_definitions(
    clean_registry, monkeypatch, admin_client
):
    anon = TestClient(app).get("/api/v1/follow-up/config")
    assert anon.status_code == 401
    assert admin_client["client"].get("/api/v1/follow-up/config").status_code == 403
    _make_physician(admin_client, "dr_config")
    client, _ = _physician_client(clean_registry, monkeypatch, "dr_config", "pw123")
    response = client.get("/api/v1/follow-up/config")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_version"] == "follow-up-v1"
    assert body["defaults"]["cssrs_recent"] == "no"
    assert body["definitions"]["recovery"] == ["controlled", "partial", "none_or_worse"]
    assert body["hashes"]["workflow_manifest_hash"]
    assert "lab_fields" in body and "lai_equivalences" in body
    assert "password" not in response.text and "api_key" not in response.text


# --- Gated creation, read, strict autosave, preview ---


def test_follow_up_v1_creation_read_autosave_and_preview(clean_registry, monkeypatch, admin_client):
    client, csrf, patient_id, created = _open_follow_up(clean_registry, monkeypatch, admin_client)
    encounter_id = created["encounter"]["id"]
    assert created["encounter"]["workflow_version"] == "follow-up-v1"
    assert created["revision"] == 1

    body = created["draft_data"]
    assert body["follow_up"]["schema_version"] == "follow-up-state-v1"
    assert body["follow_up"]["visit_year"] == contracts.utcnow().year
    assert body["follow_up"]["baseline"]["has_source"] is False
    assert body["panss"]["answers"] == {}
    assert body["cssrs"]["answers"] == {f"css_i{i}_recent": "no" for i in range(1, 6)}
    assert body["medications"]["entries"] == []

    read = client.get(f"/api/v1/encounters/{encounter_id}")
    assert read.status_code == 200
    assert read.json()["draft_data"] == body  # served verbatim, no legacy normalization
    assert read.headers["etag"] == '"1"'

    preview = client.get(f"/api/v1/encounters/{encounter_id}/follow-up")
    assert preview.status_code == 200, preview.text
    state = preview.json()
    assert state["revision"] == 1
    assert state["proposal"] is None  # reading never creates a proposal
    assert state["can_complete"] is False
    assert state["errors_by_step"]["panss"] == []  # PANSS optional
    assert preview.headers["etag"] == '"1"'

    # A valid full-state autosave succeeds and bumps the revision.
    edited = deepcopy(body)
    edited["follow_up"]["considerations"]["recovery"] = "controlled"
    edited["panss"]["answers"]["P1"] = 3
    saved = _save(client, csrf, encounter_id, edited, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert saved.json()["draft_data"]["panss"]["answers"]["P1"] == 3

    # Forged server-owned metadata / provenance / cross-workflow state: 422,
    # revision unchanged (failed saves never look saved).
    for mutation in (
        {"follow_up": {**edited["follow_up"], "schema_version": "follow-up-v9"}},
        {"follow_up": {**edited["follow_up"], "baseline": {"has_source": True}}},
        {"follow_up": {**edited["follow_up"], "content_pins": {"x": 1}}},
        {**edited, "new_case": {"forged": True}},
    ):
        rejected = _save(client, csrf, encounter_id, mutation, 2)
        assert rejected.status_code == 422, rejected.text
    forged_provenance = deepcopy(edited)
    forged_provenance["follow_up"]["history"]["conditions"]["hepatic_failure"]["inherited"] = True
    assert _save(client, csrf, encounter_id, forged_provenance, 2).status_code == 422
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 2

    # Stale revision is 412 and changes nothing.
    assert _save(client, csrf, encounter_id, edited, 1).status_code == 412
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 2


def test_validate_step_is_read_only_and_blocks_incomplete_navigation(
    clean_registry, monkeypatch, admin_client
):
    client, csrf, _, created = _open_follow_up(clean_registry, monkeypatch, admin_client)
    encounter_id = created["encounter"]["id"]
    response = client.post(
        f"/api/v1/encounters/{encounter_id}/follow-up/validate-step",
        json={"destination": "proposal"},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["can_navigate"] is False
    assert result["navigation_errors"]
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 1
    # Unknown destination is refused; stale If-Match is 412.
    bad = client.post(
        f"/api/v1/encounters/{encounter_id}/follow-up/validate-step",
        json={"destination": "unknown"},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert bad.status_code == 422
    stale = client.post(
        f"/api/v1/encounters/{encounter_id}/follow-up/validate-step",
        json={"destination": "harm"},
        headers={**_auth_headers(csrf), "If-Match": '"9"'},
    )
    assert stale.status_code == 412


def test_inline_baseline_and_legacy_sibling_routes_are_refused(
    clean_registry, monkeypatch, admin_client
):
    client, csrf, patient_id, created = _open_follow_up(clean_registry, monkeypatch, admin_client)
    encounter_id = created["encounter"]["id"]
    # A client cannot forge the signed source through the inline baseline.
    assert _discard(client, csrf, encounter_id, 1).status_code == 200
    forged = _new_follow_up(
        client,
        csrf,
        patient_id,
        baseline={"history_values": {}, "prior_scores": {}, "medications": []},
    )
    assert forged.status_code == 422

    client, csrf, _, created = _open_follow_up(
        clean_registry, monkeypatch, admin_client, username="dr_fu2", identifier="0012345679"
    )
    encounter_id = created["encounter"]["id"]
    # Legacy routes fail closed on the new workflow (no silent body rewrite).
    assert (
        client.post(
            f"/api/v1/encounters/{encounter_id}/history",
            json={"values": {}},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/encounters/{encounter_id}/medications",
            json={"medications": []},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 409
    )
    legacy_baseline = client.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert legacy_baseline.status_code == 409
    # Stranger physician gets 403 without state; admin likewise.
    _make_physician(admin_client, "dr_stranger")
    stranger, stranger_csrf = _physician_client(clean_registry, monkeypatch, "dr_stranger", "pw123")
    assert stranger.get(f"/api/v1/encounters/{encounter_id}/follow-up").status_code == 403


def test_activation_gate_off_keeps_legacy_follow_up_unchanged(
    clean_registry, monkeypatch, admin_client
):
    client, csrf, _, created = _open_follow_up(
        clean_registry, monkeypatch, admin_client, username="dr_legacy", active=False
    )
    assert created["encounter"]["workflow_version"] == "legacy-v1"
    body = created["draft_data"]
    assert "follow_up" not in body
    assert "history" in body and "panss" in body and "cssrs" in body and "medications" in body
    assert client.get("/api/v1/follow-up/config").status_code == 200


# --- Latest signed source across variants, checked against real SQL ---


def _insert_synthetic_snapshot(engine, patient_id, *, schema, signed_at, encounter_id, proposal):
    """Insert one immutable signed snapshot with FKs deferred (superuser test DB).

    Returns the snapshot's own primary key, which is the stable ID tie-break
    used when two snapshots share ``signed_at``.
    """
    snapshot_id = uuid.uuid4()
    snapshot = {
        "schema_version": schema,
        "encounter": {
            "id": str(encounter_id),
            "kind": "follow_up",
            "workflow_version": "new-case-v1",
            "created_at": "2026-01-01T00:00:00Z",
        },
        "draft_data": {},
        "secondary_plan": {"clozapine_included": "unknown"},
    }
    with engine.begin() as connection:
        connection.execute(text("SET session_replication_role = replica"))
        try:
            connection.execute(
                text(
                    "INSERT INTO signed_encounter_snapshots "
                    "(id, encounter_id, patient_id, batch_id, proposal_id, "
                    "new_case_proposal_id, follow_up_proposal_id, secondary_plan_revision, "
                    "secondary_plan_text, snapshot, snapshot_hash, signer_id, signer_username, "
                    "signed_at, encounter_revision, created_at) VALUES "
                    "(:id, :encounter_id, :patient_id, NULL, NULL, :proposal, NULL, 1, '', "
                    ":snapshot, :hash, :signer, 'tester', :signed_at, 1, :created_at)"
                ),
                {
                    "id": snapshot_id,
                    "encounter_id": encounter_id,
                    "patient_id": patient_id,
                    "proposal": proposal,
                    "snapshot": contracts.canonical_json(snapshot).decode(),
                    "hash": "b" * 64,
                    "signer": uuid.uuid4(),
                    "signed_at": signed_at,
                    "created_at": signed_at,
                },
            )
        finally:
            connection.execute(text("SET session_replication_role = DEFAULT"))
    return snapshot_id


def test_latest_signed_source_selects_newest_with_stable_tie_break(
    clean_registry, monkeypatch, admin_client
):
    from x_insight.cases import follow_up_baseline

    client, csrf, _, created = _open_follow_up(
        clean_registry, monkeypatch, admin_client, username="dr_source", active=False
    )
    patient_id = uuid.UUID(created["encounter"]["patient_id"])
    # Two encounter rows to own the synthetic snapshots (FK columns deferred).
    older_id, newer_id, tied_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    moment = contracts.utcnow()
    snapshot_ids: dict[uuid.UUID, uuid.UUID] = {}
    tied_snapshot_ids: list[uuid.UUID] = []
    for encounter_id, signed_at in (
        (older_id, moment - timedelta(days=3)),
        (newer_id, moment),
        (tied_id, moment),
    ):
        with clean_registry.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO encounters (id, patient_id, kind, workflow_version, author_id, "
                    "lifecycle, created_at, updated_at, revision, draft_data) VALUES "
                    "(:id, :pid, 'registration', 'new-case-v1', :author, 'signed', "
                    ":t, :t, 1, '{}'::jsonb)"
                ),
                {
                    "id": encounter_id,
                    "pid": patient_id,
                    "author": uuid.UUID(created["encounter"]["author_id"]),
                    "t": signed_at,
                },
            )
        snapshot_ids[encounter_id] = _insert_synthetic_snapshot(
            clean_registry,
            patient_id,
            schema="signed-encounter-v4",
            signed_at=signed_at,
            encounter_id=encounter_id,
            proposal=uuid.uuid4(),
        )
        if encounter_id in (newer_id, tied_id):
            tied_snapshot_ids.append(snapshot_ids[encounter_id])
    with db_module.session_scope(clean_registry) as session:
        source = follow_up_baseline.latest_signed_source(session, patient_id)
    assert source is not None
    assert source["ordinal"] == 4  # three prior signed encounters + 1
    # Both tied rows share signed_at: the larger snapshot id wins, deterministically.
    assert source["row"]["id"] == max(tied_snapshot_ids, key=str)
    assert source["row"]["snapshot"]["schema_version"] == "signed-encounter-v4"
