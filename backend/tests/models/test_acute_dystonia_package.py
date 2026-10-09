"""S36 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/acute_dystonia"
PREFIX = "candidate/history/acute_dystonia.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_ad_airway_concern",
    f"{PREFIX}h_ad_episode_phase",
    f"{PREFIX}h_ad_antipsychotic_association",
    f"{PREFIX}h_ad_anticholinergic_safety_concern",
    f"{PREFIX}h_ad_older_age_vulnerability",
    f"{PREFIX}h_ad_other_anticholinergic_burden",
    f"{PREFIX}h_ad_recurrence_prevention_need",
    f"{PREFIX}h_ad_continuation_preference_concern",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-11.xml"
DECLARED_ORDER = (
    "AirwayConcern",
    "AcuteDystoniaEpisodePhase",
    "AcuteDystoniaAntipsychoticAssociation",
    "AnticholinergicSafetyConcern",
    "OlderAgeVulnerability",
    "OtherAnticholinergicBurden",
    "RecurrencePreventionNeed",
    "AnticholinergicContinuationPreferenceConcern",
    "AcuteDystoniaReviewPathway",
    "AnticholinergicSafetyReviewFocus",
    "AcuteDystoniaAnticholinergicContinuationReview",
    "AcuteDystoniaAntipsychoticRegimenReview",
)
QUERY_NODES = (
    "AcuteDystoniaReviewPathway",
    "AnticholinergicSafetyReviewFocus",
    "AcuteDystoniaAnticholinergicContinuationReview",
    "AcuteDystoniaAntipsychoticRegimenReview",
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


def test_acute_dystonia_shape_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "acute_dystonia"
    assert package["manifest"]["workflow"] == "followup"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}
    assert tuple(package["manifest"]["query_nodes"]) == QUERY_NODES


def test_acute_dystonia_source_fidelity_without_clinical_claim() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "c0208a71c631b0a0d468202b821d47c69fdf5277c366ce815bf5c35b23308871"
    )
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    expected_states = {
        "AirwayConcern": ("Present", "Absent"),
        "AcuteDystoniaEpisodePhase": ("Active", "Resolved", "NoEpisode"),
        "AcuteDystoniaAntipsychoticAssociation": ("Established", "NotEstablished"),
        "AnticholinergicSafetyConcern": ("Present", "Absent"),
        "OlderAgeVulnerability": ("Present", "Absent"),
        "OtherAnticholinergicBurden": ("Present", "Absent"),
        "RecurrencePreventionNeed": ("Present", "Absent"),
        "AnticholinergicContinuationPreferenceConcern": ("Present", "Absent"),
        "AcuteDystoniaReviewPathway": (
            "UrgentAirwayAssessment",
            "ReviewAcuteAnticholinergicTreatment",
            "AssessEpisodeAndDifferential",
            "PostResolutionReview",
            "NoAcuteTreatmentRule",
        ),
        "AnticholinergicSafetyReviewFocus": (
            "ReviewPatientSpecificConcernAndBurden",
            "ReviewPatientSpecificConcern",
            "ReviewAgeOrMedicationBurden",
            "RoutineIndividualizedReview",
        ),
        "AcuteDystoniaAnticholinergicContinuationReview": (
            "NotPostResolutionReview",
            "EstablishEpisodeAssociation",
            "ReviewNeedAndPatientConcerns",
            "ConsiderShortTermPreventionReview",
            "ReviewStoppingOrAvoidingUnneededContinuation",
        ),
        "AcuteDystoniaAntipsychoticRegimenReview": (
            "ReviewDoseOrAlternative",
            "AssessMedicationAssociation",
            "NoEpisodeSpecificReview",
        ),
    }
    expected_parents = {
        "AirwayConcern": (),
        "AcuteDystoniaEpisodePhase": (),
        "AcuteDystoniaAntipsychoticAssociation": (),
        "AnticholinergicSafetyConcern": (),
        "OlderAgeVulnerability": (),
        "OtherAnticholinergicBurden": (),
        "RecurrencePreventionNeed": (),
        "AnticholinergicContinuationPreferenceConcern": (),
        "AcuteDystoniaReviewPathway": (
            "AirwayConcern",
            "AcuteDystoniaEpisodePhase",
            "AcuteDystoniaAntipsychoticAssociation",
        ),
        "AnticholinergicSafetyReviewFocus": (
            "AnticholinergicSafetyConcern",
            "OlderAgeVulnerability",
            "OtherAnticholinergicBurden",
        ),
        "AcuteDystoniaAnticholinergicContinuationReview": (
            "AcuteDystoniaEpisodePhase",
            "AcuteDystoniaAntipsychoticAssociation",
            "RecurrencePreventionNeed",
            "AnticholinergicContinuationPreferenceConcern",
        ),
        "AcuteDystoniaAntipsychoticRegimenReview": (
            "AcuteDystoniaEpisodePhase",
            "AcuteDystoniaAntipsychoticAssociation",
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


def test_acute_dystonia_gate_and_history_contracts() -> None:
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    manifest = package["manifest"]
    assert manifest["applicability"]["expression"] == "gate == 'true'"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["applicability"]["unknown_policy"] == "needs_clarification"
    assert manifest["missingness_contract"]["required_source_paths"] == list(CANDIDATE_SOURCE_PATHS)
    assert manifest["missingness_contract"]["false_gate"] == {
        "status": "not_applicable",
        "not_a_negative_posterior": True,
        "posterior_claim": None,
    }
    assert manifest["missingness_contract"]["unknown_gate"] == {
        "status": "needs_clarification",
        "pauses_before_estimation": True,
    }
    assert manifest["missingness_contract"]["evidence"] == {}
    mappings = manifest["patient_mappings"]
    assert [m["allowed_source_paths"] for m in mappings] == [[p] for p in CANDIDATE_SOURCE_PATHS]
    assert all(m["usage"] == "cpt_context" for m in mappings)
    history = json.loads((ROOT / "content/history/history.acute_dystonia.v1.json").read_text())
    assert history["status"] == "awaiting_review"
    assert [f["source_path"] for f in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    for field in history["fields"]:
        assert field["usage"] == "cpt_context"
        assert field["provenance_required"] is True
    cases = {c["id"]: c for c in package["examples"]["clinical"]}
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
    assert cases["gate-false-absent"]["expected_for_review"]["status"] == "not_applicable"
    assert cases["gate-false-absent"]["expected_for_review"]["not_a_negative_posterior"] is True
    assert cases["gate-false-absent"]["expected_for_review"]["posterior_claim"] is None
    assert (
        cases["gate-unknown-not-assessed"]["expected_for_review"]["status"] == "needs_clarification"
    )
    assert cases["gate-missing-alternative-context"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )
    assert cases["gate-conflict-needs-clarification"]["expected_for_review"]["status"] == (
        "needs_clarification"
    )


def test_acute_dystonia_fixture_infers_and_replays_without_clamping() -> None:
    package, source = _read_package()
    fixture = next(
        (
            e
            for e in package["examples"]["numerical"]
            if e.get("id") == "full-graph-asymmetric-mathematical"
        ),
        None,
    )
    assert fixture is not None
    document = validate(source)
    report = validate_cpts(document, fixture["cpt_payload"])
    assert report.valid, report.errors
    assert len(report.tables) == 12
    artifact = build_effective_artifact(document, fixture["cpt_payload"])
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    expected = fixture["expected"]
    assert set(expected) == set(DECLARED_ORDER)
    # Spot-check worked marginals (full derivations in examples.json work field).
    assert expected["AcuteDystoniaReviewPathway"]["UrgentAirwayAssessment"] == pytest.approx(0.2)
    assert expected["AcuteDystoniaReviewPathway"][
        "ReviewAcuteAnticholinergicTreatment"
    ] == pytest.approx(0.24)
    assert expected["AnticholinergicSafetyReviewFocus"][
        "ReviewPatientSpecificConcernAndBurden"
    ] == pytest.approx(0.105)
    assert expected["AcuteDystoniaAnticholinergicContinuationReview"][
        "ReviewStoppingOrAvoidingUnneededContinuation"
    ] == pytest.approx(0.36)
    assert expected["AcuteDystoniaAntipsychoticRegimenReview"][
        "AssessMedicationAssociation"
    ] == pytest.approx(0.5)
    projections = fixture["context_examples"]
    assert len(projections) == 2
    results = [
        infer_effective(artifact, query_nodes=query, patient_projection=p) for p in projections
    ]
    for result in results:
        assert dict(result.evidence) == {}
        for posterior in result.posteriors:
            assert dict(
                zip(posterior.states, posterior.probabilities, strict=True)
            ) == pytest.approx(expected[posterior.node_id], abs=1e-6)
    repeated = replay(artifact, results[0], patient_projection=projections[1])
    for before, after in zip(results[0].posteriors, repeated.posteriors, strict=True):
        assert (after.node_id, after.states) == (before.node_id, before.states)
        assert after.probabilities == pytest.approx(before.probabilities, abs=1e-6)
    assert dict(repeated.evidence) == {}


def test_acute_dystonia_review_template_preserves_urgent_without_treatment_choice() -> None:
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    prompt = (PACKAGE / "prompt.txt").read_text().lower()
    assert "estimate" in prompt and "percentage" in prompt
    for marker in (
        "choose applicability",
        "write a plan",
        "full record",
        "execute the network",
        "modify the structure",
        "argmax",
    ):
        assert marker not in prompt
    assert "f4" in prompt and "empty" in prompt
    for forbidden in ("dose", "route", "frequency", "active/stopped"):
        assert forbidden in prompt
    assert "acute dystonia dx criteria" in prompt
    assert "mild" in prompt and "moderate" in prompt and "severe" in prompt
    template = package["template"]
    assert template["version"] == "s36-v1"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    assert len(branches) == 17
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    for node, states in {
        "AcuteDystoniaReviewPathway": (
            "UrgentAirwayAssessment",
            "ReviewAcuteAnticholinergicTreatment",
            "AssessEpisodeAndDifferential",
            "PostResolutionReview",
            "NoAcuteTreatmentRule",
        ),
        "AnticholinergicSafetyReviewFocus": (
            "ReviewPatientSpecificConcernAndBurden",
            "ReviewPatientSpecificConcern",
            "ReviewAgeOrMedicationBurden",
            "RoutineIndividualizedReview",
        ),
        "AcuteDystoniaAnticholinergicContinuationReview": (
            "NotPostResolutionReview",
            "EstablishEpisodeAssociation",
            "ReviewNeedAndPatientConcerns",
            "ConsiderShortTermPreventionReview",
            "ReviewStoppingOrAvoidingUnneededContinuation",
        ),
        "AcuteDystoniaAntipsychoticRegimenReview": (
            "ReviewDoseOrAlternative",
            "AssessMedicationAssociation",
            "NoEpisodeSpecificReview",
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
    urgent_banner = template["urgent_banner"].lower()
    assert "saved" in urgent_banner and "independently" in urgent_banner
    assert "not a computed risk percentage" in urgent_banner


def test_acute_dystonia_open_assumptions_stay_open_without_approval() -> None:
    package, _ = _read_package()
    review = package["review"]
    assumptions = " ".join(review["assumptions"]).lower()
    assert "deterministic" in assumptions
    assert "estimated" in assumptions
    assert "open assumption" in assumptions
    assert "without approval" in assumptions
    assert "quality measurement" in assumptions
    assert "table 10" in assumptions
    assert "deterministic" in review["open_assumptions"].lower()
    assert "estimated" in review["open_assumptions"].lower()
    assert "quality measurement" in review["open_assumptions"].lower()
    assert "table 10" in review["open_assumptions"].lower()
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
