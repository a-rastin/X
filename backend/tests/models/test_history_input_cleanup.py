"""Current History contract and upstream C-SSRS context, without provider calls."""

from __future__ import annotations

import json

import pytest

from tests.assessments.test_cssrs import _all_no, _all_unknown, _level3_complete
from x_insight import contracts
from x_insight.cases.history import compute_history_state, validate_history_values
from x_insight.models.question_package import load_question_package
from x_insight.models.workflow_release import content_root, history_fields, resolve_workflow
from x_insight.reasoning.readiness import compute_proposal_readiness
from x_insight.reasoning.snapshots import build_question_projection, evaluate_gate

LAI = "candidate/history/lai_indication_choice.v1/"
AGGRESSION = "candidate/history/aggression_clozapine.v1/"


def test_current_inventory_and_revised_enum_defaults():
    fields = history_fields()
    assert not any("high_suicide_clozapine" in path for path in fields)
    assert {path.removeprefix(LAI) for path in fields if path.startswith(LAI)} == {
        "h_lai_preference",
        "h_adherence_history",
        "h_discussion_context",
        "h_prior_nms",
        "h_delivery_barriers",
    }
    assert {path.removeprefix(AGGRESSION) for path in fields if path.startswith(AGGRESSION)} == {
        "h_substantial_aggression_risk",
        "h_clozapine_treatment_status",
        "h_willing_to_discuss",
        "h_monitoring_plan",
        "h_medical_review",
    }
    expected = {
        LAI + "h_lai_preference": [
            "PrefersLAI",
            "PrefersOral",
            "Undecided",
            "DeclinesLAI",
            "unknown",
        ],
        LAI + "h_adherence_history": ["Adequate", "Poor", "unknown"],
        LAI + "h_discussion_context": ["Present", "Absent", "unknown"],
        LAI + "h_delivery_barriers": ["Present", "Absent", "unknown"],
        AGGRESSION + "h_substantial_aggression_risk": ["Yes", "No", "unknown"],
    }
    state = compute_history_state({}, revision=1, author_id="synthetic")
    for path, permitted in expected.items():
        assert fields[path]["permitted"] == permitted
        assert state["values"][path] == "unknown"
        assert path not in state["provenance"]
        with pytest.raises(contracts.ContractError):
            validate_history_values({path: "not_assessed"})
    with pytest.raises(contracts.ContractError):
        validate_history_values({LAI + "h_adherence_history": "Uncertain"})


@pytest.mark.parametrize("exposure", ["yes", "no", "unknown"])
def test_legacy_cleanup_removes_retired_values_and_changed_attribution(exposure):
    retired = {
        "candidate/history/high_suicide_clozapine.v1/h_substantial_suicide_risk": "Yes",
        LAI + "h_schizophrenia_established": "Yes",
        LAI + "h_oral_experience": "Supported",
        LAI + "h_product_plan": "Complete",
        LAI + "h_lai_treatment_status": "Current",
        **{
            AGGRESSION + name: "Yes"
            for name in (
                "h_schizophrenia_established",
                "h_persistence_despite_treatment",
                "h_urgent_safety_concern",
                "h_adherence_concern",
                "h_modifiable_targets",
            )
        },
    }
    values = {
        **retired,
        "h_exposure_dopamine_blocker": exposure,
        LAI + "h_prior_nms": "Yes",
        LAI + "h_adherence_history": "Uncertain",
    }
    state = compute_history_state(
        {
            "history": {
                "values": values,
                "provenance": {path: {"source": "copied_baseline"} for path in values},
            }
        },
        revision=2,
        author_id="synthetic",
    )
    assert not retired.keys() & state["values"].keys()
    assert not retired.keys() & state["provenance"].keys()
    assert state["values"][LAI + "h_adherence_history"] == "unknown"
    assert LAI + "h_adherence_history" not in state["provenance"]
    assert (LAI + "h_prior_nms" in state["values"]) == (exposure == "yes")
    assert (LAI + "h_prior_nms" in state["provenance"]) == (exposure == "yes")
    for path, value in retired.items():
        with pytest.raises(contracts.ContractError):
            validate_history_values({path: value})


@pytest.mark.parametrize(
    "answers,ready,projected",
    [
        ({}, True, _all_unknown()),
        ({"__skipped": True}, False, None),
        (
            {"css_i1_recent": "no"},
            True,
            {**_all_unknown(), "css_i1_recent": "no"},
        ),
        ({"css_i1_recent": "invented"}, False, None),
        (_all_no(), True, _all_no()),
        (_level3_complete(), True, _level3_complete()),
        (
            {
                **_all_no(),
                "css_i5_lifetime": "yes",
                "css_frequency": 4,
                "css_beh_actual_recent": "yes",
                "css_leth_actual_recent": 2,
            },
            True,
            _all_no(),
        ),
    ],
)
def test_high_suicide_consumes_released_assessment_without_invented_node_mapping(
    answers, ready, projected
):
    package = resolve_workflow("registration")[0][1]
    manifest = package["manifest"]
    data = {
        "cssrs": {"answers": answers},
        "working_note": "PRIVATE-SENTINEL",
        "history": {
            "values": {
                "candidate/history/high_suicide_clozapine.v1/h_substantial_suicide_risk": "Yes"
            }
        },
    }
    projection, _ = build_question_projection(manifest, data, 3)
    assert projection["variables"] == []
    context = projection["assessment_context"]
    assert context["source_path"] == "synthetic/assessments/cssrs"
    assert context["status"] == ("observed" if ready else "missing")
    assert evaluate_gate(manifest, data)[0] == ("ready" if ready else "needs_clarification")
    assert "PRIVATE-SENTINEL" not in str(projection)
    if ready:
        value = context["value"]
        assert value["answers"] == projected
        assert value["definition_version"] == "v2"
        assert value["evaluation"]["status"] == "complete"
        assert set(value["evaluation"]["scores"]) == {"ideation_severity_recent"}
        assert "behavior" not in value["evaluation"]["findings"]
        assert "lethality" not in value["evaluation"]["findings"]
        assert "intensity" not in value["evaluation"]["findings"]
        assert "css_i1_lifetime" not in value["answers"]
        assert "css_beh_actual_recent" not in value["answers"]
        assert "css_leth_actual_recent" not in value["answers"]
    else:
        assert context["value"] is None
        readiness = compute_proposal_readiness(data, 3, "registration")
        assert any(
            b["source_path"] == "synthetic/assessments/cssrs" and b["label"] == "C-SSRS assessment"
            for b in readiness["blockers"]
        )
    load_question_package(package)


def test_fresh_draft_cssrs_projection_is_complete_v2():
    manifest = resolve_workflow("registration")[0][1]["manifest"]
    projection, _ = build_question_projection(manifest, {}, 1)
    context = projection["assessment_context"]
    assert context["status"] == "observed"
    assert context["value"]["definition_version"] == "v2"
    assert context["value"]["answers"] == _all_unknown()
    assert context["value"]["evaluation"]["status"] == "complete"
    assert context["value"]["evaluation"]["scores"] == {"ideation_severity_recent": 0}
    assert context["value"]["evaluation"]["findings"]["flags"] == []
    assert evaluate_gate(manifest, {})[0] == "ready"


@pytest.mark.parametrize(
    "key", ["high_suicide_clozapine", "lai_indication_choice", "aggression_clozapine"]
)
def test_adapted_inputs_preserve_entire_reviewed_model_and_reject_historical_execution(key):
    package = next(
        p for p in resolve_workflow("registration")[0] if p["manifest"]["question_key"] == key
    )
    original_root = content_root() / f"questions/{key}/s39-reviewed-v1"
    original = json.loads((original_root / "manifest.json").read_text())
    assert package["network_xml"] == (original_root / "network.xml").read_text()
    assert package["template"] == json.loads((original_root / "template.json").read_text())
    assert package["review"] == json.loads((original_root / "review.json").read_text())
    for name in (
        "variables",
        "declared_node_order",
        "cpt_contract",
        "query_nodes",
        "execution_evidence",
    ):
        assert package["manifest"][name] == original[name]
    assert package["manifest"]["execution_evidence"] == {}
    for operation in (
        lambda: build_question_projection(original, {}, 1),
        lambda: evaluate_gate(original, {}),
    ):
        with pytest.raises(contracts.ContractError) as caught:
            operation()
        assert caught.value.code == "HISTORY_INPUT_CONTRACT_CHANGED"


def test_cssrs_change_invalidates_only_high_suicide_question_and_provider_receives_context():
    from x_insight.reasoning.provider import ProviderConfig, ProviderRequest, build_outbound_payload
    from x_insight.reasoning.snapshots import question_input_hash

    packages, _ = resolve_workflow("registration")
    initial = {
        "cssrs": {"answers": {**_all_no(), "__skipped": {"note": "PRIVATE-RESERVED-PROVIDER"}}}
    }
    changed = {
        "cssrs": {"answers": {**_level3_complete(), "__bypass": "PRIVATE-RESERVED-PROVIDER"}}
    }
    notes_only = {**initial, "working_note": "PRIVATE-SENTINEL"}
    affected = []
    for package in packages:
        manifest = package["manifest"]
        _, original_hash = build_question_projection(manifest, initial, 1)
        projection, changed_hash = build_question_projection(manifest, changed, 1)
        _, note_hash = build_question_projection(manifest, notes_only, 1)
        assert original_hash == note_hash
        original_fingerprint = question_input_hash(
            original_hash, evaluate_gate(manifest, initial)[0]
        )
        changed_fingerprint = question_input_hash(changed_hash, evaluate_gate(manifest, changed)[0])
        if original_fingerprint != changed_fingerprint:
            affected.append(manifest["question_key"])
        if manifest["question_key"] == "high_suicide_clozapine":
            payload = build_outbound_payload(
                ProviderConfig(endpoint_url="http://127.0.0.1:1/synthetic", model="synthetic"),
                ProviderRequest(
                    question_key=manifest["question_key"],
                    projection=projection,
                    projection_hash=changed_hash,
                    prompt_version=manifest["prompt_version"],
                    question_run_id="synthetic",
                    batch_id="synthetic",
                    prompt_text=package["prompt"]["text"],
                    network_hash=manifest["network_hash"],
                    network_version=manifest["version"],
                    cpt_contract=manifest["cpt_contract"],
                ),
            )
            outbound = json.loads(payload["messages"][1]["content"])
            assert (
                outbound["projection"]["assessment_context"]["value"]["answers"]
                == _level3_complete()
            )
            assert outbound["network_contract"]["nodes"] == manifest["cpt_contract"]["nodes"]
            assert "PRIVATE-SENTINEL" not in str(payload)
            assert "PRIVATE-RESERVED-PROVIDER" not in str(payload)
    assert affected == ["high_suicide_clozapine"]


@pytest.mark.parametrize("reserved", ["__skipped", "__bypass"])
@pytest.mark.parametrize(
    "forged", ["PRIVATE-RESERVED-SENTINEL", {"note": "PRIVATE-RESERVED-SENTINEL"}]
)
def test_cssrs_reserved_markers_cannot_export_free_text_context(reserved, forged):
    manifest = resolve_workflow("registration")[0][1]["manifest"]
    projection, _ = build_question_projection(
        manifest, {"cssrs": {"answers": {**_all_no(), reserved: forged}}}, 1
    )
    assert "PRIVATE-RESERVED-SENTINEL" not in str(projection)
    context = projection["assessment_context"]
    if context["status"] == "observed":
        assert context["value"]["answers"] == _all_no()


@pytest.mark.parametrize(
    "key",
    ["pharmacotherapy", "high_suicide_clozapine", "lai_indication_choice", "aggression_clozapine"],
)
def test_inline_engineering_packages_can_share_application_question_keys(key):
    from tests.worker.test_snapshots import _synthetic_package

    manifest = _synthetic_package()["manifest"]
    manifest["question_key"] = key
    data = {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}
    projection, _ = build_question_projection(manifest, data, 1)
    assert {entry["node_id"]: entry["value"] for entry in projection["variables"]} == {
        "A": "yes",
        "B": "no",
    }
    assert evaluate_gate(manifest, data)[0] == "ready"
    assert "assessment_context" not in projection


@pytest.mark.parametrize("marker", [None, "invalid", [], {"version": "retired"}])
def test_invalid_application_contract_markers_fail_closed_with_named_error(marker):
    from tests.worker.test_snapshots import _synthetic_package

    manifest = _synthetic_package()["manifest"]
    manifest["question_key"] = "pharmacotherapy"
    manifest["history_input_contract"] = marker
    for operation in (
        lambda: build_question_projection(manifest, {}, 1),
        lambda: evaluate_gate(manifest, {}),
    ):
        with pytest.raises(contracts.ContractError) as caught:
            operation()
        assert caught.value.code == "HISTORY_INPUT_CONTRACT_CHANGED"
