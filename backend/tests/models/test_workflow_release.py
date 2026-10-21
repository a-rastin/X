"""Reviewed production packages, typed inputs and fail-closed release pins."""

from __future__ import annotations

import shutil

import pytest

from x_insight import contracts
from x_insight.cases.history import validate_history_values
from x_insight.models import bundles
from x_insight.models.workflow_release import content_root, history_fields, resolve_workflow
from x_insight.reasoning.snapshots import build_question_projection, evaluate_gate


def _known_values(manifest):
    fields = history_fields()
    return {
        path: fields[path]["permitted"][0] for path in manifest["applicability"]["required_fields"]
    }


@pytest.mark.parametrize(
    "workflow,order",
    [
        ("registration", bundles.REGISTRATION_ORDER),
        ("followup", bundles.FOLLOWUP_ORDER),
    ],
)
def test_reviewed_release_projects_all_packages_and_pins_order(workflow, order):
    packages, pin = resolve_workflow(workflow)
    assert tuple(p["manifest"]["question_key"] for p in packages) == order
    assert pin["release_version"] == "2026-10-22"
    for package in packages:
        manifest = package["manifest"]
        values = _known_values(manifest)
        assert validate_history_values(values) == values
        projection, _ = build_question_projection(manifest, {"history": {"values": values}}, 7)
        assert all(entry["status"] == "observed" for entry in projection["variables"])
        assert all(entry["source_revision"] == 7 for entry in projection["variables"])
        empty, _ = build_question_projection(manifest, {}, 1)
        assert all(entry["status"] == "missing" for entry in empty["variables"])
        assert evaluate_gate(manifest, {})[0] == "needs_clarification"


def test_versioned_history_namespaces_never_borrow_same_named_answers():
    packages, _ = resolve_workflow("registration")
    pharmacotherapy, suicide = packages[:2]
    values = _known_values(pharmacotherapy["manifest"])
    values["h_urgent_safety_concern"] = "Present"  # legacy/unversioned input is unrelated
    projection, _ = build_question_projection(
        suicide["manifest"], {"history": {"values": values}}, 1
    )
    assert all(entry["status"] == "missing" for entry in projection["variables"])


def test_reviewed_gate_uses_per_question_facts_and_unknown_precedes_false():
    packages, _ = resolve_workflow("registration")
    manifest = packages[1]["manifest"]
    values = _known_values(manifest)
    prefix = "candidate/history/high_suicide_clozapine.v1/"
    values[prefix + "h_schizophrenia_established"] = "No"
    data = {"history": {"values": values}, "gate": "true"}
    assert evaluate_gate(manifest, data)[0] == "not_applicable"
    values[prefix + "h_urgent_safety_concern"] = "unknown"
    assert evaluate_gate(manifest, data)[0] == "needs_clarification"
    values[prefix + "h_urgent_safety_concern"] = "Present"
    values[prefix + "h_schizophrenia_established"] = "Yes"
    assert evaluate_gate(manifest, data)[0] == "ready"
    values[prefix + "h_urgent_safety_concern"] = "invented"
    projection, _ = build_question_projection(manifest, data, 1)
    assert (
        next(v for v in projection["variables"] if v["node_id"] == "UrgentSafetyConcern")["status"]
        == "missing"
    )
    assert evaluate_gate(manifest, data)[0] == "needs_clarification"


@pytest.mark.parametrize(
    "value,status",
    [("unknown", "missing"), ("not_assessed", "not_assessed"), (["Absent", "Present"], "conflict")],
)
def test_history_unknown_and_conflicts_are_typed(value, status):
    manifest = resolve_workflow("registration")[0][0]["manifest"]
    path = manifest["patient_mappings"][0]["allowed_source_paths"][0]
    projection, _ = build_question_projection(manifest, {"history": {"values": {path: value}}}, 1)
    assert projection["variables"][0]["status"] == status
    assert projection["variables"][0]["value"] is None


def test_release_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    shutil.copytree(
        content_root(), tmp_path / "content", ignore=shutil.ignore_patterns("ddi", "medications")
    )
    network = tmp_path / "content/questions/pharmacotherapy/s39-reviewed-v1/network.xml"
    network.write_text(network.read_text() + " ")
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(tmp_path / "content"))
    with pytest.raises(contracts.ContractError) as caught:
        resolve_workflow("registration")
    assert caught.value.code == "WORKFLOW_CONTENT_UNAVAILABLE"


@pytest.mark.parametrize(
    "key,false_field,false_value",
    [
        ("pharmacotherapy", None, None),
        ("high_suicide_clozapine", "h_substantial_suicide_risk", "No"),
        ("lai_indication_choice", "h_schizophrenia_established", "No"),
        ("aggression_clozapine", "h_substantial_aggression_risk", "No"),
        ("established_case_clozapine", "h_schizophrenia_established", "No"),
        ("tardive_dyskinesia", "h_td_assessment", "Absent"),
        ("akathisia", "h_ak_assessment", "Absent"),
        ("parkinsonism", "h_p_assessment", "Absent"),
        ("acute_dystonia", "h_ad_episode_phase", "NoEpisode"),
        ("no_improvement_clozapine", "h_residual_symptoms", "No"),
        ("continue_or_adjust", None, None),
    ],
)
def test_each_reviewed_applicability_truth_table(key, false_field, false_value):
    workflow = "registration" if key in bundles.REGISTRATION_ORDER else "followup"
    manifest = next(
        p["manifest"] for p in resolve_workflow(workflow)[0] if p["manifest"]["question_key"] == key
    )
    values = _known_values(manifest)
    if key == "no_improvement_clozapine":
        overrides = {
            "h_clozapine_treatment_status": "CurrentTreatment",
            "h_residual_symptoms": "Yes",
        }
        values.update(
            {
                path: overrides[path.rsplit("/", 1)[-1]]
                for path in values
                if path.rsplit("/", 1)[-1] in overrides
            }
        )
    data = {"history": {"values": values}, "gate": "false"}
    # Reviewed per-question facts take precedence over the legacy shared gate.
    assert evaluate_gate(manifest, data)[0] == "ready"
    if false_field:
        path = next(path for path in values if path.endswith("/" + false_field))
        values[path] = false_value
        assert evaluate_gate(manifest, data)[0] == "not_applicable"
    values[next(iter(values))] = "not_assessed"
    assert evaluate_gate(manifest, data)[0] == "needs_clarification"
