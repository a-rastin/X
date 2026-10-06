"""Define the reusable question-package contract and review harness (S25, seam T5).

Seam T5 only: ``validate_question_package`` / ``load_question_package`` plus
the shared registry (``KNOWN_SOURCE_PREFIXES`` / ``SAFE_EXPRESSIONS``) and the
S21/S22/S23 harnesses they integrate with. Never private helpers, DB, HTTP,
provider/MCP, or rendering (runtime section rendering is S45, not here).
Expected values are worked literals, never implementation output. Synthetic
fixtures only; source XML unmodified.

Slices (one observable behavior at a time, small synthetic XMLBIF in-test):
1. Complete synthetic package validates (fixed variables/types/states/order,
   typed mappings, gate/missingness, all-CPT contract, query, prompt,
   template, review references) and the loader freezes a canonical hash.
   One reusable validator covers a second question key (content, not code).
2. Rejections: unknown/note source paths, posterior chaining, any
   patient-evidence mapping (usage or execution evidence, review cannot
   enable it), incomplete CPT schema (contract and S23 payload), arbitrary
   expression, and fixed-variable mismatches.
3. Review dossier for S27/S29-S38: presence/shape only, never approval.
   Draft (awaiting_review) validates without activation.
4. Template slots/branches: escaped {variable} + explicit branches on
   declared states with allowlisted operators; undeclared states, unsupported
   operators, argmax selection, bad slots, and LLM prose are rejected.
"""

from __future__ import annotations

import copy
from typing import Any

from x_insight.models.admission import (
    KNOWN_SOURCE_PREFIXES as ADMISSION_PREFIXES,
)
from x_insight.models.admission import (
    SAFE_EXPRESSIONS as ADMISSION_EXPRESSIONS,
)
from x_insight.models.admission import (
    admission_decision,
    validate_package_contract,
)
from x_insight.models.inference import validate_cpts
from x_insight.models.question_package import (
    DOSSIER_FIELDS,
    KNOWN_SOURCE_PREFIXES,
    MANIFEST_SCHEMA_VERSION,
    SAFE_EXPRESSIONS,
    SUPPORTED_TEMPLATE_OPERATORS,
    QuestionPackageError,
    load_question_package,
    validate_question_package,
)
from x_insight.models.validation import validate


def _two_node_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>TwoNode</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _cpt_tables(doc_hash: str) -> dict[str, Any]:
    return {
        "network_hash": doc_hash,
        "tables": [
            {
                "node_id": "A",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["80", "20"]}],
            },
            {
                "node_id": "B",
                "parent_ids": ["A"],
                "states": ["no", "yes"],
                "rows": [
                    {"parent_states": ["no"], "percentages": ["90", "10"]},
                    {"parent_states": ["yes"], "percentages": ["30", "70"]},
                ],
            },
        ],
    }


def _complete_package(doc_hash: str, **overrides: Any) -> dict[str, Any]:
    package: dict[str, Any] = {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": "synthetic_example",
            "title": "Synthetic example question",
            "workflow": "registration",
            "version": "v1",
            "review_status": "draft",
            "network_file": "network.xml",
            "network_hash": doc_hash,
            "source_refs": ["synthetic/source.md#example"],
            "declared_node_order": ["A", "B"],
            "variables": [
                {
                    "node_id": "A",
                    "kind": "nature",
                    "patient_value_type": "tristate",
                    "states": ["no", "yes"],
                    "ordered_parents": [],
                },
                {
                    "node_id": "B",
                    "kind": "nature",
                    "patient_value_type": "tristate",
                    "states": ["no", "yes"],
                    "ordered_parents": ["A"],
                },
            ],
            "patient_mappings": [
                {
                    "node_id": "A",
                    "allowed_source_paths": ["synthetic/history/h_example"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
                {
                    "node_id": "B",
                    "allowed_source_paths": ["synthetic/assessments/a_example"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
            ],
            "applicability": {
                "expression": "true",
                "required_fields": [],
                "unknown_policy": "needs_clarification",
            },
            "cpt_contract": {
                "nodes": [
                    {"node_id": "A", "parent_ids": [], "states": ["no", "yes"]},
                    {"node_id": "B", "parent_ids": ["A"], "states": ["no", "yes"]},
                ]
            },
            "query_nodes": ["A", "B"],
            "execution_evidence": {},
            "prompt_version": "v1",
            "template_version": "v1",
        },
        "prompt": {
            "version": "v1",
            "text": (
                "Estimate every CPT in percentage units for this question "
                "using only the supplied inputs. Return strict schema."
            ),
        },
        "template": {
            "version": "v1",
            "branches": [
                {
                    "when": {"node": "B", "state": "yes", "operator": "=="},
                    "text": "Result for {B} is present.",
                },
                {
                    "when": {"node": "B", "state": "no", "operator": "=="},
                    "text": "Result for {B} is absent.",
                },
            ],
        },
        "examples": {
            "numerical": [{"inputs": {}, "expected": {"A": {"no": 0.8, "yes": 0.2}}}],
            "clinical": [
                {
                    "inputs": {},
                    "expected_for_review": {"B": "yes"},
                    "note": "review candidate, never self-validating",
                }
            ],
        },
        "review": {
            "reviewer": "owner",
            "decision": "awaiting_review",
            "date": "2026-10-04",
            "source_hashes": {"network.xml": doc_hash},
            "assumptions": ["synthetic only, no clinical use"],
            "source_comparison": "synthetic source comparison",
            "explicit_graph": "A -> B",
            "reference_table_provenance": "synthetic placeholders, not patient estimates",
            "estimation_instructions": "estimate every CPT including roots",
            "result_mapping": "B yes/no maps to explicit branches",
            "numerical_examples": "two-node 80/20 fixture",
            "clinical_examples": "synthetic clinical review candidate",
            "admission_measurements": "semantic valid, CPT exact",
            "open_assumptions": "synthetic only",
        },
        "cpt_tables": _cpt_tables(doc_hash),
    }
    for key, value in overrides.items():
        package[key] = value
    return package


def _manifest_override(package: dict[str, Any], **fields: Any) -> dict[str, Any]:
    clone = copy.deepcopy(package)
    clone["manifest"] = {**clone["manifest"], **fields}
    return clone


# --- Slice 1: complete package validates; one reusable loader ---


def test_registry_allowists_are_explicit_synthetic_default_deny() -> None:
    assert MANIFEST_SCHEMA_VERSION == "question-package-v1"
    assert KNOWN_SOURCE_PREFIXES == (
        "synthetic/history/",
        "synthetic/assessments/",
        "synthetic/demographics/",
    )
    assert SAFE_EXPRESSIONS == frozenset(
        {"true", "gate == 'true'", "gate == 'false'", "gate == 'unknown'"}
    )
    # S22 hooks share the same registry object values.
    assert ADMISSION_PREFIXES == KNOWN_SOURCE_PREFIXES
    assert ADMISSION_EXPRESSIONS == SAFE_EXPRESSIONS
    assert SUPPORTED_TEMPLATE_OPERATORS == frozenset({"==", "!=", "in", "and", "or", "not"})
    assert len(DOSSIER_FIELDS) == 9


def test_complete_synthetic_package_validates() -> None:
    doc = validate(_two_node_xml())
    assert doc.xsd.valid is True
    package = _complete_package(doc.source_sha256)

    report = validate_question_package(package, doc)
    assert report.valid is True, [e.code for e in report.errors]
    assert report.errors == ()


def test_loader_freezes_canonical_hash_for_handoff() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)

    first = load_question_package(package, doc)
    second = load_question_package(copy.deepcopy(package), doc)

    assert first.question_key == "synthetic_example"
    assert first.network_hash == doc.source_sha256
    assert first.declared_node_order == ("A", "B")
    assert first.query_nodes == ("A", "B")
    assert len(first.package_hash) == 64
    assert first.package_hash == second.package_hash


def test_reusable_validator_covers_second_question_without_new_code() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"] = {
        **clone["manifest"],
        "question_key": "synthetic_second",
        "workflow": "followup",
    }

    # Same validator/loader, different content: no per-question pipeline.
    assert validate_question_package(clone, doc).valid is True
    loaded = load_question_package(clone, doc)
    assert loaded.question_key == "synthetic_second"
    assert loaded.package_hash != load_question_package(package, doc).package_hash


def test_draft_validates_without_activation() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    assert package["manifest"]["review_status"] == "draft"
    assert package["review"]["decision"] == "awaiting_review"

    # S25 shape is valid for a draft; activation is a separate decision.
    assert validate_question_package(package, doc).valid is True
    try:
        load_question_package(package, doc)
    except QuestionPackageError:
        raise AssertionError("draft shape must load without activation")


# --- Slice 2: rejections ---


def test_unknown_source_path_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["patient_mappings"][0] = {
        **clone["manifest"]["patient_mappings"][0],
        "allowed_source_paths": ["unknown/path"],
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "unknown_source_path" in [e.code for e in report.errors]


def test_note_source_path_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["patient_mappings"][0] = {
        **clone["manifest"]["patient_mappings"][0],
        "allowed_source_paths": ["encounters/notes/page"],
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "note_source_path" in [e.code for e in report.errors]


def test_posterior_chaining_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["patient_mappings"][1] = {
        **clone["manifest"]["patient_mappings"][1],
        "allowed_source_paths": ["synthetic/history/posterior_B"],
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "posterior_chaining" in [e.code for e in report.errors]


def test_patient_evidence_usage_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["patient_mappings"][0] = {
        **clone["manifest"]["patient_mappings"][0],
        "usage": "evidence",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "evidence_mapping" in [e.code for e in report.errors]


def test_execution_evidence_rejected_and_review_cannot_enable_it() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    # Even an approved review cannot enable evidence under the contract.
    clone = copy.deepcopy(package)
    clone["manifest"] = {**clone["manifest"], "execution_evidence": {"A": "yes"}}
    clone["review"] = {**clone["review"], "decision": "approved"}

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "evidence_not_empty" in [e.code for e in report.errors]


def test_incomplete_cpt_contract_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["cpt_contract"] = {
        "nodes": [{"node_id": "A", "parent_ids": [], "states": ["no", "yes"]}]
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "incomplete_cpt_contract" in [e.code for e in report.errors]


def test_cpt_tables_inexact_total_rejected_via_s23() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    bad_tables = _cpt_tables(doc.source_sha256)
    bad_tables["tables"][0]["rows"][0]["percentages"] = ["81", "20"]
    clone["cpt_tables"] = bad_tables

    # S23 itself rejects the payload; S25 surfaces it as incomplete contract.
    assert validate_cpts(doc, bad_tables).valid is False
    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "incomplete_cpt_contract" in [e.code for e in report.errors]


def test_arbitrary_expression_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["applicability"] = {
        "expression": "DROP TABLE encounters",
        "required_fields": [],
        "unknown_policy": "needs_clarification",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "unsafe_expression" in [e.code for e in report.errors]


def test_wrong_states_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["variables"][0] = {
        **clone["manifest"]["variables"][0],
        "states": ["no", "maybe"],
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "wrong_states" in [e.code for e in report.errors]


def test_wrong_parent_order_rejected() -> None:
    xml = (
        b'<BIF VERSION="0.3"><NETWORK><NAME>TwoParent</NAME>'
        b"<VARIABLE><NAME>P1</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>P2</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>C</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>P1</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>P2</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>C</FOR><GIVEN>P1</GIVEN><GIVEN>P2</GIVEN>"
        b"<TABLE>0.9 0.1 0.8 0.2 0.3 0.7 0.1 0.9</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    doc = validate(xml)
    assert doc.xsd.valid is True
    package = _complete_package(doc.source_sha256)
    # Reshape the synthetic package to the three-node document order.
    clone = copy.deepcopy(package)
    clone["manifest"] = {
        **clone["manifest"],
        "declared_node_order": ["P1", "P2", "C"],
        "variables": [
            {
                "node_id": "P1",
                "kind": "nature",
                "patient_value_type": "tristate",
                "states": ["no", "yes"],
                "ordered_parents": [],
            },
            {
                "node_id": "P2",
                "kind": "nature",
                "patient_value_type": "tristate",
                "states": ["no", "yes"],
                "ordered_parents": [],
            },
            {
                "node_id": "C",
                "kind": "nature",
                "patient_value_type": "tristate",
                "states": ["no", "yes"],
                # Swapped GIVEN order P2,P1 instead of P1,P2.
                "ordered_parents": ["P2", "P1"],
            },
        ],
        "patient_mappings": [
            {
                "node_id": "P1",
                "allowed_source_paths": ["synthetic/history/h1"],
                "transform": "copy",
                "time_window": "current_encounter",
                "usage": "cpt_context",
                "missing_policy": "needs_clarification",
            },
            {
                "node_id": "P2",
                "allowed_source_paths": ["synthetic/history/h2"],
                "transform": "copy",
                "time_window": "current_encounter",
                "usage": "cpt_context",
                "missing_policy": "needs_clarification",
            },
            {
                "node_id": "C",
                "allowed_source_paths": ["synthetic/assessments/a1"],
                "transform": "copy",
                "time_window": "current_encounter",
                "usage": "cpt_context",
                "missing_policy": "needs_clarification",
            },
        ],
        "cpt_contract": {
            "nodes": [
                {"node_id": "P1", "parent_ids": [], "states": ["no", "yes"]},
                {"node_id": "P2", "parent_ids": [], "states": ["no", "yes"]},
                {"node_id": "C", "parent_ids": ["P2", "P1"], "states": ["no", "yes"]},
            ]
        },
        "query_nodes": ["P1", "P2", "C"],
    }
    clone["template"] = {
        "version": "v1",
        "branches": [
            {"when": {"node": "C", "state": "yes", "operator": "=="}, "text": "Result {C}."},
            {"when": {"node": "C", "state": "no", "operator": "=="}, "text": "Other {C}."},
        ],
    }
    clone.pop("cpt_tables", None)

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "wrong_parent_order" in [e.code for e in report.errors]


def test_wrong_node_order_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = _manifest_override(package, declared_node_order=["B", "A"])

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "wrong_node_order" in [e.code for e in report.errors]


def test_bad_patient_value_type_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["variables"][0] = {
        **clone["manifest"]["variables"][0],
        "patient_value_type": "free_text",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "bad_patient_value_type" in [e.code for e in report.errors]


# --- Slice 3: review dossier (shape only, never approval) ---


def test_incomplete_dossier_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    del clone["review"]["admission_measurements"]

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "incomplete_dossier" in [e.code for e in report.errors]


def test_review_shape_requires_reviewer_decision_date() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["review"] = {**clone["review"], "reviewer": "  "}

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "bad_review_shape" in [e.code for e in report.errors]


def test_examples_require_numerical_and_clinical() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["examples"] = {"numerical": [{"inputs": {}, "expected": {}}], "clinical": []}

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "missing_examples" in [e.code for e in report.errors]


def test_clinical_expected_outputs_stay_review_candidates() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["examples"]["clinical"] = [{"inputs": {}, "note": "no expected_for_review"}]

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "bad_clinical_examples" in [e.code for e in report.errors]


def test_loader_rejects_invalid_package_with_stable_code() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["manifest"]["applicability"] = {
        "expression": "A == 'yes'",
        "required_fields": [],
        "unknown_policy": "needs_clarification",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    try:
        load_question_package(clone, doc)
    except QuestionPackageError as exc:
        assert exc.code == "unsafe_expression"
        assert len(exc.message) <= 500
    else:
        raise AssertionError("invalid package loaded")


# --- Slice 4: template slots/branches ---


def test_undeclared_output_state_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"]["branches"][0] = {
        "when": {"node": "B", "state": "maybe", "operator": "=="},
        "text": "Result for {B}.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "undeclared_output_state" in [e.code for e in report.errors]


def test_unsupported_operator_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"]["branches"][0] = {
        "when": {"node": "B", "state": "yes", "operator": ">"},
        "text": "Result for {B}.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "unsupported_operator" in [e.code for e in report.errors]


def test_argmax_selection_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"]["branches"][0] = {
        "when": {"node": "B", "state": "yes", "operator": "=="},
        "text": "Recommend argmax treatment for {B}.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "argmax_selection" in [e.code for e in report.errors]


def test_bad_template_slot_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"]["branches"][0] = {
        "when": {"node": "B", "state": "yes", "operator": "=="},
        "text": "Result for {ZZZ}.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "bad_template_slot" in [e.code for e in report.errors]


def test_template_without_slots_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"]["branches"][0] = {
        "when": {"node": "B", "state": "yes", "operator": "=="},
        "text": "Result is present.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "bad_template_slot" in [e.code for e in report.errors]


def test_llm_prose_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["template"] = {
        "version": "v1",
        "prose": "The LLM decides the treatment.",
        "branches": [
            {"when": {"node": "B", "state": "yes", "operator": "=="}, "text": "Hit {B}."},
            {"when": {"node": "B", "state": "no", "operator": "=="}, "text": "Miss {B}."},
        ],
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "llm_prose" in [e.code for e in report.errors]


def test_forbidden_prompt_directive_rejected() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)
    clone = copy.deepcopy(package)
    clone["prompt"] = {
        "version": "v1",
        "text": "Estimate CPT percentages, then choose applicability and write a plan.",
    }

    report = validate_question_package(clone, doc)
    assert report.valid is False
    assert "forbidden_prompt_directive" in [e.code for e in report.errors]


# --- Integration: S22 hooks + S23 CPT contract, behavior unchanged ---


def test_integrates_with_s22_admission_and_s23_cpt() -> None:
    doc = validate(_two_node_xml())
    package = _complete_package(doc.source_sha256)

    # S23 CPT payload inside the package is exactly the S23 contract.
    assert validate_cpts(doc, package["cpt_tables"]).valid is True

    # S22 minimal hook still admits with an approved review + S22 keys.
    s22_package = {
        "question_mappings": [{"node": "A"}],
        "prompts": {"prompt": "estimate every CPT"},
        "templates": {"template": "result {value}"},
        "review": {"reviewer": "owner", "decision": "approved", "date": "2026-10-04"},
    }
    assert validate_package_contract(s22_package).valid is True
    decision = admission_decision(doc, s22_package)
    assert decision.executable is True

    # S25 draft (awaiting_review) is shape-valid here but not S22-executable:
    # owner review stays a separate recorded decision.
    assert validate_question_package(package, doc).valid is True
    assert admission_decision(doc, s22_package | {"review": package["review"]}).executable is False


def test_s22_unknown_paths_still_rejected_through_shared_registry() -> None:
    doc = validate(_two_node_xml())
    flagged = {
        "question_mappings": [{"node": "A"}],
        "prompts": {"prompt": "x"},
        "templates": {"template": "y"},
        "review": {"reviewer": "owner", "decision": "approved", "date": "2026-10-04"},
        "source_paths": ["unknown/path"],
        "expressions": ["bad"],
    }
    assert validate_package_contract(flagged).valid is False
    assert admission_decision(doc, flagged).executable is False
