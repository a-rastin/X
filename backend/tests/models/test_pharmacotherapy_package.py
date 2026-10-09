"""S27 candidate content through the public T5 package and inference seams.

Shape-valid drafts remain unapproved; clinical examples never validate themselves.
"""

from __future__ import annotations

import json
from decimal import Decimal
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
PACKAGE = ROOT / "content/questions/pharmacotherapy"
CANDIDATE_SOURCE_PATHS = (
    "candidate/history/pharmacotherapy.v2/h_prior_treatment_difficulty",
    "candidate/history/pharmacotherapy.v2/h_preference_barrier",
    "candidate/history/pharmacotherapy.v2/h_physical_vulnerability",
    "candidate/history/pharmacotherapy.v2/h_substance_contribution",
    "candidate/history/pharmacotherapy.v2/h_exposure_modifier",
    "candidate/history/pharmacotherapy.v2/h_antipsychotic_treatment_context",
    "candidate/history/pharmacotherapy.v2/h_trial_duration_review",
    "candidate/history/pharmacotherapy.v2/h_urgent_safety_concern",
    "candidate/history/pharmacotherapy.v2/h_monitoring_gap",
)
SOURCE_BN = ROOT / "project-documents/bayesian-networks/BN-04.xml"
STATEMENT = ROOT / "project-documents/medical-documents/guideline/STATEMENT-04.md"
LOOKUP_NODES = (
    "AkathisiaSourceCategory",
    "ParkinsonismSourceCategory",
    "DystoniaSourceCategory",
    "TardiveDyskinesiaSourceCategory",
    "HyperprolactinemiaSourceCategory",
    "AnticholinergicEffectsSourceCategory",
    "SedationSourceCategory",
    "SeizuresSourceCategory",
    "OrthostasisSourceCategory",
    "QTProlongationSourceCategory",
    "WeightGainSourceCategory",
    "HyperlipidemiaSourceCategory",
    "GlucoseAbnormalitiesSourceCategory",
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


def test_pharmacotherapy_six_file_draft_validates_without_approval_or_evidence() -> None:
    package, source = _read_package()
    document = validate(source)
    assert document.xsd.valid
    report = validate_question_package(
        package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS
    )
    assert report.valid, report.errors
    loaded = load_question_package(package, document, known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    assert loaded.question_key == "pharmacotherapy"
    assert package["manifest"]["workflow"] == "registration"
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["manifest"]["execution_evidence"] == {}


def test_pharmacotherapy_source_fidelity_is_auditable_without_clinical_probability_claims() -> None:
    package, source = _read_package()
    original = validate(SOURCE_BN.read_bytes())
    assert (
        original.source_sha256 == "5e059514127a618ac887a04a04180afa1f8b768fa2a5b27f34a3eb13fc5a8e05"
    )
    retained = [v for v in original.networks[0].variables if v.name != "DoseExposureCategory"]
    actual = validate(source).networks[0]
    assert [v.name for v in actual.variables] == [v.name for v in retained]
    extensions = {
        "OralAntipsychoticMedication": ["NoEstablishedTreatment"],
        "TrialDuration": ["NoEstablishedTreatment"],
        **{
            node: ["NotAssessable"]
            for node in (
                "EffectiveExposure",
                "SymptomResponse",
                "FunctionalBenefit",
                "SignificantAntipsychoticSideEffects",
                "ResponseReviewFlag",
                "TolerabilityReviewFlag",
            )
        },
        **{node: ["NotApplicable"] for node in LOOKUP_NODES},
    }
    definitions = {d.for_node: d for d in actual.definitions}
    for expected, variable in zip(retained, actual.variables, strict=True):
        assert variable.states == expected.states + tuple(extensions.get(expected.name, []))
        expected_parents = tuple(
            p.removeprefix("proposed_parent=")
            for p in expected.properties
            if p.startswith("proposed_parent=") and p != "proposed_parent=DoseExposureCategory"
        )
        assert definitions[variable.name].parents == expected_parents

    # Independent Markdown source, with the printed Table-6 legend; no package-derived expectations.
    section = STATEMENT.read_text().split("## Table 6\n", 1)[1].split("## Table 7\n", 1)[0]
    medication_states = next(v.states for v in retained if v.name == "OralAntipsychoticMedication")
    symbols: dict[str, list[str]] = {medication: [] for medication in medication_states}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] in symbols:
            symbols[cells[0]].extend(cell for cell in cells[2:] if cell in ("+", "++", "+++"))
    assert sum(map(len, symbols.values())) == 286
    label_positions = {"+": 0, "++": 1, "+++": 2}
    for column, node in enumerate(LOOKUP_NODES):
        values = [Decimal(value) for value in definitions[node].table]
        for row, medication in enumerate(medication_states):
            expected_row = [Decimal(0)] * 4
            expected_row[label_positions[symbols[medication][column]]] = Decimal(1)
            assert values[row * 4 : row * 4 + 4] == expected_row
        assert values[-4:] == [Decimal(0), Decimal(0), Decimal(0), Decimal(1)]

    review = package["review"]
    assert review.get("source_diff") == {
        "removed_nodes": ["DoseExposureCategory"],
        "removed_edges": [["DoseExposureCategory", "EffectiveExposure"]],
        "materialized_proposed_parents": True,
        "state_extensions": extensions,
    }
    audit = review["reference_table_audit"]
    assert audit["source_locator"] == "STATEMENT-04.md#table-6"
    assert audit["compared_cells"] == 286
    assert audit["mismatches"] == []
    assert audit["source_label_semantics"] == {"+": "Seldom", "++": "Sometimes", "+++": "Often"}
    assert audit["clinical_probability_claim"] is False
    assert audit["synthetic_extension_rows"] == {
        node: "NoEstablishedTreatment -> NotApplicable" for node in LOOKUP_NODES
    }


def test_pharmacotherapy_known_absence_and_required_uncertainty_have_distinct_draft_contracts() -> (
    None
):
    package, source = _read_package()
    load_question_package(package, validate(source), known_source_prefixes=CANDIDATE_SOURCE_PATHS)
    manifest = package["manifest"]
    assert manifest.get("missingness_contract") == {
        "required_source_paths": list(CANDIDATE_SOURCE_PATHS),
        "required_unknown_values": ["unknown", "not_assessed"],
        "required_unknown_policy": "needs_clarification",
        "required_missing_policy": "needs_clarification",
        "required_conflict_policy": "needs_clarification",
        "known_no_treatment": {
            "applicability": "true",
            "treatment_state": "NoEstablishedTreatment",
            "duration_state": "NoEstablishedTreatment",
            "outcome_state": "NotAssessable",
            "lookup_state": "NotApplicable",
            "automatic_medication_selection": False,
        },
        "evidence": {},
    }
    assert manifest["applicability"]["expression"] == "true"
    assert manifest["applicability"]["required_fields"] == list(CANDIDATE_SOURCE_PATHS)
    mappings = manifest["patient_mappings"]
    assert [mapping["allowed_source_paths"] for mapping in mappings] == [
        [path] for path in CANDIDATE_SOURCE_PATHS
    ]
    assert all(
        mapping["usage"] == "cpt_context" and mapping["missing_policy"] == "needs_clarification"
        for mapping in mappings
    )
    history = json.loads((ROOT / "content/history/history.pharmacotherapy.v2.json").read_text())
    assert history["status"] == "awaiting_review"
    assert [field["source_path"] for field in history["fields"]] == list(CANDIDATE_SOURCE_PATHS)
    for field in history["fields"]:
        assert field["source_locator"].strip()
        assert field["operationalization"].strip()
        assert field["provenance_required"] is True

    cases = {case["id"]: case for case in package["examples"]["clinical"]}
    treatment_path, duration_path = CANDIDATE_SOURCE_PATHS[5:7]
    for case_id in (
        "established-response-concern",
        "established-tolerability-concern",
        "no-established-treatment",
    ):
        case = cases[case_id]
        assert set(case["inputs"]) == set(CANDIDATE_SOURCE_PATHS)
        assert case["expected_for_review"]["status"] == "execute"
        assert "candidate" in case["note"].lower()
    no_treatment = cases["no-established-treatment"]
    assert no_treatment["inputs"][treatment_path] == "NoEstablishedTreatment"
    assert no_treatment["inputs"][duration_path] == "NoEstablishedTreatment"
    expectation = no_treatment["expected_for_review"]
    assert expectation["applicability"] == "true"
    assert expectation["SymptomResponse"] == "NotAssessable"
    assert expectation["SignificantAntipsychoticSideEffects"] == "NotAssessable"
    assert expectation["medication_selection"] is None
    for reason in ("unknown", "not-assessed", "missing", "conflict"):
        case = cases[f"required-{reason}"]
        assert case["expected_for_review"]["status"] == "needs_clarification"
        assert "candidate" in case["note"].lower()
        if reason == "missing":
            assert treatment_path not in case["inputs"]
        elif reason == "conflict":
            assert len(set(case["inputs"][treatment_path])) == 2
        else:
            assert case["inputs"][treatment_path] == reason.replace("-", "_")


def test_pharmacotherapy_full_graph_asymmetric_fixture_infers_and_replays_without_clamping() -> (
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
    assert len(report.tables) == 31
    artifact = build_effective_artifact(document, payload)
    assert dict(artifact.evidence) == {}
    query = list(
        dict.fromkeys(
            [
                "PriorTreatmentDifficulty",
                "PreferenceBarrier",
                "AdherenceDifficulty",
                *package["manifest"]["query_nodes"],
            ]
        )
    )
    expected = {
        "PriorTreatmentDifficulty": {"Absent": 0.8, "Present": 0.2},
        "PreferenceBarrier": {"Absent": 0.7, "Present": 0.3},
        # .7*.8*.1 + .7*.2*.4 + .3*.8*.7 + .3*.2*.9 = .334.
        "AdherenceDifficulty": {"Absent": 0.666, "Present": 0.334},
        "OralAntipsychoticMedication": {
            name: (1.0 if name == "Aripiprazole" else 0.0)
            for name in (
                "Chlorpromazine",
                "Fluphenazine",
                "Haloperidol",
                "Loxapine",
                "Molindone",
                "Perphenazine",
                "Pimozide",
                "Thioridazine",
                "Thiothixene",
                "Trifluoperazine",
                "Aripiprazole",
                "Asenapine",
                "Brexpiprazole",
                "Cariprazine",
                "Clozapine",
                "Iloperidone",
                "Lurasidone",
                "Olanzapine",
                "Paliperidone",
                "Quetiapine",
                "Risperidone",
                "Ziprasidone",
                "NoEstablishedTreatment",
            )
        },
        # Exposure = .666*(.2,.7,.1)+.334*(.7,.25,.05) = (.367,.5497,.0833).
        # Response = .367*(.4,.4,.2)+.5497*(.1,.3,.6)+.0833*(.1,.1,.8).
        "SymptomResponse": {
            "Inadequate": 0.2101,
            "Partial": 0.32004,
            "Adequate": 0.46986,
            "NotAssessable": 0.0,
        },
        # Constant rows on every reachable established-treatment configuration.
        "FunctionalBenefit": {"NotMeaningful": 0.7, "Meaningful": 0.3, "NotAssessable": 0.0},
        "SignificantAntipsychoticSideEffects": {
            "Absent": 0.6,
            "Present": 0.4,
            "NotAssessable": 0.0,
        },
        # Response flag triggers on inadequate or partial: .2101+.32004=.53014.
        "ResponseReviewFlag": {
            "NotTriggered": 0.46986,
            "Triggered": 0.53014,
            "NotAssessable": 0.0,
        },
        # Tolerability not-triggered requires no side effects AND no barrier: .6*.7=.42.
        "TolerabilityReviewFlag": {"NotTriggered": 0.42, "Triggered": 0.58, "NotAssessable": 0.0},
        # Independent 80/20 safety/monitoring roots map directly through deterministic flags.
        "SafetyAssessmentFlag": {"NotTriggered": 0.8, "Triggered": 0.2},
        "MonitoringReviewFlag": {"NotTriggered": 0.8, "Triggered": 0.2},
    }
    assert query == list(expected)
    for node, marginal in expected.items():
        assert fixture["expected"][node] == marginal
    projections = fixture["context_examples"]
    assert len(projections) == 2
    assert projections[0]["PriorTreatmentDifficulty"] == "Absent"
    assert projections[1]["PriorTreatmentDifficulty"] == "Present"
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
        == "5e059514127a618ac887a04a04180afa1f8b768fa2a5b27f34a3eb13fc5a8e05"
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


def test_pharmacotherapy_no_treatment_math_preserves_unassessable_and_safety_topics() -> None:
    package, source = _read_package()
    fixture = next(
        (
            example
            for example in package["examples"]["numerical"]
            if example.get("id") == "full-graph-no-established-treatment-mathematical"
        ),
        None,
    )
    assert fixture is not None, "Known no treatment needs a separate complete mathematical fixture"
    document = validate(source)
    report = validate_cpts(document, fixture["cpt_payload"])
    assert report.valid, report.errors
    assert len(report.tables) == 31
    artifact = build_effective_artifact(document, fixture["cpt_payload"])
    sentinels = {
        "OralAntipsychoticMedication": "NoEstablishedTreatment",
        "TrialDuration": "NoEstablishedTreatment",
        **{
            node: "NotAssessable"
            for node in (
                "EffectiveExposure",
                "SymptomResponse",
                "FunctionalBenefit",
                "SignificantAntipsychoticSideEffects",
                "ResponseReviewFlag",
                "TolerabilityReviewFlag",
            )
        },
        **{node: "NotApplicable" for node in LOOKUP_NODES},
    }
    parallel = {
        "UrgentSafetyConcern": {"Absent": 0.8, "Present": 0.2},
        "MonitoringGap": {"Absent": 0.8, "Present": 0.2},
        "SafetyAssessmentFlag": {"NotTriggered": 0.8, "Triggered": 0.2},
        "MonitoringReviewFlag": {"NotTriggered": 0.8, "Triggered": 0.2},
    }
    result = infer_effective(
        artifact,
        query_nodes=[*sentinels, *parallel],
        patient_projection={"OralAntipsychoticMedication": "Aripiprazole"},
    )
    assert dict(result.evidence) == {}
    for posterior in result.posteriors:
        marginal = dict(zip(posterior.states, posterior.probabilities, strict=True))
        if posterior.node_id in sentinels:
            state = sentinels[posterior.node_id]
            assert fixture["expected"][posterior.node_id][state] == 1.0
            assert marginal[state] == pytest.approx(1.0, abs=1e-6)
            assert sum(p for s, p in marginal.items() if s != state) == pytest.approx(0, abs=1e-6)
        else:
            assert fixture["expected"][posterior.node_id] == parallel[posterior.node_id]
            assert marginal == pytest.approx(parallel[posterior.node_id], abs=1e-6)
    repeated = replay(artifact, result)
    assert dict(repeated.evidence) == {}
    for before, after in zip(result.posteriors, repeated.posteriors, strict=True):
        assert (before.node_id, before.states) == (after.node_id, after.states)
        assert after.probabilities == pytest.approx(before.probabilities, abs=1e-6)
    assert package["manifest"]["applicability"]["expression"] == "true"
    assert document.source_bytes == source == (PACKAGE / "network.xml").read_bytes()
