"""Validate every CPT, convert to an effective artifact, and run exact inference (S23.a+b, seam T5).

Seam T5 only: ``validate`` (S21) to build fixtures plus ``validate_cpts`` /
``build_effective_artifact`` (S23.a) and ``infer_effective`` / ``replay``
(S23.b). No HTTP, no DB, no provider/MCP. Expected values are independently
worked literals, never implementation output. Synthetic fixtures only.

Slices (one observable behavior at a time):
A. Valid two-node conversion (S23.a): exact integer units, source preserved,
   effective hash frozen, ordered TABLEs, metadata/PROPERTY kept, runtime pinned.
B. Rejection matrix (S23.a): omitted/duplicate/extra table/row, changed ordering
   or structure, malformed/boolean/null/nonfinite/out-of-range/excess-precision/
   inexact-total values are rejected with distinct codes, tables==() on
   invalid, never silently normalized.
C. Transpose guard conversion (S23.a): asymmetric two-parent fixture proves
   child-fastest / last-parent-next / first-slowest mapping with an explicit
   engine literal; transposed row order or swapped parents rejected.
D. Two-node exact inference (S23.b slice 1): empty-evidence P(A=yes)=0.20 and
   P(B=yes)=0.22; observed projection never clamps; no fallbacks/evidence.
E. Asymmetric execution guard (S23.b slice 2): two-parent inference proves the
   mapping; transposed order would give different posteriors.
F. Replay + bounded isolation + errors (S23.b slice 3): frozen replay matches
   under pinned runtime with provider unavailable and always empty evidence;
   numeric/resource failures raise without approximation; bounded child process;
   tolerance pinned with execution policy.
"""

from __future__ import annotations

import copy
import hashlib

from lxml import etree

from x_insight.models.inference import (
    CPT_POLICY_VERSION,
    MAX_CPT_ERRORS,
    MAX_DECIMAL_PLACES,
    MAX_MESSAGE_CHARS,
    PINNED_ENGINE_CONFIG,
    UNITS_FOR_100_PCT,
    CptValidationError,
    build_effective_artifact,
    validate_cpts,
)
from x_insight.models.validation import validate


def _two_node_xml() -> bytes:
    # Synthetic A(root no/yes) -> B(child of A); non-clinical names/values only.
    # PROPERTY elements prove metadata survives effective conversion.
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>TwoNode</NAME>'
        b"<PROPERTY>net-prop=kept</PROPERTY>"
        b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME>"
        b"<PROPERTY>var-prop-a=kept</PROPERTY></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE>"
        b"<PROPERTY>def-prop-a=kept</PROPERTY></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
        b"<TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _valid_two_payload(network_hash: str) -> dict:
    # Worked literals from plan.md 7.4: A 80/20, B 90/10 when A=no else 30/70.
    # Decimal strings, at most six places, rows total exactly 100%.
    return {
        "network_hash": network_hash,
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


# --- Slice A: valid conversion ---


def test_policy_constants_pinned() -> None:
    # Decimal-string/integer-unit policy and engineering bounds, not clinical.
    assert CPT_POLICY_VERSION == "cpt-decimal-v1"
    assert UNITS_FOR_100_PCT == 100_000_000
    assert MAX_DECIMAL_PLACES == 6
    assert MAX_CPT_ERRORS == 50
    assert MAX_MESSAGE_CHARS == 500
    assert PINNED_ENGINE_CONFIG == {
        "evidence": "empty",
        "evidence_order": "given_order",
        "float_dtype": "float64",
        "elimination_order": "source_order",
        "tie_break": "source_order",
    }


def test_valid_two_node_converts_with_exact_integer_units() -> None:
    doc = validate(_two_node_xml())
    assert doc.xsd.valid is True

    report = validate_cpts(doc, _valid_two_payload(doc.source_sha256))

    assert report.valid is True
    assert report.errors == ()
    assert [t.node_id for t in report.tables] == ["A", "B"]
    # Independently worked integer units: 1% = 1_000_000 units.
    assert report.tables[0].parent_ids == ()
    assert report.tables[0].states == ("no", "yes")
    assert report.tables[0].rows[0].parent_states == ()
    assert report.tables[0].rows[0].percentages == ("80", "20")
    assert report.tables[0].rows[0].units == (80_000_000, 20_000_000)
    assert report.tables[1].parent_ids == ("A",)
    assert report.tables[1].states == ("no", "yes")
    assert [r.parent_states for r in report.tables[1].rows] == [("no",), ("yes",)]
    assert report.tables[1].rows[0].percentages == ("90", "10")
    assert report.tables[1].rows[0].units == (90_000_000, 10_000_000)
    assert report.tables[1].rows[1].percentages == ("30", "70")
    assert report.tables[1].rows[1].units == (30_000_000, 70_000_000)
    # Exact totals, never clipped or repaired: each row is exactly 100M units.
    assert sum(report.tables[0].rows[0].units) == 100_000_000
    assert sum(report.tables[1].rows[0].units) == 100_000_000
    assert sum(report.tables[1].rows[1].units) == 100_000_000

    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    assert artifact.tables == report.tables
    # Engine order is GIVEN order; columns follow Cartesian order (A=no, A=yes).
    assert artifact.engine_tables[0].node_id == "A"
    assert artifact.engine_tables[0].evidence_order == ()
    assert artifact.engine_tables[0].units_2d == ((80_000_000,), (20_000_000,))
    assert artifact.engine_tables[0].values_2d == ((0.8,), (0.2,))
    assert artifact.engine_tables[1].node_id == "B"
    assert artifact.engine_tables[1].evidence_order == ("A",)
    assert artifact.engine_tables[1].units_2d == (
        (90_000_000, 30_000_000),
        (10_000_000, 70_000_000),
    )
    assert artifact.engine_tables[1].values_2d == ((0.9, 0.3), (0.1, 0.7))


def test_effective_artifact_preserves_source_and_freezes_hash() -> None:
    source = _two_node_xml()
    doc = validate(source)
    before = bytes(doc.source_bytes)

    payload = _valid_two_payload(doc.source_sha256)
    artifact = build_effective_artifact(doc, payload)

    # Registered bytes/hash stay unchanged; effective hash is frozen on new bytes.
    assert doc.source_bytes == source
    assert doc.source_bytes == before
    assert doc.source_sha256 == hashlib.sha256(source).hexdigest()
    assert artifact.source_sha256 == doc.source_sha256
    assert artifact.effective_sha256 == hashlib.sha256(artifact.effective_bytes).hexdigest()
    assert artifact.effective_bytes != bytes(doc.source_bytes)
    # Default query is all nodes in admitted order; evidence always empty.
    assert artifact.query_nodes == ("A", "B")
    assert dict(artifact.evidence) == {}
    assert dict(artifact.engine_config) == dict(PINNED_ENGINE_CONFIG)


def test_effective_xml_contains_all_tables_in_order_with_metadata() -> None:
    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    root = etree.fromstring(bytes(artifact.effective_bytes))
    (network_el,) = root.findall("NETWORK")
    assert network_el.findtext("NAME") == "TwoNode"
    definitions = network_el.findall("DEFINITION")
    assert [d.findtext("FOR") for d in definitions] == ["A", "B"]
    assert [tuple(g.text.strip() for g in d.findall("GIVEN")) for d in definitions] == [
        (),
        ("A",),
    ]
    # Exact fractions from integer units, child state fastest within each row.
    assert definitions[0].findtext("TABLE").split() == ["0.8", "0.2"]
    assert definitions[1].findtext("TABLE").split() == ["0.9", "0.1", "0.3", "0.7"]
    # Metadata outside engine conversion is preserved verbatim.
    assert artifact.effective_bytes.count(b"net-prop=kept") == 1
    assert artifact.effective_bytes.count(b"var-prop-a=kept") == 1
    assert artifact.effective_bytes.count(b"def-prop-a=kept") == 1
    assert network_el.findall("PROPERTY")[0].text == "net-prop=kept"
    # Re-parsing the effective bytes keeps the same ordered TABLEs.
    again = etree.fromstring(bytes(artifact.effective_bytes))
    tables = [d.findtext("TABLE").split() for d in again.find("NETWORK").findall("DEFINITION")]
    assert tables == [["0.8", "0.2"], ["0.9", "0.1", "0.3", "0.7"]]


def test_runtime_record_pinned() -> None:
    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    assert artifact.runtime.python_version == "3.12.14"
    assert artifact.runtime.pgmpy_version == "1.1.2"
    assert artifact.runtime.lxml_version == "6.1.3"
    assert artifact.runtime.policy_version == "cpt-decimal-v1"
    assert dict(artifact.runtime.engine_config) == dict(PINNED_ENGINE_CONFIG)


# --- Slice B: rejection matrix (distinct codes, tables==(), never normalized) ---


def _assert_rejected(payload: dict, document, expected_code: str) -> None:
    report = validate_cpts(document, payload)
    assert report.valid is False
    assert report.tables == ()
    codes = [issue.code for issue in report.errors]
    assert expected_code in codes
    assert len(report.errors) <= MAX_CPT_ERRORS
    assert all(len(issue.message) <= MAX_MESSAGE_CHARS for issue in report.errors)
    # Deterministic fixed order: a second call yields identical codes/messages.
    again = validate_cpts(document, payload)
    assert [e.code for e in again.errors] == codes
    assert [e.message for e in again.errors] == [e.message for e in report.errors]
    # Conversion never repairs: invalid CPTs raise instead of clipping/filling.
    try:
        build_effective_artifact(document, payload)
    except CptValidationError as exc:
        assert exc.code == "cpt_invalid"
    else:
        raise AssertionError(f"invalid payload converted: {expected_code}")


def test_missing_table_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["tables"] = [payload["tables"][0]]
    _assert_rejected(payload, doc, "missing_table")


def test_missing_root_table_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["tables"] = [payload["tables"][1]]
    _assert_rejected(payload, doc, "missing_table")


def test_duplicate_table_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["tables"] = [payload["tables"][0], payload["tables"][0], payload["tables"][1]]
    _assert_rejected(payload, doc, "duplicate_table")


def test_extra_table_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["tables"] = payload["tables"] + [
        {
            "node_id": "ZZZ",
            "parent_ids": [],
            "states": ["no", "yes"],
            "rows": [{"parent_states": [], "percentages": ["50", "50"]}],
        }
    ]
    _assert_rejected(payload, doc, "extra_table")


def test_missing_row_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    trimmed = copy.deepcopy(payload)
    trimmed["tables"][1]["rows"] = trimmed["tables"][1]["rows"][:1]
    _assert_rejected(trimmed, doc, "missing_row")


def test_extra_row_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    extended = copy.deepcopy(payload)
    extended["tables"][1]["rows"] = extended["tables"][1]["rows"] + [
        {"parent_states": ["no"], "percentages": ["90", "10"]}
    ]
    _assert_rejected(extended, doc, "extra_row")


def test_duplicate_row_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    duplicated = copy.deepcopy(payload)
    duplicated["tables"][1]["rows"] = [
        {"parent_states": ["no"], "percentages": ["90", "10"]},
        {"parent_states": ["no"], "percentages": ["90", "10"]},
    ]
    _assert_rejected(duplicated, doc, "duplicate_row")


def test_wrong_node_order_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    swapped = copy.deepcopy(payload)
    swapped["tables"] = [swapped["tables"][1], swapped["tables"][0]]
    _assert_rejected(swapped, doc, "wrong_node_order")


def test_wrong_state_order_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    reordered = copy.deepcopy(payload)
    reordered["tables"][0]["states"] = ["yes", "no"]
    reordered["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["20", "80"]}]
    _assert_rejected(reordered, doc, "wrong_state_order")


def test_wrong_row_order_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    swapped = copy.deepcopy(payload)
    swapped["tables"][1]["rows"] = list(reversed(swapped["tables"][1]["rows"]))
    _assert_rejected(swapped, doc, "wrong_row_order")


def test_wrong_structure_variants_rejected() -> None:
    doc = validate(_two_node_xml())
    base = _valid_two_payload(doc.source_sha256)

    # Parents do not match GIVEN (B must have exactly ["A"]).
    no_parent = copy.deepcopy(base)
    no_parent["tables"][1]["parent_ids"] = []
    _assert_rejected(no_parent, doc, "wrong_structure")

    unknown_parent = copy.deepcopy(base)
    unknown_parent["tables"][1]["parent_ids"] = ["ZZZ"]
    _assert_rejected(unknown_parent, doc, "wrong_structure")

    # States do not match OUTCOME (unknown state, not just reordered).
    unknown_state = copy.deepcopy(base)
    unknown_state["tables"][0]["states"] = ["no", "maybe"]
    _assert_rejected(unknown_state, doc, "wrong_structure")

    # Percentages length does not match state count.
    short = copy.deepcopy(base)
    short["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["80"]}]
    _assert_rejected(short, doc, "wrong_structure")

    long = copy.deepcopy(base)
    long["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["80", "20", "0"]}]
    _assert_rejected(long, doc, "wrong_structure")

    # Envelope is not a list of per-node tables.
    not_list = copy.deepcopy(base)
    not_list["tables"] = "not-a-list"
    _assert_rejected(not_list, doc, "wrong_structure")


def test_missing_field_rejected() -> None:
    doc = validate(_two_node_xml())
    base = _valid_two_payload(doc.source_sha256)

    no_hash = {"tables": base["tables"]}
    _assert_rejected(no_hash, doc, "missing_field")

    no_states = copy.deepcopy(base)
    del no_states["tables"][0]["states"]
    _assert_rejected(no_states, doc, "missing_field")

    no_percentages = copy.deepcopy(base)
    del no_percentages["tables"][0]["rows"][0]["percentages"]
    _assert_rejected(no_percentages, doc, "missing_field")


def test_extra_field_rejected() -> None:
    doc = validate(_two_node_xml())
    base = _valid_two_payload(doc.source_sha256)

    top_extra = copy.deepcopy(base)
    top_extra["bogus"] = "extra"
    _assert_rejected(top_extra, doc, "extra_field")

    table_extra = copy.deepcopy(base)
    table_extra["tables"][0]["bogus"] = "extra"
    _assert_rejected(table_extra, doc, "extra_field")

    row_extra = copy.deepcopy(base)
    row_extra["tables"][0]["rows"][0]["bogus"] = "extra"
    _assert_rejected(row_extra, doc, "extra_field")


def test_hash_mismatch_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload("0" * 64)
    _assert_rejected(payload, doc, "hash_mismatch")


def test_network_mismatch_rejected() -> None:
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["network_name"] = "WrongName"
    _assert_rejected(payload, doc, "network_mismatch")


def test_malformed_percentage_rejected() -> None:
    doc = validate(_two_node_xml())
    for token in ["80.0.0", "", "abc", "1e2", " 80", "80 ", "--5", ".", "5."]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [token, "20"]}]
        _assert_rejected(mutated, doc, "malformed_percentage")
    # Native numbers are not decimal strings.
    for token in [80.0, 80, 20]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [token, "20"]}]
        _assert_rejected(mutated, doc, "malformed_percentage")


def test_boolean_percentage_rejected() -> None:
    doc = validate(_two_node_xml())
    for token in [True, False]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [token, "20"]}]
        _assert_rejected(mutated, doc, "boolean_percentage")


def test_null_percentage_rejected() -> None:
    doc = validate(_two_node_xml())
    mutated = _valid_two_payload(doc.source_sha256)
    mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [None, "20"]}]
    _assert_rejected(mutated, doc, "null_percentage")


def test_nonfinite_percentage_rejected() -> None:
    doc = validate(_two_node_xml())
    for token in ["nan", "NaN", "-nan", "inf", "+inf", "-inf", "infinity", "+Infinity"]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [token, "20"]}]
        _assert_rejected(mutated, doc, "nonfinite_percentage")
    for token in [float("nan"), float("inf"), float("-inf")]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": [token, "20"]}]
        _assert_rejected(mutated, doc, "nonfinite_percentage")


def test_percentage_out_of_range_rejected_never_clipped() -> None:
    doc = validate(_two_node_xml())
    # Over 100% is rejected, never clipped to 100%.
    high = _valid_two_payload(doc.source_sha256)
    high["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["200", "20"]}]
    _assert_rejected(high, doc, "percentage_out_of_range")
    # Negative is rejected, never clipped to 0.
    negative = _valid_two_payload(doc.source_sha256)
    negative["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["-5", "105"]}]
    _assert_rejected(negative, doc, "percentage_out_of_range")
    # One micro-percent over 100% is still out of range, not rounded down.
    hairline = _valid_two_payload(doc.source_sha256)
    hairline["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["100.000001", "0"]}]
    _assert_rejected(hairline, doc, "percentage_out_of_range")


def test_excess_precision_rejected_never_truncated() -> None:
    doc = validate(_two_node_xml())
    # Seven fractional digits exceed the six-place policy; never truncated.
    for percentages in [["80.0000001", "19.9999999"], ["33.3333333", "66.6666667"]]:
        mutated = _valid_two_payload(doc.source_sha256)
        mutated["tables"][0]["rows"] = [{"parent_states": [], "percentages": percentages}]
        _assert_rejected(mutated, doc, "excess_precision")


def test_inexact_total_rejected_never_rounded() -> None:
    doc = validate(_two_node_xml())
    # 50 + 49.999999 = 99.999999% (one unit short); never rounded to 100%.
    short = _valid_two_payload(doc.source_sha256)
    short["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["50", "49.999999"]}]
    _assert_rejected(short, doc, "inexact_total")
    # 33.333333 + 66.666666 = 99.999999% is also one unit short.
    short2 = _valid_two_payload(doc.source_sha256)
    short2["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["33.333333", "66.666666"]}]
    _assert_rejected(short2, doc, "inexact_total")
    # 50.000001 + 50 = 100.000001% is one unit over; never repaired down.
    over = _valid_two_payload(doc.source_sha256)
    over["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["50.000001", "50"]}]
    _assert_rejected(over, doc, "inexact_total")


def test_six_decimal_boundary_converts_with_exact_units() -> None:
    # Six places are allowed and convert exactly: no rounding or repair.
    doc = validate(_two_node_xml())
    payload = _valid_two_payload(doc.source_sha256)
    payload["tables"][0]["rows"] = [
        {"parent_states": [], "percentages": ["33.333333", "66.666667"]}
    ]
    report = validate_cpts(doc, payload)
    assert report.valid is True
    assert report.tables[0].rows[0].units == (33_333_333, 66_666_667)
    assert sum(report.tables[0].rows[0].units) == 100_000_000
    artifact = build_effective_artifact(doc, payload)
    assert artifact.engine_tables[0].units_2d == ((33_333_333,), (66_666_667,))


def test_invalid_build_raises_and_query_guards_hold() -> None:
    doc = validate(_two_node_xml())
    bad = _valid_two_payload(doc.source_sha256)
    bad["tables"][0]["rows"] = [{"parent_states": [], "percentages": ["50", "49.999999"]}]
    try:
        build_effective_artifact(doc, bad)
    except CptValidationError as exc:
        assert exc.code == "cpt_invalid"
        assert exc.errors
    else:
        raise AssertionError("inexact payload converted")

    good = _valid_two_payload(doc.source_sha256)
    try:
        build_effective_artifact(doc, good, query_nodes=["ZZZ"])
    except CptValidationError as exc:
        assert exc.code == "undeclared_query"
    else:
        raise AssertionError("undeclared query converted")


# --- Slice C: transpose guard (child-fastest / last-parent-next / first-slowest) ---


def _two_parent_xml() -> bytes:
    # Synthetic P1,P2 roots -> C child of [P1, P2]; GIVEN order is P1 then P2.
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>TwoParent</NAME>'
        b"<VARIABLE><NAME>P1</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>P2</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>C</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>P1</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>P2</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>C</FOR><GIVEN>P1</GIVEN><GIVEN>P2</GIVEN>"
        b"<TABLE>0.5 0.5 0.5 0.5 0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _valid_two_parent_payload(network_hash: str) -> dict:
    # Asymmetric rows prove the mapping: child fastest, P2 next, P1 slowest.
    # Cartesian order: (no,no) 90/10, (no,yes) 80/20, (yes,no) 30/70, (yes,yes) 10/90.
    return {
        "network_hash": network_hash,
        "tables": [
            {
                "node_id": "P1",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["50", "50"]}],
            },
            {
                "node_id": "P2",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["50", "50"]}],
            },
            {
                "node_id": "C",
                "parent_ids": ["P1", "P2"],
                "states": ["no", "yes"],
                "rows": [
                    {"parent_states": ["no", "no"], "percentages": ["90", "10"]},
                    {"parent_states": ["no", "yes"], "percentages": ["80", "20"]},
                    {"parent_states": ["yes", "no"], "percentages": ["30", "70"]},
                    {"parent_states": ["yes", "yes"], "percentages": ["10", "90"]},
                ],
            },
        ],
    }


def test_two_parent_asymmetric_passes_with_explicit_engine_literal() -> None:
    doc = validate(_two_parent_xml())
    assert doc.xsd.valid is True
    payload = _valid_two_parent_payload(doc.source_sha256)

    report = validate_cpts(doc, payload)
    assert report.valid is True
    assert report.errors == ()
    assert [t.node_id for t in report.tables] == ["P1", "P2", "C"]
    table_c = report.tables[2]
    assert table_c.parent_ids == ("P1", "P2")
    assert [r.parent_states for r in table_c.rows] == [
        ("no", "no"),
        ("no", "yes"),
        ("yes", "no"),
        ("yes", "yes"),
    ]
    assert table_c.rows[0].units == (90_000_000, 10_000_000)
    assert table_c.rows[1].units == (80_000_000, 20_000_000)
    assert table_c.rows[2].units == (30_000_000, 70_000_000)
    assert table_c.rows[3].units == (10_000_000, 90_000_000)

    artifact = build_effective_artifact(doc, payload)
    engine_c = [t for t in artifact.engine_tables if t.node_id == "C"][0]
    # Child state fastest (rows of the 2-D matrix), last parent P2 next,
    # first parent P1 slowest: columns follow the Cartesian row order above.
    assert engine_c.evidence_order == ("P1", "P2")
    assert engine_c.units_2d == (
        (90_000_000, 80_000_000, 30_000_000, 10_000_000),
        (10_000_000, 20_000_000, 70_000_000, 90_000_000),
    )
    assert engine_c.values_2d == ((0.9, 0.8, 0.3, 0.1), (0.1, 0.2, 0.7, 0.9))

    root = etree.fromstring(bytes(artifact.effective_bytes))
    definitions = {d.findtext("FOR"): d for d in root.find("NETWORK").findall("DEFINITION")}
    assert definitions["C"].findtext("TABLE").split() == [
        "0.9",
        "0.1",
        "0.8",
        "0.2",
        "0.3",
        "0.7",
        "0.1",
        "0.9",
    ]


def test_transposed_row_order_rejected() -> None:
    doc = validate(_two_parent_xml())
    transposed = _valid_two_parent_payload(doc.source_sha256)
    # Swap the middle Cartesian rows: (no,yes) <-> (yes,no).
    rows = transposed["tables"][2]["rows"]
    transposed["tables"][2]["rows"] = [rows[0], rows[2], rows[1], rows[3]]
    _assert_rejected(transposed, doc, "wrong_row_order")


def test_swapped_parent_ids_rejected() -> None:
    doc = validate(_two_parent_xml())
    swapped = _valid_two_parent_payload(doc.source_sha256)
    swapped["tables"][2]["parent_ids"] = ["P2", "P1"]
    _assert_rejected(swapped, doc, "wrong_parent_order")


def test_transposed_engine_matrix_differs_from_correct_literal() -> None:
    # Correct literal from the passing test above (independently worked).
    correct = (
        (90_000_000, 80_000_000, 30_000_000, 10_000_000),
        (10_000_000, 20_000_000, 70_000_000, 90_000_000),
    )
    # A parent-reversed (transposed) mapping would order columns as
    # (no,no),(yes,no),(no,yes),(yes,yes): middle columns swapped.
    transposed = (
        (90_000_000, 30_000_000, 80_000_000, 10_000_000),
        (10_000_000, 70_000_000, 20_000_000, 90_000_000),
    )
    assert transposed != correct
    # The implementation's converted matrix must equal the correct literal,
    # proving it did not silently transpose.
    doc = validate(_two_parent_xml())
    artifact = build_effective_artifact(doc, _valid_two_parent_payload(doc.source_sha256))
    engine_c = [t for t in artifact.engine_tables if t.node_id == "C"][0]
    assert engine_c.units_2d == correct
    assert engine_c.units_2d != transposed


# --- Slice D (S23.b slice 1): two-node exact inference, empty evidence only ---


def test_infer_two_node_empty_evidence_yields_worked_marginals() -> None:
    from x_insight.models.inference import INFERENCE_COMPARISON_TOLERANCE, infer_effective

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    result = infer_effective(artifact)

    # Pinned execution policy: empty evidence, float64, source-order elimination.
    assert dict(result.evidence) == {}
    assert result.query_nodes == ("A", "B")
    assert result.float_dtype == "float64"
    assert result.elimination_order == "source_order"
    assert result.tie_break == "source_order"
    assert result.tolerance == INFERENCE_COMPARISON_TOLERANCE == 1e-6
    assert result.effective_sha256 == artifact.effective_sha256
    assert dict(result.engine_config) == dict(PINNED_ENGINE_CONFIG)
    # Independently worked literals, never produced by the impl under test:
    # P(A=yes)=20/100=0.20; P(B=yes)=0.1*0.8+0.7*0.2=0.08+0.14=0.22.
    by_node = {p.node_id: p for p in result.posteriors}
    assert by_node["A"].states == ("no", "yes")
    assert by_node["B"].states == ("no", "yes")
    assert abs(by_node["A"].probabilities[0] - 0.80) <= 1e-6
    assert abs(by_node["A"].probabilities[1] - 0.20) <= 1e-6
    assert abs(by_node["B"].probabilities[0] - 0.78) <= 1e-6
    assert abs(by_node["B"].probabilities[1] - 0.22) <= 1e-6
    # Deterministic repeat: same bounded execution gives identical posteriors.
    again = infer_effective(artifact)
    again_by = {p.node_id: p for p in again.posteriors}
    for node in ("A", "B"):
        for a, b in zip(by_node[node].probabilities, again_by[node].probabilities):
            assert abs(a - b) <= 1e-12


def test_observed_projection_does_not_clamp_node() -> None:
    from x_insight.models.inference import infer_effective

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    # Observed patient values are provenance only; execution evidence stays empty.
    observed = {"A": "yes", "B": "no"}
    result = infer_effective(artifact, patient_projection=observed)

    assert dict(result.evidence) == {}
    by_node = {p.node_id: p for p in result.posteriors}
    assert abs(by_node["A"].probabilities[1] - 0.20) <= 1e-6
    assert abs(by_node["B"].probabilities[1] - 0.22) <= 1e-6
    # Same as without any projection: the observed value did not clamp.
    plain = infer_effective(artifact)
    plain_by = {p.node_id: p for p in plain.posteriors}
    for node in ("A", "B"):
        for a, b in zip(by_node[node].probabilities, plain_by[node].probabilities):
            assert abs(a - b) <= 1e-12


def test_infer_rejects_nonempty_evidence_without_approximation() -> None:
    import dataclasses

    from x_insight.models.inference import InferenceError, infer_effective

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    clamped = dataclasses.replace(artifact, evidence={"A": "yes"})

    try:
        infer_effective(clamped)
    except InferenceError as exc:
        assert exc.code == "evidence_not_empty"
    else:
        raise AssertionError("nonempty evidence inferred")


def test_infer_uses_effective_tables_not_source_placeholders() -> None:
    from x_insight.models.inference import infer_effective

    doc = validate(_two_node_xml())
    # Registered source placeholders are uniform 0.5, never the accepted CPTs.
    assert doc.source_bytes.count(b"0.5 0.5") == 3
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    result = infer_effective(artifact)
    by_node = {p.node_id: p for p in result.posteriors}
    # Accepted CPTs give 0.20/0.22, not the 0.5 placeholder marginals.
    assert abs(by_node["A"].probabilities[1] - 0.20) <= 1e-6
    assert abs(by_node["A"].probabilities[1] - 0.5) > 0.1
    assert abs(by_node["B"].probabilities[1] - 0.22) <= 1e-6
    assert abs(by_node["B"].probabilities[1] - 0.5) > 0.1


# --- Slice E (S23.b slice 2): asymmetric execution proves the mapping ---


def _asymmetric_infer_payload(network_hash: str) -> dict:
    # P1 80/20 breaks symmetry so a parent-reversed mapping changes P(C).
    # C rows stay the S23.a asymmetric fixture: 90/10, 80/20, 30/70, 10/90.
    return {
        "network_hash": network_hash,
        "tables": [
            {
                "node_id": "P1",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["80", "20"]}],
            },
            {
                "node_id": "P2",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["50", "50"]}],
            },
            {
                "node_id": "C",
                "parent_ids": ["P1", "P2"],
                "states": ["no", "yes"],
                "rows": [
                    {"parent_states": ["no", "no"], "percentages": ["90", "10"]},
                    {"parent_states": ["no", "yes"], "percentages": ["80", "20"]},
                    {"parent_states": ["yes", "no"], "percentages": ["30", "70"]},
                    {"parent_states": ["yes", "yes"], "percentages": ["10", "90"]},
                ],
            },
        ],
    }


def test_infer_two_parent_asymmetric_proves_mapping() -> None:
    import hashlib

    from lxml import etree

    from x_insight.models.inference import infer_effective

    source = _two_parent_xml()
    doc = validate(source)
    before = bytes(doc.source_bytes)
    payload = _asymmetric_infer_payload(doc.source_sha256)
    artifact = build_effective_artifact(doc, payload)

    # Registered XML/hash unchanged; effective XML holds every accepted table.
    assert doc.source_bytes == before == source
    assert artifact.source_sha256 == hashlib.sha256(source).hexdigest()
    assert artifact.effective_sha256 == hashlib.sha256(artifact.effective_bytes).hexdigest()
    root = etree.fromstring(bytes(artifact.effective_bytes))
    definitions = {d.findtext("FOR"): d for d in root.find("NETWORK").findall("DEFINITION")}
    assert definitions["C"].findtext("TABLE").split() == [
        "0.9",
        "0.1",
        "0.8",
        "0.2",
        "0.3",
        "0.7",
        "0.1",
        "0.9",
    ]
    engine_c = [t for t in artifact.engine_tables if t.node_id == "C"][0]
    assert engine_c.evidence_order == ("P1", "P2")
    assert engine_c.units_2d == (
        (90_000_000, 80_000_000, 30_000_000, 10_000_000),
        (10_000_000, 20_000_000, 70_000_000, 90_000_000),
    )

    result = infer_effective(artifact)
    assert dict(result.evidence) == {}
    by_node = {p.node_id: p for p in result.posteriors}
    # Independently worked: P(P1=yes)=0.20, P(P2=yes)=0.50,
    # P(C=yes)=0.8*0.5*0.1+0.8*0.5*0.2+0.2*0.5*0.7+0.2*0.5*0.9=0.28.
    assert abs(by_node["P1"].probabilities[1] - 0.20) <= 1e-6
    assert abs(by_node["P2"].probabilities[1] - 0.50) <= 1e-6
    assert abs(by_node["C"].probabilities[1] - 0.28) <= 1e-6
    assert abs(by_node["C"].probabilities[0] - 0.72) <= 1e-6
    # Transposed middle columns would give 0.43, far outside tolerance.
    transposed_yes = 0.43  # 0.04+0.28+0.02+0.09 with (no,yes)<->(yes,no) swapped
    assert abs(0.28 - transposed_yes) > 0.1
    assert abs(by_node["C"].probabilities[1] - transposed_yes) > 0.1


# --- Slice F (S23.b slice 3): replay + bounded isolation + errors ---


def test_replay_matches_under_pinned_runtime_with_provider_unavailable() -> None:
    import socket

    from x_insight.models.inference import (
        INFERENCE_COMPARISON_TOLERANCE,
        PINNED_ENGINE_CONFIG,
        infer_effective,
        replay,
    )

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    original = infer_effective(artifact)

    # Provider/network unavailable: replay uses only frozen CPTs/query/config.
    real_create_connection = socket.create_connection

    def _unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("provider unavailable")

    socket.create_connection = _unavailable  # type: ignore[method-assign]
    try:
        repeated = replay(artifact, original)
    finally:
        socket.create_connection = real_create_connection  # type: ignore[method-assign]

    assert dict(repeated.evidence) == {}
    assert repeated.query_nodes == original.query_nodes == ("A", "B")
    assert repeated.effective_sha256 == artifact.effective_sha256
    assert dict(repeated.engine_config) == dict(PINNED_ENGINE_CONFIG)
    assert repeated.runtime.python_version == "3.12.14"
    assert repeated.runtime.pgmpy_version == "1.1.2"
    assert repeated.runtime.lxml_version == "6.1.3"
    assert repeated.runtime.policy_version == "cpt-decimal-v1"
    assert repeated.tolerance == INFERENCE_COMPARISON_TOLERANCE == 1e-6
    want = {p.node_id: p for p in original.posteriors}
    got = {p.node_id: p for p in repeated.posteriors}
    for node in ("A", "B"):
        assert want[node].states == got[node].states
        for a, b in zip(want[node].probabilities, got[node].probabilities):
            assert abs(a - b) <= INFERENCE_COMPARISON_TOLERANCE


def test_replay_mismatch_detected_without_approximation() -> None:
    import dataclasses

    from x_insight.models.inference import InferenceError, NodePosterior, infer_effective, replay

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    original = infer_effective(artifact)

    # Corrupt the expectation far outside tolerance (transposed-style 0.43).
    wrong_posteriors = []
    for post in original.posteriors:
        if post.node_id == "B":
            wrong_posteriors.append(
                NodePosterior(node_id="B", states=post.states, probabilities=(0.57, 0.43))
            )
        else:
            wrong_posteriors.append(post)
    wrong = dataclasses.replace(original, posteriors=tuple(wrong_posteriors))

    try:
        replay(artifact, wrong)
    except InferenceError as exc:
        assert exc.code == "replay_mismatch"
    else:
        raise AssertionError("mismatched replay approximated")


def test_numeric_failure_returns_explicit_error() -> None:
    import dataclasses

    from x_insight.models.inference import InferenceError, infer_effective

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    # Freeze a nonfinite engine value: must raise, never approximate.
    broken_tables = []
    for engine in artifact.engine_tables:
        if engine.node_id == "A":
            broken_tables.append(
                dataclasses.replace(
                    engine, values_2d=((float("nan"),), (0.2,)), units_2d=engine.units_2d
                )
            )
        else:
            broken_tables.append(engine)
    broken = dataclasses.replace(artifact, engine_tables=tuple(broken_tables))

    try:
        infer_effective(broken)
    except InferenceError as exc:
        assert exc.code == "inference_numeric"
    else:
        raise AssertionError("nonfinite engine approximated")


def test_resource_timeout_returns_explicit_error_without_approximation() -> None:
    from x_insight.models.inference import InferenceError, infer_effective

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))

    # Bounded child process: a millisecond budget cannot fit spawn+pgmpy import.
    try:
        infer_effective(artifact, timeout_secs=0.001)
    except InferenceError as exc:
        assert exc.code == "inference_timeout"
    else:
        raise AssertionError("over-budget inference approximated")


def test_tolerance_pinned_with_execution_policy() -> None:
    from x_insight.models.inference import (
        INFERENCE_COMPARISON_TOLERANCE,
        PINNED_ENGINE_CONFIG,
        infer_effective,
    )

    doc = validate(_two_node_xml())
    artifact = build_effective_artifact(doc, _valid_two_payload(doc.source_sha256))
    result = infer_effective(artifact)

    assert INFERENCE_COMPARISON_TOLERANCE == 1e-6
    assert result.tolerance == 1e-6
    assert result.float_dtype == "float64"
    assert result.elimination_order == "source_order"
    assert result.tie_break == "source_order"
    assert dict(result.engine_config) == dict(PINNED_ENGINE_CONFIG)
    assert dict(result.runtime.engine_config) == dict(PINNED_ENGINE_CONFIG)
