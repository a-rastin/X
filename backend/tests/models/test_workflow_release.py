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
        **{
            path: fields[path]["permitted"][0]
            for path in manifest["applicability"]["required_fields"]
            if path in fields
        },
        "h_exposure_dopamine_blocker": "yes",
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
        normalized = validate_history_values(values)
        assert all(
            normalized.get(path) == value
            for path, value in values.items()
            if not path.endswith("/h_antipsychotic_treatment_context")
        )
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
    manifest = resolve_workflow("registration")[0][3]["manifest"]
    values = _known_values(manifest)
    prefix = "candidate/history/aggression_clozapine.v1/"
    values[prefix + "h_substantial_aggression_risk"] = "No"
    data = {"history": {"values": values}, "gate": "true"}
    assert evaluate_gate(manifest, data)[0] == "not_applicable"
    values[prefix + "h_monitoring_plan"] = "unknown"
    assert evaluate_gate(manifest, data)[0] == "needs_clarification"
    values[prefix + "h_monitoring_plan"] = "Yes"
    values[prefix + "h_substantial_aggression_risk"] = "Yes"
    assert evaluate_gate(manifest, data)[0] == "ready"
    values[prefix + "h_monitoring_plan"] = "invented"
    projection, _ = build_question_projection(manifest, data, 1)
    assert (
        next(
            v for v in projection["variables"] if v["node_id"] == "ClozapineMonitoringPlanAvailable"
        )["status"]
        == "missing"
    )
    assert evaluate_gate(manifest, data)[0] == "needs_clarification"


@pytest.mark.parametrize(
    "value,status",
    [("unknown", "missing"), ("not_assessed", "missing"), (["Absent", "Present"], "conflict")],
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
        ("lai_indication_choice", None, None),
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


@pytest.mark.parametrize("exposure,required", [("yes", 3), ("no", 2), ("unknown", 2)])
def test_active_pharmacotherapy_omits_retired_and_inapplicable_inputs(exposure, required):
    package, pin = resolve_workflow("registration")
    manifest = package[0]["manifest"]
    prefix = "candidate/history/pharmacotherapy.v2/"
    values = {
        "h_exposure_dopamine_blocker": exposure,
        prefix + "h_physical_vulnerability": "yes",
        prefix + "h_substance_contribution": "no",
        prefix + "h_antipsychotic_treatment_context": "Clozapine",
        prefix + "h_prior_treatment_difficulty": "Present",
        prefix + "h_preference_barrier": "Present",
        prefix + "h_exposure_modifier": "Present",
        prefix + "h_trial_duration_review": "Elapsed",
        prefix + "h_urgent_safety_concern": "Present",
        prefix + "h_monitoring_gap": "Present",
    }
    data = {"history": {"values": values}}
    projection, _ = build_question_projection(manifest, data, 7)
    assert {entry["node_id"]: entry["value"] for entry in projection["variables"]} == {
        "PhysicalVulnerability": "Present",
        "SubstanceContribution": "Absent",
        **({"OralAntipsychoticMedication": "Clozapine"} if exposure == "yes" else {}),
    }
    assert len(projection["variables"]) == required
    assert evaluate_gate(manifest, data)[0] == "ready"
    assert pin["history_input_contract_version"] == "history-inputs-2026-10-22-v3"
    assert manifest["review_status"] == "awaiting_review"


def test_active_contract_preserves_full_network_estimation_and_historical_package():
    import json

    from x_insight.models.question_package import load_question_package

    package = resolve_workflow("registration")[0][0]
    directory = content_root() / "questions/pharmacotherapy/s39-reviewed-v1"
    original = json.loads((directory / "manifest.json").read_text())
    manifest = package["manifest"]
    assert package["network_xml"] == (directory / "network.xml").read_text()
    assert package["template"] == json.loads((directory / "template.json").read_text())
    assert package["review"] == json.loads((directory / "review.json").read_text())
    assert manifest["declared_node_order"] == original["declared_node_order"]
    assert manifest["variables"] == original["variables"]
    assert manifest["execution_evidence"] == original["execution_evidence"] == {}
    assert manifest["cpt_contract"] == original["cpt_contract"]
    assert manifest["query_nodes"] == original["query_nodes"]
    assert len(original["patient_mappings"]) == 9
    assert len(manifest["patient_mappings"]) == 3
    from x_insight.reasoning.provider import ProviderConfig, ProviderRequest, build_outbound_payload

    payload = build_outbound_payload(
        ProviderConfig(endpoint_url="http://127.0.0.1:1/synthetic", model="synthetic"),
        ProviderRequest(
            question_key="pharmacotherapy",
            projection={"variables": []},
            projection_hash="0" * 64,
            prompt_version=manifest["prompt_version"],
            question_run_id="synthetic-run",
            batch_id="synthetic-batch",
            prompt_text=package["prompt"]["text"],
            network_hash=manifest["network_hash"],
            network_version=manifest["version"],
            cpt_contract=manifest["cpt_contract"],
        ),
    )
    outbound = json.loads(payload["messages"][1]["content"])
    assert outbound["network_contract"] == {"nodes": original["cpt_contract"]["nodes"]}
    table_schema = payload["response_format"]["json_schema"]["schema"]["properties"]["tables"]
    assert (
        table_schema["minItems"] == table_schema["maxItems"] == len(original["declared_node_order"])
    )
    loaded = load_question_package(package)
    assert list(loaded.declared_node_order) == original["declared_node_order"]


@pytest.mark.parametrize("damage", ["active_changed", "other_missing", "other_invalid"])
def test_history_inventory_fails_closed_after_cached_definition_damage(
    tmp_path, monkeypatch, damage
):
    shutil.copytree(content_root() / "history", tmp_path / "history")
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(tmp_path))
    history_fields()
    if damage == "active_changed":
        path = tmp_path / "history/history.pharmacotherapy.v3.json"
        path.write_text(path.read_text() + " ")
    else:
        path = tmp_path / "history/history.established_case_clozapine.v1.json"
        if damage == "other_missing":
            path.unlink()
        else:
            path.write_text("{")
    with pytest.raises(contracts.ContractError) as caught:
        history_fields()
    assert caught.value.code == "WORKFLOW_CONTENT_UNAVAILABLE"


def test_frozen_historical_pharmacotherapy_cannot_execute_retired_contract():
    import json

    old = json.loads(
        (content_root() / "questions/pharmacotherapy/s39-reviewed-v1/manifest.json").read_text()
    )
    for operation in [
        lambda: evaluate_gate(old, {}),
        lambda: build_question_projection(old, {}, 7),
    ]:
        with pytest.raises(contracts.ContractError) as caught:
            operation()
        assert caught.value.code == "HISTORY_INPUT_CONTRACT_CHANGED"
