"""Structured history + adverse effects (S12, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 module evaluators (no
frontend, no live provider/MCP):

1. History persists with provenance; unknown/not_assessed stay distinct from
   false (no); undeclared/excluded (dose/route/frequency/active-stopped/
   free-text) fail strict validation with 422.
2. Each of four effects accepts present/absent/not_assessed; present requires
   the corresponding complete reviewed questionnaire + reviewed severity
   (BARS global match, SAS complete, AIMS awaiting_source 422, Acute
   complete); absent/not_assessed carry null severity with no completion
   requirement; status change must explicitly clear obsolete severity.
3. Item responses/version/completeness/nullable results preserved; missing
   suppresses to null (never zero); urgent independent of completion;
   completeness only for present; phone update + reconciliation state;
   analysis-visible history vs notes separation via serializer/HTTP.
4. Author/revision rules (403/401/404, 412 stale, 422 If-Match, CSRF 403,
   idempotency replay/409).

History lives in ``draft_data["history"]``; effects in
``draft_data["effects"]``. Answers travel via the S07 PATCH autosave;
strict ``POST .../history`` and ``POST .../effects/{effect}/status``
stamp provenance and enforce the present/severity contract. Definitions
under ``content/history/`` are drafts awaiting owner review — tests assert
against them, never approve them.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.cases import effects as effects_service
from x_insight.cases import history as history_service
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
            connection.execute(
                text(
                    "TRUNCATE question_runs, generation_batches, "
                    "notes, encounters, patients CASCADE"
                )
            )
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


def _get_history(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/history")


def _post_history(
    client, csrf, encounter_id, values, revision, reconciliation=None, phone=None, key=None
):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    body: dict = {"values": values}
    if reconciliation is not None:
        body["reconciliation"] = reconciliation
    if phone is not None:
        body["phone_update"] = phone
    return client.post(f"/api/v1/encounters/{encounter_id}/history", json=body, headers=headers)


def _get_effects(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/effects")


def _post_effect(client, csrf, encounter_id, effect, status, severity, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/effects/{effect}/status",
        json={"status": status, "severity": severity},
        headers=headers,
    )


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


def _effects_draft(effect_key: str, answers: dict) -> dict:
    return {"effects": {effect_key: {"questionnaire": {"answers": dict(answers)}}}}


# Independent literals (worked from content/history/*.v1.json, never from code).
HISTORY_IDS = (
    "h_exposure_dopamine_blocker",
    "h_exposure_duration_3m",
    "h_exposure_1m_if_60plus",
    "h_movement_persistence_4w",
    "h_onset_timing",
    "h_trial_adequacy_prior",
    "h_prior_response",
    "h_monitoring_baseline",
    "h_alternative_cause_considered",
    "h_functional_impact",
    "h_falls_or_limitation",
    "h_medication_timeline_documented",
)

COMPLETE_HISTORY = {
    "h_exposure_dopamine_blocker": "no",
    "h_exposure_duration_3m": "no",
    "h_exposure_1m_if_60plus": "no",
    "h_movement_persistence_4w": "no",
    "h_onset_timing": "not_assessed",
    "h_trial_adequacy_prior": "not_assessed",
    "h_prior_response": "not_assessed",
    "h_monitoring_baseline": "no",
    "h_alternative_cause_considered": "unknown",
    "h_functional_impact": "no",
    "h_falls_or_limitation": "no",
    "h_medication_timeline_documented": "no",
}

BARS_COMPLETE = {
    "bars_objective": 2,
    "bars_awareness": 2,
    "bars_distress": 2,
    "bars_global": 3,
}

SAS_ALL_ONE = {
    "sas_gait": 1,
    "sas_arm_dropping": 1,
    "sas_shoulder_shaking": 1,
    "sas_elbow_rigidity": 1,
    "sas_wrist_rigidity": 1,
    "sas_leg_pendulousness": 1,
    "sas_head_dropping": 1,
    "sas_glabellar_tap": 1,
    "sas_tremor": 1,
    "sas_salivation": 1,
}

ACUTE_COMPLETE = {
    "addx_sustained_posture": "yes",
    "addx_medication_timeline": "yes",
    "addx_distribution_persistence": "yes",
    "addx_exclusions": "yes",
    "addx_urgent_airway": "no",
}


def _history_root() -> Path:
    return Path(__file__).resolve().parents[3] / "content" / "history"


# --- Slice 0: definitions stay awaiting_review (assert, never approve) ---


def test_history_definitions_stay_awaiting_review() -> None:
    root = _history_root()
    review = json.loads((root / "review.json").read_text())
    assert review["package"] == "history"
    assert review["version"] == "v1"
    assert review["status"] == "awaiting_review"
    assert review["decision"] == "pending"
    assert review["approval"] == "none"
    assert review["reviewer"] == "owner"
    expected = {
        "history.v1.json",
        "bars.v1.json",
        "sas.v1.json",
        "aims.v1.json",
        "acute-dystonia-dx-criteria.v1.json",
    }
    assert {entry["path"] for entry in review["files"]} == expected
    for name in expected:
        payload = json.loads((root / name).read_text())
        assert payload["version"] == "v1"
        assert payload["review_status"] == "awaiting_review"
        assert payload["approval"] == "none"
    history_payload = json.loads((root / "history.v1.json").read_text())
    assert len(history_payload["fields"]) == 12
    assert {field["id"] for field in history_payload["fields"]} == set(HISTORY_IDS)
    assert all(field["analysis_visible"] is True for field in history_payload["fields"])


# --- Slice 1: history persists with provenance; unknown distinct from false ---


def test_fresh_history_is_unanswered_with_12_missing(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist1", "0012348001"
    )
    direct = history_service.evaluate_history({})
    assert direct["status"] == "unanswered"
    assert direct["missing_item_ids"] == list(HISTORY_IDS)
    assert direct["item_errors"] == {}
    assert direct["definition_version"] == "v1"

    preview = _get_history(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["values"] == {}
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v1"
    assert body["revision"] == 1
    assert body["analysis_visible"] is True
    assert body["analysis_visible_label"] == "analysis_visible"
    assert preview.headers["etag"] == '"1"'


def test_history_strict_save_persists_values_with_server_provenance(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist2", "0012348002"
    )
    saved = _post_history(client, csrf, encounter_id, dict(COMPLETE_HISTORY), 1)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["values"] == COMPLETE_HISTORY
    assert body["evaluation"]["status"] == "complete"
    assert body["evaluation"]["missing_item_ids"] == []
    assert body["evaluation"]["item_errors"] == {}
    assert body["revision"] == 2
    assert saved.headers["etag"] == '"2"'
    # Server-stamped provenance per field: clinician_entry + author + timestamp.
    for field_id in COMPLETE_HISTORY:
        entry = body["provenance"][field_id]
        assert entry["source"] == "clinician_entry"
        assert entry["author_id"] == user["id"]
        assert isinstance(entry["recorded_at"], str) and entry["recorded_at"]

    reread = _get_history(client, encounter_id).json()
    assert reread["values"] == COMPLETE_HISTORY
    assert reread["evaluation"]["status"] == "complete"
    assert reread["revision"] == 2
    # The shared autosave body carries the same history section.
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["history"]["values"] == COMPLETE_HISTORY
    assert draft["revision"] == 2


def test_history_unknown_and_not_assessed_stay_distinct_from_false(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist3", "0012348003"
    )
    values = dict(COMPLETE_HISTORY)
    values["h_exposure_dopamine_blocker"] = "unknown"
    values["h_exposure_duration_3m"] = "not_assessed"
    values["h_movement_persistence_4w"] = "no"
    direct = history_service.evaluate_history(values)
    assert direct["status"] == "complete"

    saved = _post_history(client, csrf, encounter_id, values, 1)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["values"]["h_exposure_dopamine_blocker"] == "unknown"
    assert body["values"]["h_exposure_duration_3m"] == "not_assessed"
    assert body["values"]["h_movement_persistence_4w"] == "no"
    # None was coerced to false: explicit no stays no, unknown stays unknown.
    assert body["values"]["h_exposure_dopamine_blocker"] != "no"
    assert body["evaluation"]["status"] == "complete"


def test_history_undeclared_and_excluded_fields_fail_422(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist4", "0012348004"
    )
    bad_values = {"h_exposure_dopamine_blocker": "yes", "dose": "5mg"}
    rejected = _post_history(client, csrf, encounter_id, bad_values, 1)
    assert rejected.status_code == 422, rejected.text
    assert "dose" in rejected.json()["field_errors"]
    assert _get_history(client, encounter_id).json()["revision"] == 1

    for excluded in (
        "dose_unit",
        "route",
        "frequency",
        "active_stopped",
        "free_text_medication",
        "free_text_history",
    ):
        attempt = _post_history(
            client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "yes", excluded: "x"}, 1
        )
        assert attempt.status_code == 422, (excluded, attempt.text)
        assert excluded in attempt.json()["field_errors"], (excluded, attempt.text)

    undeclared = _post_history(
        client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "yes", "h_fake": "yes"}, 1
    )
    assert undeclared.status_code == 422, undeclared.text
    assert "h_fake" in undeclared.json()["field_errors"]
    # Nothing wrote: revision and values unchanged.
    assert _get_history(client, encounter_id).json()["values"] == {}


def test_history_invalid_values_rejected_server_side(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist5", "0012348005"
    )
    for field_id, bad in (
        ("h_exposure_dopamine_blocker", "yesno"),
        ("h_exposure_dopamine_blocker", 1),
        ("h_exposure_dopamine_blocker", True),
        ("h_exposure_dopamine_blocker", None),
        ("h_onset_timing", "yesterday"),
        ("h_trial_adequacy_prior", "yes"),
    ):
        attempt = _post_history(client, csrf, encounter_id, {field_id: bad}, 1)
        assert attempt.status_code == 422, (field_id, bad, attempt.text)
    assert _get_history(client, encounter_id).json()["revision"] == 1

    # Forged PATCH values surface as preview item_errors (never valid).
    forged = dict(COMPLETE_HISTORY)
    forged["dose"] = "5mg"
    assert _patch(client, csrf, encounter_id, {"history": {"values": forged}}, 1).status_code == 200
    preview = _get_history(client, encounter_id).json()
    assert "dose" in preview["evaluation"]["item_errors"]
    assert preview["evaluation"]["status"] == "partial"


# --- Slice 2: four effects, present contract, stale severity ---


def test_effects_fresh_all_unrecorded(admin_client, clean_registry, monkeypatch) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff1", "0012348101"
    )
    body = _get_effects(client, encounter_id).json()
    assert body["definition_versions"] == {
        "tardive_dyskinesia": "v1",
        "akathisia": "v1",
        "parkinsonism": "v1",
        "acute_dystonia": "v1",
    }
    assert body["revision"] == 1
    for key in ("tardive_dyskinesia", "akathisia", "parkinsonism", "acute_dystonia"):
        assert body["effects"][key]["status"] is None
        assert body["effects"][key]["severity"] is None
    assert body["effects"]["akathisia"]["questionnaire"]["evaluation"]["status"] == "unanswered"
    assert body["effects"]["parkinsonism"]["questionnaire"]["evaluation"]["status"] == "unanswered"
    assert (
        body["effects"]["acute_dystonia"]["questionnaire"]["evaluation"]["status"] == "unanswered"
    )
    assert (
        body["effects"]["tardive_dyskinesia"]["questionnaire"]["evaluation"]["status"]
        == "awaiting_source"
    )
    assert body["effects"]["acute_dystonia"]["urgent"] is None


def test_effects_absent_and_not_assessed_need_null_severity_no_completion(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff2", "0012348102"
    )
    revision = 1
    for effect, status in (
        ("akathisia", "absent"),
        ("parkinsonism", "not_assessed"),
        ("tardive_dyskinesia", "absent"),
        ("acute_dystonia", "not_assessed"),
    ):
        response = _post_effect(client, csrf, encounter_id, effect, status, None, revision)
        assert response.status_code == 200, (effect, status, response.text)
        revision = response.json()["revision"]
        entry = response.json()["effects"][effect]
        assert entry["status"] == status
        assert entry["severity"] is None
    # Empty questionnaires imposed no completion requirement.
    body = _get_effects(client, encounter_id).json()
    assert body["effects"]["akathisia"]["questionnaire"]["evaluation"]["status"] == "unanswered"
    assert (
        body["effects"]["tardive_dyskinesia"]["questionnaire"]["evaluation"]["status"]
        == "awaiting_source"
    )


def test_effects_stale_severity_must_be_cleared_explicitly(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff3", "0012348103"
    )
    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("akathisia", BARS_COMPLETE), 1
        ).status_code
        == 200
    )
    present = _post_effect(client, csrf, encounter_id, "akathisia", "present", 3, 2)
    assert present.status_code == 200, present.text
    revision = present.json()["revision"]

    stale = _post_effect(client, csrf, encounter_id, "akathisia", "absent", 3, revision)
    assert stale.status_code == 422, stale.text
    assert "severity" in stale.json()["field_errors"]
    assert _get_effects(client, encounter_id).json()["revision"] == revision

    cleared = _post_effect(client, csrf, encounter_id, "akathisia", "absent", None, revision)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["effects"]["akathisia"] == {
        "status": "absent",
        "severity": None,
        "questionnaire": cleared.json()["effects"]["akathisia"]["questionnaire"],
        "urgent": None,
    }
    # Saved answers are kept verbatim; only the severity was cleared.
    assert cleared.json()["effects"]["akathisia"]["questionnaire"]["answers"] == BARS_COMPLETE


def test_akathisia_present_requires_complete_bars_and_matching_global(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff4", "0012348104"
    )
    # Incomplete BARS cannot support present.
    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("akathisia", {"bars_global": 3}), 1
        ).status_code
        == 200
    )
    incomplete = _post_effect(client, csrf, encounter_id, "akathisia", "present", 3, 2)
    assert incomplete.status_code == 422, incomplete.text
    assert "questionnaire" in incomplete.json()["field_errors"]

    # Complete BARS with mismatched severity is rejected.
    partial = dict(BARS_COMPLETE)
    assert (
        _patch(client, csrf, encounter_id, _effects_draft("akathisia", partial), 2).status_code
        == 200
    )
    mismatch = _post_effect(client, csrf, encounter_id, "akathisia", "present", 2, 3)
    assert mismatch.status_code == 422, mismatch.text

    # Global below the present threshold (0/1) is rejected even when matching.
    low = {"bars_objective": 0, "bars_awareness": 0, "bars_distress": 0, "bars_global": 0}
    assert (
        _patch(client, csrf, encounter_id, _effects_draft("akathisia", low), 3).status_code == 200
    )
    below = _post_effect(client, csrf, encounter_id, "akathisia", "present", 0, 4)
    assert below.status_code == 422, below.text

    # Matching global >= 2 with complete BARS succeeds.
    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("akathisia", BARS_COMPLETE), 4
        ).status_code
        == 200
    )
    done = _post_effect(client, csrf, encounter_id, "akathisia", "present", 3, 5)
    assert done.status_code == 200, done.text
    entry = done.json()["effects"]["akathisia"]
    assert entry["status"] == "present"
    assert entry["severity"] == 3
    assert entry["questionnaire"]["evaluation"]["status"] == "complete"
    assert entry["questionnaire"]["evaluation"]["scores"]["global"] == 3


def test_parkinsonism_present_requires_complete_sas_and_reviewed_severity(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff5", "0012348105"
    )
    partial = dict(SAS_ALL_ONE)
    del partial["sas_salivation"]
    assert (
        _patch(client, csrf, encounter_id, _effects_draft("parkinsonism", partial), 1).status_code
        == 200
    )
    incomplete = _post_effect(client, csrf, encounter_id, "parkinsonism", "present", "mild", 2)
    assert incomplete.status_code == 422, incomplete.text

    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("parkinsonism", SAS_ALL_ONE), 2
        ).status_code
        == 200
    )
    bad_label = _post_effect(client, csrf, encounter_id, "parkinsonism", "present", "global-3", 3)
    assert bad_label.status_code == 422, bad_label.text

    done = _post_effect(client, csrf, encounter_id, "parkinsonism", "present", "mild", 3)
    assert done.status_code == 200, done.text
    entry = done.json()["effects"]["parkinsonism"]
    assert entry["questionnaire"]["evaluation"]["scores"] == {"raw_total": 10, "mean": 1.0}


def test_tardive_present_always_awaiting_source_422(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff6", "0012348106"
    )
    direct = effects_service.evaluate_aims({})
    assert direct["status"] == "awaiting_source"
    assert direct["scores"] is None

    present = _post_effect(client, csrf, encounter_id, "tardive_dyskinesia", "present", "mild", 1)
    assert present.status_code == 422, present.text
    assert present.json()["code"] == "AWAITING_SOURCE"
    assert _get_effects(client, encounter_id).json()["revision"] == 1

    absent = _post_effect(client, csrf, encounter_id, "tardive_dyskinesia", "absent", None, 1)
    assert absent.status_code == 200, absent.text


def test_acute_present_requires_all_five_criteria(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff7", "0012348107"
    )
    partial = dict(ACUTE_COMPLETE)
    del partial["addx_urgent_airway"]
    assert (
        _patch(client, csrf, encounter_id, _effects_draft("acute_dystonia", partial), 1).status_code
        == 200
    )
    incomplete = _post_effect(
        client, csrf, encounter_id, "acute_dystonia", "present", "moderate", 2
    )
    assert incomplete.status_code == 422, incomplete.text

    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("acute_dystonia", ACUTE_COMPLETE), 2
        ).status_code
        == 200
    )
    done = _post_effect(client, csrf, encounter_id, "acute_dystonia", "present", "moderate", 3)
    assert done.status_code == 200, done.text
    entry = done.json()["effects"]["acute_dystonia"]
    assert entry["questionnaire"]["evaluation"]["status"] == "complete"
    assert entry["questionnaire"]["evaluation"]["scores"] is None
    assert entry["urgent"] is False


def test_effects_unknown_effect_or_status_422(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff8", "0012348108"
    )
    unknown_effect = _post_effect(client, csrf, encounter_id, "headache", "present", "mild", 1)
    assert unknown_effect.status_code == 422, unknown_effect.text
    unknown_status = _post_effect(client, csrf, encounter_id, "akathisia", "maybe", None, 1)
    assert unknown_status.status_code == 422, unknown_status.text
    assert _get_effects(client, encounter_id).json()["revision"] == 1


# --- Slice 3: evaluators, preservation, urgent, phone/reconciliation, notes ---


def test_questionnaire_evaluators_direct_match_route_preview(
    admin_client, clean_registry, monkeypatch
) -> None:
    # BARS all-zero: complete, global 0 Absent, threshold not met.
    bars_zero = effects_service.evaluate_bars(
        {"bars_objective": 0, "bars_awareness": 0, "bars_distress": 0, "bars_global": 0}
    )
    assert bars_zero["status"] == "complete"
    assert bars_zero["scores"] == {
        "objective": 0,
        "awareness": 0,
        "distress": 0,
        "global": 0,
        "component_total": 0,
    }
    assert bars_zero["findings"]["global_label"] == "Absent"
    assert bars_zero["findings"]["threshold_met"] is False

    # BARS moderate present fixture from bars.v1.json.
    bars_mod = effects_service.evaluate_bars(dict(BARS_COMPLETE))
    assert bars_mod["status"] == "complete"
    assert bars_mod["scores"]["global"] == 3
    assert bars_mod["scores"]["component_total"] == 6
    assert bars_mod["findings"]["threshold_met"] is True

    # Pseudoakathisia note: movements without subjective restlessness.
    pseudo = effects_service.evaluate_bars(
        {"bars_objective": 2, "bars_awareness": 0, "bars_distress": 0, "bars_global": 0}
    )
    assert pseudo["status"] == "complete"
    assert "pseudoakathisia_note" in pseudo["findings"]

    # SAS all-one: raw 10, mean 1.0, no severity bands.
    sas_one = effects_service.evaluate_sas(dict(SAS_ALL_ONE))
    assert sas_one["status"] == "complete"
    assert sas_one["scores"] == {"raw_total": 10, "mean": 1.0}
    assert sas_one["findings"]["no_severity_bands"] is True

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eval1", "0012348201"
    )
    assert (
        _patch(
            client,
            csrf,
            encounter_id,
            {
                "effects": {
                    "akathisia": {"questionnaire": {"answers": dict(BARS_COMPLETE)}},
                    "parkinsonism": {"questionnaire": {"answers": dict(SAS_ALL_ONE)}},
                }
            },
            1,
        ).status_code
        == 200
    )
    body = _get_effects(client, encounter_id).json()
    assert body["effects"]["akathisia"]["questionnaire"]["evaluation"] == bars_mod
    assert body["effects"]["parkinsonism"]["questionnaire"]["evaluation"] == sas_one
    for key in ("akathisia", "parkinsonism", "acute_dystonia", "tardive_dyskinesia"):
        assert body["effects"][key]["questionnaire"]["definition_version"] == "v1"


def test_missing_items_suppress_results_to_null_never_zero(
    admin_client, clean_registry, monkeypatch
) -> None:
    bars_partial = effects_service.evaluate_bars(
        {"bars_objective": 2, "bars_awareness": 2, "bars_distress": 2}
    )
    assert bars_partial["status"] == "partial"
    assert bars_partial["missing_item_ids"] == ["bars_global"]
    assert bars_partial["scores"] == {
        "objective": None,
        "awareness": None,
        "distress": None,
        "global": None,
        "component_total": None,
    }

    sas_partial_answers = dict(SAS_ALL_ONE)
    del sas_partial_answers["sas_salivation"]
    sas_partial = effects_service.evaluate_sas(sas_partial_answers)
    assert sas_partial["status"] == "partial"
    assert sas_partial["missing_item_ids"] == ["sas_salivation"]
    assert sas_partial["scores"] == {"raw_total": None, "mean": None}

    acute_partial = effects_service.evaluate_acute(
        {
            "addx_sustained_posture": "yes",
            "addx_medication_timeline": "yes",
            "addx_distribution_persistence": "yes",
            "addx_exclusions": "yes",
        }
    )
    assert acute_partial["status"] == "partial"
    assert acute_partial["missing_item_ids"] == ["addx_urgent_airway"]
    assert acute_partial["scores"] is None

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eval2", "0012348202"
    )
    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("parkinsonism", sas_partial_answers), 1
        ).status_code
        == 200
    )
    body = _get_effects(client, encounter_id).json()
    assert body["effects"]["parkinsonism"]["questionnaire"]["evaluation"] == sas_partial
    assert (
        body["effects"]["parkinsonism"]["questionnaire"]["evaluation"]["scores"]["raw_total"]
        is None
    )


def test_urgent_airway_independent_of_completion(admin_client, clean_registry, monkeypatch) -> None:
    # A lone airway yes already routes urgently while the checklist is partial.
    lone = effects_service.evaluate_acute({"addx_urgent_airway": "yes"})
    assert lone["status"] == "partial"
    assert lone["findings"]["urgent"] is True

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eval3", "0012348203"
    )
    assert (
        _patch(
            client,
            csrf,
            encounter_id,
            _effects_draft("acute_dystonia", {"addx_urgent_airway": "yes"}),
            1,
        ).status_code
        == 200
    )
    body = _get_effects(client, encounter_id).json()
    assert body["effects"]["acute_dystonia"]["questionnaire"]["evaluation"]["status"] == "partial"
    assert body["effects"]["acute_dystonia"]["urgent"] is True

    # Completing the checklist keeps the flag; answering no clears it.
    assert (
        _patch(
            client, csrf, encounter_id, _effects_draft("acute_dystonia", ACUTE_COMPLETE), 2
        ).status_code
        == 200
    )
    assert _get_effects(client, encounter_id).json()["effects"]["acute_dystonia"]["urgent"] is False


def test_phone_update_and_reconciliation_state(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist6", "0012348301"
    )
    saved = _post_history(
        client,
        csrf,
        encounter_id,
        {"h_exposure_dopamine_blocker": "yes"},
        1,
        reconciliation={"status": "pending", "baseline_encounter_id": str(uuid.uuid4())},
        phone="+43 699 999",
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["reconciliation"]["status"] == "pending"
    assert body["phone_update"] == "+43 699 999"

    reconciled = _post_history(
        client,
        csrf,
        encounter_id,
        {"h_exposure_dopamine_blocker": "yes"},
        body["revision"],
        reconciliation={"status": "reconciled"},
        phone="+43 699 999",
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["reconciliation"]["status"] == "reconciled"

    for bad_recon in (
        {"status": "later"},
        {"status": "pending", "baseline_encounter_id": 5},
        "pending",
    ):
        attempt = _post_history(
            client,
            csrf,
            encounter_id,
            {"h_exposure_dopamine_blocker": "yes"},
            reconciled.json()["revision"],
            reconciliation=bad_recon,
        )
        assert attempt.status_code == 422, (bad_recon, attempt.text)

    bad_phone = client.post(
        f"/api/v1/encounters/{encounter_id}/history",
        json={"values": {"h_exposure_dopamine_blocker": "yes"}, "phone_update": 5},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(reconciled.json()["revision"]),
        },
    )
    assert bad_phone.status_code == 422, bad_phone.text


def test_history_notes_separation_via_serializer_and_http(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist7", "0012348302"
    )
    # Unrelated draft keys (working note, assessments, page-note-like text)
    # ride the same opaque autosave body but never influence history.
    noise = {
        "working_note": "free-text note that must never become history",
        "panss": {"answers": {"P1": 1}},
        "notes": [{"page": "history", "text": "page note text"}],
        "history": {"values": {"h_exposure_dopamine_blocker": "unknown"}},
    }
    assert _patch(client, csrf, encounter_id, noise, 1).status_code == 200
    after = _get_history(client, encounter_id).json()
    assert after["values"] == {"h_exposure_dopamine_blocker": "unknown"}
    assert after["evaluation"]["status"] == "partial"
    assert after["evaluation"]["missing_item_ids"] == [
        fid for fid in HISTORY_IDS if fid != "h_exposure_dopamine_blocker"
    ]
    # Notes text never leaks into the history serializer.
    blob = contracts.canonical_json(after).decode()
    assert "free-text note that must never become history" not in blob
    assert "page note text" not in blob
    assert after["analysis_visible"] is True
    assert "notes" not in after
    # Phone update is the only free-text companion, kept separate from values.
    assert after["phone_update"] is None


# --- Slice 4: author/revision rules ---


def test_history_effects_author_only_and_auth(admin_client, clean_registry, monkeypatch) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_auth1", "0012348401"
    )
    _make_physician(admin_client, "dr_auth_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_auth_stranger", "pw123")
    for denied in (
        stranger.get(f"/api/v1/encounters/{encounter_id}/history"),
        stranger.get(f"/api/v1/encounters/{encounter_id}/effects"),
    ):
        assert denied.status_code == 403, denied.text
    assert (
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/history").status_code == 403
    )
    assert (
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/effects").status_code == 403
    )
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/history").status_code == 401
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/effects").status_code == 401
    missing = uuid.uuid4()
    assert client.get(f"/api/v1/encounters/{missing}/history").status_code == 404
    assert client.get(f"/api/v1/encounters/{missing}/effects").status_code == 404


def test_history_effects_stale_revision_412_and_if_match_422(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_rev1", "0012348402"
    )
    stale_history = _post_history(
        client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "yes"}, 99
    )
    assert stale_history.status_code == 412, stale_history.text
    assert stale_history.json()["code"] == "STALE_REVISION"
    stale_effect = _post_effect(client, csrf, encounter_id, "akathisia", "absent", None, 99)
    assert stale_effect.status_code == 412, stale_effect.text

    for raw in (None, "*", "not-a-revision"):
        headers = dict(_auth_headers(csrf))
        if raw is not None:
            headers["If-Match"] = raw
        denied_history = client.post(
            f"/api/v1/encounters/{encounter_id}/history",
            json={"values": {"h_exposure_dopamine_blocker": "yes"}},
            headers=headers,
        )
        assert denied_history.status_code == 422, (raw, denied_history.text)
        denied_effect = client.post(
            f"/api/v1/encounters/{encounter_id}/effects/akathisia/status",
            json={"status": "absent", "severity": None},
            headers=headers,
        )
        assert denied_effect.status_code == 422, (raw, denied_effect.text)
    assert _get_history(client, encounter_id).json()["revision"] == 1


def test_history_effects_csrf_403(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_csrf1", "0012348403"
    )
    no_csrf_history = client.post(
        f"/api/v1/encounters/{encounter_id}/history",
        json={"values": {"h_exposure_dopamine_blocker": "yes"}},
        headers={"If-Match": contracts.format_etag(1)},
    )
    assert no_csrf_history.status_code == 403, no_csrf_history.text
    wrong_csrf = client.post(
        f"/api/v1/encounters/{encounter_id}/history",
        json={"values": {"h_exposure_dopamine_blocker": "yes"}},
        headers={**{"X-CSRF-Token": "wrong"}, "If-Match": contracts.format_etag(1)},
    )
    assert wrong_csrf.status_code == 403, wrong_csrf.text
    no_csrf_effect = client.post(
        f"/api/v1/encounters/{encounter_id}/effects/akathisia/status",
        json={"status": "absent", "severity": None},
        headers={"If-Match": contracts.format_etag(1)},
    )
    assert no_csrf_effect.status_code == 403, no_csrf_effect.text
    assert _get_history(client, encounter_id).json()["revision"] == 1
    assert _get_effects(client, encounter_id).json()["revision"] == 1
    assert csrf  # CSRF token was issued; silence unused-variable lint without touching auth.


def test_history_effects_idempotency_replay_and_conflict(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_idem1", "0012348404"
    )
    first = _post_history(
        client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "yes"}, 1, key="hist-key-001"
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 2

    replay = _post_history(
        client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "yes"}, 1, key="hist-key-001"
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"] == 2

    conflict = _post_history(
        client, csrf, encounter_id, {"h_exposure_dopamine_blocker": "no"}, 2, key="hist-key-001"
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _get_history(client, encounter_id).json()["revision"] == 2

    first_effect = _post_effect(
        client, csrf, encounter_id, "akathisia", "absent", None, 2, key="eff-key-001"
    )
    assert first_effect.status_code == 200, first_effect.text
    replay_effect = _post_effect(
        client, csrf, encounter_id, "akathisia", "absent", None, 2, key="eff-key-001"
    )
    assert replay_effect.status_code == 200, replay_effect.text
    assert replay_effect.json()["revision"] == first_effect.json()["revision"]
    conflict_effect = _post_effect(
        client,
        csrf,
        encounter_id,
        "akathisia",
        "not_assessed",
        None,
        first_effect.json()["revision"],
        key="eff-key-001",
    )
    assert conflict_effect.status_code == 409, conflict_effect.text
