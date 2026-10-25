"""Structured history + adverse effects (S12, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 module evaluators (no
frontend, no live provider/MCP):

1. Current History uses Unknown defaults and conditional questions with
   provenance; retired inputs and invalid choices are rejected. Undeclared/excluded
   (dose/route/frequency/active-stopped/
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
stamp provenance and enforce the present/severity contract. Effect capture
(GET, status POST, answer PATCH) is follow-up only; registration is 422
and does not write. Definitions under ``content/history/`` are drafts
awaiting owner review — tests assert against them, never approve them.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.cases import effects as effects_service
from x_insight.cases import history as history_service
from x_insight.cases import tables as cases_tables
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


def _discard_draft(client, csrf, encounter_id, revision: int):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _setup_draft(
    admin_client, clean_registry, monkeypatch, username, identifier, *, follow_up: bool = False
):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    encounter_id = created["draft"]["id"]
    if not follow_up:
        return client, csrf, user, encounter_id
    # POST /patients occupies the one open slot; discard it, then open follow-up.
    discarded = _discard_draft(client, csrf, encounter_id, 1)
    assert discarded.status_code == 200, discarded.text
    started = client.post(
        f"/api/v1/patients/{created['patient']['id']}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert started.status_code == 201, started.text
    return client, csrf, user, started.json()["encounter"]["id"]


def _seed_draft_data(engine, encounter_id: str, draft_data: dict) -> None:
    """Plant a section the registration API will no longer write."""
    with engine.begin() as connection:
        connection.execute(
            update(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == uuid.UUID(str(encounter_id)))
            .values(draft_data=draft_data)
        )


def _effects_draft(effect_key: str, answers: dict) -> dict:
    return {"effects": {effect_key: {"questionnaire": {"answers": dict(answers)}}}}


EFFECTS_FOLLOW_UP_ONLY = "Adverse effects are recorded on follow-up encounters only."


def _assert_effects_follow_up_only(response, field: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["message"] == EFFECTS_FOLLOW_UP_ONLY
    assert body["field_errors"] == {field: [EFFECTS_FOLLOW_UP_ONLY]}


# Independent literals (worked from content/history/*.v1.json, never from code).
HISTORY_IDS = (
    "h_exposure_dopamine_blocker",
    "h_movement_persistence_4w",
    "h_onset_timing",
    "h_trial_adequacy_prior",
    "h_prior_response",
    "h_alternative_cause_considered",
    "h_falls_or_limitation",
)

PHYSICAL_PATH = "candidate/history/pharmacotherapy.v2/h_physical_vulnerability"
SUBSTANCE_PATH = "candidate/history/pharmacotherapy.v2/h_substance_contribution"
TREATMENT_PATH = "candidate/history/pharmacotherapy.v2/h_antipsychotic_treatment_context"
REVISED_DEFAULTS = {
    **{
        f"candidate/history/lai_indication_choice.v1/{field}": "unknown"
        for field in (
            "h_lai_preference",
            "h_adherence_history",
            "h_discussion_context",
            "h_delivery_barriers",
        )
    },
    "candidate/history/aggression_clozapine.v1/h_substantial_aggression_risk": "unknown",
}
FRESH_HISTORY = {
    **REVISED_DEFAULTS,
    "h_exposure_dopamine_blocker": "unknown",
    "h_movement_persistence_4w": "unknown",
    PHYSICAL_PATH: "unknown",
    SUBSTANCE_PATH: "unknown",
}

COMPLETE_HISTORY = {
    **REVISED_DEFAULTS,
    PHYSICAL_PATH: "unknown",
    SUBSTANCE_PATH: "unknown",
    "h_exposure_dopamine_blocker": "no",
    "h_movement_persistence_4w": "no",
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
        assert payload["version"] == ("v2" if name == "history.v1.json" else "v1")
        assert payload["review_status"] == "awaiting_review"
        assert payload["approval"] == "none"
    history_payload = json.loads((root / "history.v1.json").read_text())
    assert len(history_payload["fields"]) == 7
    assert {field["id"] for field in history_payload["fields"]} == set(HISTORY_IDS)
    assert all(field["analysis_visible"] is True for field in history_payload["fields"])


# --- Slice 1: history persists with provenance; unknown distinct from false ---


def test_fresh_history_defaults_unknown_without_hidden_questions(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist1", "0012348001"
    )
    direct = history_service.evaluate_history({})
    assert direct["status"] == "complete"
    assert direct["missing_item_ids"] == []
    assert direct["item_errors"] == {}
    assert direct["definition_version"] == "v2"

    preview = _get_history(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["values"] == FRESH_HISTORY
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v2"
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


def test_history_unknown_stays_distinct_from_explicit_no(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_hist3", "0012348003"
    )
    values = dict(COMPLETE_HISTORY)
    values["h_exposure_dopamine_blocker"] = "unknown"
    values["h_movement_persistence_4w"] = "no"
    direct = history_service.evaluate_history(values)
    assert direct["status"] == "complete"

    saved = _post_history(client, csrf, encounter_id, values, 1)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["values"]["h_exposure_dopamine_blocker"] == "unknown"
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
    assert _get_history(client, encounter_id).json()["values"] == FRESH_HISTORY


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
        admin_client, clean_registry, monkeypatch, "dr_eff1", "0012348101", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff2", "0012348102", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff3", "0012348103", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff4", "0012348104", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff5", "0012348105", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff6", "0012348106", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff7", "0012348107", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eff8", "0012348108", follow_up=True
    )
    unknown_effect = _post_effect(client, csrf, encounter_id, "headache", "present", "mild", 1)
    assert unknown_effect.status_code == 422, unknown_effect.text
    unknown_status = _post_effect(client, csrf, encounter_id, "akathisia", "maybe", None, 1)
    assert unknown_status.status_code == 422, unknown_status.text
    assert _get_effects(client, encounter_id).json()["revision"] == 1


# --- Registration cannot capture effects (422, no write) ---


def test_registration_refuses_effects_read_and_status(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff_reg", "0012348501"
    )
    _assert_effects_follow_up_only(_get_effects(client, encounter_id), "kind")
    refused = _post_effect(client, csrf, encounter_id, "akathisia", "absent", None, 1)
    _assert_effects_follow_up_only(refused, "kind")
    draft = client.get(f"/api/v1/encounters/{encounter_id}")
    assert draft.status_code == 200, draft.text
    assert draft.json()["revision"] == 1
    assert draft.json()["draft_data"] == {}


def test_registration_patch_effects_refuses_change_and_keeps_equal(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_eff_patch", "0012348502"
    )
    added = _patch(
        client,
        csrf,
        encounter_id,
        {"working_note": "nope", "effects": {"akathisia": {"status": "absent"}}},
        1,
    )
    _assert_effects_follow_up_only(added, "draft_data.effects")
    fresh = client.get(f"/api/v1/encounters/{encounter_id}")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["revision"] == 1
    assert fresh.json()["draft_data"] == {}

    stored_effects = {
        "akathisia": {"status": "absent", "marker": "legacy"},
        "kept": "yes",
    }
    seeded = {"effects": stored_effects, "drop_me": True}
    _seed_draft_data(clean_registry, encounter_id, seeded)
    planted = client.get(f"/api/v1/encounters/{encounter_id}")
    assert planted.json()["draft_data"] == seeded

    changed = _patch(
        client,
        csrf,
        encounter_id,
        {
            "working_note": "nope",
            "effects": {"kept": "yes", "akathisia": {"status": "present", "marker": "legacy"}},
        },
        1,
    )
    _assert_effects_follow_up_only(changed, "draft_data.effects")
    unchanged = client.get(f"/api/v1/encounters/{encounter_id}")
    assert unchanged.json()["revision"] == 1
    assert unchanged.json()["draft_data"] == seeded

    omitted = _patch(client, csrf, encounter_id, {"working_note": "saved"}, 1)
    assert omitted.status_code == 200, omitted.text
    kept = client.get(f"/api/v1/encounters/{encounter_id}")
    assert kept.json()["revision"] == 2
    assert kept.json()["draft_data"] == {"working_note": "saved", "effects": stored_effects}

    # Canonical JSON ignores key order: a reordered equal section still saves.
    equal_effects = {"kept": "yes", "akathisia": {"marker": "legacy", "status": "absent"}}
    round_trip = _patch(
        client,
        csrf,
        encounter_id,
        {"working_note": "round-trip", "effects": equal_effects},
        2,
    )
    assert round_trip.status_code == 200, round_trip.text
    saved = client.get(f"/api/v1/encounters/{encounter_id}")
    assert saved.json()["revision"] == 3
    assert saved.json()["draft_data"] == {
        "working_note": "round-trip",
        "effects": stored_effects,
    }


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
        admin_client, clean_registry, monkeypatch, "dr_eval1", "0012348201", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eval2", "0012348202", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_eval3", "0012348203", follow_up=True
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
        "history": {"values": FRESH_HISTORY},
    }
    assert _patch(client, csrf, encounter_id, noise, 1).status_code == 200
    after = _get_history(client, encounter_id).json()
    assert after["values"] == FRESH_HISTORY
    assert after["evaluation"]["status"] == "complete"
    assert after["evaluation"]["missing_item_ids"] == []
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
        admin_client, clean_registry, monkeypatch, "dr_csrf1", "0012348403", follow_up=True
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
        admin_client, clean_registry, monkeypatch, "dr_idem1", "0012348404", follow_up=True
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


@pytest.mark.parametrize(
    ("exposure", "adequacy", "count"),
    [
        ("no", "inadequate", 9),
        ("unknown", "inadequate", 9),
        ("yes", "adequate", 13),
        ("yes", "unknown", 13),
        ("yes", "inadequate", 14),
    ],
)
def test_conditional_questions_roundtrip(
    admin_client, clean_registry, monkeypatch, exposure, adequacy, count
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_conditional", "0012348601"
    )
    values = {
        **COMPLETE_HISTORY,
        "h_exposure_dopamine_blocker": exposure,
        "h_onset_timing": "other",
        "h_trial_adequacy_prior": adequacy,
        "h_prior_response": "response",
        **({TREATMENT_PATH: "unknown"} if exposure == "yes" else {}),
    }
    saved = _post_history(client, csrf, eid, values, 1)
    assert saved.status_code == 200, saved.text
    state = saved.json()
    assert len(state["values"]) == count
    assert state["evaluation"]["status"] == "complete"
    assert ("h_onset_timing" in state["values"]) == (exposure == "yes")
    assert ("h_trial_adequacy_prior" in state["values"]) == (exposure == "yes")
    assert ("h_prior_response" in state["values"]) == (
        exposure == "yes" and adequacy == "inadequate"
    )
    assert set(state["provenance"]) == set(state["values"]) - {
        "candidate/history/lai_indication_choice.v1/h_prior_nms"
    }
    assert _get_history(client, eid).json()["values"] == state["values"]
    draft = client.get(f"/api/v1/encounters/{eid}").json()
    patched = _patch(client, csrf, eid, draft["draft_data"], 2)
    assert patched.status_code == 200, patched.text
    assert _get_history(client, eid).json()["values"] == state["values"]


def test_parent_transitions_clear_children_and_default_without_provenance(
    admin_client, clean_registry, monkeypatch
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_defaults", "0012348602"
    )
    saved = _post_history(
        client, csrf, eid, {**COMPLETE_HISTORY, "h_exposure_dopamine_blocker": "yes"}, 1
    )
    assert saved.status_code == 200, saved.text
    state = saved.json()
    assert state["values"]["h_onset_timing"] == "unknown"
    assert state["values"]["h_trial_adequacy_prior"] == "unknown"
    assert "h_onset_timing" not in state["provenance"]
    assert "h_trial_adequacy_prior" not in state["provenance"]
    for exposure, adequacy in [
        ("yes", "inadequate"),
        ("yes", "adequate"),
        ("yes", "unknown"),
        ("no", "inadequate"),
        ("unknown", "inadequate"),
        ("yes", "inadequate"),
    ]:
        values = {
            **state["values"],
            "h_exposure_dopamine_blocker": exposure,
            "h_trial_adequacy_prior": adequacy,
        }
        changed = _post_history(client, csrf, eid, values, state["revision"])
        assert changed.status_code == 200, changed.text
        state = changed.json()
        active = exposure == "yes" and adequacy == "inadequate"
        assert ("h_prior_response" in state["values"]) == active
        assert "h_prior_response" not in state["provenance"]
        if active:
            assert state["values"]["h_prior_response"] == "unknown"
        if exposure != "yes":
            assert not {"h_onset_timing", "h_trial_adequacy_prior", "h_prior_response"} & set(
                state["values"]
            )
            assert not {"h_onset_timing", "h_trial_adequacy_prior", "h_prior_response"} & set(
                state["provenance"]
            )


@pytest.mark.parametrize(
    "field_id",
    [
        "h_exposure_duration_3m",
        "h_exposure_1m_if_60plus",
        "h_medication_timeline_documented",
        "h_monitoring_baseline",
        "h_functional_impact",
        *[
            "candidate/history/pharmacotherapy.v2/" + name
            for name in (
                "h_prior_treatment_difficulty",
                "h_preference_barrier",
                "h_exposure_modifier",
                "h_trial_duration_review",
                "h_urgent_safety_concern",
                "h_monitoring_gap",
            )
        ],
    ],
)
def test_removed_history_fields_rejected_on_new_writes(
    admin_client, clean_registry, monkeypatch, field_id
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_removed", "0012348603"
    )
    for response in [
        _post_history(client, csrf, eid, {field_id: "yes"}, 1),
        _patch(client, csrf, eid, {"history": {"values": {field_id: "yes"}}}, 1),
    ]:
        assert response.status_code == 422, response.text
        assert field_id in response.json()["field_errors"]
    assert _get_history(client, eid).json()["revision"] == 1


@pytest.mark.parametrize(
    "field_id",
    [
        "h_exposure_dopamine_blocker",
        "h_onset_timing",
        "h_trial_adequacy_prior",
        "h_prior_response",
        "h_movement_persistence_4w",
        "h_alternative_cause_considered",
        "h_falls_or_limitation",
        PHYSICAL_PATH,
        SUBSTANCE_PATH,
        TREATMENT_PATH,
    ],
)
@pytest.mark.parametrize("bad", ["not_assessed", None])
def test_invalid_revised_answers_rejected_on_both_paths(
    admin_client, clean_registry, monkeypatch, field_id, bad
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_badchoices", "0012348604"
    )
    values = {
        "h_exposure_dopamine_blocker": "yes",
        "h_trial_adequacy_prior": "inadequate",
        field_id: bad,
    }
    for response in [
        _post_history(client, csrf, eid, values, 1),
        _patch(client, csrf, eid, {"history": {"values": values}}, 1),
    ]:
        assert response.status_code == 422, response.text
        assert field_id in response.json()["field_errors"]
    assert _get_history(client, eid).json()["revision"] == 1


def test_legacy_read_autosave_cleanup_preserves_frozen_baseline(
    admin_client, clean_registry, monkeypatch
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_legacy", "0012348605"
    )
    legacy = {
        **COMPLETE_HISTORY,
        "h_exposure_dopamine_blocker": "not_assessed",
        "h_monitoring_baseline": "yes",
        "h_functional_impact": "yes",
        PHYSICAL_PATH: "Present",
        SUBSTANCE_PATH: "Absent",
        TREATMENT_PATH: "NoEstablishedTreatment",
        **{
            "candidate/history/pharmacotherapy.v2/" + field: "Present"
            for field in (
                "h_prior_treatment_difficulty",
                "h_preference_barrier",
                "h_exposure_modifier",
                "h_trial_duration_review",
                "h_urgent_safety_concern",
                "h_monitoring_gap",
            )
        },
        "h_exposure_duration_3m": "yes",
        "h_exposure_1m_if_60plus": "no",
        "h_medication_timeline_documented": "yes",
        "h_onset_timing": "during_exposure",
        "h_trial_adequacy_prior": "inadequate",
        "h_prior_response": "response",
    }
    provenance = {
        key: {"source": "copied_baseline", "recorded_at": "2020-01-01T00:00:00Z"} for key in legacy
    }
    raw = {
        "history": {"values": legacy, "provenance": provenance},
        "followup_baseline": {"history_values": dict(legacy)},
    }
    _seed_draft_data(clean_registry, eid, raw)
    expected = {
        **COMPLETE_HISTORY,
        "h_exposure_dopamine_blocker": "unknown",
        PHYSICAL_PATH: "yes",
        SUBSTANCE_PATH: "no",
    }
    read = _get_history(client, eid).json()
    assert read["values"] == expected
    assert set(read["provenance"]) == set(expected) - {"h_exposure_dopamine_blocker"}
    draft = client.get(f"/api/v1/encounters/{eid}").json()["draft_data"]
    assert draft["history"]["values"] == expected
    assert draft["followup_baseline"]["history_values"] == legacy
    saved = _patch(client, csrf, eid, raw, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_data"]["history"]["values"] == expected
    assert _get_history(client, eid).json()["values"] == expected
    assert saved.json()["draft_data"]["followup_baseline"]["history_values"] == legacy
    strict_resave = _post_history(client, csrf, eid, read["values"], 2)
    assert strict_resave.status_code == 200, strict_resave.text
    assert strict_resave.json()["values"] == expected
    assert _get_history(client, eid).json()["values"] == expected


def test_legacy_defaults_active_children_but_explicit_null_stays_invalid(
    admin_client, clean_registry, monkeypatch
):
    client, _, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_legacy_null", "0012348606"
    )
    _seed_draft_data(
        clean_registry,
        eid,
        {
            "history": {
                "values": {
                    **COMPLETE_HISTORY,
                    "h_exposure_dopamine_blocker": "yes",
                    "h_trial_adequacy_prior": "inadequate",
                    "h_onset_timing": "not_assessed",
                    "h_prior_response": None,
                }
            }
        },
    )
    state = _get_history(client, eid).json()
    assert state["values"]["h_onset_timing"] == "unknown"
    assert state["values"]["h_prior_response"] is None
    assert "h_prior_response" in state["evaluation"]["item_errors"]
    assert state["evaluation"]["status"] == "partial"
    assert state["provenance"] == {}


def test_all_revised_choices_and_versioned_context_survive_autosave(
    admin_client, clean_registry, monkeypatch
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_options", "0012348607"
    )
    initial = _get_history(client, eid).json()
    assert set(HISTORY_IDS) <= set(initial["allowed_field_ids"])
    assert not {
        "h_exposure_duration_3m",
        "h_exposure_1m_if_60plus",
        "h_medication_timeline_documented",
    } & set(initial["allowed_field_ids"])
    context_key = TREATMENT_PATH
    values = {
        **COMPLETE_HISTORY,
        "h_exposure_dopamine_blocker": "yes",
        "h_trial_adequacy_prior": "inadequate",
        context_key: "Clozapine",
        "candidate/history/lai_indication_choice.v1/h_prior_nms": "unknown",
    }
    revision = 1
    for onset, response in [
        ("during_exposure", "response"),
        ("within_4w_oral_withdrawal", "no_response"),
        ("within_8w_lai_withdrawal", "unknown"),
        ("other", "response"),
        ("unknown", "unknown"),
    ]:
        values = {**values, "h_onset_timing": onset, "h_prior_response": response}
        saved = _post_history(client, csrf, eid, values, revision)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        assert saved.json()["values"] == values
        assert saved.json()["evaluation"]["status"] == "complete"
        draft = client.get(f"/api/v1/encounters/{eid}").json()["draft_data"]
        patched = _patch(client, csrf, eid, draft, revision)
        assert patched.status_code == 200, patched.text
        revision = patched.json()["revision"]
        assert _get_history(client, eid).json()["values"] == values


def test_movement_parent_clears_hidden_answers_and_provenance_on_both_save_paths(
    admin_client, clean_registry, monkeypatch
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_movement_cleanup", "0012348701"
    )
    saved = _post_history(
        client,
        csrf,
        eid,
        {
            **COMPLETE_HISTORY,
            "h_movement_persistence_4w": "yes",
            "h_alternative_cause_considered": "yes",
            "h_falls_or_limitation": "no",
        },
        1,
    )
    assert saved.status_code == 200, saved.text
    state = saved.json()
    children = {"h_alternative_cause_considered", "h_falls_or_limitation"}
    assert children <= set(state["provenance"])
    for parent, autosave in [("no", False), ("yes", True), ("unknown", True), ("yes", False)]:
        values = {**state["values"], "h_movement_persistence_4w": parent}
        response = (
            _patch(client, csrf, eid, {"history": {"values": values}}, state["revision"])
            if autosave
            else _post_history(client, csrf, eid, values, state["revision"])
        )
        assert response.status_code == 200, response.text
        state = _get_history(client, eid).json()
        if parent == "yes":
            assert {key: state["values"][key] for key in children} == {
                "h_alternative_cause_considered": "unknown",
                "h_falls_or_limitation": "unknown",
            }
        else:
            assert not children & set(state["values"])
        assert not children & set(state["provenance"])
        assert state["evaluation"]["status"] == "complete"


def test_pharmacotherapy_choices_and_exposure_cleanup(admin_client, clean_registry, monkeypatch):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_pharma_cleanup", "0012348702"
    )
    state = _get_history(client, eid).json()
    for bad in ["not_assessed", "NoEstablishedTreatment", None]:
        values = {**COMPLETE_HISTORY, "h_exposure_dopamine_blocker": "yes", TREATMENT_PATH: bad}
        for response in [
            _post_history(client, csrf, eid, values, 1),
            _patch(client, csrf, eid, {"history": {"values": values}}, 1),
        ]:
            assert response.status_code == 422, response.text
            assert TREATMENT_PATH in response.json()["field_errors"]
    values = {
        **COMPLETE_HISTORY,
        "h_exposure_dopamine_blocker": "yes",
        PHYSICAL_PATH: "yes",
        SUBSTANCE_PATH: "no",
        TREATMENT_PATH: "Clozapine",
    }
    saved = _post_history(client, csrf, eid, values, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"][TREATMENT_PATH] == "Clozapine"
    assert TREATMENT_PATH in saved.json()["provenance"]
    for exposure in ["no", "unknown", "yes"]:
        state = saved.json()
        values = {**state["values"], "h_exposure_dopamine_blocker": exposure}
        saved = _post_history(client, csrf, eid, values, state["revision"])
        assert saved.status_code == 200, saved.text
        if exposure == "yes":
            assert saved.json()["values"][TREATMENT_PATH] == "unknown"
        else:
            assert TREATMENT_PATH not in saved.json()["values"]
        assert TREATMENT_PATH not in saved.json()["provenance"]


@pytest.mark.parametrize("outage", ["missing_active", "corrupt_active", "missing_other"])
def test_existing_draft_and_unrelated_autosave_survive_workflow_content_outage(
    admin_client, clean_registry, monkeypatch, tmp_path, outage
):
    from copy import deepcopy
    from shutil import copytree, ignore_patterns

    from x_insight.models.workflow_release import content_root

    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_content_outage", "0012348801"
    )
    # Observe application errors as HTTP responses, including missing deployment files.
    client = TestClient(app, raise_server_exceptions=False, cookies=client.cookies)
    other_path = "candidate/history/lai_indication_choice.v1/h_lai_preference"
    values = {
        **COMPLETE_HISTORY,
        PHYSICAL_PATH: "yes",
        SUBSTANCE_PATH: "no",
        other_path: "PrefersLAI",
    }
    saved = _post_history(client, csrf, eid, values, 1)
    assert saved.status_code == 200, saved.text
    expected_values = {**values, SUBSTANCE_PATH: "unknown"}
    expected_provenance = saved.json()["provenance"]
    expected_provenance.pop(SUBSTANCE_PATH)
    existing = client.get(f"/api/v1/encounters/{eid}")
    assert existing.status_code == 200, existing.text
    raw = deepcopy(existing.json()["draft_data"])
    # Older persisted records can omit a newly defaulted workflow question.
    raw["history"]["values"].pop(SUBSTANCE_PATH)
    raw["history"]["provenance"].pop(SUBSTANCE_PATH)
    raw["history"]["values"][PHYSICAL_PATH] = "Present"
    legacy = {
        "h_monitoring_baseline": "yes",
        "h_functional_impact": "yes",
        "h_alternative_cause_considered": "yes",
        "h_falls_or_limitation": "yes",
        TREATMENT_PATH: "Clozapine",
        "candidate/history/pharmacotherapy.v2/h_monitoring_gap": "Present",
    }
    raw["history"]["values"].update(legacy)
    raw["history"]["provenance"].update(
        {
            key: {"source": "copied_baseline", "recorded_at": "2020-01-01T00:00:00Z"}
            for key in legacy
        }
    )
    _seed_draft_data(clean_registry, eid, raw)
    deployed = tmp_path / "content"
    copytree(content_root(), deployed, ignore=ignore_patterns("ddi", "medications"))
    if outage == "missing_active":
        (deployed / "history/history.pharmacotherapy.v3.json").unlink()
    elif outage == "corrupt_active":
        target = deployed / "history/history.pharmacotherapy.v3.json"
        target.write_text(target.read_text() + " ")
    else:
        (deployed / "history/history.established_case_clozapine.v1.json").unlink()
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(deployed))

    reopened = client.get(f"/api/v1/encounters/{eid}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["revision"] == 2
    assert reopened.json()["draft_data"]["history"]["values"] == expected_values
    assert reopened.json()["draft_data"]["history"]["provenance"] == expected_provenance
    # A browser may still hold the complete body fetched before the content outage.
    raw["history"]["values"][SUBSTANCE_PATH] = "unknown"
    raw["working_note"] = "Synthetic unrelated note during content outage"
    note_saved = _patch(client, csrf, eid, raw, 2)
    assert note_saved.status_code == 200, note_saved.text
    assert note_saved.json()["draft_data"]["history"]["values"] == expected_values
    assessment_body = deepcopy(note_saved.json()["draft_data"])
    assessment_body["panss"] = {"answers": {"P1": 3}}
    assessment_saved = _patch(client, csrf, eid, assessment_body, 3)
    assert assessment_saved.status_code == 200, assessment_saved.text
    current = client.get(f"/api/v1/encounters/{eid}")
    assert current.status_code == 200, current.text
    assert current.json()["revision"] == 4
    assert current.json()["draft_data"]["working_note"] == raw["working_note"]
    assert current.json()["draft_data"]["panss"] == {"answers": {"P1": 3}}
    assert current.json()["draft_data"]["history"]["values"] == expected_values
    assert current.json()["draft_data"]["history"]["provenance"] == expected_provenance

    for response in (
        client.get(f"/api/v1/encounters/{eid}/proposal-readiness"),
        client.post(
            f"/api/v1/encounters/{eid}/generation-batches",
            json={},
            headers={**_auth_headers(csrf), "If-Match": '"4"'},
        ),
        _post_history(client, csrf, eid, expected_values, 4),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "WORKFLOW_CONTENT_UNAVAILABLE"
    assert client.get(f"/api/v1/encounters/{eid}/generation-batches/latest").json() == {
        "batch": None
    }
    for field, bad in [
        ("h_movement_persistence_4w", "not_assessed"),
        ("h_monitoring_baseline", "yes"),
        (PHYSICAL_PATH, "not_assessed"),
        (other_path, "invented"),
    ]:
        invalid = deepcopy(current.json()["draft_data"])
        invalid["history"]["values"][field] = bad
        rejected = _patch(client, csrf, eid, invalid, 4)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["code"] in {"VALIDATION_FAILED", "WORKFLOW_CONTENT_UNAVAILABLE"}
    assert client.get(f"/api/v1/encounters/{eid}").json() == current.json()


def test_revised_history_http_rejects_retired_and_obsolete_choices_and_clears_nms(
    admin_client, clean_registry, monkeypatch
):
    client, csrf, _, eid = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_revised_history", "0012348821"
    )
    lai = "candidate/history/lai_indication_choice.v1/"
    aggression = "candidate/history/aggression_clozapine.v1/"
    for path, value in [
        (lai + "h_schizophrenia_established", "Yes"),
        (lai + "h_oral_experience", "Supported"),
        (lai + "h_product_plan", "Complete"),
        (lai + "h_lai_treatment_status", "CurrentTreatment"),
        (aggression + "h_persistence_despite_treatment", "Yes"),
        (aggression + "h_urgent_safety_concern", "Present"),
        (aggression + "h_adherence_concern", "Present"),
        (aggression + "h_modifiable_targets", "Present"),
        ("candidate/history/high_suicide_clozapine.v1/h_substantial_suicide_risk", "Yes"),
        (lai + "h_adherence_history", "Uncertain"),
        (lai + "h_adherence_history", "not_assessed"),
        (lai + "h_lai_preference", "not_assessed"),
        (lai + "h_discussion_context", "not_assessed"),
        (lai + "h_delivery_barriers", "not_assessed"),
        (aggression + "h_substantial_aggression_risk", "not_assessed"),
    ]:
        for rejected in (
            _post_history(client, csrf, eid, {path: value}, 1),
            _patch(client, csrf, eid, {"history": {"values": {path: value}}}, 1),
        ):
            assert rejected.status_code == 422, (path, rejected.text)
            assert path in rejected.json()["field_errors"]
    initial = _get_history(client, eid).json()
    assert initial["revision"] == 1
    assert lai + "h_prior_nms" not in initial["values"]
    saved = _post_history(
        client,
        csrf,
        eid,
        {**COMPLETE_HISTORY, "h_exposure_dopamine_blocker": "yes", lai + "h_prior_nms": "Yes"},
        1,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"][lai + "h_prior_nms"] == "Yes"
    assert saved.json()["provenance"][lai + "h_prior_nms"]["source"] == "clinician_entry"
    for exposure in ("no", "unknown", "yes"):
        previous = saved.json()
        saved = _post_history(
            client,
            csrf,
            eid,
            {**previous["values"], "h_exposure_dopamine_blocker": exposure},
            previous["revision"],
        )
        assert saved.status_code == 200, saved.text
        if exposure == "yes":
            assert saved.json()["values"][lai + "h_prior_nms"] == "unknown"
        else:
            assert lai + "h_prior_nms" not in saved.json()["values"]
        assert lai + "h_prior_nms" not in saved.json()["provenance"]
