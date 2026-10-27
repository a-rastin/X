"""Active application order is separate from the historical reviewed bundles."""

import json

import pytest

from tests.models.test_proposal_readiness import known_context
from x_insight import contracts
from x_insight.models.bundles import FOLLOWUP_ORDER, REGISTRATION_ORDER
from x_insight.models.workflow_release import (
    content_root,
    require_current_workflow_contract,
    resolve_workflow,
    workflow_history_fields,
)
from x_insight.reasoning.readiness import compute_proposal_readiness


def test_established_questions_only_belong_to_eligible_followup():
    registration, _ = resolve_workflow("registration")
    assert "established_case_clozapine" not in [p["manifest"]["question_key"] for p in registration]
    assert not any(
        f["question_key"] == "established_case_clozapine"
        for f in workflow_history_fields("registration")
    )
    followup, _ = resolve_workflow("followup", clozapine_eligible=True)
    keys = [p["manifest"]["question_key"] for p in followup]
    assert keys[keys.index("no_improvement_clozapine") - 1] == "established_case_clozapine"
    fields = workflow_history_fields("followup", clozapine_eligible=True)
    assert len([f for f in fields if f["question_key"] == "established_case_clozapine"]) == 15
    ineligible, _ = resolve_workflow("followup")
    assert "established_case_clozapine" not in [p["manifest"]["question_key"] for p in ineligible]


def test_moved_fields_keep_exact_wording_choices_and_no_improvement_is_unchanged():
    original = json.loads(
        (content_root() / "history/history.established_case_clozapine.v1.json").read_text()
    )
    active = [
        field
        for field in workflow_history_fields("followup", clozapine_eligible=True)
        if field["question_key"] == "established_case_clozapine"
    ]
    assert len(active) == 15
    assert [
        {key: field[key] for key in ("source_path", "definition", "permitted", "node_id")}
        for field in active
    ] == [
        {key: field[key] for key in ("source_path", "definition", "permitted", "node_id")}
        for field in original["fields"]
    ]
    eligible, _ = resolve_workflow("followup", clozapine_eligible=True)
    ineligible, _ = resolve_workflow("followup")
    assert next(
        p for p in eligible if p["manifest"]["question_key"] == "no_improvement_clozapine"
    ) == next(p for p in ineligible if p["manifest"]["question_key"] == "no_improvement_clozapine")
    # Historical validation orders continue describing the original reviewed files.
    assert REGISTRATION_ORDER[-1] == "established_case_clozapine"
    assert "established_case_clozapine" not in FOLLOWUP_ORDER


def test_hidden_established_fields_never_block_but_eligible_followup_warns_all_fifteen():
    data = {"history": {"values": known_context("followup")}}
    hidden = compute_proposal_readiness(data, 1, "followup")
    assert hidden["ready"] is True
    assert hidden["required_count"] == 76
    assert hidden["blockers"] == []
    applicable = compute_proposal_readiness(data, 1, "followup", clozapine_eligible=True)
    # Issue-04: missing established context estimates with disclosed
    # uncertainty; it warns without blocking generation.
    assert applicable["ready"] is True
    assert applicable["required_count"] == 91
    assert applicable["blockers"] == []
    assert len(applicable["warnings"]) == 15
    assert all(
        warning["question_key"] == "established_case_clozapine"
        for warning in applicable["warnings"]
    )
    assert {warning["status"] for warning in applicable["warnings"]} == {"missing"}
    # A genuinely blocking established answer still needs clarification.
    blocking_values = {
        **known_context("followup"),
        "candidate/history/established_case_clozapine.v1/h_schizophrenia_established": (
            "not_assessed"
        ),
    }
    blocking = compute_proposal_readiness(
        {"history": {"values": blocking_values}}, 1, "followup", clozapine_eligible=True
    )
    assert blocking["ready"] is False
    assert [(b["source_path"], b["status"]) for b in blocking["blockers"]] == [
        (
            "candidate/history/established_case_clozapine.v1/h_schizophrenia_established",
            "not_assessed",
        )
    ]
    assert len(blocking["warnings"]) == 14
    registration = compute_proposal_readiness(data, 1, "registration", clozapine_eligible=True)
    assert all(
        question["question_key"] != "established_case_clozapine"
        for question in registration["questions"]
    )


def test_active_workflow_pins_reject_old_placement_and_changed_eligibility():
    _, pin = resolve_workflow("followup")
    assert pin["application_workflow_contract_version"] == "application-workflow-2026-10-27-v1"
    require_current_workflow_contract(pin, clozapine_eligible=False)
    for obsolete, code in (
        (
            {
                key: value
                for key, value in pin.items()
                if key != "application_workflow_contract_version"
            },
            "WORKFLOW_INPUT_CONTRACT_CHANGED",
        ),
        ({**pin, "established_clozapine_eligible": True}, "WORKFLOW_ELIGIBILITY_CHANGED"),
    ):
        with pytest.raises(contracts.ContractError) as error:
            require_current_workflow_contract(obsolete, clozapine_eligible=False)
        assert error.value.code == code
