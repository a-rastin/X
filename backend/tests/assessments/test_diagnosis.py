"""Diagnosis, threshold warning, and bypass (S09, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 evaluator (no frontend):

1. S08 full criterion logic via evaluate(): a source-derived qualifying case
   satisfies every required criterion; a symptom-count-only case does not.
   Live preview (GET diagnosis) uses the same evaluator (no drift).
2. Partial answers have no completed threshold result: incomplete saves via
   the shared PATCH autosave never mislabel as below_threshold.
3. Completed below-threshold needs an attributed acknowledgment tied to the
   assessed revision (actor/time/status); later relevant edits invalidate it
   while unrelated draft keys do not.
4. Bypass succeeds with an empty body (no reason field exists), records
   actor/time/status, survives resume, and uses the shared autosave contract
   (draft_data + revision + If-Match/412 + Idempotency-Key).

Diagnosis state lives in ``draft_data["diagnosis"]``; answers travel through
the existing PATCH path, ack/bypass through server-attributed POSTs.
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
from x_insight.assessments import evaluate
from x_insight.assessments.released import get_released_definition
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


def _truncate(engine) -> None:
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
    _truncate(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
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
    return {
        "client": client,
        "csrf": response.json()["csrf_token"],
        "user": response.json()["user"],
        "engine": clean_registry,
    }


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    created = admin_client["client"].post(
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


def _valid_patient(identifier: str = "0012345678") -> dict:
    return {
        "identifier": identifier,
        "given_name": "Anna",
        "family_name": "Novak",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "+43 699 123456",
    }


def _create_draft(client, csrf, identifier="0012345678") -> dict:
    created = client.post(
        "/api/v1/patients", json=_valid_patient(identifier), headers=_auth_headers(csrf)
    )
    assert created.status_code == 201, created.text
    return created.json()


def _patch(client, csrf, encounter_id, draft_data, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(
        f"/api/v1/encounters/{encounter_id}", json={"draft_data": draft_data}, headers=headers
    )


def _get_diagnosis(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/diagnosis")


def _ack(client, csrf, encounter_id, revision, key=None, body=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/acknowledgment",
        json={} if body is None else body,
        headers=headers,
    )


def _bypass(client, csrf, encounter_id, revision, key=None, body=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/bypass",
        json={} if body is None else body,
        headers=headers,
    )


def _base_answers() -> dict:
    return {
        "a_delusions": "no",
        "a_hallucinations": "no",
        "a_disorganized_speech": "no",
        "a_disorganized_behavior": "no",
        "a_negative_symptoms": "no",
        "b_functional_decline": "no",
        "c_six_months": "no",
        "c_active_month": "no",
        "c_shortened_by_intervention": "no",
        "d_mood_exclusion": "no",
        "e_substance_medical_exclusion": "no",
        "f_autism_present": "no",
    }


def _qualifying_answers() -> dict:
    answers = _base_answers()
    answers.update(
        {
            "a_delusions": "yes",
            "a_hallucinations": "yes",
            "b_functional_decline": "yes",
            "c_six_months": "yes",
            "c_active_month": "yes",
            "d_mood_exclusion": "yes",
            "e_substance_medical_exclusion": "yes",
        }
    )
    return answers


def _symptom_only_answers() -> dict:
    answers = _base_answers()
    answers.update(
        {"a_delusions": "yes", "a_hallucinations": "yes", "a_disorganized_speech": "yes"}
    )
    return answers


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


# --- Slice 1: full criterion logic via evaluate() + live preview (no drift) ---


def test_qualifying_case_satisfies_every_criterion_preview_matches_evaluator(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag1", "0012345001"
    )
    answers = _qualifying_answers()
    direct = evaluate(get_released_definition("diagnosis"), answers)
    assert direct["status"] == "complete"
    assert direct["findings"]["overall"] == "criteria_satisfied"

    saved = _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_diagnosis(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["evaluation"]["findings"]["criteria"] == {
        "A": "met",
        "B": "met",
        "C": "met",
        "D": "met",
        "E": "met",
        "F": "met",
    }
    assert body["can_proceed"] is True
    assert body["requires_acknowledgment"] is False
    assert body["proceed_via"] == "criteria_satisfied"
    assert preview.headers["etag"] == '"2"'


def test_symptom_count_only_does_not_satisfy(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag2", "0012345002"
    )
    answers = _symptom_only_answers()
    direct = evaluate(get_released_definition("diagnosis"), answers)
    assert direct["status"] == "complete"
    assert direct["findings"]["criteria"]["A"] == "met"
    assert direct["findings"]["overall"] == "below_threshold"

    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )
    preview = _get_diagnosis(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["findings"]["overall"] == "below_threshold"
    assert body["can_proceed"] is False
    assert body["requires_acknowledgment"] is True


# --- Slice 2: partial has no completed result; never below_threshold ---


def test_partial_answers_save_without_mislabeling_as_below_threshold(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag3", "0012345003"
    )
    partial = {"a_delusions": "yes", "a_hallucinations": "yes"}
    direct = evaluate(get_released_definition("diagnosis"), partial)
    assert direct["status"] == "partial"
    assert direct["findings"] == {}

    saved = _patch(client, csrf, encounter_id, {"diagnosis": {"answers": partial}}, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    preview = _get_diagnosis(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["evaluation"]["status"] == "partial"
    assert body["evaluation"]["findings"] == {}
    assert body["evaluation"]["findings"].get("overall") is None
    assert body["evaluation"].get("scores", {}) == {}
    assert "b_functional_decline" in body["evaluation"]["missing_item_ids"]
    assert body["can_proceed"] is False
    assert body["requires_acknowledgment"] is False


def test_unknown_stays_unknown_missing_never_zero(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag4", "0012345004"
    )
    answers = _qualifying_answers()
    answers.update({"a_delusions": "unknown", "a_disorganized_speech": "unknown"})
    direct = evaluate(get_released_definition("diagnosis"), answers)
    assert direct["status"] == "complete"
    assert direct["findings"]["criteria"]["A"] == "unknown"
    assert direct["findings"]["overall"] == "indeterminate"

    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )
    body = _get_diagnosis(client, encounter_id).json()
    assert body["evaluation"]["findings"]["overall"] == "indeterminate"
    assert body["can_proceed"] is False
    assert body["requires_acknowledgment"] is False


# --- Slice 3: attributed acknowledgment tied to revision; relevant edits invalidate ---


def test_below_threshold_acknowledgment_is_attributed_and_gates_proceed(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag5", "0012345005"
    )
    answers = _symptom_only_answers()
    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )

    before = _get_diagnosis(client, encounter_id).json()
    assert before["requires_acknowledgment"] is True
    assert before["can_proceed"] is False

    acked = _ack(client, csrf, encounter_id, 2)
    assert acked.status_code == 200, acked.text
    body = acked.json()
    assert body["revision"] == 3
    assert acked.headers["etag"] == '"3"'
    ack = body["acknowledgment"]
    assert ack["status"] == "acknowledged"
    assert ack["actor_id"] == user["id"]
    assert ack["revision"] == 2
    assert contracts.parse_utc(ack["acknowledged_at"])
    assert ack["definition_version"] == "v1"
    assert ack["answers_hash"] == contracts.canonical_hash(answers)
    assert body["acknowledgment_valid"] is True
    assert body["can_proceed"] is True
    assert body["proceed_via"] == "acknowledged_below_threshold"
    assert body["requires_acknowledgment"] is False


def test_ack_requires_completed_below_threshold(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag6", "0012345006"
    )
    # Partial cannot be acknowledged.
    assert (
        _patch(
            client, csrf, encounter_id, {"diagnosis": {"answers": {"a_delusions": "yes"}}}, 1
        ).status_code
        == 200
    )
    denied = _ack(client, csrf, encounter_id, 2)
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "DIAGNOSIS_STATE_CONFLICT"

    # Satisfied criteria need no acknowledgment.
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    qualifying = _qualifying_answers()
    kept_ack = draft["draft_data"].get("diagnosis", {}).get("acknowledgment")
    kept_bypass = draft["draft_data"].get("diagnosis", {}).get("bypass")
    saved = _patch(
        client,
        csrf,
        encounter_id,
        {"diagnosis": {"answers": qualifying, "acknowledgment": kept_ack, "bypass": kept_bypass}},
        draft["revision"],
    )
    assert saved.status_code == 200, saved.text
    refused = _ack(client, csrf, encounter_id, saved.json()["revision"])
    assert refused.status_code == 409, refused.text


def test_relevant_edits_invalidate_ack_unrelated_keys_do_not(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag7", "0012345007"
    )
    answers = _symptom_only_answers()
    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )
    assert _ack(client, csrf, encounter_id, 2).status_code == 200

    # Unrelated top-level key preserves validity (revision moves, hash does not).
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["revision"] == 3
    diagnosis_section = draft["draft_data"]["diagnosis"]
    unrelated = {
        "diagnosis": diagnosis_section,
        "visit_note": "unrelated wizard page",
    }
    saved = _patch(client, csrf, encounter_id, unrelated, 3)
    assert saved.status_code == 200, saved.text
    still = _get_diagnosis(client, encounter_id).json()
    assert still["acknowledgment_valid"] is True
    assert still["can_proceed"] is True

    # Relevant edit (one answer flips) invalidates even though the stale record remains.
    changed = dict(answers)
    changed["b_functional_decline"] = "yes"
    stale_ack = still["acknowledgment"]
    edited = _patch(
        client,
        csrf,
        encounter_id,
        {"diagnosis": {"answers": changed, "acknowledgment": stale_ack}, "visit_note": "x"},
        still["revision"],
    )
    assert edited.status_code == 200, edited.text
    invalid = _get_diagnosis(client, encounter_id).json()
    assert invalid["acknowledgment_valid"] is False
    # The edited case now satisfies B as well but still misses C-F, so it stays
    # below threshold and once again requires acknowledgment.
    assert invalid["evaluation"]["findings"]["overall"] == "below_threshold"
    assert invalid["can_proceed"] is False
    assert invalid["requires_acknowledgment"] is True


def test_ack_stale_revision_and_idempotency(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag8", "0012345008"
    )
    answers = _symptom_only_answers()
    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )

    stale = _ack(client, csrf, encounter_id, 1)
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"

    first = _ack(client, csrf, encounter_id, 2, key="ack-key-1")
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 3
    replay = _ack(client, csrf, encounter_id, 2, key="ack-key-1")
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"] == 3
    conflict = _ack(client, csrf, encounter_id, 3, key="ack-key-1")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_ack_body_has_no_reason_field(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag9", "0012345009"
    )
    answers = _symptom_only_answers()
    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": answers}}, 1).status_code
        == 200
    )
    rejected = _ack(client, csrf, encounter_id, 2, body={"reason": "family insisted"})
    assert rejected.status_code == 422, rejected.text


# --- Slice 4: bypass without reason, attributed, survives resume ---


def test_bypass_succeeds_without_reason_and_survives_resume(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag10", "0012345010"
    )
    bypassed = _bypass(client, csrf, encounter_id, 1)
    assert bypassed.status_code == 200, bypassed.text
    body = bypassed.json()
    assert body["revision"] == 2
    assert body["answers"] == {}
    record = body["bypass"]
    assert record["status"] == "bypassed"
    assert record["actor_id"] == user["id"]
    assert record["revision"] == 1
    assert contracts.parse_utc(record["bypassed_at"])
    assert "reason" not in record
    assert body["bypass_valid"] is True
    assert body["can_proceed"] is True
    assert body["proceed_via"] == "bypass"

    # Resume after restart: a fresh client re-authenticates and sees the bypass.
    fresh = TestClient(app)
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_diag10", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh.get(f"/api/v1/encounters/{encounter_id}/diagnosis")
    assert resumed.status_code == 200, resumed.text
    resumed_body = resumed.json()
    assert resumed_body["bypass_valid"] is True
    assert resumed_body["bypass"] == record
    assert resumed_body["can_proceed"] is True
    # The shared autosave body carries the bypass through GET /encounters.
    draft = fresh.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["diagnosis"]["bypass"] == record
    assert draft["revision"] == 2


def test_bypass_rejects_reason_field_and_clears_answers(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag11", "0012345011"
    )
    partial = {"a_delusions": "yes"}
    assert (
        _patch(client, csrf, encounter_id, {"diagnosis": {"answers": partial}}, 1).status_code
        == 200
    )
    rejected = _bypass(client, csrf, encounter_id, 2, body={"reason": "no time"})
    assert rejected.status_code == 422, rejected.text
    cleared = _bypass(client, csrf, encounter_id, 2)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["answers"] == {}
    assert cleared.json()["bypass_valid"] is True


def test_bypass_stale_revision_and_idempotency(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag12", "0012345012"
    )
    stale = _bypass(client, csrf, encounter_id, 99)
    assert stale.status_code == 412, stale.text
    first = _bypass(client, csrf, encounter_id, 1, key="bypass-key-1")
    assert first.status_code == 200, first.text
    replay = _bypass(client, csrf, encounter_id, 1, key="bypass-key-1")
    assert replay.status_code == 200
    assert replay.json()["revision"] == first.json()["revision"]
    conflict = _bypass(client, csrf, encounter_id, 2, key="bypass-key-1")
    assert conflict.status_code == 409, conflict.text


# --- Privacy + auth contracts (S07 carried forward) ---


def test_diagnosis_author_only_and_csrf(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag13", "0012345013"
    )
    _make_physician(admin_client, "dr_stranger")
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_stranger", "pw123"
    )
    assert stranger.get(f"/api/v1/encounters/{encounter_id}/diagnosis").status_code == 403
    stranger_ack = stranger.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/acknowledgment",
        json={},
        headers={**_auth_headers(stranger_csrf), "If-Match": '"1"'},
    )
    assert stranger_ack.status_code == 403
    stranger_bypass = stranger.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/bypass",
        json={},
        headers={**_auth_headers(stranger_csrf), "If-Match": '"1"'},
    )
    assert stranger_bypass.status_code == 403

    # Administrator is not the author either.
    assert (
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/diagnosis").status_code
        == 403
    )
    # Missing CSRF on a mutation is 403 and changes nothing.
    no_csrf = client.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/bypass",
        json={},
        headers={"If-Match": '"1"'},
    )
    assert no_csrf.status_code == 403
    assert _get_diagnosis(client, encounter_id).json()["revision"] == 1
    # Anonymous reads are 401.
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/diagnosis").status_code == 401
    # Unknown encounter is 404, not 403.
    assert client.get(f"/api/v1/encounters/{uuid.uuid4()}/diagnosis").status_code == 404


def test_diagnosis_requires_if_match(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_diag14", "0012345014"
    )
    missing = client.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/bypass",
        json={},
        headers=_auth_headers(csrf),
    )
    assert missing.status_code == 422, missing.text
    wildcard = client.post(
        f"/api/v1/encounters/{encounter_id}/diagnosis/bypass",
        json={},
        headers={**_auth_headers(csrf), "If-Match": "*"},
    )
    assert wildcard.status_code == 422, wildcard.text
