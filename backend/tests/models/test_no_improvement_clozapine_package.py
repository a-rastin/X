"""S37 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/no_improvement_clozapine"
PREFIX = "candidate/history/no_improvement_clozapine.v1/"
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
    f"{PREFIX}h_baseline_encounter_ref",
    f"{PREFIX}h_baseline_window_validity",
    f"{PREFIX}h_panss_baseline_status",
    f"{PREFIX}h_panss_followup_status",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-07.xml"
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


def test_no_improvement_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "no_improvement_clozapine"
    assert loaded.question_key != "established_case_clozapine"
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}
    assert tuple(package["manifest"]["query_nodes"]) == QUERY_NODES


def test_no_improvement_source_fidelity_is_auditable_without_clinical_probability_claims() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "ab947f741f0d4a76d01bacf3327fd6c6457cb61eaf08bcfb19a85da4695a222f"
    )
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

    # F5 follow-up keeps a distinct identity from the R7 registration package
    # even though both retain the same BN-07 concepts.
    sibling = ROOT / "content/questions/established_case_clozapine"
    sibling_manifest = json.loads((sibling / "manifest.json").read_text())
    sibling_network_head = (sibling / "network.xml").read_bytes()[:2000].decode()
    own_network_head = source[:2000].decode()
    assert package["manifest"]["question_key"] != sibling_manifest["question_key"]
    assert package["manifest"]["workflow"] != sibling_manifest["workflow"]
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["prompt_version"] != sibling_manifest["prompt_version"]
    assert package["manifest"]["template_version"] != sibling_manifest["template_version"]
    assert "BN_07_No_Improvement_Clozapine_Review_F5_v1" in own_network_head
    assert "BN_07_Established_Case_Clozapine_Review_S32_v1" in sibling_network_head
    assert "BN_07_No_Improvement_Clozapine_Review_F5_v1" not in sibling_network_head
    assert (
        package["manifest"]["missingness_contract"]["gate_definition"]
        != sibling_manifest["missingness_contract"]["gate_definition"]
    )


def test_no_improvement_gate_true_false_unknown_have_distinct_draft_contracts() -> None:
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    manifest = package["manifest"]
    contract = manifest["missingness_contract"]
    assert contract["required_source_paths"] == list(CANDIDATE_SOURCE_PATHS)
    assert contract["required_unknown_values"] == ["unknown", "not_assessed"]
    assert contract["required_unknown_policy"] == "needs_clarification"
    assert contract["required_missing_policy"] == "needs_clarification"
    assert contract["required_conflict_policy"] == "needs_clarification"
    assert "CurrentTreatment" in contract["gate_definition"]
    assert "baseline" in contract["gate_definition"].lower()
    assert "PANSS" in contract["gate_definition"]
    assert contract["false_gate"] == {
        "status": "not_applicable",
        "not_a_negative_posterior": True,
        "posterior_claim": None,
    }
    assert contract["unknown_gate"] == {
        "status": "needs_clarification",
        "pauses_before_estimation": True,
    }
    assert contract["evidence"] == {}
    # Explicit no-improvement gate: improvement is never a negative posterior,
    # no percent-change cutoff is guessed, outdated baselines and
    # not-assessed/missing inputs pause rather than silently skipping.
    assert "never a fake negative" in contract["improvement_rule"].lower()
    assert "never treated as no improvement" in contract["improvement_rule"].lower()
    assert "cutoff" in contract["no_percent_cutoff_rule"].lower()
    assert "never maps" in contract["no_percent_cutoff_rule"].lower()
    assert "fresh baseline" in contract["outdated_baseline_rule"].lower()
    assert "never coerced" in contract["not_assessed_severity_rule"].lower()
    assert "completed" in contract["panss_rule"].lower()
    assert manifest["applicability"]["expression"] == "gate == 'true'"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["applicability"]["unknown_policy"] == "needs_clarification"
    mappings = manifest["patient_mappings"]
    assert [mapping["allowed_source_paths"] for mapping in mappings] == [
        [path] for path in CANDIDATE_SOURCE_PATHS[:15]
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads(
        (ROOT / "content/history/history.no_improvement_clozapine.v1.json").read_text()
    )
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    for case_id in (
        "gate-true-no-improvement-applicable",
        "gate-true-no-improvement-urgent-present",
        "gate-false-improvement-documented",
        "gate-unknown-panss-followup-missing",
        "gate-unknown-outdated-baseline",
        "gate-unknown-not-assessed-severity",
        "gate-conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert (
        cases["gate-true-no-improvement-applicable"]["expected_for_review"]["status"] == "execute"
    )
    assert (
        cases["gate-true-no-improvement-urgent-present"]["expected_for_review"]["status"]
        == "execute"
    )
    assert (
        cases["gate-true-no-improvement-urgent-present"]["expected_for_review"][
            "urgent_banner_from_saved_inputs"
        ]
        == "Present"
    )
    assert cases["gate-false-improvement-documented"]["expected_for_review"]["status"] == (
        "not_applicable"
    )
    assert (
        cases["gate-false-improvement-documented"]["expected_for_review"][
            "not_a_negative_posterior"
        ]
        is True
    )
    assert (
        cases["gate-false-improvement-documented"]["expected_for_review"]["posterior_claim"] is None
    )
    assert "fake negative" in cases["gate-false-improvement-documented"]["note"].lower()
    assert set(cases["gate-true-no-improvement-applicable"]["inputs"]) == set(
        CANDIDATE_SOURCE_PATHS
    )
    # Missing follow-up never maps to no improvement.
    assert (
        f"{PREFIX}h_panss_followup_status"
        not in cases["gate-unknown-panss-followup-missing"]["inputs"]
    )
    assert cases["gate-unknown-panss-followup-missing"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    # Differing-baseline contrast: outdated versus the current-baseline case.
    assert (
        cases["gate-unknown-outdated-baseline"]["inputs"][f"{PREFIX}h_baseline_window_validity"]
        == "outdated"
    )
    assert (
        cases["gate-true-no-improvement-applicable"]["inputs"][
            f"{PREFIX}h_baseline_window_validity"
        ]
        == "current"
    )
    assert cases["gate-unknown-outdated-baseline"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        cases["gate-unknown-not-assessed-severity"]["inputs"][
            f"{PREFIX}h_moderate_symptoms_impairment"
        ]
        == "not_assessed"
    )
    assert cases["gate-unknown-not-assessed-severity"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert (
        len(
            set(
                cases["gate-conflict-needs-clarification"]["inputs"][
                    f"{PREFIX}h_schizophrenia_established"
                ]
            )
        )
        == 2
    )
    assert cases["gate-conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_no_improvement_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> None:
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
    # TwoAdeq Yes .45*.75=.3375 (swapped T1/T2 gives .35*.75=.2625). TRS
    # Triggered .85*.65*.3375=.18646875. AdditionalResearch Present
    # .297+.044+.189+.009=.539 (swapped parents .514). PriorTrial Triggered
    # 1-.70*.65=.545. Prep Prepared .65*.55*.75=.268125. Pathway urgent .15,
    # current .85*.35=.2975, prior .85*.15=.1275, evidence .85*.30=.255,
    # discuss .85*.08=.068, address .85*.12=.102. Exposure Triggered
    # .25*(.35+.20)=.1375. PersistentSymptoms outside .45, continue
    # .55*.55=.3025, adequacy .55*.45*.45=.111375, specialist .55*.45*.55=.136125.
    expected = {
        "SchizophreniaEstablished": {"Yes": 0.85, "No": 0.15},
        "PersistentSignificantSymptoms": {"Yes": 0.65, "No": 0.35},
        "Trial1Assessment": {
            "AdequateSuboptimalResponse": 0.45,
            "AdequateSatisfactoryResponse": 0.25,
            "InadequateExposure": 0.2,
            "TruncatedByIntolerance": 0.1,
        },
        "Trial2Assessment": {
            "AdequateSuboptimalResponse": 0.35,
            "AdequateSatisfactoryResponse": 0.3,
            "InadequateExposure": 0.25,
            "TruncatedByIntolerance": 0.1,
        },
        "TrialsUseDifferentDrugs": {"Yes": 0.75, "No": 0.25},
        "SymptomsAtLeast12Weeks": {"Yes": 0.55, "No": 0.45},
        "ModerateSymptomsAndFunctionalImpairment": {"Yes": 0.6, "No": 0.4},
        "ClozapineTreatmentStatus": {
            "NeverTreated": 0.45,
            "CurrentTreatment": 0.35,
            "PreviouslyTreated": 0.2,
        },
        "WillingToDiscussClozapine": {"Yes": 0.65, "No": 0.35},
        "ClozapineMonitoringPlanAvailable": {"Yes": 0.55, "No": 0.45},
        "ClozapineInitiationDiscussionAppropriate": {"Yes": 0.75, "No": 0.25},
        "UrgentSafetyConcern": {"Present": 0.15, "Absent": 0.85},
        "ExposureInterpretationConcern": {"Yes": 0.25, "No": 0.75},
        "ClozapineTrialAdequate": {"Yes": 0.55, "No": 0.45},
        "ResidualSymptomsOnClozapine": {"Yes": 0.45, "No": 0.55},
        "TwoAdequateSuboptimalTrials": {"Yes": 0.3375, "No": 0.6625},
        "TreatmentResistantSchizophreniaReview": {
            "Triggered": 0.18646875,
            "NotTriggered": 0.81353125,
        },
        "AdditionalResearchFeatures": {"Present": 0.539, "NotAllPresent": 0.461},
        "PriorTrialAdequacyReview": {"Triggered": 0.545, "NotTriggered": 0.455},
        "ClozapineInitiationDiscussionPreparation": {
            "AddressBarriers": 0.731875,
            "PreparedForDiscussion": 0.268125,
        },
        "TreatmentResistanceClozapineReviewPathway": {
            "UrgentClinicalAssessment": 0.15,
            "ReviewCurrentTreatment": 0.2975,
            "IndividualizedPriorClozapineReview": 0.1275,
            "ReviewEvidenceAndOtherIndications": 0.255,
            "DiscussClozapine": 0.068,
            "AddressDiscussionBarriers": 0.102,
        },
        "ClozapineExposureReview": {"Triggered": 0.1375, "NotTriggered": 0.8625},
        "PersistentSymptomsReview": {
            "OutsideCurrentTreatmentScope": 0.45,
            "ContinuePeriodicReview": 0.3025,
            "ReviewTrialAdequacy": 0.111375,
            "SpecialistPersistentSymptomsReview": 0.136125,
        },
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
    # Transpose guards prove parent-order correctness: the conditional rows
    # distinguish ordered parent positions, so a swapped GIVEN order fails.
    tables = {table["node_id"]: table for table in payload["tables"]}
    assert tables["TwoAdequateSuboptimalTrials"]["parent_ids"] == [
        "Trial1Assessment",
        "Trial2Assessment",
        "TrialsUseDifferentDrugs",
    ]
    two_adequate_rows = {
        tuple(row["parent_states"]): row["percentages"]
        for row in tables["TwoAdequateSuboptimalTrials"]["rows"]
    }
    assert two_adequate_rows[
        ("AdequateSuboptimalResponse", "AdequateSatisfactoryResponse", "Yes")
    ] == ["100", "0"]
    assert two_adequate_rows[
        ("AdequateSatisfactoryResponse", "AdequateSuboptimalResponse", "Yes")
    ] == ["0", "100"]
    assert 0.35 * 0.75 != pytest.approx(0.3375)
    research_rows = {
        tuple(row["parent_states"]): row["percentages"]
        for row in tables["AdditionalResearchFeatures"]["rows"]
    }
    assert research_rows[("Yes", "No")] == ["20", "80"]
    assert research_rows[("No", "Yes")] == ["70", "30"]
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


def test_no_improvement_review_template_preserves_urgent_without_treatment_choice() -> None:
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
    assert "f5" in lowered_prompt
    assert "r7" in lowered_prompt
    assert "empty" in lowered_prompt
    for forbidden in ("dose", "route", "frequency", "active/stopped"):
        assert forbidden in lowered_prompt  # exclusion is stated, never collected
    assert "panss" in lowered_prompt
    assert "s08" in lowered_prompt
    assert "percent-change cutoff" in lowered_prompt

    template = package["template"]
    assert template["version"] == "s37-v1"
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
    assert "not a computed risk percentage" in urgent["text"].lower()
    banner = template["urgent_banner"].lower()
    assert "saved" in banner and "independently" in banner
    assert "not a computed risk percentage" in banner


def test_no_improvement_open_assumptions_stay_open_without_approval() -> None:
    package, _ = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "deterministic" in assumptions
    assert "estimated" in assumptions
    assert "open assumption" in assumptions
    assert "without approval" in assumptions
    assert "percent-change cutoff" in assumptions
    assert "outdated" in assumptions
    assert "not-assessed" in assumptions
    assert "trial-adequacy" in assumptions or "trial adequacy" in assumptions
    assert "observation-window" in assumptions or "observation window" in assumptions
    assert "monitoring" in assumptions
    assert "willingness" in assumptions
    assert "s08 panss" in assumptions
    assert "without owner approval" in assumptions
    assert "no owner-approval dependency" in assumptions
    assert "deterministic" in review["open_assumptions"].lower()
    assert "estimated" in review["open_assumptions"].lower()
    assert "percent-change cutoff" in review["open_assumptions"].lower()
    assert "recency threshold" in review["open_assumptions"].lower()
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
