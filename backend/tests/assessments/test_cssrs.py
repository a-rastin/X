"""C-SSRS form and distinct results (S11, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 evaluator (no frontend):

1. Fresh ``{}`` is unanswered with null severities (no defaults, no hidden
   zeros); Skip persists ``not_assessed``; complete explicit negatives give
   the S08-defined no-ideation result (severity 0/0/0 + ``no_positive_items``).
   The GET preview always equals direct ``evaluate()`` (no drift).
2. Source-derived worked example with level 3 endorsed gives severity 3
   without auto-filling lower responses; intensity/behavior/lethality stay
   separate dimensions with no composite score.
3. Historical versus current answers retain their periods; an incomplete
   required branch reports missing-item guidance (never a guessed negative);
   S08 alert logic holds (``high_risk_alert`` vs ``clinical_review``; NSSI
   never triggers ``high_risk_alert`` alone).
4. Invalid/out-of-range values and undeclared period-like IDs are rejected
   server-side with aggregates suppressed to null (never zero); author-only
   access holds; resume preserves completeness after PATCH + GET.

C-SSRS state lives in ``draft_data["cssrs"]``; answers travel through the
existing S07 PATCH autosave, preview through ``GET .../cssrs`` only.
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
            connection.execute(text("TRUNCATE notes, encounters, patients"))
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


def _get_cssrs(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/cssrs")


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


# Independent literal fixtures (worked from the S08 released cssrs.v1
# definition: ideation levels 1-5 in two windows, five behavior categories
# in two windows, five intensity dimensions, lethality codings — never
# produced by the implementation under test).
CSSRS_IDEATION = [
    f"css_i{level}_{window}" for window in ("recent", "lifetime") for level in (1, 2, 3, 4, 5)
]
CSSRS_BEHAVIOR = [
    f"css_beh_{category}_{window}"
    for window in ("recent", "lifetime")
    for category in ("actual", "interrupted", "aborted", "preparatory", "nssi")
]
NULL_SEVERITIES = {
    "ideation_severity_recent": None,
    "ideation_severity_lifetime": None,
    "ideation_severity_max": None,
}
INTENSITY_L3 = {
    "css_frequency": 2,
    "css_duration": 2,
    "css_controllability": 3,
    "css_deterrents": 1,
    "css_reasons": 4,
}
INTENSITY_L3_FINDINGS = {
    "frequency": 2,
    "duration": 2,
    "controllability": 3,
    "deterrents": 1,
    "reasons": 4,
}


def _all_no() -> dict:
    return {**dict.fromkeys(CSSRS_IDEATION, "no"), **dict.fromkeys(CSSRS_BEHAVIOR, "no")}


def _level3_complete() -> dict:
    answers = _all_no()
    answers["css_i3_recent"] = "yes"
    answers.update(INTENSITY_L3)
    return answers


def _assert_no_composite(result: dict) -> None:
    assert set(result["scores"]) == {
        "ideation_severity_recent",
        "ideation_severity_lifetime",
        "ideation_severity_max",
    }
    assert not {key for key in result["scores"] if "composite" in key or "risk" in key}
    assert not {key for key in result["findings"] if "composite" in key or "risk" in key}


# --- Slice 1: fresh unanswered + skipped + explicit negatives; preview == direct ---


def test_fresh_form_is_unanswered_with_null_severities(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs1", "0012347001"
    )
    direct = evaluate(get_released_definition("cssrs"), {})
    assert direct["status"] == "unanswered"
    assert set(direct["missing_item_ids"]) == set(CSSRS_IDEATION + CSSRS_BEHAVIOR)
    assert direct["item_errors"] == {}
    assert direct["scores"] == NULL_SEVERITIES
    assert direct["findings"] == {}
    assert direct["definition_version"] == "v1"

    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == {}
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"]["ideation_severity_max"] is None
    assert body["definition_version"] == "v1"
    assert body["revision"] == 1
    assert preview.headers["etag"] == '"1"'


def test_skip_persists_not_assessed(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs2", "0012347002"
    )
    skipped = {"__skipped": True}
    direct = evaluate(get_released_definition("cssrs"), skipped)
    assert direct["status"] == "not_assessed"
    assert direct["scores"] == NULL_SEVERITIES
    assert direct["findings"] == {"skipped": True}

    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": skipped}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == skipped
    assert body["evaluation"] == direct
    assert body["evaluation"]["status"] == "not_assessed"
    assert body["revision"] == 2
    assert preview.headers["etag"] == '"2"'


def test_complete_explicit_negatives_give_no_ideation_result(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs3", "0012347003"
    )
    answers = _all_no()
    direct = evaluate(get_released_definition("cssrs"), answers)
    assert direct["status"] == "complete"
    assert direct["item_errors"] == {}
    assert direct["missing_item_ids"] == []
    # S08-defined no-ideation result: severity 0/0/0, never null, never zero-filled.
    assert direct["scores"] == {
        "ideation_severity_recent": 0,
        "ideation_severity_lifetime": 0,
        "ideation_severity_max": 0,
    }
    assert direct["findings"]["flags"] == ["no_positive_items"]
    assert direct["findings"]["intensity"] is None
    assert direct["findings"]["windows"] == {
        "recent_ideation": "past_month",
        "recent_behavior": "past_3_months",
        "lifetime": "lifetime",
    }
    _assert_no_composite(direct)

    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v1"
    assert preview.headers["etag"] == '"2"'


# --- Slice 2: level-3 severity without autofill; dimensions stay separate ---


def test_level3_endorsed_gives_severity3_without_autofilling_lower(
    admin_client, clean_registry, monkeypatch
) -> None:
    # A lone level-3 yes infers nothing: lower levels stay missing, no severity.
    partial = evaluate(get_released_definition("cssrs"), {"css_i3_recent": "yes"})
    assert partial["status"] == "partial"
    assert "css_i1_recent" in partial["missing_item_ids"]
    assert "css_i2_recent" in partial["missing_item_ids"]
    assert partial["scores"] == NULL_SEVERITIES

    answers = _level3_complete()
    direct = evaluate(get_released_definition("cssrs"), answers)
    assert direct["status"] == "complete"
    assert direct["item_errors"] == {}
    assert direct["scores"]["ideation_severity_recent"] == 3
    assert direct["scores"]["ideation_severity_lifetime"] == 0
    assert direct["scores"]["ideation_severity_max"] == 3
    assert direct["findings"]["intensity"] == INTENSITY_L3_FINDINGS
    assert direct["findings"]["flags"] == ["clinical_review"]
    _assert_no_composite(direct)

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs4", "0012347004"
    )
    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"]["ideation_severity_recent"] == 3


def test_severity_is_highest_endorsed_with_separate_dimensions(
    admin_client, clean_registry, monkeypatch
) -> None:
    answers = _all_no()
    answers.update({"css_i2_recent": "yes", "css_i4_recent": "yes", "css_i5_lifetime": "yes"})
    intensity = {
        "css_frequency": 4,
        "css_duration": 3,
        "css_controllability": 4,
        "css_deterrents": 5,
        "css_reasons": 5,
    }
    answers.update(intensity)
    direct = evaluate(get_released_definition("cssrs"), answers)
    assert direct["status"] == "complete"
    assert direct["scores"] == {
        "ideation_severity_recent": 4,
        "ideation_severity_lifetime": 5,
        "ideation_severity_max": 5,
    }
    findings = direct["findings"]
    assert set(findings) >= {"intensity", "behavior", "lethality", "flags", "windows", "notes"}
    assert findings["intensity"] == {
        "frequency": 4,
        "duration": 3,
        "controllability": 4,
        "deterrents": 5,
        "reasons": 5,
    }
    # Behavior/lethality are recorded separately even when nothing is endorsed.
    assert findings["behavior"]["recent"] == {
        "actual": "no",
        "interrupted": "no",
        "aborted": "no",
        "preparatory": "no",
        "nssi": "no",
    }
    assert findings["lethality"]["recent"] == {"actual": None, "potential": None}
    assert findings["lethality"]["lifetime"] == {"actual": None, "potential": None}
    _assert_no_composite(direct)

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs5", "0012347005"
    )
    assert _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1).status_code == 200
    body = _get_cssrs(client, encounter_id).json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"]["ideation_severity_max"] == 5


# --- Slice 3: periods, missing-item guidance, alert logic ---


def test_historical_vs_current_periods_retained(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs6", "0012347006"
    )
    answers = _all_no()
    answers.update(
        {
            "css_i2_lifetime": "yes",
            "css_beh_actual_lifetime": "yes",
            "css_leth_actual_lifetime": 2,
        }
    )
    direct = evaluate(get_released_definition("cssrs"), answers)
    assert direct["status"] == "complete"
    # Recent and lifetime windows never overwrite each other.
    assert direct["scores"]["ideation_severity_recent"] == 0
    assert direct["scores"]["ideation_severity_lifetime"] == 2
    assert direct["scores"]["ideation_severity_max"] == 2
    assert direct["findings"]["intensity"] is None
    assert direct["findings"]["behavior"]["recent"]["actual"] == "no"
    assert direct["findings"]["behavior"]["lifetime"]["actual"] == "yes"
    assert direct["findings"]["lethality"]["recent"] == {"actual": None, "potential": None}
    assert direct["findings"]["lethality"]["lifetime"] == {"actual": 2, "potential": None}
    assert direct["findings"]["flags"] == ["clinical_review"]
    assert direct["findings"]["windows"] == {
        "recent_ideation": "past_month",
        "recent_behavior": "past_3_months",
        "lifetime": "lifetime",
    }

    assert _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1).status_code == 200
    body = _get_cssrs(client, encounter_id).json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["findings"]["behavior"]["lifetime"]["actual"] == "yes"


def test_incomplete_required_branch_has_missing_item_guidance(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs7", "0012347007"
    )
    started = _all_no()
    started["css_beh_actual_recent"] = "yes"
    direct = evaluate(get_released_definition("cssrs"), started)
    assert direct["status"] == "partial"
    assert "css_leth_actual_recent" in direct["missing_item_ids"]
    assert direct["scores"] == NULL_SEVERITIES
    assert direct["findings"] == {}

    assert _patch(client, csrf, encounter_id, {"cssrs": {"answers": started}}, 1).status_code == 200
    body = _get_cssrs(client, encounter_id).json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"] == NULL_SEVERITIES

    # Actual damage 0 additionally requires potential lethality: still guidance, no guess.
    damaged = dict(started)
    damaged["css_leth_actual_recent"] = 0
    needs_potential = evaluate(get_released_definition("cssrs"), damaged)
    assert needs_potential["status"] == "partial"
    assert "css_leth_potential_recent" in needs_potential["missing_item_ids"]
    assert needs_potential["scores"] == NULL_SEVERITIES

    complete = dict(damaged)
    complete["css_leth_potential_recent"] = 1
    finished = evaluate(get_released_definition("cssrs"), complete)
    assert finished["status"] == "complete"
    assert finished["findings"]["lethality"]["recent"] == {"actual": 0, "potential": 1}
    assert "high_risk_alert" in finished["findings"]["flags"]

    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": complete}}, 2)
    assert saved.status_code == 200, saved.text
    resumed = _get_cssrs(client, encounter_id).json()
    assert resumed["evaluation"] == finished
    assert resumed["evaluation"]["findings"]["lethality"]["recent"] == {"actual": 0, "potential": 1}


def test_alert_logic_high_risk_vs_clinical_review_nssi_never_high_risk(
    admin_client, clean_registry, monkeypatch
) -> None:
    # Recent ideation level 4 triggers the urgent flag alongside review.
    urgent = _all_no()
    urgent["css_i4_recent"] = "yes"
    urgent.update(INTENSITY_L3)
    urgent_result = evaluate(get_released_definition("cssrs"), urgent)
    assert urgent_result["status"] == "complete"
    assert urgent_result["scores"]["ideation_severity_recent"] == 4
    assert "high_risk_alert" in urgent_result["findings"]["flags"]
    assert "clinical_review" in urgent_result["findings"]["flags"]

    # Lifetime ideation alone needs review without the recent urgent flag.
    historical = _all_no()
    historical["css_i2_lifetime"] = "yes"
    historical_result = evaluate(get_released_definition("cssrs"), historical)
    assert historical_result["status"] == "complete"
    assert historical_result["findings"]["flags"] == ["clinical_review"]

    # NSSI alone contributes to review but never triggers high risk by itself.
    for window in ("recent", "lifetime"):
        nssi = _all_no()
        nssi[f"css_beh_nssi_{window}"] = "yes"
        result = evaluate(get_released_definition("cssrs"), nssi)
        assert result["status"] == "complete", window
        assert result["scores"] == {
            "ideation_severity_recent": 0,
            "ideation_severity_lifetime": 0,
            "ideation_severity_max": 0,
        }
        assert result["findings"]["flags"] == ["clinical_review"], window

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs8", "0012347008"
    )
    nssi_recent = _all_no()
    nssi_recent["css_beh_nssi_recent"] = "yes"
    direct = evaluate(get_released_definition("cssrs"), nssi_recent)
    assert (
        _patch(client, csrf, encounter_id, {"cssrs": {"answers": nssi_recent}}, 1).status_code
        == 200
    )
    body = _get_cssrs(client, encounter_id).json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["findings"]["flags"] == ["clinical_review"]
    assert "high_risk_alert" not in body["evaluation"]["findings"]["flags"]


# --- Slice 4: server-side value/period validation, auth, resume ---


def test_invalid_values_and_periods_rejected_server_side_aggregates_null_never_zero(
    admin_client, clean_registry, monkeypatch
) -> None:
    base = _level3_complete()
    # Invalid yes/no, out-of-range numerics, wrong types, and undeclared
    # period-like IDs are all answer errors with aggregates suppressed.
    bad_cases = [
        ("css_i1_recent", "maybe"),
        ("css_i1_recent", 0),
        ("css_i1_recent", 1),
        ("css_i1_recent", True),
        ("css_i1_recent", None),
        ("css_i5_lifetime", "sometimes"),
        ("css_frequency", 0),
        ("css_frequency", 6),
        ("css_frequency", "2"),
        ("css_frequency", 2.5),
        ("css_frequency", True),
        ("css_i1_current", "yes"),
        ("css_beh_actual_now", "no"),
        ("PX", 1),
        ("__draft", True),
    ]
    for item_id, bad in bad_cases:
        answers = dict(base)
        answers[item_id] = bad
        result = evaluate(get_released_definition("cssrs"), answers)
        assert item_id in result["item_errors"], (item_id, bad)
        assert result["status"] == "partial"
        assert result["scores"] == NULL_SEVERITIES
        assert result["findings"] == {}

    lethality_base = _all_no()
    lethality_base["css_beh_actual_recent"] = "yes"
    lethality_base["css_leth_actual_recent"] = 2
    lethality_base["css_leth_potential_recent"] = 1
    for bad in (6, -1, "1", 1.5, True, None):
        answers = dict(lethality_base)
        answers["css_leth_actual_recent"] = bad
        result = evaluate(get_released_definition("cssrs"), answers)
        assert "css_leth_actual_recent" in result["item_errors"], bad
        assert result["scores"] == NULL_SEVERITIES

    # The same rejection is visible through the GET route (never zero-filled).
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs9", "0012347009"
    )
    revision = 1
    for item_id, bad in [("css_frequency", 9), ("css_i1_recent", "maybe"), ("PX", 1)]:
        answers = dict(base)
        answers[item_id] = bad
        saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, revision)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        body = _get_cssrs(client, encounter_id).json()
        assert item_id in body["evaluation"]["item_errors"], (item_id, bad)
        assert body["evaluation"]["scores"] == NULL_SEVERITIES
        assert body["evaluation"]["findings"] == {}


def test_cssrs_author_only_and_auth(admin_client, clean_registry, monkeypatch) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs10", "0012347010"
    )
    _make_physician(admin_client, "dr_cssrs_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_cssrs_stranger", "pw123")
    assert stranger.get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 403

    # Administrator is not the author either.
    assert admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 403
    # Anonymous reads are 401.
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 401
    # Unknown encounter is 404, not 403.
    assert client.get(f"/api/v1/encounters/{uuid.uuid4()}/cssrs").status_code == 404


def test_resume_preserves_completeness_after_patch_and_get(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs11", "0012347011"
    )
    answers = _level3_complete()
    direct = evaluate(get_released_definition("cssrs"), answers)
    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    before = _get_cssrs(client, encounter_id).json()
    assert before["evaluation"] == direct
    assert before["evaluation"]["status"] == "complete"

    # Resume after restart: a fresh client re-authenticates and sees the same state.
    fresh = TestClient(app)
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_cssrs11", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh.get(f"/api/v1/encounters/{encounter_id}/cssrs")
    assert resumed.status_code == 200, resumed.text
    resumed_body = resumed.json()
    assert resumed_body["answers"] == answers
    assert resumed_body["evaluation"] == direct
    assert resumed_body["evaluation"]["scores"] == {
        "ideation_severity_recent": 3,
        "ideation_severity_lifetime": 0,
        "ideation_severity_max": 3,
    }
    assert resumed_body["revision"] == 2
    assert resumed.headers["etag"] == '"2"'
    # The shared autosave body carries the answers through GET /encounters.
    draft = fresh.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["cssrs"]["answers"] == answers
    assert draft["revision"] == 2
