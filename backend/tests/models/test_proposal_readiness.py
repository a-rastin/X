"""Read-only proposal readiness agrees with reviewed execution gates (issue-04)."""

from __future__ import annotations

import pytest

from tests.assessments.test_cssrs import _all_no
from x_insight.cases.history import ALLOWED_FIELDS, compute_history_state
from x_insight.models.workflow_release import history_fields, resolve_workflow
from x_insight.reasoning.readiness import compute_proposal_readiness
from x_insight.reasoning.snapshots import (
    build_question_projection,
    evaluate_gate,
)

FIRST_PATH = "candidate/history/aggression_clozapine.v1/h_clozapine_treatment_status"


def known_context(workflow="registration"):
    """Synthetic answers only; expected readiness/counts are independent literals."""
    fields = history_fields()
    values = {
        path: fields[path]["permitted"][0]
        for package in resolve_workflow(workflow)[0]
        for path in package["manifest"]["applicability"]["required_fields"]
        if path in fields
    }
    if workflow == "registration":
        values["h_exposure_dopamine_blocker"] = "yes"
    if workflow == "followup":
        values["candidate/history/no_improvement_clozapine.v1/h_clozapine_treatment_status"] = (
            "CurrentTreatment"
        )
    return values


def test_complete_core_history_reports_warnings_not_blockers():
    core = {key: spec["permitted"][0] for key, spec in ALLOWED_FIELDS.items()}
    state = compute_history_state(
        {"history": {"values": core}}, revision=54, author_id="synthetic-author"
    )
    assert state["evaluation"]["status"] == "complete"
    readiness = state["proposal_readiness"]
    assert readiness["source_revision"] == 54
    # Issue-04: missing workflow context is a nonblocking warning, not a blocker.
    assert readiness["ready"] is True
    assert readiness["required_count"] == 14
    assert readiness["blockers"] == []
    assert len(readiness["warnings"]) == 13
    assert all(
        item["source_path"] != "synthetic/assessments/cssrs" for item in readiness["warnings"]
    )
    high = next(
        question
        for question in readiness["questions"]
        if question["question_key"] == "high_suicide_clozapine"
    )
    assert high["status"] == "ready"
    assert high["blockers"] == []
    assert high["warnings"] == []
    assert (
        readiness["warnings"][0]["source_path"]
        == "candidate/history/pharmacotherapy.v2/h_physical_vulnerability"
    )
    assert {item["status"] for item in readiness["warnings"]} <= {
        "missing",
        "unknown",
        "null",
        "empty",
    }
    assert all(item["label"] != item["source_path"] for item in readiness["warnings"])
    assert all(item["question_label"] and item["reason"] for item in readiness["warnings"])
    assert len({item["source_path"] for item in readiness["warnings"]}) == 13


@pytest.mark.parametrize(
    "value,status",
    [
        ("unknown", "unknown"),
        (None, "null"),
        ("", "empty"),
        ("   ", "empty"),
    ],
)
def test_nonblocking_missing_allows_estimation_with_typed_missing(value, status):
    values = known_context()
    values[FIRST_PATH] = value
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 54, "registration")
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert len(readiness["warnings"]) == 1
    warning = readiness["warnings"][0]
    assert warning["source_path"] == FIRST_PATH
    assert warning["status"] == status
    assert warning["reason"]
    # Estimation receives typed missing/null context, never a fabricated answer.
    manifest = next(
        p["manifest"]
        for p in resolve_workflow("registration")[0]
        if FIRST_PATH in p["manifest"]["applicability"]["required_fields"]
    )
    projection, _ = build_question_projection(manifest, data, 54)
    entry = next(v for v in projection["variables"] if v["source_path"] == FIRST_PATH)
    assert entry["status"] == "missing"
    assert entry["value"] is None
    assert evaluate_gate(manifest, data)[0] == "ready"


def test_omitted_required_input_is_nonblocking_warning():
    values = known_context()
    del values[FIRST_PATH]
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 54, "registration")
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert [(w["source_path"], w["status"]) for w in readiness["warnings"]] == [
        (FIRST_PATH, "missing")
    ]


@pytest.mark.parametrize(
    "value,status",
    [
        ("not_assessed", "not_assessed"),
        ("Unresolved", "unresolved"),
        ("invented", "invalid"),
        (42, "invalid"),
        ({"value": "Present"}, "invalid"),
        (["Absent", "Present"], "conflict"),
        ("conflict", "invalid"),
    ],
)
def test_blocking_inputs_still_need_clarification(value, status):
    values = known_context()
    values[FIRST_PATH] = value
    readiness = compute_proposal_readiness(
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 54, "registration"
    )
    assert readiness["ready"] is False
    assert readiness["warnings"] == []
    assert len(readiness["blockers"]) == 1
    blocker = readiness["blockers"][0]
    assert blocker["source_path"] == FIRST_PATH
    assert blocker["status"] == status
    assert blocker["reason"]


def test_versioned_requirement_cannot_borrow_unversioned_or_other_question_answer():
    values = known_context()
    del values[FIRST_PATH]
    values["h_clozapine_treatment_status"] = "CurrentTreatment"
    values["candidate/history/established_case_clozapine.v1/h_clozapine_treatment_status"] = (
        "CurrentTreatment"
    )
    readiness = compute_proposal_readiness(
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 3, "registration"
    )
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert [(w["source_path"], w["status"]) for w in readiness["warnings"]] == [
        (FIRST_PATH, "missing")
    ]


@pytest.mark.parametrize("workflow,count", [("registration", 14), ("followup", 76)])
def test_known_context_and_known_false_applicability_are_ready(workflow, count):
    values = known_context(workflow)
    false_field = (
        "candidate/history/aggression_clozapine.v1/h_substantial_aggression_risk"
        if workflow == "registration"
        else "candidate/history/no_improvement_clozapine.v1/h_residual_symptoms"
    )
    values[false_field] = "No"
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 7, workflow)
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert readiness["warnings"] == []
    assert readiness["answered_count"] == readiness["execution_ready_count"] == count
    assert any(question["status"] == "not_applicable" for question in readiness["questions"])
    assert [q["status"] for q in readiness["questions"]] == [
        evaluate_gate(package["manifest"], data)[0] for package in resolve_workflow(workflow)[0]
    ]
    # Uncertain applicability estimates with disclosure: unknown never becomes
    # false and never drops a section.
    values[false_field] = "unknown"
    uncertain = compute_proposal_readiness(data, 8, workflow)
    assert uncertain["ready"] is True
    assert uncertain["blockers"] == []
    assert any(w["source_path"] == false_field for w in uncertain["warnings"])
    assert any(
        q["status"] == "ready"
        for q in uncertain["questions"]
        if any(w["source_path"] == false_field for w in q["warnings"])
    )


def test_one_missing_secondary_input_leaves_primary_ready():
    values = known_context()
    lai_path = "candidate/history/lai_indication_choice.v1/h_lai_preference"
    values[lai_path] = "unknown"
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 9, "registration")
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    pharmacotherapy = readiness["questions"][0]
    assert pharmacotherapy["question_key"] == "pharmacotherapy"
    assert pharmacotherapy["status"] == "ready"
    assert pharmacotherapy["blockers"] == []
    assert pharmacotherapy["warnings"] == []
    lai = next(q for q in readiness["questions"] if q["question_key"] == "lai_indication_choice")
    assert lai["status"] == "ready"
    assert lai["blockers"] == []
    assert [w["source_path"] for w in lai["warnings"]] == [lai_path]


def test_all_optional_registration_context_empty_still_ready():
    values = known_context()
    for path in list(values):
        if path.startswith("candidate/history/"):
            values[path] = "unknown"
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 10, "registration")
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert len(readiness["warnings"]) >= 1
    assert all(q["status"] in ("ready", "not_applicable") for q in readiness["questions"])


def test_followup_lists_every_known_but_invalid_baseline_trial_and_panss_control():
    values = known_context("followup")
    invalid = {
        "no_improvement_clozapine.v1/h_baseline_encounter_ref": "outdated",
        "no_improvement_clozapine.v1/h_baseline_window_validity": "outdated",
        "no_improvement_clozapine.v1/h_clozapine_treatment_status": "NeverTreated",
        "no_improvement_clozapine.v1/h_panss_baseline_status": "missing",
        "no_improvement_clozapine.v1/h_panss_followup_status": "missing",
        "no_improvement_clozapine.v1/h_clozapine_trial_adequate": "No",
        "continue_or_adjust.v1/h_baseline_encounter_ref": "missing",
        "continue_or_adjust.v1/h_baseline_window_validity": "outdated",
    }
    values.update({"candidate/history/" + path: value for path, value in invalid.items()})
    readiness = compute_proposal_readiness(
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 12, "followup"
    )
    assert readiness["ready"] is False
    assert readiness["answered_count"] == 76
    assert readiness["execution_ready_count"] == 68
    assert len(readiness["blockers"]) == 8
    assert {b["source_path"] for b in readiness["blockers"]} == {
        "candidate/history/" + path for path in invalid
    }
    assert {b["status"] for b in readiness["blockers"]} == {"gate_requirement"}
    assert all(b["label"] and b["reason"] for b in readiness["blockers"])


def test_unknown_followup_prerequisites_still_block_without_asserting_met():
    values = known_context("followup")
    values["candidate/history/no_improvement_clozapine.v1/h_baseline_encounter_ref"] = "unknown"
    readiness = compute_proposal_readiness(
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 13, "followup"
    )
    assert readiness["ready"] is False
    assert any(
        b["source_path"] == "candidate/history/no_improvement_clozapine.v1/h_baseline_encounter_ref"
        and b["status"] == "gate_requirement"
        for b in readiness["blockers"]
    )


def test_explicit_unresolved_assessment_is_answered_but_cannot_execute():
    values = known_context("followup")
    path = "candidate/history/tardive_dyskinesia.v1/h_td_assessment"
    values[path] = "Unresolved"
    readiness = compute_proposal_readiness(
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 8, "followup"
    )
    assert readiness["ready"] is False
    assert readiness["answered_count"] == 76
    assert readiness["execution_ready_count"] == 75
    assert [(b["source_path"], b["status"]) for b in readiness["blockers"]] == [
        (path, "unresolved")
    ]


@pytest.mark.parametrize("exposure,count", [("yes", 14), ("no", 12), ("unknown", 12)])
def test_exposure_conditional_treatment_matches_readiness_and_execution(exposure, count):
    treatment = "candidate/history/pharmacotherapy.v2/h_antipsychotic_treatment_context"
    values = known_context()
    values["h_exposure_dopamine_blocker"] = exposure
    values[treatment] = "unknown"
    data = {"history": {"values": values}, "cssrs": {"answers": _all_no()}}
    readiness = compute_proposal_readiness(data, 4, "registration")
    pharmacotherapy = readiness["questions"][0]
    manifest = resolve_workflow("registration")[0][0]["manifest"]
    assert readiness["required_count"] == count
    assert pharmacotherapy["required_count"] == (3 if exposure == "yes" else 2)
    assert pharmacotherapy["status"] == evaluate_gate(manifest, data)[0]
    # Issue-04: unknown treatment context estimates with disclosure.
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    if exposure == "yes":
        assert [(w["source_path"], w["status"]) for w in readiness["warnings"]] == [
            (treatment, "unknown")
        ]
    else:
        assert readiness["warnings"] == []
    assert readiness["execution_ready_count"] == count
