"""Package C: pure typed follow-up-v1 state (no database, no provider).

Covers exact defaults, strict unknown-key/partial-state refusal, forged
metadata/provenance rejection, override priority, conditional validation and
age handling. Integration through public HTTP lives in
``tests/http/test_follow_up_state.py``.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from x_insight import contracts
from x_insight.cases import follow_up as f
from x_insight.cases import follow_up_baseline as b

PATIENT = {"sex": "F", "age": 30, "birth_year": None, "clinical_status": "established"}
PINS = {"workflow_manifest_hash": "abc"}


def _patient(**overrides):
    return {**PATIENT, **overrides}


def _fresh(**carry_overrides):
    baseline = b.project_baseline(None, None, _patient(), lexicomp_version="v1")
    if carry_overrides:
        baseline.setdefault("carry", {}).update(carry_overrides)
    return f.fresh_draft(2026, PINS, baseline)


def _codes(errors):
    return {e["code"] for e in errors}


# --- Exact defaults (spec §3, plan §6.4) ---


def test_fresh_draft_defaults_are_exact():
    draft = _fresh()
    fu = draft["follow_up"]
    assert fu["schema_version"] == "follow-up-state-v1"
    assert fu["visit_year"] == 2026
    assert fu["baseline"]["has_source"] is False
    assert fu["harm"] == {"self": False, "others": False}
    assert draft["panss"]["answers"] == {}
    assert draft["cssrs"]["answers"] == {f"css_i{i}_recent": "no" for i in range(1, 6)}
    assert fu["effects"] == {
        "sedation": "none",
        **{name: False for name in f.EFFECT_BOOLEANS},
    }
    assert fu["considerations"]["recovery"] is None
    assert fu["considerations"]["adherence"] is True
    assert fu["considerations"]["basic_needs_met"] is True
    assert fu["considerations"]["needs_observation"] is False
    assert fu["considerations"]["lai_preference"] is False
    assert all(fu["branches"][name] is None for name in f.BRANCH_ANSWERS)
    assert draft["medications"]["entries"] == []
    assert draft["medications"]["pending"] == []
    # No historical assessment/effect/plan is ever copied as a current answer.
    assert "diagnosis" not in draft
    assert "effects" not in draft


def test_structural_draft_rejects_unknown_partial_and_malformed():
    draft = _fresh()
    with pytest.raises(contracts.ContractError):
        f.structural_draft({**draft, "unexpected_top_key": {}})
    with pytest.raises(contracts.ContractError):
        f.structural_draft({**draft, "follow_up": {"schema_version": "follow-up-state-v1"}})
    bad = deepcopy(draft)
    bad["follow_up"]["history"]["bmi"]["height_cm"] = "NaN"
    with pytest.raises(contracts.ContractError):
        f.structural_draft(bad)
    bad = deepcopy(draft)
    bad["follow_up"]["effects"]["sedation"] = "severe"
    with pytest.raises(contracts.ContractError):
        f.structural_draft(bad)
    bad = deepcopy(draft)
    bad["cssrs"]["answers"]["css_i1_recent"] = "unknown"
    with pytest.raises(contracts.ContractError):
        f.structural_draft(bad)
    # A forged rule result / evaluated factor cannot ride along.
    bad = deepcopy(draft)
    bad["follow_up"]["decision_trace"] = {"terminal": "clozapine"}
    with pytest.raises(contracts.ContractError):
        f.structural_draft(bad)
    # A malformed frozen baseline cannot crash structural validation.
    bad = deepcopy(draft)
    bad["follow_up"]["baseline"]["unmatched_medications"] = "forged"
    with pytest.raises(contracts.ContractError):
        f.structural_draft(bad)


def test_prepare_autosave_rejects_forged_server_owned_metadata():
    stored = _fresh()
    mutations = {
        "schema_version": "follow-up-v2",
        "visit_year": 2025,
        "content_pins": {"workflow_manifest_hash": "forged"},
        "baseline": {"has_source": True},
    }
    for key, value in mutations.items():
        incoming = deepcopy(stored)
        incoming["follow_up"][key] = value
        with pytest.raises(contracts.ContractError):
            f.prepare_autosave(stored, incoming, actor_id="a", recorded_at="t")
    # Inherited answers and override attribution are server-controlled.
    for field, value in (("inherited", True), ("manual_actor_id", "forged")):
        incoming = deepcopy(stored)
        incoming["follow_up"]["history"]["conditions"]["hepatic_failure"][field] = value
        with pytest.raises(contracts.ContractError):
            f.prepare_autosave(stored, incoming, actor_id="a", recorded_at="t")


def test_prepare_autosave_stamps_manual_override_and_keeps_explicit_no():
    stored = _fresh()
    incoming = deepcopy(stored)
    incoming["follow_up"]["history"]["conditions"]["hepatic_failure"]["manual"] = False
    saved = f.prepare_autosave(stored, incoming, actor_id="dr1", recorded_at="2026-01-01T00:00:00Z")
    answer = saved["follow_up"]["history"]["conditions"]["hepatic_failure"]
    assert answer["manual"] is False
    assert answer["manual_actor_id"] == "dr1"
    assert answer["manual_recorded_at"] == "2026-01-01T00:00:00Z"
    # Reset to automatic clears attribution but keeps the explicit No semantics.
    reset = f.prepare_autosave(saved, saved, actor_id="dr1", recorded_at="later")
    assert reset["follow_up"]["history"]["conditions"]["hepatic_failure"]["manual"] is False
    unchanged = deepcopy(saved)
    unchanged["follow_up"]["history"]["conditions"]["hepatic_failure"]["manual"] = None
    cleared = f.prepare_autosave(saved, unchanged, actor_id="dr1", recorded_at="later")
    assert (
        cleared["follow_up"]["history"]["conditions"]["hepatic_failure"]["manual_actor_id"] is None
    )
    assert (
        cleared["follow_up"]["history"]["conditions"]["hepatic_failure"]["manual_recorded_at"]
        is None
    )


def test_valid_autosave_round_trips_and_cross_workflow_is_refused():
    stored = _fresh()
    incoming = deepcopy(stored)
    incoming["panss"]["answers"]["P1"] = 3
    incoming["follow_up"]["considerations"]["recovery"] = "controlled"
    saved = f.prepare_autosave(stored, incoming, actor_id="dr1", recorded_at="t")
    assert saved["panss"]["answers"]["P1"] == 3
    assert saved["follow_up"]["considerations"]["recovery"] == "controlled"
    foreign = deepcopy(stored)
    foreign["new_case"] = {"anything": True}
    with pytest.raises(contracts.ContractError):
        f.structural_draft(foreign)


# --- Conditional validation (spec §3, §4) ---


def test_missing_applicable_values_block_history_and_considerations():
    state = f.evaluate_state(_fresh(), _patient())
    history = _codes(state["errors_by_step"]["history"])
    assert "bmi_category_required" in history
    assert "carried_value_required" in history
    cons = _codes(state["errors_by_step"]["considerations"])
    assert "recovery_required" in cons
    assert "carried_value_required" in cons
    assert state["can_complete"] is False
    # PANSS is optional: an empty instrument never blocks completion.
    assert state["errors_by_step"]["panss"] == []


def _complete():
    """Fill every required applicable input for a female, no-main, no-source draft."""
    draft = _fresh()
    fu = draft["follow_up"]
    h = fu["history"]
    h["psychiatric"]["present"] = False
    h["substances"]["present"] = False
    h["heart_failure"] = False
    h["tobacco"] = False
    h["pregnant"] = False
    h["breastfeeding"] = False
    h["bmi"] = {
        "height_cm": "170",
        "weight_kg": "70",
        "source": "calculated",
        "manual_category": None,
    }
    for name in (
        "focal_neurological_deficit",
        "suspected_brain_lesion",
        "suspected_autoimmune_encephalitis",
    ):
        h["neurological"][name] = False
    fu["considerations"]["recovery"] = "controlled"
    fu["considerations"]["seizure_risk"] = False
    fu["considerations"]["qtc_risk"] = False
    return draft


def test_complete_inputs_pass_and_panss_stays_optional():
    state = f.evaluate_state(_complete(), _patient())
    assert state["errors_by_step"] == {name: [] for name in f.STEPS}
    assert state["can_complete"] is True
    assert state["bmi_category"] == "normal"
    assert state["calculated_bmi"] is not None
    assert state["proposal"] is None  # reading never creates a proposal


def test_conditional_selections_and_antidepressant_confirmation():
    draft = _complete()
    h = draft["follow_up"]["history"]
    h["psychiatric"] = {"present": True, "conditions": ["depression"], "other_text": ""}
    codes = _codes(f.evaluate_state(draft, _patient())["errors_by_step"]["history"])
    assert "antidepressant_confirmation_required" in codes
    h["current_antidepressant"] = False
    assert "antidepressant_confirmation_required" not in _codes(
        f.evaluate_state(draft, _patient())["errors_by_step"]["history"]
    )
    h["psychiatric"]["conditions"] = []
    assert "psychiatric_selection_required" in _codes(
        f.evaluate_state(draft, _patient())["errors_by_step"]["history"]
    )


def test_pending_medication_must_be_resolved():
    baseline = b.project_baseline(None, None, _patient(), lexicomp_version="v1")
    baseline["unmatched_medications"] = [{"source_id": "old:drug", "status": "pending"}]
    draft = f.fresh_draft(2026, PINS, baseline)
    assert draft["medications"]["pending"][0]["resolution"] == "pending"
    state = f.evaluate_state(draft, _patient())
    assert "pending_medication_required" in _codes(state["errors_by_step"]["medications"])
    draft["medications"]["pending"][0]["resolution"] = "removed"
    state = f.evaluate_state(draft, _patient())
    assert "pending_medication_required" not in _codes(state["errors_by_step"]["medications"])
    # A replacement must reference a selected catalog id.
    draft["medications"]["pending"][0] = {
        "source_id": "old:drug",
        "resolution": "replaced",
        "replacement_catalog_drug_id": "not-in-list",
    }
    with pytest.raises(contracts.ContractError):
        f.structural_draft(draft)


def test_override_priority_and_normal_prolactin():
    draft = _complete()
    h = draft["follow_up"]["history"]
    h["conditions"]["hyperprolactinemia"]["inherited"] = True
    h["labs"] = {"present": True, "values": {"prolactin": "30"}}
    state = f.evaluate_state(draft, _patient())
    flag = state["flags"]["hyperprolactinemia"]
    assert flag["suggested"] is True and flag["effective"] is True
    # Explicit current No beats both the abnormal suggestion and inherited Yes.
    h["conditions"]["hyperprolactinemia"]["manual"] = False
    flag = f.evaluate_state(draft, _patient())["flags"]["hyperprolactinemia"]
    assert flag["effective"] is False and flag["mode"] == "manual"
    # No lab, no override, no inherited answer => unresolved, never a "No".
    h["labs"] = {"present": False, "values": {}}
    h["conditions"]["hyperprolactinemia"]["manual"] = None
    h["conditions"]["hyperprolactinemia"]["inherited"] = None
    flag = f.evaluate_state(draft, _patient())["flags"]["hyperprolactinemia"]
    assert flag["established"] is False
    # A current normal prolactin establishes No (but is still not "suggested").
    h["labs"] = {"present": True, "values": {"prolactin": "10"}}
    flag = f.evaluate_state(draft, _patient())["flags"]["hyperprolactinemia"]
    assert flag["established"] is True and flag["effective"] is False


def test_reached_questions_recompute_from_current_sources():
    draft = _complete()
    fu = draft["follow_up"]
    assert f.reached_questions(draft) == []  # no main drug => physician review
    draft["medications"]["entries"] = [{"catalog_drug_id": "oral:x"}]
    fu["treatment"] = {"main_catalog_drug_id": "oral:x", "formulation": "oral"}
    assert "switch_needed" in f.reached_questions(draft)
    fu["treatment"] = {"main_catalog_drug_id": "oral:x", "formulation": "lai"}
    assert f.reached_questions(draft) == []  # physician review
    fu["treatment"] = {"main_catalog_drug_id": "oral:x", "formulation": "oral"}
    fu["considerations"]["recovery"] = "none_or_worse"
    assert f.reached_questions(draft) == ["treatment_resistant"]
    assert "treatment_resistant" in f.reached_questions(draft)
    assert "branch_answer_required" in _codes(
        f.evaluate_state(draft, _patient())["errors_by_step"]["considerations"]
    )
    fu["branches"]["treatment_resistant"] = False
    assert "persistent_harm_risk" in f.reached_questions(draft)
    # An effect taking the oral-model branch suppresses graph questions.
    fu["effects"]["parkinsonism"] = True
    assert f.reached_questions(draft) == []
    assert "branch_answer_required" not in _codes(
        f.evaluate_state(draft, _patient())["errors_by_step"]["considerations"]
    )


def test_age_uses_frozen_visit_year_not_inferred_birth_year():
    draft = _fresh()
    assert f.evaluate_state(draft, _patient())["age"] == 30
    assert f.evaluate_state(draft, _patient(birth_year=1990))["age"] == 36
    assert draft["follow_up"]["visit_year"] == 2026


def test_config_has_no_credentials_and_pins_hashes():
    cfg = f.config()
    assert cfg["workflow_version"] == "follow-up-v1"
    assert cfg["steps"] == list(f.STEPS)
    assert cfg["definitions"]["recovery"] == list(f.RECOVERY)
    assert cfg["defaults"]["cssrs_recent"] == "no"
    assert cfg["hashes"]["workflow_manifest_hash"]
    assert "lab_fields" in cfg and "lai_equivalences" in cfg
    serialized = contracts.canonical_json(cfg).decode()
    assert "password" not in serialized and "api_key" not in serialized
