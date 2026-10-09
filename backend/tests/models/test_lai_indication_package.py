"""S30 candidate content through the public T5 package and inference seams.

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
PACKAGE = ROOT / "content/questions/lai_indication_choice"
PREFIX = "candidate/history/lai_indication_choice.v1/"
CANDIDATE_SOURCE_PATHS = (
    f"{PREFIX}h_schizophrenia_established",
    f"{PREFIX}h_lai_preference",
    f"{PREFIX}h_adherence_history",
    f"{PREFIX}h_discussion_context",
    f"{PREFIX}h_oral_experience",
    f"{PREFIX}h_prior_nms",
    f"{PREFIX}h_product_plan",
    f"{PREFIX}h_delivery_barriers",
    f"{PREFIX}h_lai_treatment_status",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-10.xml"
STATEMENT = ROOT / "project-documents/medical-documents/guideline/STATEMENT-10.md"
DECLARED_ORDER = (
    "SchizophreniaEstablished",
    "LAIPreference",
    "AdherenceHistory",
    "AdditionalDiscussionContext",
    "SameDrugOralExperience",
    "PriorNeurolepticMalignantSyndrome",
    "ProductPlanReviewed",
    "DeliveryBarriers",
    "LAITreatmentStatus",
    "LAIGuidelineCriterion",
    "DiscussionOpportunity",
    "ClinicalReviewFocus",
    "DeliveryReviewFocus",
    "LAIReviewPathway",
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


def test_lai_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "lai_indication_choice"
    assert package["manifest"]["workflow"] == "registration"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}
    assert (
        package["manifest"]["network_hash"]
        == "e0bc286f7402c5e73905830b1ae9ee88ca6d9d911500a99e7b8e9d67d315a806"
    )
    assert document.source_sha256 == package["manifest"]["network_hash"]
    assert package["review"]["source_hashes"]["network.xml"] == package["manifest"]["network_hash"]
    assert package["manifest"]["prompt_version"] == "s30-v1"
    assert package["manifest"]["template_version"] == "s30-v1"
    assert package["manifest"]["query_nodes"] == [
        "LAIGuidelineCriterion",
        "DiscussionOpportunity",
        "ClinicalReviewFocus",
        "DeliveryReviewFocus",
        "LAIReviewPathway",
    ]


def test_lai_source_fidelity_is_auditable_without_clinical_probability_claims() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "b18db707328c793a9b8c586ea9bba277ee12c4283fb604e42f5b3d311994ce80"
    )
    assert STATEMENT.read_bytes()  # STATEMENT-10 exists as review source
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == list(DECLARED_ORDER)
    expected_states = {
        "SchizophreniaEstablished": ("Yes", "No"),
        "LAIPreference": ("PrefersLAI", "PrefersOral", "Undecided", "DeclinesLAI"),
        "AdherenceHistory": ("Adequate", "Poor", "Uncertain"),
        "AdditionalDiscussionContext": ("Present", "Absent"),
        "SameDrugOralExperience": ("Supported", "Concern", "NotEstablished"),
        "PriorNeurolepticMalignantSyndrome": ("Yes", "No"),
        "ProductPlanReviewed": ("Yes", "No"),
        "DeliveryBarriers": ("Present", "Absent"),
        "LAITreatmentStatus": ("Considering", "Current", "Previous"),
        "LAIGuidelineCriterion": ("Present", "NotEstablished"),
        "DiscussionOpportunity": (
            "OutsideScope",
            "GuidelineCriterion",
            "ContextualDiscussion",
            "RoutineEducation",
        ),
        "ClinicalReviewFocus": (
            "NMSCaution",
            "ReviewEfficacyOrTolerabilityConcern",
            "EstablishCandidateEvidence",
            "CandidateEvidenceReviewed",
        ),
        "DeliveryReviewFocus": (
            "ReviewProductPlanAndBarriers",
            "ReviewProductPlan",
            "AddressBarriers",
            "ReviewItemsDocumented",
        ),
        "LAIReviewPathway": (
            "OutsideScope",
            "RespectPreferenceReviewAlternatives",
            "ReviewCurrentLAITreatment",
            "IndividualizedPriorLAIReview",
            "DiscussLAIOption",
            "ConsiderContextualDiscussion",
            "OfferRoutineEducation",
        ),
    }
    expected_parents = {
        "SchizophreniaEstablished": (),
        "LAIPreference": (),
        "AdherenceHistory": (),
        "AdditionalDiscussionContext": (),
        "SameDrugOralExperience": (),
        "PriorNeurolepticMalignantSyndrome": (),
        "ProductPlanReviewed": (),
        "DeliveryBarriers": (),
        "LAITreatmentStatus": (),
        "LAIGuidelineCriterion": (
            "SchizophreniaEstablished",
            "LAIPreference",
            "AdherenceHistory",
        ),
        "DiscussionOpportunity": (
            "SchizophreniaEstablished",
            "LAIGuidelineCriterion",
            "AdditionalDiscussionContext",
        ),
        "ClinicalReviewFocus": (
            "PriorNeurolepticMalignantSyndrome",
            "SameDrugOralExperience",
        ),
        "DeliveryReviewFocus": (
            "ProductPlanReviewed",
            "DeliveryBarriers",
        ),
        "LAIReviewPathway": (
            "DiscussionOpportunity",
            "LAIPreference",
            "LAITreatmentStatus",
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


def test_lai_gate_true_false_unknown_have_distinct_draft_contracts() -> None:
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
            "inputs known; gate false is SchizophreniaEstablished explicitly No with no "
            "unknown/missing/conflict among required inputs; otherwise gate unknown."
        ),
        "false_gate": {
            "status": "not_applicable",
            "not_a_negative_posterior": True,
            "posterior_claim": None,
        },
        "unknown_gate": {"status": "needs_clarification", "pauses_before_estimation": True},
        "preference_rule": (
            "LAIPreference Undecided is an explicit known uncertain value "
            "that still executes; DeclinesLAI is an explicit known decline "
            "that still executes with preference-respecting review; "
            "PrefersLAI/PrefersOral require documented shared discussion. "
            "Absent discussion (unknown, not_assessed, missing or conflicting "
            "preference) pauses for clarification and is never coerced to "
            "PrefersOral or DeclinesLAI."
        ),
        "exposure_rule": (
            "AdherenceHistory Uncertain is an explicit known value that still "
            "executes per the poor-or-uncertain-adherence suggestion; "
            "SameDrugOralExperience NotEstablished is an explicit known value "
            "that still executes with candidate-evidence review; "
            "LAITreatmentStatus Considering/Current/Previous are explicit "
            "known values. Unknown, not_assessed, missing or conflicting "
            "exposure input pauses for clarification and is never coerced to "
            "Adequate or Supported."
        ),
        "scope_rule": (
            "Discussion and review only; no specific LAI product selection "
            "and no product-choice contract. No dose, route, frequency or "
            "active/stopped fields are collected or accepted."
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
        (ROOT / "content/history/history.lai_indication_choice.v1.json").read_text()
    )
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    for field in history["fields"]:
        assert field["source_locator"].strip()
        assert field["operationalization"].strip()
        assert field["provenance_required"] is True
        assert field["usage"] == "cpt_context"
        assert "unknown" in field["permitted"]
        assert "not_assessed" in field["permitted"]
        assert field["unknown_values"] == ["unknown", "not_assessed"]
        assert field["missing_policy"] == "needs_clarification"
        assert field["conflict_policy"] == "needs_clarification"

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    for case_id in (
        "gate-true-standard",
        "gate-true-declines-respect",
        "gate-false-not-applicable",
        "gate-unknown-needs-clarification",
        "gate-missing-needs-clarification",
        "gate-conflict-needs-clarification",
    ):
        assert "candidate" in cases[case_id]["note"].lower()
        assert "expected_for_review" in cases[case_id]
    assert cases["gate-true-standard"]["expected_for_review"]["status"] == "execute"
    assert cases["gate-true-declines-respect"]["expected_for_review"]["status"] == "execute"
    assert (
        cases["gate-true-declines-respect"]["expected_for_review"]["pathway_review"]
        == "RespectPreferenceReviewAlternatives"
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


def test_lai_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> None:
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
    assert payload["network_hash"] == package["manifest"]["network_hash"]
    assert sum(len(table["rows"]) for table in payload["tables"]) == 99
    assert sum(len(table["rows"]) * len(table["states"]) for table in payload["tables"]) == 479
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(DECLARED_ORDER)
    # Independent literals with worked derivations in examples.json work field.
    # Guideline Present .9*(.5*.8+.3*.6+.2*(.4*.7+.3*.2+.2*.3+.1*.1))=.5958.
    # Discussion: Outside .172126 etc. with ordered-parent transpose guard.
    # Clinical: NMS .155 etc. Delivery: Both .268/Plan .352/Barriers .172/Doc .208
    # with swapped parents swapping .352/.172. Pathway: Outside .172126 etc.
    expected = {
        "SchizophreniaEstablished": {"Yes": 0.9, "No": 0.1},
        "LAIPreference": {
            "PrefersLAI": 0.4,
            "PrefersOral": 0.3,
            "Undecided": 0.2,
            "DeclinesLAI": 0.1,
        },
        "AdherenceHistory": {"Adequate": 0.2, "Poor": 0.5, "Uncertain": 0.3},
        "AdditionalDiscussionContext": {"Present": 0.3, "Absent": 0.7},
        "SameDrugOralExperience": {"Supported": 0.5, "Concern": 0.3, "NotEstablished": 0.2},
        "PriorNeurolepticMalignantSyndrome": {"Yes": 0.1, "No": 0.9},
        "ProductPlanReviewed": {"Yes": 0.7, "No": 0.3},
        "DeliveryBarriers": {"Present": 0.4, "Absent": 0.6},
        "LAITreatmentStatus": {"Considering": 0.5, "Current": 0.3, "Previous": 0.2},
        "LAIGuidelineCriterion": {"Present": 0.5958, "NotEstablished": 0.4042},
        "DiscussionOpportunity": {
            "OutsideScope": 0.172126,
            "GuidelineCriterion": 0.432774,
            "ContextualDiscussion": 0.177336,
            "RoutineEducation": 0.217764,
        },
        "ClinicalReviewFocus": {
            "NMSCaution": 0.155,
            "ReviewEfficacyOrTolerabilityConcern": 0.268,
            "EstablishCandidateEvidence": 0.2085,
            "CandidateEvidenceReviewed": 0.3685,
        },
        "DeliveryReviewFocus": {
            "ReviewProductPlanAndBarriers": 0.268,
            "ReviewProductPlan": 0.352,
            "AddressBarriers": 0.172,
            "ReviewItemsDocumented": 0.208,
        },
        "LAIReviewPathway": {
            "OutsideScope": 0.172126,
            "RespectPreferenceReviewAlternatives": 0.0649161,
            "ReviewCurrentLAITreatment": 0.2483622,
            "IndividualizedPriorLAIReview": 0.1655748,
            "DiscussLAIOption": 0.1514709,
            "ConsiderContextualDiscussion": 0.088668,
            "OfferRoutineEducation": 0.108882,
        },
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
    # Ordered-parent transpose guards: asymmetric parents must stay distinct,
    # so a transpose bug cannot pass by symmetrizing these pairs.
    assert (
        expected["DeliveryReviewFocus"]["ReviewProductPlan"]
        != expected["DeliveryReviewFocus"]["AddressBarriers"]
    )
    assert (
        expected["DiscussionOpportunity"]["GuidelineCriterion"]
        != expected["DiscussionOpportunity"]["ContextualDiscussion"]
    )
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
    # Opposing projections must agree: patient context never clamps a node.
    for first, second in zip(results[0].posteriors, results[1].posteriors, strict=True):
        assert (second.node_id, second.states) == (first.node_id, first.states)
        assert second.probabilities == pytest.approx(first.probabilities, abs=1e-6)
    repeated = replay(artifact, results[0], patient_projection=projections[1])
    for before, after in zip(results[0].posteriors, repeated.posteriors, strict=True):
        assert (after.node_id, after.states) == (before.node_id, before.states)
        assert after.probabilities == pytest.approx(before.probabilities, abs=1e-6)
    assert dict(repeated.evidence) == {}
    assert document.source_bytes == source == (PACKAGE / "network.xml").read_bytes()
    assert (
        validate(SOURCE_BN.read_bytes()).source_sha256
        == "b18db707328c793a9b8c586ea9bba277ee12c4283fb604e42f5b3d311994ce80"
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


def test_lai_discussion_review_renders_without_product_selection() -> None:
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
    assert "Undecided" in prompt_text
    assert "route" in lowered_prompt
    assert "frequency" in lowered_prompt
    assert "product-choice contract" in prompt_text
    assert "empty" in lowered_prompt

    template = package["template"]
    assert template["version"] == "s30-v1"
    assert template["status"] == "awaiting_review"
    assert "prose" not in template and "free_text" not in template
    branches = template["branches"]
    by_state = {(b["when"]["node"], b["when"]["state"]): b for b in branches}
    assert len(branches) == 21
    assert sorted({branch["when"]["node"] for branch in branches}) == sorted(
        package["manifest"]["query_nodes"]
    )
    for node, states in {
        "LAIGuidelineCriterion": ("Present", "NotEstablished"),
        "DiscussionOpportunity": (
            "OutsideScope",
            "GuidelineCriterion",
            "ContextualDiscussion",
            "RoutineEducation",
        ),
        "ClinicalReviewFocus": (
            "NMSCaution",
            "ReviewEfficacyOrTolerabilityConcern",
            "EstablishCandidateEvidence",
            "CandidateEvidenceReviewed",
        ),
        "DeliveryReviewFocus": (
            "ReviewProductPlanAndBarriers",
            "ReviewProductPlan",
            "AddressBarriers",
            "ReviewItemsDocumented",
        ),
        "LAIReviewPathway": (
            "OutsideScope",
            "RespectPreferenceReviewAlternatives",
            "ReviewCurrentLAITreatment",
            "IndividualizedPriorLAIReview",
            "DiscussLAIOption",
            "ConsiderContextualDiscussion",
            "OfferRoutineEducation",
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
    respect = by_state[("LAIReviewPathway", "RespectPreferenceReviewAlternatives")]
    assert "respect" in respect["text"].lower()
    assert "does not select a product" in respect["text"].lower()
    discuss = by_state[("LAIReviewPathway", "DiscussLAIOption")]
    assert "does not prescribe" in discuss["text"].lower()
