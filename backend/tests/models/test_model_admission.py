"""Enforce model semantics and admission limits (S22, seam T5, FR-31-32/37/NFR-04).

Seam T5 only: ``check_semantics`` / ``validate_package_contract`` /
``admission_decision`` plus S21 ``validate`` and the public ``MAX_*`` /
``ENGINEERING_LIMITS`` guardrails. Never private helpers, DB, HTTP, or
network. Expected values are worked literals, never implementation output.

Slices (one observable behavior at a time, small synthetic XMLBIF in-test):
1. XSD-valid-but-semantic-invalid -> nonexecutable with distinct codes.
2. Valid root/parent ordering passes; decision/utility stay drafts; all 11
   supplied BNs remain inactive (files only read, never modified).
3. Content gating prevents activation; ``reviewed=true`` alone is insufficient.
4. Configured engineering limits + deterministic bounded diagnostics.

XSD success is structural only; ``executable`` means admitted for the exact
inference pipeline (S23), never clinically valid. Numeric thresholds below
are engineering resource/diagnostic bounds, not clinical thresholds.
"""

from __future__ import annotations

from pathlib import Path

from x_insight.models.admission import (
    ENGINEERING_LIMITS,
    MAX_ADMISSION_DIAGNOSTICS,
    MAX_CONTRACT_ERRORS,
    MAX_SEMANTIC_ERRORS,
    NORMALIZATION_TOLERANCE,
    admission_decision,
    check_semantics,
    validate_package_contract,
)
from x_insight.models.validation import (
    MAX_MESSAGE_CHARS,
    MAX_NETWORKS,
    MAX_OUTCOMES_PER_VARIABLE,
    MAX_TABLE_VALUES_PER_DEFINITION,
    MAX_VARIABLES_PER_NETWORK,
    MAX_XML_BYTES,
    MAX_XSD_ERRORS,
    XmlValidationError,
    validate,
)


def _complete_package(**overrides):
    package = {
        "question_mappings": [{"node": "A"}],
        "prompts": {"prompt": "estimate every CPT"},
        "templates": {"template": "result {value}"},
        "review": {"reviewer": "owner", "decision": "approved", "date": "2026-10-04"},
    }
    package.update(overrides)
    return package


def _cycle_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Cyc</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><GIVEN>B</GIVEN><TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _missing_definition_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Miss</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _wrong_dimensions_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Dim</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _out_of_range_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>OOR</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>1.5 -0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _row_not_normalized_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Norm</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.2 0.2</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _empty_outcomes_xml() -> bytes:
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Empty</NAME>'
        b"<VARIABLE><NAME>A</NAME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _valid_two_node_xml() -> bytes:
    # Root A declared before child B; DEFINITION order matches; rows sum to 1.
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Valid</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# --- Slice 1: XSD-valid-but-semantic-invalid, distinct codes, nonexecutable ---


def test_cycle_is_xsd_valid_but_semantic_invalid() -> None:
    doc = validate(_cycle_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["cycle_detected"]
    assert admission_decision(doc, None).executable is False


def test_missing_definition_is_distinct_code() -> None:
    doc = validate(_missing_definition_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["missing_definition"]
    assert report.errors[0].node == "B"
    assert admission_decision(doc, None).executable is False


def test_wrong_table_dimensions_is_distinct_code() -> None:
    doc = validate(_wrong_dimensions_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["wrong_dimensions"]
    assert report.errors[0].node == "B"
    assert admission_decision(doc, None).executable is False


def test_probability_out_of_range_is_distinct_code() -> None:
    doc = validate(_out_of_range_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["probability_out_of_range"]
    assert admission_decision(doc, None).executable is False


def test_row_not_normalized_is_distinct_code() -> None:
    doc = validate(_row_not_normalized_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["row_not_normalized"]
    assert admission_decision(doc, None).executable is False


def test_empty_outcomes_is_distinct_code() -> None:
    doc = validate(_empty_outcomes_xml())

    assert doc.xsd.valid is True
    report = check_semantics(doc)
    assert report.valid is False
    assert [e.code for e in report.errors] == ["empty_outcomes"]
    assert admission_decision(doc, None).executable is False


def test_semantic_families_have_distinct_codes_and_stay_nonexecutable() -> None:
    fixtures = {
        "cycle_detected": _cycle_xml(),
        "missing_definition": _missing_definition_xml(),
        "wrong_dimensions": _wrong_dimensions_xml(),
        "probability_out_of_range": _out_of_range_xml(),
        "row_not_normalized": _row_not_normalized_xml(),
        "empty_outcomes": _empty_outcomes_xml(),
    }

    primary: dict[str, str] = {}
    for expected, source in fixtures.items():
        doc = validate(source)
        assert doc.xsd.valid is True, expected
        report = check_semantics(doc)
        assert report.valid is False, expected
        codes = [e.code for e in report.errors]
        assert expected in codes, (expected, codes)
        primary[expected] = expected
        decision = admission_decision(doc, None)
        assert decision.executable is False, expected
        assert expected in [d.code for d in decision.diagnostics], expected

    # Six fixtures, six distinct primary codes.
    assert len(set(primary)) == 6


# --- Slice 2: valid ordering passes; drafts stay inactive ---


def test_valid_root_parent_ordering_with_complete_probabilities_passes() -> None:
    doc = validate(_valid_two_node_xml())

    assert doc.xsd.valid is True
    assert [v.name for v in doc.networks[0].variables] == ["A", "B"]
    assert [d.for_node for d in doc.networks[0].definitions] == ["A", "B"]
    assert doc.networks[0].definitions[1].parents == ("A",)

    semantic = check_semantics(doc)
    assert semantic.valid is True
    assert semantic.errors == ()

    decision = admission_decision(doc, _complete_package())
    assert decision.contract.valid is True
    assert decision.executable is True
    assert decision.diagnostics == ()


def test_decision_nodes_stay_drafts_never_executable() -> None:
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>D</NAME>'
        b'<VARIABLE TYPE="decision"><NAME>Act</NAME>'
        b"<OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>S</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>S</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )

    assert doc.xsd.valid is True
    semantic = check_semantics(doc)
    assert semantic.valid is False
    assert "unsupported_kind" in [e.code for e in semantic.errors]
    assert admission_decision(doc, _complete_package()).executable is False


def test_utility_nodes_stay_drafts_never_executable() -> None:
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>U</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b'<VARIABLE TYPE="utility"><NAME>Payoff</NAME></VARIABLE>'
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>Payoff</FOR><GIVEN>A</GIVEN><TABLE>10 20</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )

    assert doc.xsd.valid is True
    semantic = check_semantics(doc)
    assert semantic.valid is False
    assert "unsupported_kind" in [e.code for e in semantic.errors]
    assert admission_decision(doc, _complete_package()).executable is False


def test_all_eleven_supplied_bns_remain_inactive() -> None:
    paths = sorted((_repo_root() / "project-documents/bayesian-networks").glob("BN-*.xml"))

    assert len(paths) == 11
    for path in paths:
        before = path.read_bytes()
        data = path.read_bytes()
        assert data == before
        assert data.strip()

        doc = validate(data)
        assert check_semantics(doc).valid is False
        assert admission_decision(doc, None).executable is False
        assert admission_decision(doc, _complete_package()).executable is False

        # Source file was only read, never modified to fit the application.
        assert path.read_bytes() == data


# --- Slice 3: content gating prevents activation ---


def test_missing_question_mappings_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())

    report = validate_package_contract(_complete_package(question_mappings=[]))
    assert report.valid is False
    assert [e.code for e in report.errors] == ["missing_question_mappings"]
    assert admission_decision(doc, _complete_package(question_mappings=[])).executable is False


def test_missing_prompts_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())

    report = validate_package_contract(_complete_package(prompts=None))
    assert report.valid is False
    assert "missing_prompts" in [e.code for e in report.errors]
    assert admission_decision(doc, _complete_package(prompts=None)).executable is False


def test_missing_templates_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())

    report = validate_package_contract(_complete_package(templates={}))
    assert report.valid is False
    assert "missing_templates" in [e.code for e in report.errors]
    assert admission_decision(doc, _complete_package(templates={})).executable is False


def test_note_source_path_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())

    report = validate_package_contract(_complete_package(source_paths=["encounters/notes/page"]))
    assert report.valid is False
    assert "note_source_path" in [e.code for e in report.errors]
    decision = admission_decision(doc, _complete_package(source_paths=["encounters/notes/page"]))
    assert decision.executable is False
    assert "note_source_path" in [d.code for d in decision.diagnostics]


def test_unreviewed_package_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())

    report = validate_package_contract(_complete_package(review=None))
    assert report.valid is False
    assert "unreviewed_package" in [e.code for e in report.errors]
    assert admission_decision(doc, _complete_package(review=None)).executable is False


def test_reviewed_flag_alone_without_review_record_is_insufficient() -> None:
    doc = validate(_valid_two_node_xml())
    assert check_semantics(doc).valid is True

    # JSON `reviewed=true` alone, with no review record object, must not admit.
    flagged = {
        "question_mappings": [{"node": "A"}],
        "prompts": {"prompt": "x"},
        "templates": {"template": "y"},
        "reviewed": True,
    }
    report = validate_package_contract(flagged)
    assert report.valid is False
    assert "unreviewed_package" in [e.code for e in report.errors]
    assert admission_decision(doc, flagged).executable is False


def test_undeclared_query_node_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())
    package = _complete_package(query_nodes=["ZZZ"])

    assert validate_package_contract(package).valid is True
    decision = admission_decision(doc, package)
    assert decision.executable is False
    assert "undeclared_query" in [d.code for d in decision.diagnostics]


def test_undeclared_state_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())
    package = _complete_package(query_states={"A": ["maybe"]})

    assert validate_package_contract(package).valid is True
    decision = admission_decision(doc, package)
    assert decision.executable is False
    assert "undeclared_state" in [d.code for d in decision.diagnostics]


def test_unsafe_expression_prevents_activation() -> None:
    doc = validate(_valid_two_node_xml())
    package = _complete_package(expressions=["DROP TABLE encounters"])

    report = validate_package_contract(package)
    assert report.valid is False
    assert "unsafe_expression" in [e.code for e in report.errors]
    assert admission_decision(doc, package).executable is False


# --- Slice 4: engineering limits + deterministic bounded diagnostics ---


def test_engineering_limits_are_pinned_resource_bounds_not_clinical() -> None:
    # Reused S21 resource guardrails plus S22 diagnostic caps; engineering
    # limits sized from actual model measurements, not clinical thresholds.
    assert MAX_XML_BYTES == 1_048_576
    assert MAX_NETWORKS == 16
    assert MAX_VARIABLES_PER_NETWORK == 256
    assert MAX_OUTCOMES_PER_VARIABLE == 64
    assert MAX_TABLE_VALUES_PER_DEFINITION == 131_072
    assert MAX_XSD_ERRORS == 20
    assert MAX_MESSAGE_CHARS == 500
    assert MAX_SEMANTIC_ERRORS == 50
    assert MAX_CONTRACT_ERRORS == 50
    assert MAX_ADMISSION_DIAGNOSTICS == 100
    assert NORMALIZATION_TOLERANCE == 1e-9
    assert ENGINEERING_LIMITS["MAX_XML_BYTES"] == MAX_XML_BYTES
    assert ENGINEERING_LIMITS["MAX_SEMANTIC_ERRORS"] == MAX_SEMANTIC_ERRORS
    assert ENGINEERING_LIMITS["MAX_CONTRACT_ERRORS"] == MAX_CONTRACT_ERRORS
    assert ENGINEERING_LIMITS["MAX_ADMISSION_DIAGNOSTICS"] == MAX_ADMISSION_DIAGNOSTICS
    assert ENGINEERING_LIMITS["NORMALIZATION_TOLERANCE"] == NORMALIZATION_TOLERANCE


def test_over_limit_input_is_handled_safely_with_bounded_message() -> None:
    try:
        validate(b"x" * (MAX_XML_BYTES + 1))
    except XmlValidationError as exc:
        assert exc.code == "too_large"
        assert len(exc.message) <= MAX_MESSAGE_CHARS
    else:
        raise AssertionError("oversized input accepted")


def test_admission_diagnostics_are_deterministic() -> None:
    doc = validate(_missing_definition_xml())
    package = _complete_package(source_paths=["unknown/path"], expressions=["bad"])

    first = admission_decision(doc, package)
    second = admission_decision(doc, package)

    assert first.executable is False
    assert [(d.code, d.message) for d in first.diagnostics] == [
        (d.code, d.message) for d in second.diagnostics
    ]
    # Stable order: semantic errors, then contract errors, then cross refs.
    codes = [d.code for d in first.diagnostics]
    assert codes.index("missing_definition") < codes.index("unknown_source_path")
    assert codes.index("unknown_source_path") < codes.index("unsafe_expression")


def test_diagnostic_counts_and_messages_are_bounded() -> None:
    variables = b"".join(
        b"<VARIABLE><NAME>V%d</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>" % i
        for i in range(60)
    )
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>Big</NAME>' + variables + b"</NETWORK></BIF>"
    )
    assert doc.xsd.valid is True

    semantic = check_semantics(doc)
    assert len(semantic.errors) == MAX_SEMANTIC_ERRORS
    assert all(len(e.message) <= MAX_MESSAGE_CHARS for e in semantic.errors)

    package: dict = {
        "question_mappings": [],
        "prompts": {},
        "templates": {},
        "review": None,
        "source_paths": [f"unknown/path{i}" for i in range(80)],
        "expressions": [f"expression-{i}" for i in range(80)],
    }
    contract = validate_package_contract(package)
    assert len(contract.errors) == MAX_CONTRACT_ERRORS
    assert all(len(e.message) <= MAX_MESSAGE_CHARS for e in contract.errors)

    decision = admission_decision(doc, package)
    assert decision.executable is False
    assert len(decision.diagnostics) <= MAX_ADMISSION_DIAGNOSTICS
    assert all(len(d.message) <= MAX_MESSAGE_CHARS for d in decision.diagnostics)
