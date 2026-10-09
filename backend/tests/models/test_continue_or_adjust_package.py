"""S38 candidate content through the public T5 package and inference seams.

Shape-valid drafts remain unapproved; clinical examples never validate themselves.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from x_insight.models.inference import (
    build_effective_artifact,
    infer_effective,
    replay,
    validate_cpts,
)
from x_insight.models.question_package import load_question_package, validate_question_package
from x_insight.models.validation import validate

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "content/questions/continue_or_adjust"
PREFIX = "candidate/history/continue_or_adjust.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_schizophrenia_established",
    f"{PREFIX}h_symptom_improvement_current",
    f"{PREFIX}h_urgent_safety_concern",
    f"{PREFIX}h_residual_clinical_burden",
    f"{PREFIX}h_metabolic_concern",
    f"{PREFIX}h_other_tolerability_concern",
    f"{PREFIX}h_continuation_preference",
    f"{PREFIX}h_current_agent_available",
    f"{PREFIX}h_lai_under_consideration",
    f"{PREFIX}h_same_agent_lai_available",
    f"{PREFIX}h_baseline_encounter_ref",
    f"{PREFIX}h_baseline_window_validity",
)
SOURCE_BN06 = ROOT / "project-documents/bayesian-networks/BN-06.xml"
SOURCE_BN04 = ROOT / "project-documents/bayesian-networks/BN-04.xml"
SOURCE_BN05 = ROOT / "project-documents/bayesian-networks/BN-05.xml"
DECLARED_ORDER = (
    "SchizophreniaEstablished",
    "SymptomImprovementOnCurrentAntipsychotic",
    "UrgentSafetyConcern",
    "ResidualClinicalBurden",
    "MetabolicConcern",
    "OtherTolerabilityConcern",
    "SameAntipsychoticContinuationPreference",
    "CurrentAgentAvailable",
    "LAIUnderConsideration",
    "SameAgentLAIAvailable",
    "SameAntipsychoticContinuationScope",
    "LAIAgentMismatch",
    "TolerabilityReview",
    "ClinicalChangeReason",
    "PracticalChangeReason",
    "SameAgentDiscussion",
    "ObservedSameAntipsychoticStrategy",
    "SwitchMonitoringContext",
    "AssignedRegimenDiscontinued",
    "ClinicalDestabilization",
    "MetabolicTrajectory",
)
QUERY_NODES = (
    "SameAntipsychoticContinuationScope",
    "SameAgentDiscussion",
    "ObservedSameAntipsychoticStrategy",
    "TolerabilityReview",
    "ClinicalChangeReason",
    "PracticalChangeReason",
    "SwitchMonitoringContext",
    "MetabolicTrajectory",
    "AssignedRegimenDiscontinued",
    "ClinicalDestabilization",
)


@pytest.fixture(autouse=True, params=("s38-v1", "s38-v2"))
def package_revision(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    path = ROOT / "content/questions/continue_or_adjust"
    if request.param == "s38-v2":
        path /= "s38-v2"
    monkeypatch.setitem(globals(), "PACKAGE", path)


def _revision() -> str:
    return "s38-v2" if PACKAGE.name == "s38-v2" else "s38-v1"


def _read_package() -> tuple[dict[str, Any], bytes]:
    package = {
        name: json.loads((PACKAGE / f"{name}.json").read_text())
        for name in ("manifest", "template", "examples", "review")
    }
    package["prompt"] = {
        "version": package["manifest"]["prompt_version"],
        "text": (PACKAGE / "prompt.txt").read_text(),
    }
    return package, (PACKAGE / "network.xml").read_bytes()


def test_continue_or_adjust_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "continue_or_adjust"
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["version"] == _revision()
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}
    assert tuple(package["manifest"]["query_nodes"]) == QUERY_NODES


def test_continue_or_adjust_source_fidelity_is_auditable_without_clinical_probability_claims() -> (
    None
):
    package, source = _read_package()
    original06 = validate(SOURCE_BN06.read_bytes())
    assert (
        original06.source_sha256
        == "e29107fa432b29ca082a66dcae6aae7b3ecdf8aad00f9610c699c6da61518f96"
    )
    assert (
        validate(SOURCE_BN04.read_bytes()).source_sha256
        == "5e059514127a618ac887a04a04180afa1f8b768fa2a5b27f34a3eb13fc5a8e05"
    )
    assert (
        validate(SOURCE_BN05.read_bytes()).source_sha256
        == "27b92883167c639859f99db7b5d7610862d90f11d69d0d34ff581e76c22e3fac"
    )
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    assert [v.name for v in original06.networks[0].variables] == list(DECLARED_ORDER)
    expected_states = {
        "SchizophreniaEstablished": ("No", "Yes"),
        "SymptomImprovementOnCurrentAntipsychotic": ("No", "Yes"),
        "UrgentSafetyConcern": ("Absent", "Present"),
        "ResidualClinicalBurden": ("No", "Yes"),
        "MetabolicConcern": ("No", "Yes"),
        "OtherTolerabilityConcern": ("No", "Yes"),
        "SameAntipsychoticContinuationPreference": (
            "ContinueSame",
            "DiscussChange",
            "NoExpressedPreference",
        ),
        "CurrentAgentAvailable": ("No", "Yes"),
        "LAIUnderConsideration": ("No", "Yes"),
        "SameAgentLAIAvailable": ("No", "Yes"),
        "SameAntipsychoticContinuationScope": (
            "OutsideScope",
            "WithinScope",
            "UrgentSeparateAssessment",
        ),
        "LAIAgentMismatch": ("No", "Yes"),
        "TolerabilityReview": ("No", "Yes"),
        "ClinicalChangeReason": ("No", "Yes"),
        "PracticalChangeReason": ("No", "Yes"),
        "SameAgentDiscussion": (
            "OutsideScope",
            "ContinuationDiscussion",
            "IndividualizedChangeReview",
            "UrgentSeparateAssessment",
        ),
        "ObservedSameAntipsychoticStrategy": (
            "ContinueSameAgent",
            "SwitchAgent",
            "OtherStrategy",
        ),
        "SwitchMonitoringContext": (
            "RoutineReview",
            "SwitchMonitoringDiscussion",
            "SeparatePlanReview",
        ),
        "AssignedRegimenDiscontinued": ("No", "Yes"),
        "ClinicalDestabilization": ("No", "Yes"),
        "MetabolicTrajectory": ("Improved", "NoMeaningfulChange", "Worsened"),
    }
    expected_parents = {
        "SchizophreniaEstablished": (),
        "SymptomImprovementOnCurrentAntipsychotic": (),
        "UrgentSafetyConcern": (),
        "ResidualClinicalBurden": (),
        "MetabolicConcern": (),
        "OtherTolerabilityConcern": (),
        "SameAntipsychoticContinuationPreference": (),
        "CurrentAgentAvailable": (),
        "LAIUnderConsideration": (),
        "SameAgentLAIAvailable": (),
        "SameAntipsychoticContinuationScope": (
            "SchizophreniaEstablished",
            "SymptomImprovementOnCurrentAntipsychotic",
            "UrgentSafetyConcern",
        ),
        "LAIAgentMismatch": ("LAIUnderConsideration", "SameAgentLAIAvailable"),
        "TolerabilityReview": ("MetabolicConcern", "OtherTolerabilityConcern"),
        "ClinicalChangeReason": ("ResidualClinicalBurden", "TolerabilityReview"),
        "PracticalChangeReason": ("CurrentAgentAvailable", "LAIAgentMismatch"),
        "SameAgentDiscussion": (
            "SameAntipsychoticContinuationScope",
            "ClinicalChangeReason",
            "PracticalChangeReason",
            "SameAntipsychoticContinuationPreference",
        ),
        "ObservedSameAntipsychoticStrategy": (
            "ClinicalChangeReason",
            "PracticalChangeReason",
            "SameAntipsychoticContinuationPreference",
        ),
        "SwitchMonitoringContext": ("ObservedSameAntipsychoticStrategy",),
        "AssignedRegimenDiscontinued": (
            "ObservedSameAntipsychoticStrategy",
            "SameAntipsychoticContinuationPreference",
        ),
        "ClinicalDestabilization": (
            "ObservedSameAntipsychoticStrategy",
            "ResidualClinicalBurden",
        ),
        "MetabolicTrajectory": ("ObservedSameAntipsychoticStrategy", "MetabolicConcern"),
    }
    retained = {v.name: v for v in original06.networks[0].variables}
    definitions = {d.for_node: d for d in actual.definitions}
    for variable in actual.variables:
        assert variable.states == expected_states[variable.name]
        assert variable.states == retained[variable.name].states
        expected_proposed = tuple(
            p.removeprefix("proposed_parent=")
            for p in retained[variable.name].properties
            if p.startswith("proposed_parent=")
        )
        if expected_proposed:
            assert definitions[variable.name].parents == expected_proposed
        assert definitions[variable.name].parents == expected_parents[variable.name]

    review = package["review"]
    assert review.get("source_diff") == {
        "removed_nodes": [],
        "removed_edges": [],
        "renamed": {},
        "materialized_proposed_parents": True,
        "state_extensions": {},
        "retained_variables": list(DECLARED_ORDER),
    }
    audit = review["reference_table_audit"]
    assert audit["compared_cells"] == 0
    assert audit["mismatches"] == []
    assert audit["clinical_probability_claim"] is False
    assert "13 Table-6" in review["source_comparison_details"]["bn_parameter_inventory"]
    assert "not reused" in review["source_comparison_details"]["bn_parameter_inventory"]
    assert "0 source-lookup" in review["source_comparison_details"]["bn_parameter_inventory"]

    # BN-04/05 stay background only: no runtime merge of three models.
    assert (
        "never merged at runtime"
        in package["manifest"]["missingness_contract"]["no_runtime_merge"].lower()
    )
    assert "background" in review["bn_comparison"]["BN-04_Antipsychotic_Treatment_Review"].lower()
    assert (
        "background only"
        in review["bn_comparison"]["BN-05_Continuing_Antipsychotic_Treatment"].lower()
    )
    assert (
        "no runtime merge" in review["bn_comparison"]["BN-06_Continuing_Same_Antipsychotic"].lower()
    )
    background04 = {v.name for v in validate(SOURCE_BN04.read_bytes()).networks[0].variables}
    background05 = {v.name for v in validate(SOURCE_BN05.read_bytes()).networks[0].variables}
    assert len(background04) == 32
    assert len(background05) == 20
    assert "OralAntipsychoticMedication" in background04
    assert "ObservedMaintenanceStrategy" in background05
    assert "OralAntipsychoticMedication" not in set(DECLARED_ORDER)
    assert "ObservedMaintenanceStrategy" not in set(DECLARED_ORDER)
    assert len(actual.variables) == 21

    # Distinct F6 follow-up identity on a BN-06-derived single graph.
    own_head = source[:4000].decode()
    assert f"BN_06_Continue_Or_Adjust_Review_F6_v{_revision()[-1]}" in own_head
    assert package["manifest"]["question_key"] == "continue_or_adjust"
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["prompt_version"] == _revision()
    assert package["manifest"]["template_version"] == _revision()
    assert validate(source).source_sha256 != original06.source_sha256
    assert (
        validate(source).source_sha256
        == {
            "s38-v1": "5f0d4eca4637a6cd1bc9e67bb55bea93f98c335cdc98f98552be4808b8cfb3e5",
            "s38-v2": "70bce25d3c9d283d09840f8d594a4b3203ada92fc14515b039a57ddffdd100db",
        }[_revision()]
    )


def test_continue_or_adjust_gate_missing_stale_and_insufficient_information_stay_unapproved() -> (
    None
):
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    manifest = package["manifest"]
    contract = manifest["missingness_contract"]
    assert contract["required_source_paths"] == list(CANDIDATE_SOURCE_PATHS)
    assert contract["required_unknown_values"] == ["unknown", "not_assessed"]
    assert contract["required_unknown_policy"] == "needs_clarification"
    assert contract["required_missing_policy"] == "needs_clarification"
    assert contract["required_conflict_policy"] == "needs_clarification"
    assert "every follow-up encounter is applicable" in contract["gate_definition"].lower()
    assert "never coerced" in contract["gate_definition"].lower()
    assert "fresh baseline" in contract["stale_baseline_rule"].lower()
    assert "never silently reuses" in contract["stale_baseline_rule"].lower()
    assert "no recency threshold" in contract["stale_baseline_rule"].lower()
    assert "parallel" in contract["urgent_banner_rule"].lower()
    assert "never a computed risk percentage" in contract["urgent_banner_rule"].lower()
    assert "empty evidence" in contract["no_intervention_conditioning"].lower()
    assert "no do-operator" in contract["no_intervention_conditioning"].lower()
    assert "one bn-06-derived network" in contract["no_runtime_merge"].lower()
    assert contract["evidence"] == {}
    assert manifest["applicability"]["expression"] == "true"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["applicability"]["unknown_policy"] == "needs_clarification"
    assert (
        manifest["applicability_truth_table"]["true"]
        .lower()
        .startswith("every follow-up encounter is applicable")
    )
    assert "unreachable" in manifest["applicability_truth_table"]["false"].lower()
    assert "needs_clarification" in manifest["applicability_truth_table"]["unknown"].lower()
    mappings = manifest["patient_mappings"]
    assert [mapping["allowed_source_paths"] for mapping in mappings] == [
        [path] for path in CANDIDATE_SOURCE_PATHS[:10]
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads((ROOT / "content/history/history.continue_or_adjust.v1.json").read_text())
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    assert [field["usage"] for field in history["fields"][:10]] == ["cpt_context"] * 10
    assert [field["usage"] for field in history["fields"][10:]] == [
        "applicability_context",
        "applicability_context",
    ]

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    for case_id in (
        "continuation-applicable",
        "adjustment-change-reason-review",
        "urgent-present-parallel-banner",
        "insufficient-unknown-preference",
        "stale-baseline-outdated",
        "required-missing-baseline",
        "conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert cases["continuation-applicable"]["expected_for_review"]["status"] == "execute"
    assert cases["adjustment-change-reason-review"]["expected_for_review"]["status"] == "execute"
    assert cases["urgent-present-parallel-banner"]["expected_for_review"]["status"] == "execute"
    assert (
        cases["urgent-present-parallel-banner"]["expected_for_review"][
            "urgent_banner_from_saved_inputs"
        ]
        == "Present"
    )
    assert (
        "never a computed risk percentage"
        in cases["urgent-present-parallel-banner"]["note"].lower()
    )
    assert set(cases["continuation-applicable"]["inputs"]) == set(CANDIDATE_SOURCE_PATHS)
    assert (
        cases["insufficient-unknown-preference"]["inputs"][f"{PREFIX}h_continuation_preference"]
        == "unknown"
    )
    assert cases["insufficient-unknown-preference"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert "never coerced" in cases["insufficient-unknown-preference"]["note"].lower()
    # Stale-baseline contrast: outdated versus the current-baseline case.
    assert (
        cases["stale-baseline-outdated"]["inputs"][f"{PREFIX}h_baseline_window_validity"]
        == "outdated"
    )
    assert (
        cases["continuation-applicable"]["inputs"][f"{PREFIX}h_baseline_window_validity"]
        == "current"
    )
    assert cases["stale-baseline-outdated"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert "never silently reuses" in cases["stale-baseline-outdated"]["note"].lower()
    assert f"{PREFIX}h_baseline_encounter_ref" not in cases["required-missing-baseline"]["inputs"]
    assert cases["required-missing-baseline"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        len(
            set(
                cases["conflict-needs-clarification"]["inputs"][
                    f"{PREFIX}h_symptom_improvement_current"
                ]
            )
        )
        == 2
    )
    assert cases["conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_continue_or_adjust_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> (
    None
):
    package, source = _read_package()
    fixture = next(
        (
            example
            for example in package["examples"]["numerical"]
            if example.get("id") == "full-graph-asymmetric-mathematical"
        ),
        None,
    )
    assert fixture is not None, (
        "Draft must provide a complete independently worked mathematical fixture"
    )
    document = validate(source)
    payload = fixture["cpt_payload"]
    report = validate_cpts(document, payload)
    assert report.valid, report.errors
    assert len(report.tables) == 21
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    # Independent literals with worked derivations in examples.json work field.
    # Scope Within .85*.65*.9=.49725, Urgent .1, Outside .40275. Mismatch Yes
    # .3*.4=.12. Tolerability Yes .75*.8*.05+.75*.2*.5+.25*.8*.9+.25*.2*.95
    # =.3325 (swapped parents .3125). Clinical No .55*.6675=.367125.
    # Practical No .9*.88=.792. Discussion Continuation .49725*.367125*.792
    # =.1445819, Individualized .49725*.709237=.3526681. Strategy Continue
    # .367125*.792=.290763, Switch .709237*.25=.17730925, Other .709237*.75
    # =.53192775. Discontinued Yes .709237*.4=.2836948. Destabilization Yes
    # .45*.25=.1125. Metabolic Improved .25*(1-.039204)=.240199, Worsened
    # .25*.039204=.009801, NoMeaningfulChange .75.
    expected = {
        "SchizophreniaEstablished": {"No": 0.15, "Yes": 0.85},
        "SymptomImprovementOnCurrentAntipsychotic": {"No": 0.35, "Yes": 0.65},
        "UrgentSafetyConcern": {"Absent": 0.9, "Present": 0.1},
        "ResidualClinicalBurden": {"No": 0.55, "Yes": 0.45},
        "MetabolicConcern": {"No": 0.75, "Yes": 0.25},
        "OtherTolerabilityConcern": {"No": 0.8, "Yes": 0.2},
        "SameAntipsychoticContinuationPreference": {
            "ContinueSame": 0.6,
            "DiscussChange": 0.25,
            "NoExpressedPreference": 0.15,
        },
        "CurrentAgentAvailable": {"No": 0.1, "Yes": 0.9},
        "LAIUnderConsideration": {"No": 0.7, "Yes": 0.3},
        "SameAgentLAIAvailable": {"No": 0.4, "Yes": 0.6},
        "SameAntipsychoticContinuationScope": {
            "OutsideScope": 0.40275,
            "WithinScope": 0.49725,
            "UrgentSeparateAssessment": 0.1,
        },
        "LAIAgentMismatch": {"No": 0.88, "Yes": 0.12},
        "TolerabilityReview": {"No": 0.6675, "Yes": 0.3325},
        "ClinicalChangeReason": {"No": 0.367125, "Yes": 0.632875},
        "PracticalChangeReason": {"No": 0.792, "Yes": 0.208},
        "SameAgentDiscussion": {
            "OutsideScope": 0.40275,
            "ContinuationDiscussion": 0.1445819,
            "IndividualizedChangeReview": 0.3526681,
            "UrgentSeparateAssessment": 0.1,
        },
        "ObservedSameAntipsychoticStrategy": {
            "ContinueSameAgent": 0.290763,
            "SwitchAgent": 0.17730925,
            "OtherStrategy": 0.53192775,
        },
        "SwitchMonitoringContext": {
            "RoutineReview": 0.290763,
            "SwitchMonitoringDiscussion": 0.17730925,
            "SeparatePlanReview": 0.53192775,
        },
        "AssignedRegimenDiscontinued": {"No": 0.7163052, "Yes": 0.2836948},
        "ClinicalDestabilization": {"No": 0.8875, "Yes": 0.1125},
        "MetabolicTrajectory": {
            "Improved": 0.240199,
            "NoMeaningfulChange": 0.75,
            "Worsened": 0.009801,
        },
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
    # Transpose guard proves parent-order correctness: the conditional rows
    # distinguish ordered parent positions, so a swapped GIVEN order fails.
    tables = {table["node_id"]: table for table in payload["tables"]}
    assert tables["TolerabilityReview"]["parent_ids"] == [
        "MetabolicConcern",
        "OtherTolerabilityConcern",
    ]
    tolerability_rows = {
        tuple(row["parent_states"]): row["percentages"]
        for row in tables["TolerabilityReview"]["rows"]
    }
    assert tolerability_rows[("No", "Yes")] == ["50", "50"]
    assert tolerability_rows[("Yes", "No")] == ["10", "90"]
    assert 0.75 * 0.2 * 0.9 + 0.25 * 0.8 * 0.5 + 0.0475 + 0.03 != pytest.approx(0.3325)
    projections = fixture["context_examples"]
    assert len(projections) == 2
    assert projections[0]["SchizophreniaEstablished"] == "Yes"
    assert projections[1]["SchizophreniaEstablished"] == "No"
    results = [
        infer_effective(artifact, query_nodes=query, patient_projection=projection)
        for projection in projections
    ]
    for result in results:
        assert dict(result.evidence) == {}
        assert result.tolerance == 1e-6
        assert [posterior.node_id for posterior in result.posteriors] == list(expected)
        for posterior in result.posteriors:
            assert posterior.states == tuple(expected[posterior.node_id])
            assert dict(
                zip(posterior.states, posterior.probabilities, strict=True)
            ) == pytest.approx(expected[posterior.node_id], abs=1e-6)
    repeated = replay(artifact, results[0], patient_projection=projections[1])
    for before, after in zip(results[0].posteriors, repeated.posteriors, strict=True):
        assert (after.node_id, after.states) == (before.node_id, before.states)
        assert after.probabilities == pytest.approx(before.probabilities, abs=1e-6)
    assert dict(repeated.evidence) == {}
    assert document.source_bytes == source == (PACKAGE / "network.xml").read_bytes()
    assert (
        validate(SOURCE_BN06.read_bytes()).source_sha256
        == "e29107fa432b29ca082a66dcae6aae7b3ecdf8aad00f9610c699c6da61518f96"
    )
    assert package["manifest"]["numerical_policy"] == {
        "cpt_policy": "cpt-decimal-v1",
        "evidence": {},
        "comparison_tolerance": 1e-6,
        "engine": {
            "python": "3.12.14",
            "pgmpy": "1.1.2",
            "lxml": "6.1.3",
            "float_dtype": "float64",
            "elimination_order": "source_order",
        },
    }
    # Urgent-present contrast pins Scope/Discussion to urgent while the other
    # 19 marginals equal the asymmetric fixture (no other node descends from it).
    contrast = next(
        (
            example
            for example in package["examples"]["numerical"]
            if example.get("id") == "full-graph-urgent-present-contrast-mathematical"
        ),
        None,
    )
    assert contrast is not None
    assert contrast["expected"]["SameAntipsychoticContinuationScope"] == {
        "OutsideScope": 0.0,
        "WithinScope": 0.0,
        "UrgentSeparateAssessment": 1.0,
    }
    assert contrast["expected"]["SameAgentDiscussion"] == {
        "OutsideScope": 0.0,
        "ContinuationDiscussion": 0.0,
        "IndividualizedChangeReview": 0.0,
        "UrgentSeparateAssessment": 1.0,
    }
    for node in expected:
        if node not in (
            "SameAntipsychoticContinuationScope",
            "SameAgentDiscussion",
            "UrgentSafetyConcern",
        ):
            assert contrast["expected"][node] == expected[node]


def test_continue_or_adjust_review_template_preserves_urgent_without_treatment_choice() -> None:
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    prompt_text = (PACKAGE / "prompt.txt").read_text()
    lowered_prompt = prompt_text.lower()
    assert "estimate" in lowered_prompt
    assert "percentage" in lowered_prompt
    for marker in (
        "choose applicability",
        "write a plan",
        "full record",
        "execute the network",
        "modify the structure",
        "argmax",
    ):
        assert marker not in lowered_prompt
    assert "bn-06" in lowered_prompt
    assert "f6" in lowered_prompt or "follow-up" in lowered_prompt
    assert "empty" in lowered_prompt
    assert "intervention" in lowered_prompt
    assert "experimental/educational" in lowered_prompt
    assert "quality measure" in lowered_prompt
    assert "electronic decision support" in lowered_prompt
    assert "synthetic mathematical references" in lowered_prompt

    template = package["template"]
    assert template["version"] == _revision()
    assert template["status"] == "awaiting_review"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    assert len(branches) == 26
    for node, states in {
        "SameAntipsychoticContinuationScope": (
            "OutsideScope",
            "WithinScope",
            "UrgentSeparateAssessment",
        ),
        "SameAgentDiscussion": (
            "OutsideScope",
            "ContinuationDiscussion",
            "IndividualizedChangeReview",
            "UrgentSeparateAssessment",
        ),
        "ObservedSameAntipsychoticStrategy": (
            "ContinueSameAgent",
            "SwitchAgent",
            "OtherStrategy",
        ),
        "TolerabilityReview": ("No", "Yes"),
        "ClinicalChangeReason": ("No", "Yes"),
        "PracticalChangeReason": ("No", "Yes"),
        "SwitchMonitoringContext": (
            "RoutineReview",
            "SwitchMonitoringDiscussion",
            "SeparatePlanReview",
        ),
        "MetabolicTrajectory": ("Improved", "NoMeaningfulChange", "Worsened"),
        "AssignedRegimenDiscontinued": ("No", "Yes"),
        "ClinicalDestabilization": ("No", "Yes"),
    }.items():
        for state in states:
            branch = by_state[(node, state)]
            assert branch["when"]["operator"] == "=="
            assert "{" + node + "}" in branch["text"]
            lowered = branch["text"].lower()
            assert "argmax" not in lowered
            assert "largest posterior" not in lowered
            assert "highest posterior" not in lowered
            assert "threshold" not in lowered or "no probability threshold" in lowered
    urgent_scope = by_state[("SameAntipsychoticContinuationScope", "UrgentSeparateAssessment")]
    assert "independently" in urgent_scope["text"].lower()
    banner = template["urgent_banner"].lower()
    assert "saved" in banner and "independently" in banner
    assert "not a computed risk percentage" in banner
    rendering = template["rendering_contract"].lower()
    assert "deterministic" in rendering
    assert "estimated" in rendering
    assert "open assumption" in rendering
    assert "assumes neither" in rendering


def test_continue_or_adjust_open_assumptions_stay_open_without_approval() -> None:
    package, source = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "no clinical model or history definition approved" in assumptions
    assert "background comparison" in assumptions
    assert "never a runtime merge" in assumptions or "never merged" in assumptions
    assert "quality-measure" in assumptions or "quality measure" in assumptions
    assert (
        "electronic-decision-support" in assumptions or "electronic decision support" in assumptions
    )
    assert "does not approve" in assumptions
    assert "deterministic-rule versus estimated-table" in review["open_assumptions"].lower()
    assert "24-week" in review["open_assumptions"].lower()
    assert "urgent-banner independence" in review["open_assumptions"].lower()
    assert "without merge" in review["open_assumptions"].lower()
    assert "full clinical cpt provenance" in review["open_assumptions"].lower()
    assert "12-field history" in review["open_assumptions"].lower()
    assert "2026-10-04 owner decision" in review["owner_retention_decision"]
    assert "experimental/educational" in review["owner_retention_decision"].lower()
    assert "does not approve" in review["owner_retention_decision"].lower()
    limitation = review["statement_06_limitation"]
    assert "not appropriate for use as a quality measure" in limitation.lower()
    assert "electronic decision support" in limitation.lower()
    assert "lacks this literal restriction text" in limitation.lower()
    assert "quality measurement considerations" in limitation.lower()
    network_text = source.decode()
    assert "24 weeks, a modeler assumption for review" in network_text
    assert "experimental/educational scope" in network_text.lower()
    assert "not appropriate for use as a quality measure" in network_text.lower()
    rendering = package["template"]["rendering_contract"].lower()
    assert "deterministic" in rendering
    assert "estimated" in rendering
    assert "open assumption" in rendering
    assert "assumes neither" in rendering
    history = json.loads((ROOT / "content/history/history.continue_or_adjust.v1.json").read_text())
    assert "experimental/educational scope" in history["purpose"].lower()
    assert "statement-06" in history["purpose"].lower()
    assert "awaiting_review" in history["status"]
