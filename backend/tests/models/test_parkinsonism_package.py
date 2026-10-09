"""S35 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/parkinsonism"
PREFIX = "candidate/history/parkinsonism.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_p_assessment",
    f"{PREFIX}h_p_antipsychotic_association",
    f"{PREFIX}h_p_tardive_concern",
    f"{PREFIX}h_p_dose_reduction_feasible",
    f"{PREFIX}h_p_switch_feasible",
    f"{PREFIX}h_p_psychotic_worsening_concern",
    f"{PREFIX}h_p_preference_concern",
    f"{PREFIX}h_p_older_age_vulnerability",
    f"{PREFIX}h_p_other_anticholinergic_burden",
    f"{PREFIX}h_p_anticholinergic_safety_concern",
    f"{PREFIX}h_p_current_anticholinergic_use",
    f"{PREFIX}h_p_prior_significant_parkinsonism",
    f"{PREFIX}h_p_amantadine_safety_concern",
    f"{PREFIX}h_p_urgent_nms_concern",
)
# BN-12 carries no urgent node: the first 13 paths map to network nodes as
# CPT-estimation context; the urgent NMS-triage path gates applicability and
# drives a parallel banner from saved inputs with no network mapping.
MAPPED_SOURCE_PATHS = CANDIDATE_SOURCE_PATHS[:-1]
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-12.xml"
STATEMENT = ROOT / "project-documents/medical-documents/guideline/STATEMENT-12.md"
DECLARED_ORDER = (
    "ParkinsonismAssessment",
    "ParkinsonismAntipsychoticAssociation",
    "TardiveDyskinesiaConcern",
    "DoseReductionFeasible",
    "LowerParkinsonismRiskSwitchFeasible",
    "PsychoticWorseningConcern",
    "ParkinsonismMedicationPreferenceConcern",
    "OlderAgeVulnerability",
    "OtherAnticholinergicBurden",
    "AnticholinergicSafetyConcern",
    "CurrentParkinsonismAnticholinergicUse",
    "PriorSignificantParkinsonism",
    "AmantadineSafetyConcern",
    "ParkinsonismIndicationReview",
    "ParkinsonismAntipsychoticRegimenReview",
    "AnticholinergicSafetyReview",
    "SymptomaticMedicationReview",
    "ParkinsonismAnticholinergicContinuationReview",
    "AmantadineReview",
    "ProphylaxisReview",
)
QUERY_NODES = (
    "ParkinsonismIndicationReview",
    "ParkinsonismAntipsychoticRegimenReview",
    "AnticholinergicSafetyReview",
    "SymptomaticMedicationReview",
    "ParkinsonismAnticholinergicContinuationReview",
    "AmantadineReview",
    "ProphylaxisReview",
)


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


def test_parkinsonism_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "parkinsonism"
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}
    assert tuple(package["manifest"]["query_nodes"]) == QUERY_NODES


def test_parkinsonism_source_fidelity_is_auditable_without_clinical_probability_claims() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "6998e4d305f5b2671c4fffc304a4fcd7c4bd932cacff4c4285d307f65ecfd058"
    )
    assert STATEMENT.read_bytes()  # STATEMENT-12 exists as review source
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    expected_states = {
        "ParkinsonismAssessment": ("Present", "Absent", "Unresolved"),
        "ParkinsonismAntipsychoticAssociation": ("Established", "NotEstablished"),
        "TardiveDyskinesiaConcern": ("Present", "Absent"),
        "DoseReductionFeasible": ("Yes", "No"),
        "LowerParkinsonismRiskSwitchFeasible": ("Yes", "No"),
        "PsychoticWorseningConcern": ("Present", "Absent"),
        "ParkinsonismMedicationPreferenceConcern": ("Present", "Absent"),
        "OlderAgeVulnerability": ("Present", "Absent"),
        "OtherAnticholinergicBurden": ("Present", "Absent"),
        "AnticholinergicSafetyConcern": ("Present", "Absent"),
        "CurrentParkinsonismAnticholinergicUse": ("Yes", "No"),
        "PriorSignificantParkinsonism": ("Yes", "No"),
        "AmantadineSafetyConcern": ("Present", "Absent"),
        "ParkinsonismIndicationReview": (
            "EstablishedCurrentAssociation",
            "AssessDifferentialAndAssociation",
            "NoCurrentTreatmentRule",
        ),
        "ParkinsonismAntipsychoticRegimenReview": (
            "ReviewDoseAndSwitch",
            "ReviewDose",
            "ReviewSwitch",
            "ReviewConstraints",
            "PrioritizePsychosisTradeoff",
            "EstablishIndicationFirst",
        ),
        "AnticholinergicSafetyReview": (
            "EnhancedIndividualizedReview",
            "RoutineIndividualizedReview",
        ),
        "SymptomaticMedicationReview": (
            "ReviewTardiveDifferential",
            "DiscussPatientConcerns",
            "DiscussAnticholinergicOption",
            "EstablishIndicationFirst",
        ),
        "ParkinsonismAnticholinergicContinuationReview": (
            "ReviewLowestDoseAndDuration",
            "ReassessNeedAndDifferential",
            "NoCurrentContinuationRule",
        ),
        "AmantadineReview": (
            "DiscussAlternativeAndSafety",
            "ReviewSpecificSafetyConcerns",
            "EstablishIndicationFirst",
        ),
        "ProphylaxisReview": (
            "IndividualizedExceptionalRiskReview",
            "NoRoutineProphylaxisRule",
            "AddressCurrentAssessment",
        ),
    }
    expected_parents = {
        "ParkinsonismAssessment": (),
        "ParkinsonismAntipsychoticAssociation": (),
        "TardiveDyskinesiaConcern": (),
        "DoseReductionFeasible": (),
        "LowerParkinsonismRiskSwitchFeasible": (),
        "PsychoticWorseningConcern": (),
        "ParkinsonismMedicationPreferenceConcern": (),
        "OlderAgeVulnerability": (),
        "OtherAnticholinergicBurden": (),
        "AnticholinergicSafetyConcern": (),
        "CurrentParkinsonismAnticholinergicUse": (),
        "PriorSignificantParkinsonism": (),
        "AmantadineSafetyConcern": (),
        "ParkinsonismIndicationReview": (
            "ParkinsonismAssessment",
            "ParkinsonismAntipsychoticAssociation",
        ),
        "ParkinsonismAntipsychoticRegimenReview": (
            "ParkinsonismIndicationReview",
            "DoseReductionFeasible",
            "LowerParkinsonismRiskSwitchFeasible",
            "PsychoticWorseningConcern",
        ),
        "AnticholinergicSafetyReview": (
            "AnticholinergicSafetyConcern",
            "OlderAgeVulnerability",
            "OtherAnticholinergicBurden",
        ),
        "SymptomaticMedicationReview": (
            "ParkinsonismIndicationReview",
            "TardiveDyskinesiaConcern",
            "ParkinsonismMedicationPreferenceConcern",
        ),
        "ParkinsonismAnticholinergicContinuationReview": (
            "CurrentParkinsonismAnticholinergicUse",
            "ParkinsonismAssessment",
        ),
        "AmantadineReview": (
            "ParkinsonismIndicationReview",
            "AmantadineSafetyConcern",
        ),
        "ProphylaxisReview": (
            "ParkinsonismAssessment",
            "PriorSignificantParkinsonism",
        ),
    }
    retained = {v.name: v for v in original.networks[0].variables}
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


def test_parkinsonism_gate_true_false_unknown_have_distinct_draft_contracts() -> None:
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    manifest = package["manifest"]
    assert manifest.get("missingness_contract") == {
        "required_source_paths": list(CANDIDATE_SOURCE_PATHS),
        "required_unknown_values": ["unknown", "not_assessed"],
        "required_unknown_policy": "needs_clarification",
        "required_missing_policy": "needs_clarification",
        "required_conflict_policy": "needs_clarification",
        "gate_definition": (
            "Gate true requires ParkinsonismAssessment Present with all other required "
            "inputs known; gate false is ParkinsonismAssessment Absent (absent case) "
            "with no unknown/missing/conflict among required inputs; otherwise gate unknown "
            "(including Unresolved, unknown, not_assessed, missing or conflicting)."
        ),
        "false_gate": {
            "status": "not_applicable",
            "not_a_negative_posterior": True,
            "posterior_claim": None,
        },
        "unknown_gate": {"status": "needs_clarification", "pauses_before_estimation": True},
        "absent_rule": (
            "An absent case (ParkinsonismAssessment Absent with no unknown/missing/conflict) "
            "records not_applicable with reason and claims no posterior; never a negative finding "
            "and never a fake negative review posterior."
        ),
        "not_assessed_rule": (
            "An unresolved or not_assessed effect (Unresolved, "
            "unknown, not_assessed, missing or conflicting "
            "required input, including missing onset, "
            "medication-timeline, alternative-cause, "
            "tardive-differential, NMS-triage, safety or "
            "preference context) pauses as needs_clarification; "
            "never coerced to Absent and never mapped to No. A "
            "missing SAS item assessment never becomes a negative finding."
        ),
        "alternative_context": (
            "Missing onset, medication-timeline, "
            "alternative-cause, antipsychotic-association, "
            "tardive-differential, dose-feasibility, "
            "switch-feasibility, psychotic-worsening, "
            "preference, anticholinergic safety, amantadine "
            "safety, NMS-triage or prophylaxis context pauses "
            "as needs_clarification; missing context is never "
            "treated as an established-association or "
            "no-concern finding."
        ),
        "current_urgent": (
            "Urgent NMS-triage concern Present renders an urgent banner from saved structured "
            "inputs in parallel with review topics, independently of estimation latency; "
            "never a computed risk percentage."
        ),
        "evidence": {},
    }
    assert manifest["applicability"]["expression"] == "gate == 'true'"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["applicability"]["unknown_policy"] == "needs_clarification"
    mappings = manifest["patient_mappings"]
    assert [mapping["allowed_source_paths"] for mapping in mappings] == [
        [path] for path in MAPPED_SOURCE_PATHS
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads((ROOT / "content/history/history.parkinsonism.v1.json").read_text())
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    for field in history["fields"][:-1]:
        assert field["source_locator"].strip()
        assert field["operationalization"].strip()
        assert field["provenance_required"] is True
        assert field["usage"] == "cpt_context"
        assert field["node_id"] is not None
    urgent_field = history["fields"][-1]
    assert urgent_field["source_locator"].strip()
    assert urgent_field["operationalization"].strip()
    assert urgent_field["provenance_required"] is True
    assert urgent_field["usage"] == "parallel_banner"
    assert urgent_field["node_id"] is None

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    for case_id in (
        "gate-true-present-applicable",
        "gate-true-urgent-present",
        "gate-false-absent",
        "gate-unknown-not-assessed",
        "gate-missing-alternative-context",
        "gate-conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert cases["gate-true-present-applicable"]["expected_for_review"]["status"] == "execute"
    assert cases["gate-true-urgent-present"]["expected_for_review"]["status"] == "execute"
    assert (
        cases["gate-true-urgent-present"]["expected_for_review"]["urgent_banner_from_saved_inputs"]
        == "Present"
    )
    assert cases["gate-false-absent"]["expected_for_review"]["status"] == "not_applicable"
    assert cases["gate-false-absent"]["expected_for_review"]["not_a_negative_posterior"] is True
    assert cases["gate-false-absent"]["expected_for_review"]["posterior_claim"] is None
    assert "fake negative" in cases["gate-false-absent"]["note"].lower()
    assert set(cases["gate-true-present-applicable"]["inputs"]) == set(CANDIDATE_SOURCE_PATHS)
    assert cases["gate-unknown-not-assessed"]["inputs"][CANDIDATE_SOURCE_PATHS[0]] == "Unresolved"
    assert cases["gate-unknown-not-assessed"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert CANDIDATE_SOURCE_PATHS[1] not in cases["gate-missing-alternative-context"]["inputs"]
    assert cases["gate-missing-alternative-context"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        len(set(cases["gate-conflict-needs-clarification"]["inputs"][CANDIDATE_SOURCE_PATHS[0]]))
        == 2
    )
    assert cases["gate-conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_parkinsonism_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> None:
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
    assert len(report.tables) == 20
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    # Independent literals with worked derivations in examples.json work field.
    expected = {
        "ParkinsonismAssessment": {"Present": 0.5, "Absent": 0.3, "Unresolved": 0.2},
        "ParkinsonismAntipsychoticAssociation": {"Established": 0.6, "NotEstablished": 0.4},
        "TardiveDyskinesiaConcern": {"Present": 0.25, "Absent": 0.75},
        "DoseReductionFeasible": {"Yes": 0.4, "No": 0.6},
        "LowerParkinsonismRiskSwitchFeasible": {"Yes": 0.5, "No": 0.5},
        "PsychoticWorseningConcern": {"Present": 0.3, "Absent": 0.7},
        "ParkinsonismMedicationPreferenceConcern": {"Present": 0.4, "Absent": 0.6},
        "OlderAgeVulnerability": {"Present": 0.35, "Absent": 0.65},
        "OtherAnticholinergicBurden": {"Present": 0.2, "Absent": 0.8},
        "AnticholinergicSafetyConcern": {"Present": 0.3, "Absent": 0.7},
        "CurrentParkinsonismAnticholinergicUse": {"Yes": 0.45, "No": 0.55},
        "PriorSignificantParkinsonism": {"Yes": 0.25, "No": 0.75},
        "AmantadineSafetyConcern": {"Present": 0.2, "Absent": 0.8},
        "ParkinsonismIndicationReview": {
            "EstablishedCurrentAssociation": 0.3,
            "AssessDifferentialAndAssociation": 0.4,
            "NoCurrentTreatmentRule": 0.3,
        },
        "ParkinsonismAntipsychoticRegimenReview": {
            "ReviewDoseAndSwitch": 0.042,
            "ReviewDose": 0.06,
            "ReviewSwitch": 0.09,
            "ReviewConstraints": 0.09,
            "PrioritizePsychosisTradeoff": 0.018,
            "EstablishIndicationFirst": 0.7,
        },
        "AnticholinergicSafetyReview": {
            "EnhancedIndividualizedReview": 0.349,
            "RoutineIndividualizedReview": 0.651,
        },
        "SymptomaticMedicationReview": {
            "ReviewTardiveDifferential": 0.075,
            "DiscussPatientConcerns": 0.09,
            "DiscussAnticholinergicOption": 0.135,
            "EstablishIndicationFirst": 0.7,
        },
        "ParkinsonismAnticholinergicContinuationReview": {
            "ReviewLowestDoseAndDuration": 0.225,
            "ReassessNeedAndDifferential": 0.225,
            "NoCurrentContinuationRule": 0.55,
        },
        "AmantadineReview": {
            "DiscussAlternativeAndSafety": 0.24,
            "ReviewSpecificSafetyConcerns": 0.06,
            "EstablishIndicationFirst": 0.7,
        },
        "ProphylaxisReview": {
            "IndividualizedExceptionalRiskReview": 0.125,
            "NoRoutineProphylaxisRule": 0.5,
            "AddressCurrentAssessment": 0.375,
        },
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
    projections = fixture["context_examples"]
    assert len(projections) == 2
    assert projections[0]["ParkinsonismAssessment"] == "Present"
    assert projections[1]["ParkinsonismAssessment"] == "Absent"
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
        validate(SOURCE_BN.read_bytes()).source_sha256
        == "6998e4d305f5b2671c4fffc304a4fcd7c4bd932cacff4c4285d307f65ecfd058"
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


def test_parkinsonism_review_template_preserves_urgent_without_treatment_choice() -> None:
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
    assert "f3" in lowered_prompt
    assert "empty" in lowered_prompt
    for forbidden in ("dose", "route", "frequency", "active/stopped"):
        assert forbidden in lowered_prompt  # exclusion is stated, never collected
    assert "sas" in lowered_prompt
    assert "band" in lowered_prompt  # no universal mild/moderate/severe bands are created
    assert "nms" in lowered_prompt

    template = package["template"]
    assert template["version"] == "s35-v1"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    assert len(branches) == 24
    for node, states in {
        "ParkinsonismIndicationReview": (
            "EstablishedCurrentAssociation",
            "AssessDifferentialAndAssociation",
            "NoCurrentTreatmentRule",
        ),
        "ParkinsonismAntipsychoticRegimenReview": (
            "ReviewDoseAndSwitch",
            "ReviewDose",
            "ReviewSwitch",
            "ReviewConstraints",
            "PrioritizePsychosisTradeoff",
            "EstablishIndicationFirst",
        ),
        "AnticholinergicSafetyReview": (
            "EnhancedIndividualizedReview",
            "RoutineIndividualizedReview",
        ),
        "SymptomaticMedicationReview": (
            "ReviewTardiveDifferential",
            "DiscussPatientConcerns",
            "DiscussAnticholinergicOption",
            "EstablishIndicationFirst",
        ),
        "ParkinsonismAnticholinergicContinuationReview": (
            "ReviewLowestDoseAndDuration",
            "ReassessNeedAndDifferential",
            "NoCurrentContinuationRule",
        ),
        "AmantadineReview": (
            "DiscussAlternativeAndSafety",
            "ReviewSpecificSafetyConcerns",
            "EstablishIndicationFirst",
        ),
        "ProphylaxisReview": (
            "IndividualizedExceptionalRiskReview",
            "NoRoutineProphylaxisRule",
            "AddressCurrentAssessment",
        ),
    }.items():
        for state in states:
            branch = by_state[(node, state)]
            assert branch["when"]["operator"] == "=="
            assert "{" + node + "}" in branch["text"]
            lowered = branch["text"].lower()
            assert "argmax" not in lowered
            assert "largest posterior" not in lowered
            assert "highest posterior" not in lowered
    urgent = template["urgent_banner"]
    assert "saved" in urgent.lower()
    assert "independently" in urgent.lower()
    assert "percentage" in urgent.lower()
    assert "not a computed risk percentage" in urgent.lower()


def test_parkinsonism_open_assumptions_stay_open_without_approval() -> None:
    package, _ = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "deterministic" in assumptions
    assert "estimated" in assumptions
    assert "open assumption" in assumptions
    assert "without approval" in assumptions
    assert "sas" in assumptions
    assert "band" in assumptions
    assert "threshold" in assumptions
    assert "nms" in assumptions or "nms" in review["open_assumptions"].lower()
    assert "deterministic" in review["open_assumptions"].lower()
    assert "estimated" in review["open_assumptions"].lower()
    assert "threshold" in review["open_assumptions"].lower()
    assert "deterministic" in review["estimation_instructions"].lower()
    assert "estimated" in review["estimation_instructions"].lower()
    rendering = package["template"]["rendering_contract"].lower()
    assert "deterministic" in rendering
    assert "estimated" in rendering
    assert "open assumption" in rendering
    assert "assumes neither" in rendering
    lowered_prompt = (PACKAGE / "prompt.txt").read_text().lower()
    assert "deterministic" in lowered_prompt
    assert "estimated" in lowered_prompt
    assert "open assumption" in lowered_prompt
    assert "without approval" in lowered_prompt
