"""Safely import and inspect XMLBIF drafts (S21, seam T5, FR-31/37/NFR-05).

Seam T5 only: ``validate`` / ``display_graph`` / ``export_source`` plus the
public ``MAX_*`` guardrails and ``XmlValidationError``. Never private helpers,
DB, or HTTP. Expected values are worked literals, never implementation output.

Slices (one observable behavior at a time):
1. Valid synthetic XML -> ordered nodes/edges + separate XSD report; bytes/hash kept.
2. DTD/entity, remote, excessive, malformed, unsupported content fail safely + bounded.
3. BN-04/BN-08 drafts storable/inspectable despite missing DEFINITIONs; graph
   never converts ``proposed_parent`` PROPERTYs into edges.
4. Order/metadata preserved; multi-network / decision / utility reported
   nonactivatable under v1 (never silently selected/dropped).

XSD success is structural only: it is never labeled executable or clinically
valid. No test here claims clinical validity. Source XML files are only read.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from x_insight.models.validation import (
    MAX_MESSAGE_CHARS,
    MAX_NETWORKS,
    MAX_OUTCOMES_PER_VARIABLE,
    MAX_TABLE_VALUES_PER_DEFINITION,
    MAX_VARIABLES_PER_NETWORK,
    MAX_XML_BYTES,
    MAX_XSD_ERRORS,
    XmlValidationError,
    display_graph,
    export_source,
    validate,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ordered_doc() -> bytes:
    # Variables declared C, A, B (non-alphabetical proves source order is kept).
    # Definitions in A, B, C order; C has two parents to prove GIVEN order.
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>OrderNet</NAME>'
        b"<VARIABLE><NAME>C</NAME><OUTCOME>c2</OUTCOME><OUTCOME>c1</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>a1</OUTCOME><OUTCOME>a2</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>b1</OUTCOME><OUTCOME>b2</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
        b"<TABLE>0.25 0.75 0.75 0.25</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>C</FOR><GIVEN>B</GIVEN><GIVEN>A</GIVEN>"
        b"<TABLE>0.1 0.9 0.2 0.8 0.3 0.7 0.4 0.6</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


# --- Slice 1: valid synthetic XML, ordered graph, separate XSD report ---


def test_guardrail_constants_pinned() -> None:
    # Engineering limits, not clinical thresholds; pin so drift is explicit.
    assert MAX_XML_BYTES == 1_048_576
    assert MAX_NETWORKS == 16
    assert MAX_VARIABLES_PER_NETWORK == 256
    assert MAX_OUTCOMES_PER_VARIABLE == 64
    assert MAX_TABLE_VALUES_PER_DEFINITION == 131_072
    assert MAX_XSD_ERRORS == 20
    assert MAX_MESSAGE_CHARS == 500


def test_valid_synthetic_returns_ordered_nodes_edges_and_separate_xsd_report() -> None:
    doc = validate(_ordered_doc())

    # Separate XSD report: structural validity only, never an executability claim.
    assert doc.xsd.valid is True
    assert doc.xsd.errors == ()
    (network,) = doc.networks
    assert network.name == "OrderNet"
    assert [v.name for v in network.variables] == ["C", "A", "B"]
    assert [d.for_node for d in network.definitions] == ["A", "B", "C"]
    assert network.definitions[2].parents == ("B", "A")

    graph = display_graph(network)
    assert graph.network_name == "OrderNet"
    assert graph.nodes == ("C", "A", "B")
    # DEFINITION order, each definition's GIVEN order.
    assert graph.edges == (("A", "B"), ("B", "C"), ("A", "C"))

    # Structurally complete synthetic fixture is activatable under v1;
    # that flag is structural only, never executable/clinically valid.
    assert doc.activatable_v1 is True
    assert doc.nonactivatable_reasons == ()


def test_source_bytes_and_hash_preserved() -> None:
    source = _ordered_doc()
    doc = validate(source)

    assert doc.source_bytes == source
    assert doc.source_sha256 == hashlib.sha256(source).hexdigest()
    assert export_source(doc) == source

    # bytearray input is accepted but stored/returned as exact bytes.
    doc2 = validate(bytearray(source))
    assert doc2.source_bytes == source
    assert isinstance(doc2.source_bytes, bytes)
    assert export_source(doc2) == source


def test_table_tokens_keep_source_order_as_raw_strings() -> None:
    # No float coercion: "0.50" stays "0.50", order kept verbatim.
    source = (
        b'<BIF VERSION="0.3"><NETWORK><NAME>T</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
        b"<TABLE>0.50  1e-1\n  0.5\t0.90</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    doc = validate(source)

    assert doc.xsd.valid is True
    by_node = {d.for_node: d for d in doc.networks[0].definitions}
    assert by_node["B"].table == ("0.50", "1e-1", "0.5", "0.90")
    assert by_node["A"].table == ("0.5", "0.5")


def test_kind_defaults_to_nature_when_type_absent() -> None:
    source = (
        b'<BIF VERSION="0.3"><NETWORK><NAME>K</NAME>'
        b"<VARIABLE><NAME>Plain</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b'<VARIABLE TYPE="nature"><NAME>Explicit</NAME>'
        b"<OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>Plain</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>Explicit</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    doc = validate(source)

    assert [v.kind for v in doc.networks[0].variables] == ["nature", "nature"]


# --- Slice 2: unsafe / excessive / malformed / unsupported input fails safely ---


def _assert_bounded_error(exc: XmlValidationError) -> None:
    assert isinstance(exc.code, str) and exc.code
    assert isinstance(exc.message, str) and exc.message
    assert len(exc.message) <= MAX_MESSAGE_CHARS
    assert isinstance(exc.line, int) and exc.line >= 0
    assert isinstance(exc.column, int) and exc.column >= 0


def test_rejects_doctype_and_entity_declarations() -> None:
    payloads = [
        b'<?xml version="1.0"?><!DOCTYPE BIF><BIF VERSION="0.3"/>',
        b'<?xml version="1.0"?><!doctype BIF><BIF VERSION="0.3"/>',
        # XXE attempt: external general entity must never be expanded/fetched.
        b'<?xml version="1.0"?><!DOCTYPE BIF [<!ENTITY xxe SYSTEM '
        b'"file:///etc/passwd">]><BIF VERSION="0.3"><NETWORK><NAME>N</NAME>'
        b"</NETWORK></BIF>",
        # Bare ENTITY marker outside a DOCTYPE is still rejected pre-parse.
        b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME><!-- <!ENTITY x> --></NETWORK></BIF>',
    ]
    for payload in payloads:
        try:
            validate(payload)
        except XmlValidationError as exc:
            assert exc.code == "unsafe_doctype"
            _assert_bounded_error(exc)
        else:
            raise AssertionError(f"unsafe input accepted: {payload[:60]!r}")


def test_remote_references_are_never_resolved() -> None:
    # XInclude element must not be expanded: it stays an unknown element,
    # so the document is XSD-invalid but no file/network content is read.
    doc = validate(
        b'<BIF VERSION="0.3" xmlns:xi="http://www.w3.org/2001/XInclude">'
        b"<NETWORK><NAME>N</NAME>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b'<xi:include href="file:///etc/passwd" parse="text"/>'
        b"</NETWORK></BIF>"
    )
    assert doc.xsd.valid is False
    assert doc.activatable_v1 is False
    assert doc.nonactivatable_reasons == ("xsd_invalid",)
    assert len(doc.networks[0].variables) == 1

    # A remote schema-location hint must not trigger a network fetch;
    # validation still uses the supplied local schema and succeeds.
    hinted = validate(
        b'<BIF VERSION="0.3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        b' xsi:noNamespaceSchemaLocation="http://example.invalid/schema.xml">'
        b"<NETWORK><NAME>N</NAME>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    assert hinted.xsd.valid is True
    assert hinted.activatable_v1 is True


def test_rejects_non_bytes_and_empty_input() -> None:
    for bad in ("<BIF/>", None, 123):
        try:
            validate(bad)  # type: ignore[arg-type]
        except XmlValidationError as exc:
            assert exc.code == "not_bytes"
            _assert_bounded_error(exc)
        else:
            raise AssertionError(f"non-bytes accepted: {bad!r}")
    for empty in (b"", b"   \n  ", bytearray()):
        try:
            validate(empty)
        except XmlValidationError as exc:
            assert exc.code == "empty_input"
            _assert_bounded_error(exc)
        else:
            raise AssertionError(f"empty input accepted: {empty!r}")


def test_rejects_oversized_input() -> None:
    payload = b"x" * (MAX_XML_BYTES + 1)
    try:
        validate(payload)
    except XmlValidationError as exc:
        assert exc.code == "too_large"
        _assert_bounded_error(exc)
    else:
        raise AssertionError("oversized input accepted")


def test_rejects_malformed_xml_with_bounded_location() -> None:
    try:
        validate(b'<BIF VERSION="0.3"><NETWORK>')
    except XmlValidationError as exc:
        assert exc.code == "malformed_xml"
        _assert_bounded_error(exc)
        assert exc.line >= 1
    else:
        raise AssertionError("malformed XML accepted")


def test_rejects_too_many_networks() -> None:
    body = b"".join(
        b"<NETWORK><NAME>N%d</NAME>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK>" % i
        for i in range(MAX_NETWORKS + 1)
    )
    try:
        validate(b'<BIF VERSION="0.3">' + body + b"</BIF>")
    except XmlValidationError as exc:
        assert exc.code == "too_many_networks"
        _assert_bounded_error(exc)
    else:
        raise AssertionError("too many networks accepted")


def test_rejects_too_many_variables_per_network() -> None:
    body = b"".join(
        b"<VARIABLE><NAME>V%d</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>" % i
        for i in range(MAX_VARIABLES_PER_NETWORK + 1)
    )
    try:
        validate(b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME>' + body + b"</NETWORK></BIF>")
    except XmlValidationError as exc:
        assert exc.code == "too_many_variables"
        _assert_bounded_error(exc)
    else:
        raise AssertionError("too many variables accepted")


def test_rejects_too_many_outcomes_per_variable() -> None:
    outcomes = b"".join(b"<OUTCOME>S%d</OUTCOME>" % i for i in range(MAX_OUTCOMES_PER_VARIABLE + 1))
    try:
        validate(
            b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME><VARIABLE><NAME>A</NAME>'
            + outcomes
            + b"</VARIABLE>"
            b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
            b"</NETWORK></BIF>"
        )
    except XmlValidationError as exc:
        assert exc.code == "too_many_outcomes"
        _assert_bounded_error(exc)
    else:
        raise AssertionError("too many outcomes accepted")


def test_rejects_oversized_table() -> None:
    table = ("0.5 " * (MAX_TABLE_VALUES_PER_DEFINITION + 1)).encode()
    try:
        validate(
            b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME>'
            b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
            b"<DEFINITION><FOR>A</FOR><TABLE>" + table + b"</TABLE></DEFINITION>"
            b"</NETWORK></BIF>"
        )
    except XmlValidationError as exc:
        assert exc.code == "table_too_large"
        _assert_bounded_error(exc)
    else:
        raise AssertionError("oversized table accepted")


def test_unsupported_root_returns_draft_with_bounded_reason() -> None:
    doc = validate(b"<FOO/>")

    assert doc.xsd.valid is False
    assert len(doc.xsd.errors) == 1
    assert all(len(e.message) <= MAX_MESSAGE_CHARS for e in doc.xsd.errors)
    assert doc.networks == ()
    assert doc.activatable_v1 is False
    assert doc.nonactivatable_reasons == ("unsupported_content",)
    assert doc.source_bytes == b"<FOO/>"


def test_xsd_violations_return_report_instead_of_raising() -> None:
    # "TODO" is not a finite real: content violation, not a hard failure.
    # Raw tokens are still preserved for inspection.
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>TODO</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )

    assert doc.xsd.valid is False
    assert doc.xsd.errors
    for issue in doc.xsd.errors:
        assert isinstance(issue.line, int) and issue.line >= 0
        assert isinstance(issue.column, int) and issue.column >= 0
        assert len(issue.message) <= MAX_MESSAGE_CHARS
    assert doc.networks[0].definitions[0].table == ("TODO",)
    assert doc.activatable_v1 is False
    assert doc.nonactivatable_reasons == ("xsd_invalid",)


def test_xsd_errors_bounded_to_max() -> None:
    count = 25
    variables = b"".join(
        b"<VARIABLE><NAME>A%d</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>" % i
        for i in range(count)
    )
    definitions = b"".join(
        b"<DEFINITION><FOR>A%d</FOR><TABLE>TODO</TABLE></DEFINITION>" % i for i in range(count)
    )
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>N</NAME>'
        + variables
        + definitions
        + b"</NETWORK></BIF>"
    )

    assert doc.xsd.valid is False
    assert len(doc.xsd.errors) == MAX_XSD_ERRORS
    assert all(len(e.message) <= MAX_MESSAGE_CHARS for e in doc.xsd.errors)


# --- Slice 3: supplied drafts stay storable/inspectable; no PROPERTY->edge ---

BN04_RELATIVE = Path("project-documents/bayesian-networks/BN-04.xml")
BN08_RELATIVE = Path("project-documents/bayesian-networks/BN-08.xml")


def _read_draft(relative: Path) -> bytes:
    # Drafts are only read; no test may modify the source XML.
    path = _repo_root() / relative
    before = path.read_bytes()
    data = path.read_bytes()
    assert data == before
    assert data.strip()
    return data


def test_bn04_storable_and_inspectable_despite_missing_definitions() -> None:
    source = _read_draft(BN04_RELATIVE)
    doc = validate(source)

    # Structurally valid draft, but never executable/clinically valid.
    assert doc.xsd.valid is True
    assert doc.xsd.errors == ()
    assert doc.activatable_v1 is False
    assert len(doc.nonactivatable_reasons) == 1
    assert doc.nonactivatable_reasons[0].startswith("missing_definitions:")

    (network,) = doc.networks
    assert network.name == "BN_04_Antipsychotic_Treatment_Review"
    assert len(network.variables) == 32
    assert len(network.definitions) == 13
    assert doc.source_bytes == source
    assert doc.source_sha256 == hashlib.sha256(source).hexdigest()
    assert export_source(doc) == source
    # Re-read proves the source file was not modified to fit the application.
    assert (_repo_root() / BN04_RELATIVE).read_bytes() == source


def test_bn08_storable_and_inspectable_with_zero_definitions() -> None:
    source = _read_draft(BN08_RELATIVE)
    doc = validate(source)

    assert doc.xsd.valid is True
    assert doc.xsd.errors == ()
    assert doc.activatable_v1 is False
    assert doc.nonactivatable_reasons[0].startswith("missing_definitions:")

    (network,) = doc.networks
    assert network.name == "Statement08_Clozapine_Suicide_Risk_Review"
    assert len(network.variables) == 14
    assert len(network.definitions) == 0
    assert display_graph(network).edges == ()
    assert export_source(doc) == source
    assert (_repo_root() / BN08_RELATIVE).read_bytes() == source


def test_graph_never_converts_proposed_parent_into_edges() -> None:
    bn04 = validate(_read_draft(BN04_RELATIVE))
    graph04 = display_graph(bn04.networks[0])

    # 13 DEFINITIONs each carry one GIVEN -> exactly 13 authoritative edges.
    assert len(bn04.networks[0].definitions) == 13
    assert len(graph04.edges) == 13
    # AdherenceDifficulty declares proposed_parent PreferenceBarrier /
    # PriorTreatmentDifficulty but has no DEFINITION: no edge may appear.
    assert ("PreferenceBarrier", "AdherenceDifficulty") not in graph04.edges
    assert not [e for e in graph04.edges if e[1] == "AdherenceDifficulty"]
    assert not [e for e in graph04.edges if e[1] == "EffectiveExposure"]

    bn08 = validate(_read_draft(BN08_RELATIVE))
    assert len(bn08.networks[0].definitions) == 0
    assert display_graph(bn08.networks[0]).edges == ()

    # Synthetic minimal case: PROPERTY-only parent hint yields no edge.
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>P</NAME>'
        b"<VARIABLE><NAME>Parent</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>Child</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME>"
        b"<PROPERTY>proposed_parent=Parent</PROPERTY></VARIABLE>"
        b"<DEFINITION><FOR>Parent</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    assert display_graph(doc.networks[0]).edges == ()


def test_xsd_success_is_never_executable_or_clinically_valid() -> None:
    # Both supplied drafts are XSD-valid yet must not activate under v1,
    # and no field on the document claims executability or clinical validity.
    for relative in (BN04_RELATIVE, BN08_RELATIVE):
        doc = validate(_read_draft(relative))
        assert doc.xsd.valid is True
        assert doc.activatable_v1 is False
        for attr in ("executable", "clinically_valid", "clinical_valid", "is_executable"):
            assert not hasattr(doc, attr)


# --- Slice 4: order/metadata kept; multi-network & kinds nonactivatable ---


def test_state_parent_and_property_order_preserved() -> None:
    doc = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>Net</NAME>'
        b"<PROPERTY>p1</PROPERTY><PROPERTY>p2</PROPERTY>"
        b"<VARIABLE><NAME>C</NAME><OUTCOME>c2</OUTCOME><OUTCOME>c1</OUTCOME>"
        b"<PROPERTY>vp2</PROPERTY><PROPERTY>vp1</PROPERTY></VARIABLE>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>a1</OUTCOME><OUTCOME>a2</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>b1</OUTCOME><OUTCOME>b2</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
        b"<TABLE>0.25 0.75 0.75 0.25</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>C</FOR><GIVEN>B</GIVEN><GIVEN>A</GIVEN>"
        b"<TABLE>0.1 0.9 0.2 0.8 0.3 0.7 0.4 0.6</TABLE>"
        b"<PROPERTY>dp1</PROPERTY><PROPERTY>dp2</PROPERTY></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    (network,) = doc.networks

    assert network.properties == ("p1", "p2")
    assert network.variables[0].states == ("c2", "c1")
    assert network.variables[0].properties == ("vp2", "vp1")
    assert network.definitions[2].parents == ("B", "A")
    assert network.definitions[2].properties == ("dp1", "dp2")
    assert [d.for_node for d in network.definitions] == ["A", "B", "C"]


def test_bn_drafts_retain_state_parent_order_and_metadata() -> None:
    source = _read_draft(BN04_RELATIVE)
    doc = validate(source)
    (network,) = doc.networks

    by_name = {v.name: v for v in network.variables}
    # Declared OUTCOME order is kept verbatim (not sorted/normalized).
    assert by_name["AdherenceDifficulty"].states == ("Absent", "Present")
    assert by_name["PriorTreatmentDifficulty"].states == ("Absent", "Present")
    # Metadata-only hints stay metadata in source order.
    adherence_props = by_name["AdherenceDifficulty"].properties
    assert adherence_props[-3:] == (
        "proposed_parent=PreferenceBarrier",
        "proposed_parent=PriorTreatmentDifficulty",
        "required_table_entries=8",
    )
    # DEFINITION order and per-definition GIVEN order match the source file.
    assert [d.for_node for d in network.definitions][0] == "AkathisiaSourceCategory"
    assert all(d.parents == ("OralAntipsychoticMedication",) for d in network.definitions)
    # Export is byte-identical, so order/metadata survive the round trip.
    assert export_source(doc) == source
    assert validate(export_source(doc)).networks[0].variables[0].states == ("Absent", "Present")


def test_multi_network_returns_all_without_selection() -> None:
    source = (
        b'<BIF VERSION="0.3">'
        b"<NETWORK><NAME>First</NAME>"
        b"<VARIABLE><NAME>X</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>X</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK>"
        b"<NETWORK><NAME>Second</NAME>"
        b"<VARIABLE><NAME>Y</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>Y</FOR><TABLE>0.25 0.75</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    doc = validate(source)

    # Nothing silently selected or dropped: both networks in source order.
    assert doc.xsd.valid is True
    assert [n.name for n in doc.networks] == ["First", "Second"]
    assert doc.activatable_v1 is False
    assert doc.nonactivatable_reasons == ("multiple_networks",)
    assert display_graph(doc.networks[0]).nodes == ("X",)
    assert display_graph(doc.networks[1]).nodes == ("Y",)
    assert export_source(doc) == source


def test_decision_and_utility_kinds_reported_nonactivatable() -> None:
    decision = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>D</NAME>'
        b'<VARIABLE TYPE="decision"><NAME>Act</NAME>'
        b"<OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>S</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>S</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    assert decision.xsd.valid is True
    assert [v.kind for v in decision.networks[0].variables] == ["decision", "nature"]
    assert decision.activatable_v1 is False
    assert decision.nonactivatable_reasons == ("unsupported_kind:decision",)

    utility = validate(
        b'<BIF VERSION="0.3"><NETWORK><NAME>U</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>yes</OUTCOME><OUTCOME>no</OUTCOME></VARIABLE>"
        b'<VARIABLE TYPE="utility"><NAME>Payoff</NAME></VARIABLE>'
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>Payoff</FOR><GIVEN>A</GIVEN><TABLE>10 20</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )
    assert utility.xsd.valid is True
    assert utility.activatable_v1 is False
    assert utility.nonactivatable_reasons == ("unsupported_kind:utility",)
    # Both networks are retained for inspection, not dropped.
    assert len(decision.networks) == 1
    assert len(utility.networks) == 1
