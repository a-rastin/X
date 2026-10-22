"""Read-only proposal readiness agrees with reviewed execution gates."""

from __future__ import annotations

import pytest

from x_insight.cases.history import ALLOWED_FIELDS, compute_history_state
from x_insight.models.workflow_release import history_fields, resolve_workflow
from x_insight.reasoning.readiness import compute_proposal_readiness
from x_insight.reasoning.snapshots import evaluate_gate

FIRST_PATH = "candidate/history/pharmacotherapy.v2/h_prior_treatment_difficulty"


def known_context(workflow="registration"):
    """Synthetic answers only; expected readiness/counts are independent literals."""
    fields = history_fields()
    values = {
        path: fields[path]["permitted"][0]
        for package in resolve_workflow(workflow)[0]
        for path in package["manifest"]["applicability"]["required_fields"]
    }
    if workflow == "followup":
        values["candidate/history/no_improvement_clozapine.v1/h_clozapine_treatment_status"] = (
            "CurrentTreatment"
        )
    return values


def test_complete_core_history_still_lists_all_53_proposal_blockers():
    core = {key: spec["permitted"][0] for key, spec in ALLOWED_FIELDS.items()}
    state = compute_history_state(
        {"history": {"values": core}}, revision=54, author_id="synthetic-author"
    )
    assert state["evaluation"]["status"] == "complete"
    readiness = state["proposal_readiness"]
    assert readiness["source_revision"] == 54
    assert readiness["ready"] is False
    assert readiness["required_count"] == 53
    assert readiness["answered_count"] == readiness["execution_ready_count"] == 0
    assert len(readiness["blockers"]) == 53
    assert readiness["blockers"][0]["source_path"] == FIRST_PATH
    assert {item["status"] for item in readiness["blockers"]} == {"missing"}
    assert all(item["label"] != item["source_path"] for item in readiness["blockers"])
    assert all(item["question_label"] and item["reason"] for item in readiness["blockers"])
    assert len({item["source_path"] for item in readiness["blockers"]}) == 53


@pytest.mark.parametrize(
    "value,status,answered",
    [
        ("unknown", "unknown", 53),
        ("not_assessed", "not_assessed", 53),
        (None, "null", 52),
        ("invented", "invalid", 52),
        (42, "invalid", 52),
        ({"value": "Present"}, "invalid", 52),
        (["Absent", "Present"], "conflict", 52),
        ("conflict", "invalid", 52),
    ],
)
def test_uncertainty_is_reported_without_counting_it_execution_ready(value, status, answered):
    values = known_context()
    values[FIRST_PATH] = value
    readiness = compute_proposal_readiness({"history": {"values": values}}, 54, "registration")
    assert readiness["ready"] is False
    assert readiness["answered_count"] == answered
    assert readiness["execution_ready_count"] == 52
    assert len(readiness["blockers"]) == 1
    blocker = readiness["blockers"][0]
    assert blocker["source_path"] == FIRST_PATH
    assert blocker["status"] == status
    assert blocker["reason"]


def test_versioned_requirement_cannot_borrow_unversioned_or_other_question_answer():
    values = known_context()
    del values[FIRST_PATH]
    values["h_prior_treatment_difficulty"] = "Absent"
    values["candidate/history/pharmacotherapy.v1/h_prior_treatment_difficulty"] = "Absent"
    values["candidate/history/high_suicide_clozapine.v1/h_prior_treatment_difficulty"] = "Absent"
    readiness = compute_proposal_readiness({"history": {"values": values}}, 3, "registration")
    assert readiness["ready"] is False
    assert readiness["execution_ready_count"] == 52
    assert [(b["source_path"], b["status"]) for b in readiness["blockers"]] == [
        (FIRST_PATH, "missing")
    ]


@pytest.mark.parametrize("workflow,count", [("registration", 53), ("followup", 76)])
def test_known_context_and_known_false_applicability_are_ready(workflow, count):
    values = known_context(workflow)
    false_field = (
        "candidate/history/high_suicide_clozapine.v1/h_substantial_suicide_risk"
        if workflow == "registration"
        else "candidate/history/no_improvement_clozapine.v1/h_residual_symptoms"
    )
    values[false_field] = "No"
    data = {"history": {"values": values}}
    readiness = compute_proposal_readiness(data, 7, workflow)
    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert readiness["answered_count"] == readiness["execution_ready_count"] == count
    assert any(question["status"] == "not_applicable" for question in readiness["questions"])
    assert [q["status"] for q in readiness["questions"]] == [
        evaluate_gate(package["manifest"], data)[0] for package in resolve_workflow(workflow)[0]
    ]
    # A false gate never makes unresolved required answers execution ready.
    values[false_field] = "unknown"
    blocked = compute_proposal_readiness(data, 8, workflow)
    assert blocked["ready"] is False
    assert any(b["source_path"] == false_field for b in blocked["blockers"])


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
    readiness = compute_proposal_readiness({"history": {"values": values}}, 12, "followup")
    assert readiness["ready"] is False
    assert readiness["answered_count"] == 76
    assert readiness["execution_ready_count"] == 68
    assert len(readiness["blockers"]) == 8
    assert {b["source_path"] for b in readiness["blockers"]} == {
        "candidate/history/" + path for path in invalid
    }
    assert {b["status"] for b in readiness["blockers"]} == {"gate_requirement"}
    assert all(b["label"] and b["reason"] for b in readiness["blockers"])


def test_explicit_unresolved_assessment_is_answered_but_cannot_execute():
    values = known_context("followup")
    path = "candidate/history/tardive_dyskinesia.v1/h_td_assessment"
    values[path] = "Unresolved"
    readiness = compute_proposal_readiness({"history": {"values": values}}, 8, "followup")
    assert readiness["ready"] is False
    assert readiness["answered_count"] == 76
    assert readiness["execution_ready_count"] == 75
    assert [(b["source_path"], b["status"]) for b in readiness["blockers"]] == [
        (path, "unresolved")
    ]
