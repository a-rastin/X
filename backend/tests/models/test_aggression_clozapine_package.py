"""S31 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/aggression_clozapine"
PREFIX = "candidate/history/aggression_clozapine.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_schizophrenia_established",
    f"{PREFIX}h_substantial_aggression_risk",
    f"{PREFIX}h_persistence_despite_treatment",
    f"{PREFIX}h_urgent_safety_concern",
    f"{PREFIX}h_adherence_concern",
    f"{PREFIX}h_modifiable_targets",
    f"{PREFIX}h_clozapine_treatment_status",
    f"{PREFIX}h_willing_to_discuss",
    f"{PREFIX}h_monitoring_plan",
    f"{PREFIX}h_medical_review",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-09.xml"
STATEMENT = ROOT / "project-documents/medical-documents/guideline/STATEMENT-09.md"
DECLARED_ORDER = (
    "SchizophreniaEstablished",
    "SubstantialAggressionRisk",
    "AggressionRiskPersistsDespiteOtherTreatments",
    "UrgentSafetyConcern",
    "AdherenceConcern",
    "ModifiableAggressionRiskTargetsPresent",
    "ClozapineTreatmentStatus",
    "WillingToDiscussClozapine",
    "ClozapineMonitoringPlanAvailable",
    "ClozapineMedicalReviewCompleted",
    "ClozapineAggressionRiskIndication",
    "AggressionRiskTreatmentPlanReview",
    "ClozapineDiscussionPreparation",
    "AggressionRiskClozapineReviewPathway",
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


def test_aggression_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "aggression_clozapine"
    assert package["manifest"]["workflow"] == "registration"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}


def test_aggression_source_fidelity_is_auditable_without_clinical_probability_claims() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "b655bada91b9dbe9b89419e924b416559af676bf3fe2adf8ea2f9892a325b6a1"
    )
    assert STATEMENT.read_bytes()  # STATEMENT-09 exists as review source
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    expected_states = {
        "SchizophreniaEstablished": ("Yes", "No"),
        "SubstantialAggressionRisk": ("Yes", "No"),
        "AggressionRiskPersistsDespiteOtherTreatments": ("Yes", "No"),
        "UrgentSafetyConcern": ("Present", "Absent"),
        "AdherenceConcern": ("Yes", "No"),
        "ModifiableAggressionRiskTargetsPresent": ("Yes", "No"),
        "ClozapineTreatmentStatus": ("NeverTreated", "CurrentTreatment", "PreviouslyTreated"),
        "WillingToDiscussClozapine": ("Yes", "No"),
        "ClozapineMonitoringPlanAvailable": ("Yes", "No"),
        "ClozapineMedicalReviewCompleted": ("Yes", "No"),
        "ClozapineAggressionRiskIndication": ("Present", "NotEstablished"),
        "AggressionRiskTreatmentPlanReview": (
            "AdherenceAndRiskTargets",
            "AdherenceReview",
            "RiskTargetReview",
            "NoAdditionalFlag",
        ),
        "ClozapineDiscussionPreparation": ("AddressReviewNeeds", "PreparedForDiscussion"),
        "AggressionRiskClozapineReviewPathway": (
            "UrgentClinicalAssessment",
            "ReviewCurrentClozapine",
            "IndividualizedPriorClozapineReview",
            "DiscussClozapine",
            "DiscussClozapineAndAddressReviewNeeds",
            "ReviewEvidenceAndOtherIndications",
        ),
    }
    expected_parents = {
        "SchizophreniaEstablished": (),
        "SubstantialAggressionRisk": (),
        "AggressionRiskPersistsDespiteOtherTreatments": (),
        "UrgentSafetyConcern": (),
        "AdherenceConcern": (),
        "ModifiableAggressionRiskTargetsPresent": (),
        "ClozapineTreatmentStatus": (),
        "WillingToDiscussClozapine": (),
        "ClozapineMonitoringPlanAvailable": (),
        "ClozapineMedicalReviewCompleted": (),
        "ClozapineAggressionRiskIndication": (
            "SchizophreniaEstablished",
            "SubstantialAggressionRisk",
            "AggressionRiskPersistsDespiteOtherTreatments",
        ),
        "AggressionRiskTreatmentPlanReview": (
            "AdherenceConcern",
            "ModifiableAggressionRiskTargetsPresent",
        ),
        "ClozapineDiscussionPreparation": (
            "WillingToDiscussClozapine",
            "ClozapineMonitoringPlanAvailable",
            "ClozapineMedicalReviewCompleted",
        ),
        "AggressionRiskClozapineReviewPathway": (
            "UrgentSafetyConcern",
            "ClozapineTreatmentStatus",
            "ClozapineAggressionRiskIndication",
            "ClozapineDiscussionPreparation",
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


def test_aggression_gate_true_false_unknown_have_distinct_draft_contracts() -> None:
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
            "Gate true requires SchizophreniaEstablished Yes, SubstantialAggressionRisk Yes "
            "and AggressionRiskPersistsDespiteOtherTreatments Yes with all other required "
            "inputs known; gate false is any of those three explicitly No with no "
            "unknown/missing/conflict among required inputs; otherwise gate unknown."
        ),
        "false_gate": {
            "status": "not_applicable",
            "not_a_negative_posterior": True,
            "posterior_claim": None,
        },
        "unknown_gate": {"status": "needs_clarification", "pauses_before_estimation": True},
        "current_urgent": (
            "UrgentSafetyConcern Present renders an urgent banner from saved structured "
            "inputs in parallel with review topics, independently of estimation latency; "
            "never a computed risk percentage."
        ),
        "hostility_rule": (
            "A PANSS hostility item score alone never maps SubstantialAggressionRisk Yes; "
            "clinician synthesis with source periods and clinician-reported fields is required."
        ),
        "evidence": {},
    }
    assert manifest["applicability"]["expression"] == "gate == 'true'"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["applicability"]["unknown_policy"] == "needs_clarification"
    mappings = manifest["patient_mappings"]
    assert [mapping["allowed_source_paths"] for mapping in mappings] == [
        [path] for path in CANDIDATE_SOURCE_PATHS
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads(
        (ROOT / "content/history/history.aggression_clozapine.v1.json").read_text()
    )
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    for field in history["fields"]:
        assert field["source_locator"].strip()
        assert field["operationalization"].strip()
        assert field["provenance_required"] is True
        assert field["usage"] == "cpt_context"

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    for case_id in (
        "gate-true-standard",
        "gate-true-urgent",
        "gate-false-not-applicable",
        "gate-unknown-needs-clarification",
        "gate-missing-needs-clarification",
        "gate-conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert cases["gate-true-standard"]["expected_for_review"]["status"] == "execute"
    assert cases["gate-true-urgent"]["expected_for_review"]["status"] == "execute"
    assert (
        cases["gate-true-urgent"]["expected_for_review"]["urgent_banner_from_saved_inputs"]
        == "Present"
    )
    assert cases["gate-false-not-applicable"]["expected_for_review"]["status"] == "not_applicable"
    assert (
        cases["gate-false-not-applicable"]["expected_for_review"]["not_a_negative_posterior"]
        is True
    )
    assert cases["gate-false-not-applicable"]["expected_for_review"]["posterior_claim"] is None
    assert set(cases["gate-true-standard"]["inputs"]) == set(CANDIDATE_SOURCE_PATHS)
    assert (
        cases["gate-unknown-needs-clarification"]["inputs"][CANDIDATE_SOURCE_PATHS[1]] == "unknown"
    )
    assert cases["gate-unknown-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert CANDIDATE_SOURCE_PATHS[2] not in cases["gate-missing-needs-clarification"]["inputs"]
    assert cases["gate-missing-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        len(set(cases["gate-conflict-needs-clarification"]["inputs"][CANDIDATE_SOURCE_PATHS[0]]))
        == 2
    )
    assert cases["gate-conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_aggression_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> None:
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
    assert len(report.tables) == 14
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    # Independent literals with worked derivations in examples.json work field.
    # Indication Present .9*.6*.5*.9 + .9*.6*.5*.2 + .9*.4*.5*.3 + .9*.4*.5*.05 = .36.
    # PlanReview: .3*.4*.7+.3*.6*.1+.7*.4*.1+.7*.6*.1=.172 etc.; swapped parents swap .208/.268.
    # Prep Prepared .21*.9+.21*.4+.14*.3+.14*.1=.329. Pathway urgent .2 etc.
    expected = {
        "SchizophreniaEstablished": {"Yes": 0.9, "No": 0.1},
        "SubstantialAggressionRisk": {"Yes": 0.6, "No": 0.4},
        "AggressionRiskPersistsDespiteOtherTreatments": {"Yes": 0.5, "No": 0.5},
        "UrgentSafetyConcern": {"Present": 0.2, "Absent": 0.8},
        "AdherenceConcern": {"Yes": 0.3, "No": 0.7},
        "ModifiableAggressionRiskTargetsPresent": {"Yes": 0.4, "No": 0.6},
        "ClozapineTreatmentStatus": {
            "NeverTreated": 0.5,
            "CurrentTreatment": 0.3,
            "PreviouslyTreated": 0.2,
        },
        "WillingToDiscussClozapine": {"Yes": 0.7, "No": 0.3},
        "ClozapineMonitoringPlanAvailable": {"Yes": 0.6, "No": 0.4},
        "ClozapineMedicalReviewCompleted": {"Yes": 0.5, "No": 0.5},
        "ClozapineAggressionRiskIndication": {"Present": 0.36, "NotEstablished": 0.64},
        "AggressionRiskTreatmentPlanReview": {
            "AdherenceAndRiskTargets": 0.172,
            "AdherenceReview": 0.208,
            "RiskTargetReview": 0.268,
            "NoAdditionalFlag": 0.352,
        },
        "ClozapineDiscussionPreparation": {
            "AddressReviewNeeds": 0.671,
            "PreparedForDiscussion": 0.329,
        },
        "AggressionRiskClozapineReviewPathway": {
            "UrgentClinicalAssessment": 0.2,
            "ReviewCurrentClozapine": 0.24,
            "IndividualizedPriorClozapineReview": 0.16,
            "DiscussClozapine": 0.047376,
            "DiscussClozapineAndAddressReviewNeeds": 0.096624,
            "ReviewEvidenceAndOtherIndications": 0.256,
        },
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
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
        validate(SOURCE_BN.read_bytes()).source_sha256
        == "b655bada91b9dbe9b89419e924b416559af676bf3fe2adf8ea2f9892a325b6a1"
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


def test_aggression_review_template_preserves_urgent_without_treatment_choice() -> None:
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
    assert "panss" in lowered_prompt
    assert "hostility" in lowered_prompt
    assert "empty" in lowered_prompt

    template = package["template"]
    assert template["version"] == "s31-v1"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    assert len(branches) == 14
    for node, states in {
        "ClozapineAggressionRiskIndication": ("Present", "NotEstablished"),
        "AggressionRiskTreatmentPlanReview": (
            "AdherenceAndRiskTargets",
            "AdherenceReview",
            "RiskTargetReview",
            "NoAdditionalFlag",
        ),
        "ClozapineDiscussionPreparation": ("AddressReviewNeeds", "PreparedForDiscussion"),
        "AggressionRiskClozapineReviewPathway": (
            "UrgentClinicalAssessment",
            "ReviewCurrentClozapine",
            "IndividualizedPriorClozapineReview",
            "DiscussClozapine",
            "DiscussClozapineAndAddressReviewNeeds",
            "ReviewEvidenceAndOtherIndications",
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
    urgent = by_state[("AggressionRiskClozapineReviewPathway", "UrgentClinicalAssessment")]
    assert "saved" in urgent["text"].lower()
    assert "independently" in urgent["text"].lower()
    assert "percentage" in urgent["text"].lower()
    assert "not a computed risk percentage" in urgent["text"].lower()


def test_aggression_deterministic_vs_estimated_remains_open_without_approval() -> None:
    package, _ = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "deterministic" in assumptions
    assert "estimated" in assumptions
    assert "open assumption" in assumptions
    assert "without approval" in assumptions
    assert "deterministic" in review["open_assumptions"].lower()
    assert "estimated" in review["open_assumptions"].lower()
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
