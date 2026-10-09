"""S32 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/established_case_clozapine"
PREFIX = "candidate/history/established_case_clozapine.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_schizophrenia_established",
    f"{PREFIX}h_persistent_symptoms",
    f"{PREFIX}h_trial1_assessment",
    f"{PREFIX}h_trial2_assessment",
    f"{PREFIX}h_trials_different_drugs",
    f"{PREFIX}h_symptoms_12_weeks",
    f"{PREFIX}h_moderate_symptoms_impairment",
    f"{PREFIX}h_clozapine_treatment_status",
    f"{PREFIX}h_willing_to_discuss",
    f"{PREFIX}h_monitoring_plan",
    f"{PREFIX}h_initiation_discussion_appropriate",
    f"{PREFIX}h_urgent_safety_concern",
    f"{PREFIX}h_exposure_interpretation_concern",
    f"{PREFIX}h_clozapine_trial_adequate",
    f"{PREFIX}h_residual_symptoms",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-07.xml"
STATEMENT = ROOT / "project-documents/medical-documents/guideline/STATEMENT-07.md"
T1 = (
    "AdequateSuboptimalResponse",
    "AdequateSatisfactoryResponse",
    "InadequateExposure",
    "TruncatedByIntolerance",
)
DECLARED_ORDER = (
    "SchizophreniaEstablished",
    "PersistentSignificantSymptoms",
    "Trial1Assessment",
    "Trial2Assessment",
    "TrialsUseDifferentDrugs",
    "SymptomsAtLeast12Weeks",
    "ModerateSymptomsAndFunctionalImpairment",
    "ClozapineTreatmentStatus",
    "WillingToDiscussClozapine",
    "ClozapineMonitoringPlanAvailable",
    "ClozapineInitiationDiscussionAppropriate",
    "UrgentSafetyConcern",
    "ExposureInterpretationConcern",
    "ClozapineTrialAdequate",
    "ResidualSymptomsOnClozapine",
    "TwoAdequateSuboptimalTrials",
    "TreatmentResistantSchizophreniaReview",
    "AdditionalResearchFeatures",
    "PriorTrialAdequacyReview",
    "ClozapineInitiationDiscussionPreparation",
    "TreatmentResistanceClozapineReviewPathway",
    "ClozapineExposureReview",
    "PersistentSymptomsReview",
)
QUERY_NODES = (
    "TwoAdequateSuboptimalTrials",
    "TreatmentResistantSchizophreniaReview",
    "AdditionalResearchFeatures",
    "PriorTrialAdequacyReview",
    "ClozapineInitiationDiscussionPreparation",
    "TreatmentResistanceClozapineReviewPathway",
    "ClozapineExposureReview",
    "PersistentSymptomsReview",
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


def test_established_case_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "established_case_clozapine"
    assert loaded.question_key != "no_improvement_clozapine"
    assert package["manifest"]["workflow"] == "registration"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}


def test_established_case_source_fidelity_is_auditable_without_clinical_probability_claims() -> (
    None
):
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "ab947f741f0d4a76d01bacf3327fd6c6457cb61eaf08bcfb19a85da4695a222f"
    )
    assert STATEMENT.read_bytes()  # STATEMENT-07 exists as review source
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    expected_states = {
        "SchizophreniaEstablished": ("Yes", "No"),
        "PersistentSignificantSymptoms": ("Yes", "No"),
        "Trial1Assessment": T1,
        "Trial2Assessment": T1,
        "TrialsUseDifferentDrugs": ("Yes", "No"),
        "SymptomsAtLeast12Weeks": ("Yes", "No"),
        "ModerateSymptomsAndFunctionalImpairment": ("Yes", "No"),
        "ClozapineTreatmentStatus": ("NeverTreated", "CurrentTreatment", "PreviouslyTreated"),
        "WillingToDiscussClozapine": ("Yes", "No"),
        "ClozapineMonitoringPlanAvailable": ("Yes", "No"),
        "ClozapineInitiationDiscussionAppropriate": ("Yes", "No"),
        "UrgentSafetyConcern": ("Present", "Absent"),
        "ExposureInterpretationConcern": ("Yes", "No"),
        "ClozapineTrialAdequate": ("Yes", "No"),
        "ResidualSymptomsOnClozapine": ("Yes", "No"),
        "TwoAdequateSuboptimalTrials": ("Yes", "No"),
        "TreatmentResistantSchizophreniaReview": ("Triggered", "NotTriggered"),
        "AdditionalResearchFeatures": ("Present", "NotAllPresent"),
        "PriorTrialAdequacyReview": ("Triggered", "NotTriggered"),
        "ClozapineInitiationDiscussionPreparation": ("AddressBarriers", "PreparedForDiscussion"),
        "TreatmentResistanceClozapineReviewPathway": (
            "UrgentClinicalAssessment",
            "ReviewCurrentTreatment",
            "IndividualizedPriorClozapineReview",
            "ReviewEvidenceAndOtherIndications",
            "DiscussClozapine",
            "AddressDiscussionBarriers",
        ),
        "ClozapineExposureReview": ("Triggered", "NotTriggered"),
        "PersistentSymptomsReview": (
            "OutsideCurrentTreatmentScope",
            "ContinuePeriodicReview",
            "ReviewTrialAdequacy",
            "SpecialistPersistentSymptomsReview",
        ),
    }
    expected_parents = {
        "SchizophreniaEstablished": (),
        "PersistentSignificantSymptoms": (),
        "Trial1Assessment": (),
        "Trial2Assessment": (),
        "TrialsUseDifferentDrugs": (),
        "SymptomsAtLeast12Weeks": (),
        "ModerateSymptomsAndFunctionalImpairment": (),
        "ClozapineTreatmentStatus": (),
        "WillingToDiscussClozapine": (),
        "ClozapineMonitoringPlanAvailable": (),
        "ClozapineInitiationDiscussionAppropriate": (),
        "UrgentSafetyConcern": (),
        "ExposureInterpretationConcern": (),
        "ClozapineTrialAdequate": (),
        "ResidualSymptomsOnClozapine": (),
        "TwoAdequateSuboptimalTrials": (
            "Trial1Assessment",
            "Trial2Assessment",
            "TrialsUseDifferentDrugs",
        ),
        "TreatmentResistantSchizophreniaReview": (
            "SchizophreniaEstablished",
            "PersistentSignificantSymptoms",
            "TwoAdequateSuboptimalTrials",
        ),
        "AdditionalResearchFeatures": (
            "SymptomsAtLeast12Weeks",
            "ModerateSymptomsAndFunctionalImpairment",
        ),
        "PriorTrialAdequacyReview": ("Trial1Assessment", "Trial2Assessment"),
        "ClozapineInitiationDiscussionPreparation": (
            "WillingToDiscussClozapine",
            "ClozapineMonitoringPlanAvailable",
            "ClozapineInitiationDiscussionAppropriate",
        ),
        "TreatmentResistanceClozapineReviewPathway": (
            "UrgentSafetyConcern",
            "ClozapineTreatmentStatus",
            "TreatmentResistantSchizophreniaReview",
            "ClozapineInitiationDiscussionPreparation",
        ),
        "ClozapineExposureReview": ("ClozapineTreatmentStatus", "ExposureInterpretationConcern"),
        "PersistentSymptomsReview": (
            "ClozapineTreatmentStatus",
            "ResidualSymptomsOnClozapine",
            "ClozapineTrialAdequate",
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


def test_established_case_gate_true_false_unknown_have_distinct_draft_contracts() -> None:
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
            "Gate true requires SchizophreniaEstablished Yes with all other required "
            "inputs known; gate false is SchizophreniaEstablished No (first-time case) "
            "with no unknown/missing/conflict among required inputs; otherwise gate unknown."
        ),
        "false_gate": {
            "status": "not_applicable",
            "not_a_negative_posterior": True,
            "posterior_claim": None,
        },
        "unknown_gate": {"status": "needs_clarification", "pauses_before_estimation": True},
        "first_time_rule": (
            "A first-time case (SchizophreniaEstablished No with no unknown/missing/conflict) "
            "records not_applicable with reason and claims no posterior; never a fake negative "
            "clozapine posterior and never a negative finding."
        ),
        "trial_context": (
            "Missing trial-adequacy or observation-window context pauses as needs_clarification; "
            "a missing trial assessment is never coerced to No and never treated as an "
            "adequate-trial finding."
        ),
        "current_urgent": (
            "UrgentSafetyConcern Present renders an urgent banner from saved structured "
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
        [path] for path in CANDIDATE_SOURCE_PATHS
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads(
        (ROOT / "content/history/history.established_case_clozapine.v1.json").read_text()
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
        "gate-true-established-applicable",
        "gate-true-urgent-present",
        "gate-false-first-time",
        "gate-unknown-needs-clarification",
        "gate-missing-trial-context",
        "gate-conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert cases["gate-true-established-applicable"]["expected_for_review"]["status"] == "execute"
    assert cases["gate-true-urgent-present"]["expected_for_review"]["status"] == "execute"
    assert (
        cases["gate-true-urgent-present"]["expected_for_review"]["urgent_banner_from_saved_inputs"]
        == "Present"
    )
    assert cases["gate-false-first-time"]["expected_for_review"]["status"] == "not_applicable"
    assert cases["gate-false-first-time"]["expected_for_review"]["not_a_negative_posterior"] is True
    assert cases["gate-false-first-time"]["expected_for_review"]["posterior_claim"] is None
    assert "fake negative" in cases["gate-false-first-time"]["note"].lower()
    assert set(cases["gate-true-established-applicable"]["inputs"]) == set(CANDIDATE_SOURCE_PATHS)
    assert (
        cases["gate-unknown-needs-clarification"]["inputs"][CANDIDATE_SOURCE_PATHS[1]] == "unknown"
    )
    assert cases["gate-unknown-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert CANDIDATE_SOURCE_PATHS[2] not in cases["gate-missing-trial-context"]["inputs"]
    assert cases["gate-missing-trial-context"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        len(set(cases["gate-conflict-needs-clarification"]["inputs"][CANDIDATE_SOURCE_PATHS[0]]))
        == 2
    )
    assert cases["gate-conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_established_case_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> (
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
    assert len(report.tables) == 23
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    # Independent literals with worked derivations in examples.json work field.
    # TwoAdeq Yes .5*.8=.4 (swapped T1/T2 gives .32). TRS Triggered .9*.7*.4=.252.
    # AdditionalResearch Present .378+.054+.168+.006=.606 (swapped parents .576).
    # PriorTrial Triggered 1-.7*.7=.51. Prep Prepared .7*.6*.8=.336.
    # Pathway urgent .2, current .8*.3=.24, prior .8*.2=.16,
    # discuss .8*.5*.252*.336=.0338688, address .8*.5*.252*.664=.0669312,
    # evidence .8*.5*.748=.2992. Exposure Triggered .5*.3=.15.
    # PersistentSymptoms outside .5, continue .5*.6=.3,
    # specialist .5*.4*.6=.12, adequacy .5*.4*.4=.08.
    expected = {
        "SchizophreniaEstablished": {"Yes": 0.9, "No": 0.1},
        "PersistentSignificantSymptoms": {"Yes": 0.7, "No": 0.3},
        "Trial1Assessment": {
            "AdequateSuboptimalResponse": 0.5,
            "AdequateSatisfactoryResponse": 0.2,
            "InadequateExposure": 0.2,
            "TruncatedByIntolerance": 0.1,
        },
        "Trial2Assessment": {
            "AdequateSuboptimalResponse": 0.4,
            "AdequateSatisfactoryResponse": 0.3,
            "InadequateExposure": 0.2,
            "TruncatedByIntolerance": 0.1,
        },
        "TrialsUseDifferentDrugs": {"Yes": 0.8, "No": 0.2},
        "SymptomsAtLeast12Weeks": {"Yes": 0.6, "No": 0.4},
        "ModerateSymptomsAndFunctionalImpairment": {"Yes": 0.7, "No": 0.3},
        "ClozapineTreatmentStatus": {
            "NeverTreated": 0.5,
            "CurrentTreatment": 0.3,
            "PreviouslyTreated": 0.2,
        },
        "WillingToDiscussClozapine": {"Yes": 0.7, "No": 0.3},
        "ClozapineMonitoringPlanAvailable": {"Yes": 0.6, "No": 0.4},
        "ClozapineInitiationDiscussionAppropriate": {"Yes": 0.8, "No": 0.2},
        "UrgentSafetyConcern": {"Present": 0.2, "Absent": 0.8},
        "ExposureInterpretationConcern": {"Yes": 0.3, "No": 0.7},
        "ClozapineTrialAdequate": {"Yes": 0.6, "No": 0.4},
        "ResidualSymptomsOnClozapine": {"Yes": 0.4, "No": 0.6},
        "TwoAdequateSuboptimalTrials": {"Yes": 0.4, "No": 0.6},
        "TreatmentResistantSchizophreniaReview": {"Triggered": 0.252, "NotTriggered": 0.748},
        "AdditionalResearchFeatures": {"Present": 0.606, "NotAllPresent": 0.394},
        "PriorTrialAdequacyReview": {"Triggered": 0.51, "NotTriggered": 0.49},
        "ClozapineInitiationDiscussionPreparation": {
            "AddressBarriers": 0.664,
            "PreparedForDiscussion": 0.336,
        },
        "TreatmentResistanceClozapineReviewPathway": {
            "UrgentClinicalAssessment": 0.2,
            "ReviewCurrentTreatment": 0.24,
            "IndividualizedPriorClozapineReview": 0.16,
            "ReviewEvidenceAndOtherIndications": 0.2992,
            "DiscussClozapine": 0.0338688,
            "AddressDiscussionBarriers": 0.0669312,
        },
        "ClozapineExposureReview": {"Triggered": 0.15, "NotTriggered": 0.85},
        "PersistentSymptomsReview": {
            "OutsideCurrentTreatmentScope": 0.5,
            "ContinuePeriodicReview": 0.3,
            "ReviewTrialAdequacy": 0.08,
            "SpecialistPersistentSymptomsReview": 0.12,
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
        == "ab947f741f0d4a76d01bacf3327fd6c6457cb61eaf08bcfb19a85da4695a222f"
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


def test_established_case_review_template_preserves_urgent_without_treatment_choice() -> None:
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
    assert "r7" in lowered_prompt
    assert "f5" in lowered_prompt
    assert "empty" in lowered_prompt
    for forbidden in ("dose", "route", "frequency", "active/stopped"):
        assert forbidden in lowered_prompt  # exclusion is stated, never collected

    template = package["template"]
    assert template["version"] == "s32-v1"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    assert len(branches) == 22
    for node, states in {
        "TwoAdequateSuboptimalTrials": ("Yes", "No"),
        "TreatmentResistantSchizophreniaReview": ("Triggered", "NotTriggered"),
        "AdditionalResearchFeatures": ("Present", "NotAllPresent"),
        "PriorTrialAdequacyReview": ("Triggered", "NotTriggered"),
        "ClozapineInitiationDiscussionPreparation": ("AddressBarriers", "PreparedForDiscussion"),
        "TreatmentResistanceClozapineReviewPathway": (
            "UrgentClinicalAssessment",
            "ReviewCurrentTreatment",
            "IndividualizedPriorClozapineReview",
            "ReviewEvidenceAndOtherIndications",
            "DiscussClozapine",
            "AddressDiscussionBarriers",
        ),
        "ClozapineExposureReview": ("Triggered", "NotTriggered"),
        "PersistentSymptomsReview": (
            "OutsideCurrentTreatmentScope",
            "ContinuePeriodicReview",
            "ReviewTrialAdequacy",
            "SpecialistPersistentSymptomsReview",
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
    urgent = by_state[("TreatmentResistanceClozapineReviewPathway", "UrgentClinicalAssessment")]
    assert "saved" in urgent["text"].lower()
    assert "independently" in urgent["text"].lower()
    assert "percentage" in urgent["text"].lower()
    assert "not a computed risk percentage" in urgent["text"].lower()


def test_established_case_open_assumptions_stay_open_without_approval() -> None:
    package, _ = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "deterministic" in assumptions
    assert "estimated" in assumptions
    assert "open assumption" in assumptions
    assert "without approval" in assumptions
    assert "trial-adequacy" in assumptions or "trial adequacy" in assumptions
    assert "monitoring" in assumptions
    assert "willingness" in assumptions
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
