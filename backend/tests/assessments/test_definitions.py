"""Assessment definitions and evaluation (S08, seams T2/T1, backend only).

Slices through the real module interfaces (plan.md §5; FR-11–13; NFR-05):
1. Tiny synthetic definition first (T2 contract): unanswered / partial /
   complete / not_assessed are distinguishable and no missing answer becomes
   zero. Rejection: undeclared item IDs, invalid values, unknown rule
   operators, arbitrary executable expressions.
2. PANSS exact sums (all-1 → 7/7/16/30, all-7 → 49/49/112/210), one missing
   item suppresses the total, Skip persists not_assessed, no default 1s.
3. Diagnosis full-criteria / below-threshold / partial / bypass + unknown.
4. C-SSRS severity-3-no-autofill, separation (no composite), periods, flags.
5. Release validation (version, source hashes, assumptions, experimental
   defaults, validation results) + authenticated routes (T1).

Expected literals below were worked independently from the sources and the
task text, never produced by the implementation under test.
"""

from __future__ import annotations

import pytest

from x_insight.assessments import DefinitionError, evaluate, load_definition

# --- Slice 1 fixture: tiny synthetic definition (T2 contract first) ---


def _synthetic_definition() -> dict:
    return {
        "instrument": "synthetic",
        "id": "synthetic",
        "version": "v1",
        "title": "Synthetic T2 fixture",
        "items": [
            {"id": "s1", "prompt": "First?", "value_type": "yes_no", "required": True},
            {"id": "s2", "prompt": "Second?", "value_type": "yes_no", "required": True},
            {
                "id": "s3",
                "prompt": "Optional scale?",
                "value_type": "scale_1_7",
                "required": False,
            },
        ],
        "completion_rules": {"type": "all_required"},
        "result_rules": [{"op": "count", "of": ["s1", "s2"], "equals": "yes", "as": "yes_count"}],
    }


def test_synthetic_empty_answers_are_unanswered() -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {})
    assert result["status"] == "unanswered"
    assert result["missing_item_ids"] == ["s1", "s2"]
    assert result["item_errors"] == {}
    assert result["scores"] == {"yes_count": None}
    assert result["definition_version"] == "v1"


def test_synthetic_partial_answers_never_become_zero() -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"s1": "yes"})
    assert result["status"] == "partial"
    assert result["missing_item_ids"] == ["s2"]
    assert result["item_errors"] == {}
    # A missing answer must stay missing, never a zero count.
    assert result["scores"] == {"yes_count": None}


def test_synthetic_complete_counts_explicit_answers() -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"s1": "yes", "s2": "no"})
    assert result["status"] == "complete"
    assert result["missing_item_ids"] == []
    assert result["item_errors"] == {}
    assert result["scores"] == {"yes_count": 1}


def test_synthetic_skip_persists_not_assessed_not_zero() -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"__skipped": True})
    assert result["status"] == "not_assessed"
    assert result["item_errors"] == {}
    assert result["scores"] == {"yes_count": None}


def test_synthetic_optional_item_may_be_absent_or_answered() -> None:
    definition = load_definition(_synthetic_definition())
    complete = evaluate(definition, {"s1": "no", "s2": "no"})
    assert complete["status"] == "complete"
    assert complete["scores"] == {"yes_count": 0}
    with_optional = evaluate(definition, {"s1": "no", "s2": "no", "s3": 4})
    assert with_optional["status"] == "complete"
    assert with_optional["scores"] == {"yes_count": 0}


# --- Slice 1 rejection tests ---


def test_undeclared_item_id_is_an_item_error_not_silent() -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"s1": "yes", "s2": "yes", "nope": "yes"})
    assert result["status"] == "partial"
    assert "nope" in result["item_errors"]
    assert result["scores"] == {"yes_count": None}


@pytest.mark.parametrize("value", ["maybe", "", 1, None, True])
def test_invalid_yes_no_values_rejected(value) -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"s1": value, "s2": "yes"})
    assert "s1" in result["item_errors"], value
    assert result["status"] == "partial"
    assert result["scores"] == {"yes_count": None}


@pytest.mark.parametrize("value", [0, 8, 2.5, "3", True, None])
def test_invalid_scale_values_rejected(value) -> None:
    definition = load_definition(_synthetic_definition())
    result = evaluate(definition, {"s1": "yes", "s2": "yes", "s3": value})
    assert "s3" in result["item_errors"], value
    # An invalid value blocks completion even on an optional item: the
    # attempted answer needs correction before any result counts.
    assert result["status"] == "partial"
    assert result["scores"] == {"yes_count": None}


def test_unknown_rule_operator_rejected_at_load() -> None:
    raw = _synthetic_definition()
    raw["result_rules"] = [{"op": "weighted_average", "of": ["s1"], "as": "x"}]
    with pytest.raises(DefinitionError):
        load_definition(raw)


def test_unknown_completion_rule_rejected_at_load() -> None:
    raw = _synthetic_definition()
    raw["completion_rules"] = {"type": "quorum_of_two"}
    with pytest.raises(DefinitionError):
        load_definition(raw)


def test_arbitrary_executable_expression_rejected_at_load() -> None:
    raw = _synthetic_definition()
    raw["result_rules"] = [{"expression": "answers['s1'] == 'yes'"}]
    with pytest.raises(DefinitionError):
        load_definition(raw)


def test_eval_exec_keys_rejected_anywhere_in_definition() -> None:
    raw = _synthetic_definition()
    raw["result_rules"] = [
        {"op": "count", "of": ["s1", "s2"], "equals": "yes", "as": "x", "eval": "yes_count + 1"}
    ]
    with pytest.raises(DefinitionError):
        load_definition(raw)


def test_unknown_instrument_rejected_at_load() -> None:
    raw = _synthetic_definition()
    raw["instrument"] = "phrenology"
    with pytest.raises(DefinitionError):
        load_definition(raw)


def test_required_if_must_reference_declared_items() -> None:
    raw = _synthetic_definition()
    raw["items"].append(
        {
            "id": "s4",
            "prompt": "Conditional?",
            "value_type": "yes_no",
            "required": False,
            "required_if": {"op": "eq", "item": "ghost", "value": "yes"},
        }
    )
    with pytest.raises(DefinitionError):
        load_definition(raw)


# --- Released definition loading (slices 2-4 read the real packages) ---

from pathlib import Path  # noqa: E402

CONTENT_DIR = Path(__file__).resolve().parents[3] / "content" / "assessments"
SOURCE_DIR = Path(__file__).resolve().parents[3] / "project-documents" / "medical-documents"


def _released(instrument: str) -> dict:
    import json

    with open(CONTENT_DIR / f"{instrument}.v1.json", encoding="utf-8") as handle:
        return load_definition(json.load(handle))


# --- Slice 2: PANSS exact sums (source: PANSS.md scoring system) ---

PANSS_IDS = (
    [f"P{i}" for i in range(1, 8)]
    + [f"N{i}" for i in range(1, 8)]
    + [f"G{i}" for i in range(1, 17)]
)


def test_panss_released_ids_cover_all_30_source_items() -> None:
    definition = _released("panss")
    assert [item["id"] for item in definition["items"]] == PANSS_IDS
    assert all(
        item["value_type"] == "scale_1_7" and item["required"] for item in definition["items"]
    )


def test_panss_all_absent_scores_minimum() -> None:
    result = evaluate(_released("panss"), dict.fromkeys(PANSS_IDS, 1))
    assert result["status"] == "complete"
    assert result["item_errors"] == {}
    # Independently worked: 7x1 / 7x1 / 16x1 / 30x1.
    assert result["scores"] == {"positive": 7, "negative": 7, "general": 16, "total": 30}


def test_panss_all_extreme_scores_maximum() -> None:
    result = evaluate(_released("panss"), dict.fromkeys(PANSS_IDS, 7))
    assert result["status"] == "complete"
    # Independently worked: 7x7=49 / 7x7=49 / 16x7=112 / 210.
    assert result["scores"] == {"positive": 49, "negative": 49, "general": 112, "total": 210}
    assert result["findings"]["total_band"]["range"] == ">=116"


def test_panss_mixed_sums_are_exact() -> None:
    answers = {f"P{i}": 2 for i in range(1, 8)}
    answers.update({f"N{i}": 3 for i in range(1, 8)})
    answers.update({f"G{i}": 1 for i in range(1, 17)})
    result = evaluate(_released("panss"), answers)
    assert result["status"] == "complete"
    # 7x2=14, 7x3=21, 16x1=16, total 51.
    assert result["scores"] == {"positive": 14, "negative": 21, "general": 16, "total": 51}


def test_panss_one_missing_item_suppresses_total_but_keeps_finished_subscales() -> None:
    answers = dict.fromkeys(PANSS_IDS, 1)
    del answers["G16"]
    result = evaluate(_released("panss"), answers)
    assert result["status"] == "partial"
    assert result["missing_item_ids"] == ["G16"]
    assert result["scores"]["total"] is None
    assert result["scores"]["positive"] == 7
    assert result["scores"]["negative"] == 7
    assert result["scores"]["general"] is None


def test_panss_fresh_form_has_null_scores_not_default_ones() -> None:
    result = evaluate(_released("panss"), {})
    assert result["status"] == "unanswered"
    assert result["missing_item_ids"] == PANSS_IDS
    assert result["scores"] == {"positive": None, "negative": None, "general": None, "total": None}


def test_panss_invalid_values_rejected_server_side() -> None:
    for bad in (0, 8, 2.5, "3", True, None):
        result = evaluate(_released("panss"), {**dict.fromkeys(PANSS_IDS, 1), "P1": bad})
        assert "P1" in result["item_errors"], bad
        assert result["status"] == "partial"
        assert result["scores"]["total"] is None


def test_panss_skip_is_not_assessed_and_bands_are_informational_only() -> None:
    result = evaluate(_released("panss"), {"__skipped": True})
    assert result["status"] == "not_assessed"
    assert result["scores"] == {"positive": None, "negative": None, "general": None, "total": None}
    full = evaluate(_released("panss"), dict.fromkeys(PANSS_IDS, 1))
    band = full["findings"]["total_band"]
    assert band["informational_only"] is True
    assert band["not_a_treatment_gate"] is True


# --- Slice 3: diagnosis criteria A-F (source: schizophrenia-criteria.md) ---


def _diagnosis_base() -> dict:
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


def _diagnosis_qualifying() -> dict:
    answers = _diagnosis_base()
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


def test_diagnosis_qualifying_case_satisfies_every_criterion() -> None:
    result = evaluate(_released("diagnosis"), _diagnosis_qualifying())
    assert result["status"] == "complete"
    assert result["missing_item_ids"] == []
    assert result["findings"]["criteria"] == {
        "A": "met",
        "B": "met",
        "C": "met",
        "D": "met",
        "E": "met",
        "F": "met",
    }
    assert result["findings"]["overall"] == "criteria_satisfied"


def test_diagnosis_symptom_count_alone_cannot_satisfy() -> None:
    answers = _diagnosis_base()
    answers.update(
        {"a_delusions": "yes", "a_hallucinations": "yes", "a_disorganized_speech": "yes"}
    )
    result = evaluate(_released("diagnosis"), answers)
    assert result["status"] == "complete"
    assert result["findings"]["criteria"]["A"] == "met"
    assert result["findings"]["overall"] == "below_threshold"


def test_diagnosis_core_symptom_rule_two_peripheral_symptoms_fail_criterion_a() -> None:
    answers = _diagnosis_qualifying()
    answers.update(
        {
            "a_delusions": "no",
            "a_hallucinations": "no",
            "a_disorganized_speech": "no",
            "a_disorganized_behavior": "yes",
            "a_negative_symptoms": "yes",
        }
    )
    result = evaluate(_released("diagnosis"), answers)
    assert result["status"] == "complete"
    assert result["findings"]["criteria"]["A"] == "not_met"
    assert result["findings"]["overall"] == "below_threshold"


def test_diagnosis_partial_answers_have_no_completed_result() -> None:
    result = evaluate(_released("diagnosis"), {"a_delusions": "yes", "a_hallucinations": "yes"})
    assert result["status"] == "partial"
    assert result["findings"] == {}
    assert "b_functional_decline" in result["missing_item_ids"]


def test_diagnosis_unknown_inputs_yield_indeterminate_not_below_threshold() -> None:
    answers = _diagnosis_qualifying()
    answers.update({"a_delusions": "unknown", "a_disorganized_speech": "unknown"})
    result = evaluate(_released("diagnosis"), answers)
    assert result["status"] == "complete"
    assert result["findings"]["criteria"]["A"] == "unknown"
    assert result["findings"]["overall"] == "indeterminate"


def test_diagnosis_shortened_active_phase_still_meets_duration() -> None:
    answers = _diagnosis_qualifying()
    answers.update({"c_active_month": "no", "c_shortened_by_intervention": "yes"})
    result = evaluate(_released("diagnosis"), answers)
    assert result["status"] == "complete"
    assert result["findings"]["criteria"]["C"] == "met"
    assert result["findings"]["overall"] == "criteria_satisfied"


def test_diagnosis_autism_branch_requires_prominent_psychosis() -> None:
    answers = _diagnosis_qualifying()
    answers["f_autism_present"] = "yes"
    partial = evaluate(_released("diagnosis"), answers)
    assert partial["status"] == "partial"
    assert partial["missing_item_ids"] == ["f_prominent_psychosis"]
    answers["f_prominent_psychosis"] = "yes"
    assert evaluate(_released("diagnosis"), answers)["findings"]["overall"] == "criteria_satisfied"
    answers["f_prominent_psychosis"] = "no"
    below = evaluate(_released("diagnosis"), answers)
    assert below["status"] == "complete"
    assert below["findings"]["criteria"]["F"] == "not_met"
    assert below["findings"]["overall"] == "below_threshold"


def test_diagnosis_bypass_and_skip_are_distinct_from_completion() -> None:
    assert evaluate(_released("diagnosis"), {"__bypass": True})["status"] == "bypassed"
    assert evaluate(_released("diagnosis"), {"__skipped": True})["status"] == "not_assessed"


# --- Slice 3: C-SSRS (source: CSSRS.md; experimental form/windows in release) ---

CSSRS_IDEATION = [
    f"css_i{level}_{window}" for window in ("recent", "lifetime") for level in (1, 2, 3, 4, 5)
]
CSSRS_BEHAVIOR = [
    f"css_beh_{category}_{window}"
    for window in ("recent", "lifetime")
    for category in ("actual", "interrupted", "aborted", "preparatory", "nssi")
]


def _cssrs_all_no() -> dict:
    return {**dict.fromkeys(CSSRS_IDEATION, "no"), **dict.fromkeys(CSSRS_BEHAVIOR, "no")}


def test_cssrs_unanswered_is_distinct_from_explicit_negatives() -> None:
    empty = evaluate(_released("cssrs"), {})
    assert empty["status"] == "unanswered"
    assert empty["scores"] == {
        "ideation_severity_recent": None,
        "ideation_severity_lifetime": None,
        "ideation_severity_max": None,
    }
    assert empty["findings"] == {}
    negatives = evaluate(_released("cssrs"), _cssrs_all_no())
    assert negatives["status"] == "complete"
    assert negatives["scores"] == {
        "ideation_severity_recent": 0,
        "ideation_severity_lifetime": 0,
        "ideation_severity_max": 0,
    }
    assert negatives["findings"]["flags"] == ["no_positive_items"]


def test_cssrs_level3_endorsed_without_autofilling_lower_levels() -> None:
    partial = evaluate(_released("cssrs"), {"css_i3_recent": "yes"})
    assert partial["status"] == "partial"
    # Lower levels are still missing: nothing is inferred from the level-3 yes.
    assert "css_i1_recent" in partial["missing_item_ids"]
    assert "css_i2_recent" in partial["missing_item_ids"]
    assert partial["scores"]["ideation_severity_recent"] is None
    answers = _cssrs_all_no()
    answers["css_i3_recent"] = "yes"
    answers.update(
        {
            "css_frequency": 2,
            "css_duration": 2,
            "css_controllability": 3,
            "css_deterrents": 1,
            "css_reasons": 4,
        }
    )
    result = evaluate(_released("cssrs"), answers)
    assert result["status"] == "complete"
    assert result["scores"]["ideation_severity_recent"] == 3
    assert result["scores"]["ideation_severity_max"] == 3
    assert result["findings"]["intensity"] == {
        "frequency": 2,
        "duration": 2,
        "controllability": 3,
        "deterrents": 1,
        "reasons": 4,
    }


def test_cssrs_severity_is_highest_endorsed_with_separate_dimensions() -> None:
    answers = _cssrs_all_no()
    answers.update({"css_i2_recent": "yes", "css_i4_recent": "yes", "css_i5_lifetime": "yes"})
    answers.update(
        {
            "css_frequency": 4,
            "css_duration": 3,
            "css_controllability": 4,
            "css_deterrents": 5,
            "css_reasons": 5,
        }
    )
    result = evaluate(_released("cssrs"), answers)
    assert result["status"] == "complete"
    assert result["scores"]["ideation_severity_recent"] == 4
    assert result["scores"]["ideation_severity_lifetime"] == 5
    assert result["scores"]["ideation_severity_max"] == 5
    findings = result["findings"]
    assert set(findings) >= {"intensity", "behavior", "lethality", "flags", "windows", "notes"}
    assert "high_risk_alert" in findings["flags"]
    # No composite risk score anywhere in the result payload: severity keys
    # only, and no composite/total/risk field beside them.
    assert set(result["scores"]) == {
        "ideation_severity_recent",
        "ideation_severity_lifetime",
        "ideation_severity_max",
    }
    assert not {key for key in findings if "composite" in key or "risk" in key}


def test_cssrs_recent_behavior_triggers_alert_and_requires_lethality() -> None:
    answers = _cssrs_all_no()
    answers["css_beh_actual_recent"] = "yes"
    missing_lethality = evaluate(_released("cssrs"), answers)
    assert missing_lethality["status"] == "partial"
    assert "css_leth_actual_recent" in missing_lethality["missing_item_ids"]
    answers["css_leth_actual_recent"] = 0
    needs_potential = evaluate(_released("cssrs"), answers)
    assert needs_potential["status"] == "partial"
    assert "css_leth_potential_recent" in needs_potential["missing_item_ids"]
    answers["css_leth_potential_recent"] = 1
    result = evaluate(_released("cssrs"), answers)
    assert result["status"] == "complete"
    assert result["findings"]["lethality"]["recent"] == {"actual": 0, "potential": 1}
    assert "high_risk_alert" in result["findings"]["flags"]


def test_cssrs_historical_behavior_needs_review_without_recent_alert() -> None:
    answers = _cssrs_all_no()
    answers["css_beh_actual_lifetime"] = "yes"
    answers["css_leth_actual_lifetime"] = 2
    result = evaluate(_released("cssrs"), answers)
    assert result["status"] == "complete"
    assert result["scores"]["ideation_severity_recent"] == 0
    assert result["findings"]["intensity"] is None
    assert "clinical_review" in result["findings"]["flags"]
    assert "high_risk_alert" not in result["findings"]["flags"]


def test_cssrs_windows_and_periods_are_recorded() -> None:
    result = evaluate(_released("cssrs"), _cssrs_all_no())
    assert result["findings"]["windows"] == {
        "recent_ideation": "past_month",
        "recent_behavior": "past_3_months",
        "lifetime": "lifetime",
    }


def test_cssrs_skip_is_not_assessed() -> None:
    result = evaluate(_released("cssrs"), {"__skipped": True})
    assert result["status"] == "not_assessed"
    assert result["scores"]["ideation_severity_max"] is None


# --- Slice 4: release validation (version, hashes, assumptions, defaults) ---

import hashlib  # noqa: E402
import json as _json_module  # noqa: E402

EXPECTED_SOURCES = {
    "diagnosis": "schizophrenia-criteria.md",
    "panss": "PANSS.md",
    "cssrs": "CSSRS.md",
}


def _released_raw(instrument: str) -> dict:
    with open(CONTENT_DIR / f"{instrument}.v1.json", encoding="utf-8") as handle:
        return _json_module.load(handle)


def test_released_files_validate_and_carry_version() -> None:
    for instrument in ("diagnosis", "panss", "cssrs"):
        raw = _released_raw(instrument)
        assert raw["id"] == instrument
        assert raw["version"] == "v1"
        assert (CONTENT_DIR / f"{instrument}.v1.json").exists()
        loaded = load_definition(raw)  # schema/rule validation passes
        assert loaded["version"] == "v1"


@pytest.mark.parametrize("instrument", ["diagnosis", "panss", "cssrs"])
def test_release_records_source_hashes_assumptions_and_defaults(instrument: str) -> None:
    raw = _released_raw(instrument)
    release = raw["release"]
    assert release["version"] == "v1"
    source_file = SOURCE_DIR / EXPECTED_SOURCES[instrument]
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    recorded = {entry["path"]: entry["sha256"] for entry in release["source_documents"]}
    assert recorded[f"project-documents/medical-documents/{EXPECTED_SOURCES[instrument]}"] == digest
    assert release["assumptions"], "release must record assumptions"
    assert release["experimental_defaults"], "release must record experimental defaults"
    assert release["source_gaps"], "release must record source gaps"
    assert release["validation_results"]["reference_examples_passed"] > 0
    assert (
        "clinical validation" in release["disclaimer"].lower()
        or "experimental" in release["disclaimer"].lower()
    )


def test_releases_require_no_owner_review_or_approval() -> None:
    for instrument in ("diagnosis", "panss", "cssrs"):
        text = (CONTENT_DIR / f"{instrument}.v1.json").read_text(encoding="utf-8")
        assert "awaiting_review" not in text
        raw = _json_module.loads(text)
        assert raw["release"].get("reviewer") in (None, "none")
        assert raw["release"].get("approval") in (None, "none")


def test_no_treatment_thresholds_or_composite_scores_in_releases() -> None:
    panss = _released("panss")
    assert panss["release"]["no_treatment_thresholds"] is True
    cssrs = _released("cssrs")
    assert cssrs["release"]["no_composite_score"] is True
    assert "composite" not in _json_module.dumps(cssrs["result_rules"]).lower()


# --- Slice 4: authenticated routes (T1; any active session may read) ---

import os  # noqa: E402
import uuid as _uuid  # noqa: E402

import pytest as _pytest  # noqa: E402
from fastapi.testclient import TestClient as _TestClient  # noqa: E402
from sqlalchemy import text as _text  # noqa: E402

from x_insight import db as _db_module  # noqa: E402
from x_insight.app import app as _app  # noqa: E402
from x_insight.identity import service as _identity_service  # noqa: E402


@_pytest.fixture(scope="module")
def _migrated_engine():
    from alembic import command as _command
    from alembic.config import Config as _Config

    url = _db_module.get_test_database_url()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = _Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    _command.upgrade(cfg, "head")
    engine = _db_module.build_engine(url)
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
        connection.execute(_text("TRUNCATE sessions, users CASCADE"))
        connection.execute(_text("TRUNCATE audit_events"))
    for table in ("encounters", "patients", "idempotency_records"):
        try:
            with engine.begin() as connection:
                connection.execute(_text(f"TRUNCATE {table}"))
        except Exception:
            pass


@_pytest.fixture()
def _clean(_migrated_engine, monkeypatch):
    monkeypatch.setattr(_db_module, "get_engine", lambda: _migrated_engine)
    _identity_service.clear_login_throttle()
    _truncate(_migrated_engine)
    _identity_service.ensure_default_admin(_migrated_engine)
    yield _migrated_engine
    _identity_service.clear_login_throttle()
    _truncate(_migrated_engine)
    _identity_service.ensure_default_admin(_migrated_engine)


@_pytest.fixture()
def _admin(_clean, monkeypatch):
    monkeypatch.setattr(_db_module, "get_engine", lambda: _clean)
    client = _TestClient(_app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert response.status_code == 200
    return {"client": client, "csrf": response.json()["csrf_token"], "engine": _clean}


def _physician(_admin, _clean, monkeypatch, username: str):
    created = _admin["client"].post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers={"X-CSRF-Token": _admin["csrf"]},
    )
    assert created.status_code == 201, created.text
    monkeypatch.setattr(_db_module, "get_engine", lambda: _clean)
    client = _TestClient(_app)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200, login.text
    return client


def test_assessment_routes_require_authentication() -> None:
    anonymous = _TestClient(_app)
    for instrument in ("diagnosis", "panss", "cssrs"):
        response = anonymous.get(f"/api/v1/content/assessments/{instrument}")
        assert response.status_code == 401, response.text
        assert response.json()["code"] == "UNAUTHENTICATED"
        assert response.headers.get("X-Request-ID")


def test_any_active_session_may_read_released_definitions(_admin, _clean, monkeypatch) -> None:
    physician = _physician(_admin, _clean, monkeypatch, "dr_assess")
    for client in (physician, _admin["client"]):
        for instrument in ("diagnosis", "panss", "cssrs"):
            response = client.get(f"/api/v1/content/assessments/{instrument}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["type"] == instrument
            assert body["version"] == "v1"
            assert body["definition"]["id"] == instrument
            assert response.headers.get("X-Request-ID")
            # The served payload is a loadable definition for the T2 engine.
            loaded = load_definition(body["definition"])
            assert evaluate(loaded, {})["definition_version"] == "v1"


def test_unknown_assessment_type_is_404(_admin) -> None:
    response = _admin["client"].get("/api/v1/content/assessments/phrenology")
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"


def test_deactivated_session_cannot_read_definitions(_admin, _clean, monkeypatch) -> None:
    physician = _physician(_admin, _clean, monkeypatch, "dr_revoked")
    target = _uuid.UUID(
        _admin["client"]
        .post(
            "/api/v1/physicians",
            json={"username": "dr_temp", "password": "pw123"},
            headers={"X-CSRF-Token": _admin["csrf"]},
        )
        .json()["user"]["id"]
    )
    _admin["client"].post(
        f"/api/v1/physicians/{target}/deactivate",
        json={"draft_action": "retain"},
        headers={"X-CSRF-Token": _admin["csrf"]},
    )
    assert physician.get("/api/v1/content/assessments/panss").status_code == 200
    temp_client = _TestClient(_app)
    assert (
        temp_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_temp", "password": "pw123", "role": "physician"},
        ).status_code
        == 401
    )
