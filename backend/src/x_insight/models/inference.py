"""CPT validation and effective-artifact conversion (S23.a, seam T5; plan.md §7.4).

Public interface (seam T5 — tests and callers use the same functions):

- :func:`validate_cpts` — validate a full CPT payload against one admitted
  :class:`~x_insight.models.validation.ValidatedXmlbif` network.
- :func:`build_effective_artifact` — convert validated CPTs into a run-local
  effective XML artifact plus pinned runtime record for later S23.b replay.

What this module does and does not promise:

- Success means "CPTs admitted for the inference pipeline subject to its own
  exact checks". It is never labeled executable or clinically valid beyond
  pipeline admission.
- Exact policy ``CPT_POLICY_VERSION`` (``cpt-decimal-v1``): decimal strings
  with at most six decimal places; integer units with ``100% = 100_000_000``.
  Every row must total exactly ``100_000_000`` units. Invalid estimates are
  never normalized, clipped, completed, or repaired.
- Registered XML bytes/hash stay unchanged; the effective artifact is a new
  run-local document with every TABLE populated, fixed structure/ordering
  preserved, and its own hash frozen before inference.
- No pgmpy inference call, no child process, no provider/MCP, no
  empty-evidence ``P(A)`` computation — those are S23.b. The artifact
  (effective bytes + hash + ordered tables + integer units + query/config)
  is directly consumable by S23.b.

Ordering contract (explicit, never incidental):

- Admitted order is source order throughout: variable order, per-definition
  ``GIVEN`` order, ``OUTCOME`` order, ``DEFINITION`` order, ``PROPERTY`` order.
- One table per node in variable order; one row per ordered Cartesian parent
  configuration with the first declared parent slowest and the last declared
  parent fastest; child states in admitted ``OUTCOME`` order within each row.
- Flat XMLBIF index for parents ``P0..Pk-1`` (cards ``N0..Nk-1``), child
  states ``Ns``: row ``r = (...((p0*N1 + p1)*N2 + p2)...)`` and flat
  ``f = r*Ns + s``. Child state fastest, last parent next, first parent
  slowest. Engine evidence order MUST equal ``GIVEN`` order with columns in
  the same Cartesian order (first parent slowest); a transposed (reversed
  parent) mapping yields different columns on asymmetric fixtures and fails.
- Effective XML TABLEs store exact fractions (``units / 100_000_000``,
  up to eight decimal places, no float rounding); engine 2-D values are
  ``units / 100_000_000`` as ``float64`` with evidence order pinned to
  ``GIVEN`` order. Metadata (``PROPERTY`` elements, ``TYPE`` attributes)
  is preserved in effective XML outside any engine conversion that drops it.

Safety/bounds:

- Deterministic fixed check order, source order inside each check, bounded
  counts (``MAX_CPT_ERRORS``) and bounded messages (``MAX_MESSAGE_CHARS``).
  Engineering bounds only, not clinical thresholds.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as _metadata
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lxml import etree

from x_insight.models.validation import ValidatedXmlbif

# --- Pinned policy and engineering bounds (not clinical thresholds) ---

#: Decimal-string/integer-unit policy version; bump only with a migration.
CPT_POLICY_VERSION = "cpt-decimal-v1"

#: Integer units for exactly 100% (1 unit = 0.000001%).
UNITS_FOR_100_PCT = 100_000_000

#: Max decimal places in a percentage string.
MAX_DECIMAL_PLACES = 6

#: Diagnostic caps: fixed order, bounded counts/messages.
MAX_CPT_ERRORS = 50
MAX_MESSAGE_CHARS = 500

#: Pinned engine boundary for S23.b replay (evidence always empty).
PINNED_ENGINE_CONFIG: dict[str, Any] = {
    "evidence": "empty",
    "evidence_order": "given_order",
    "float_dtype": "float64",
    "elimination_order": "source_order",
    "tie_break": "source_order",
}

_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_NONFINITE_TOKENS = frozenset(
    {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
)

_TOP_REQUIRED = ("network_hash", "tables")
_TOP_ALLOWED = frozenset(
    {"network_hash", "tables", "network_name", "network_version", "question_key"}
)
_TABLE_REQUIRED = ("node_id", "parent_ids", "states", "rows")
_ROW_REQUIRED = ("parent_states", "percentages")


class CptValidationError(ValueError):
    """Hard failure for conversion: CPTs invalid or source mutated.

    Attributes: ``code`` (stable short string), ``message`` (bounded),
    ``errors`` (bounded tuple of :class:`CptIssue`).
    """

    def __init__(self, code: str, message: str, errors: tuple[CptIssue, ...] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message[:MAX_MESSAGE_CHARS]
        self.errors = errors


@dataclass(frozen=True)
class CptIssue:
    code: str
    message: str
    node: str = ""
    row: int = -1


@dataclass(frozen=True)
class ValidatedCptRow:
    parent_states: tuple[str, ...]
    percentages: tuple[str, ...]
    units: tuple[int, ...]


@dataclass(frozen=True)
class ValidatedCptTable:
    node_id: str
    parent_ids: tuple[str, ...]
    states: tuple[str, ...]
    rows: tuple[ValidatedCptRow, ...]


@dataclass(frozen=True)
class CptReport:
    valid: bool
    errors: tuple[CptIssue, ...]
    tables: tuple[ValidatedCptTable, ...]


@dataclass(frozen=True)
class RuntimeRecord:
    python_version: str
    pgmpy_version: str
    lxml_version: str
    policy_version: str
    engine_config: Mapping[str, Any]


@dataclass(frozen=True)
class EngineTable:
    node_id: str
    evidence_order: tuple[str, ...]
    units_2d: tuple[tuple[int, ...], ...]
    values_2d: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class EffectiveArtifact:
    source_sha256: str
    effective_sha256: str
    effective_bytes: bytes
    tables: tuple[ValidatedCptTable, ...]
    engine_tables: tuple[EngineTable, ...]
    query_nodes: tuple[str, ...]
    evidence: Mapping[str, Any]
    engine_config: Mapping[str, Any]
    runtime: RuntimeRecord


def _clip(text: str) -> str:
    return text[:MAX_MESSAGE_CHARS]


def _pinned_versions() -> tuple[str, str, str]:
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    try:
        pgmpy_v = _metadata.version("pgmpy")
    except Exception:
        pgmpy_v = "unknown"
    try:
        lxml_v = _metadata.version("lxml")
    except Exception:
        lxml_v = "unknown"
    return py, pgmpy_v, lxml_v


def _expected_rows(
    parent_ids: tuple[str, ...], states_by_var: dict[str, tuple[str, ...]]
) -> list[tuple[str, ...]]:
    if not parent_ids:
        return [()]
    cards = [states_by_var[p] for p in parent_ids]
    rows: list[tuple[str, ...]] = [()]
    for states in cards:
        rows = [prefix + (s,) for prefix in rows for s in states]
    return rows


def _parse_percentage(value: Any) -> tuple[int | None, str | None]:
    """Return (units, error_code) without float coercion or normalization."""
    if isinstance(value, bool):
        return None, "boolean_percentage"
    if value is None:
        return None, "null_percentage"
    if isinstance(value, float):
        # Nonfinite floats are distinct; other floats are not decimal strings.
        import math as _math

        if not _math.isfinite(value):
            return None, "nonfinite_percentage"
        return None, "malformed_percentage"
    if not isinstance(value, str):
        return None, "malformed_percentage"
    token = value
    if not token:
        return None, "malformed_percentage"
    lowered = token.lower()
    if lowered in _NONFINITE_TOKENS:
        return None, "nonfinite_percentage"
    if not _DECIMAL_RE.match(token):
        return None, "malformed_percentage"
    body = token[1:] if token.startswith("-") else token
    if "." in body:
        frac = body.split(".", 1)[1]
        if len(frac) > MAX_DECIMAL_PLACES:
            return None, "excess_precision"
    # Exact integer units: percent * 1_000_000.
    negative = token.startswith("-")
    digits = body.replace(".", "")
    decimals = len(body.split(".", 1)[1]) if "." in body else 0
    try:
        raw = int(digits) if digits else 0
    except ValueError:
        return None, "malformed_percentage"
    units = raw * (10 ** (MAX_DECIMAL_PLACES - decimals))
    if negative:
        units = -units
    if units < 0 or units > UNITS_FOR_100_PCT:
        return None, "percentage_out_of_range"
    return units, None


def _format_fraction(units: int) -> str:
    if units == 0:
        return "0"
    if units == UNITS_FOR_100_PCT:
        return "1"
    whole = units // UNITS_FOR_100_PCT
    rest = units % UNITS_FOR_100_PCT
    frac = f"{rest:08d}".rstrip("0")
    if whole == 0:
        return f"0.{frac}" if frac else "0"
    return f"{whole}.{frac}" if frac else str(whole)


def validate_cpts(document: ValidatedXmlbif, payload: Mapping[str, Any]) -> CptReport:
    """Validate a full CPT payload against one admitted network.

    Fixed check order: document shape → envelope → identity → table
    existence/order → per-table parent/state order/structure → per-row
    parent order/structure → per-value policy → per-row exact total.
    Within a check, admitted source order. Bounded to ``MAX_CPT_ERRORS``.
    """
    errors: list[CptIssue] = []

    def add(code: str, message: str, node: str = "", row: int = -1) -> None:
        if len(errors) < MAX_CPT_ERRORS:
            errors.append(CptIssue(code=code, message=_clip(message), node=node, row=row))

    if len(document.networks) != 1:
        code = "no_network" if not document.networks else "multiple_networks"
        add(code, f"Expected one NETWORK for CPT validation, found {len(document.networks)}.")
        return CptReport(valid=False, errors=tuple(errors), tables=())
    (network,) = document.networks
    if not network.variables:
        add("empty_network", "Network declares no variables.")
        return CptReport(valid=False, errors=tuple(errors), tables=())

    var_order = [v.name for v in network.variables]
    declared = set(var_order)
    states_by_var = {v.name: v.states for v in network.variables}
    parents_by_var: dict[str, tuple[str, ...]] = {}
    for v in network.variables:
        parents_by_var[v.name] = ()
    for d in network.definitions:
        if d.for_node in declared:
            # Last DEFINITION wins is never relied on; duplicates are a
            # structural problem upstream, but keep deterministic behavior.
            if d.for_node not in parents_by_var or parents_by_var[d.for_node] == ():
                parents_by_var[d.for_node] = d.parents
            elif parents_by_var[d.for_node] != d.parents:
                # Keep first; structural mismatch surfaces via row checks.
                pass

    if not isinstance(payload, Mapping):
        add("malformed_payload", "CPT payload must be a mapping.")
        return CptReport(valid=False, errors=tuple(errors), tables=())

    for req in _TOP_REQUIRED:
        if req not in payload:
            add("missing_field", f"Payload is missing required field {req!r}.")
    for key in sorted(str(k) for k in payload.keys()):
        if key not in _TOP_ALLOWED:
            add("extra_field", f"Payload has extra field {key!r}.")
    if errors:
        return CptReport(valid=False, errors=tuple(errors), tables=())

    raw_hash = payload.get("network_hash")
    if not isinstance(raw_hash, str) or raw_hash != document.source_sha256:
        add("hash_mismatch", "network_hash must exactly match the admitted source SHA-256.")
    if "network_name" in payload and payload["network_name"] is not None:
        raw_name = payload["network_name"]
        if not isinstance(raw_name, str) or raw_name != network.name:
            add("network_mismatch", f"network_name must exactly match {network.name!r}.")
    for opt in ("network_version", "question_key"):
        if opt in payload and payload[opt] is not None and not isinstance(payload[opt], str):
            add("malformed_payload", f"Optional field {opt!r} must be a string.")
    if errors:
        return CptReport(valid=False, errors=tuple(errors), tables=())

    raw_tables = payload.get("tables")
    if not isinstance(raw_tables, (list, tuple)):
        add("wrong_structure", "Payload 'tables' must be a list with one entry per node.")
        return CptReport(valid=False, errors=tuple(errors), tables=())
    table_list = list(raw_tables)

    # Per-entry envelope first (deterministic payload order).
    by_node_first: dict[str, int] = {}
    by_node_count: dict[str, int] = {}
    entry_ok: list[bool] = []
    for idx, entry in enumerate(table_list):
        if not isinstance(entry, Mapping):
            add("wrong_structure", f"Table entry {idx} must be a mapping.")
            entry_ok.append(False)
            continue
        keys = set(str(k) for k in entry.keys())
        required = set(_TABLE_REQUIRED)
        for req in sorted(required):
            if req not in keys:
                node_hint = (
                    str(entry.get("node_id", "")) if isinstance(entry.get("node_id"), str) else ""
                )
                add("missing_field", f"Table entry {idx} is missing field {req!r}.", node_hint)
        for key in sorted(keys):
            if key not in required:
                node_hint = (
                    str(entry.get("node_id", "")) if isinstance(entry.get("node_id"), str) else ""
                )
                add("extra_field", f"Table entry {idx} has extra field {key!r}.", node_hint)
        node_id = entry.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            add("wrong_structure", f"Table entry {idx} has an invalid node_id.")
            entry_ok.append(False)
            continue
        entry_ok.append(True)
        by_node_count[node_id] = by_node_count.get(node_id, 0) + 1
        if node_id not in by_node_first:
            by_node_first[node_id] = idx

    payload_order: list[str] = []
    for e in table_list:
        if isinstance(e, Mapping):
            nid = e.get("node_id")
            if isinstance(nid, str):
                payload_order.append(nid)
    for extra_id in sorted(set(payload_order)):
        if extra_id not in declared:
            add("extra_table", f"Table for unknown node {extra_id!r} is not declared.", extra_id)
    for node_id in var_order:
        count = by_node_count.get(node_id, 0)
        if count == 0:
            add("missing_table", f"Table for node {node_id!r} is omitted.", node_id)
        elif count > 1:
            add("duplicate_table", f"Table for node {node_id!r} appears {count} times.", node_id)
    if [n for n in payload_order if n in declared] != [n for n in var_order if n in by_node_first]:
        # Same set but different order (or extras); report only when sets match
        # to keep omitted/duplicate/extra distinct from ordering.
        payload_set = [n for n in payload_order if n in declared]
        # Deduplicate preserving first occurrence for order comparison.
        seen: set[str] = set()
        deduped: list[str] = []
        for n in payload_set:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        expected = [n for n in var_order if n in by_node_first]
        if set(deduped) == set(expected) and deduped != expected:
            add("wrong_node_order", f"Node order {deduped} must match admitted order {expected}.")
    if errors:
        # Still continue to per-table checks only when every declared node
        # has exactly one table; otherwise further checks would cascade.
        if any(by_node_count.get(n, 0) != 1 for n in var_order) or any(
            n not in declared for n in set(payload_order)
        ):
            return CptReport(valid=False, errors=tuple(errors), tables=())

    validated: dict[str, ValidatedCptTable] = {}
    # Per-table checks in admitted source order (deterministic).
    for node_id in var_order:
        table_idx = by_node_first.get(node_id)
        if table_idx is None:
            continue
        entry = table_list[table_idx]
        assert isinstance(entry, Mapping)
        expected_parents = parents_by_var.get(node_id, ())
        expected_states = states_by_var.get(node_id, ())
        raw_parents = entry.get("parent_ids")
        raw_states = entry.get("states")
        raw_rows = entry.get("rows")
        if not isinstance(raw_parents, (list, tuple)) or not all(
            isinstance(p, str) for p in raw_parents
        ):
            add(
                "wrong_structure",
                f"Table {node_id!r} parent_ids must list declared parents.",
                node_id,
            )
            continue
        if not isinstance(raw_states, (list, tuple)) or not all(
            isinstance(s, str) for s in raw_states
        ):
            add("wrong_structure", f"Table {node_id!r} states must list declared states.", node_id)
            continue
        parent_ids = tuple(raw_parents)
        states = tuple(raw_states)
        if parent_ids != expected_parents:
            if set(parent_ids) == set(expected_parents):
                add(
                    "wrong_parent_order",
                    f"Table {node_id!r} parent order {list(parent_ids)} "
                    f"must match GIVEN order {list(expected_parents)}.",
                    node_id,
                )
            else:
                add(
                    "wrong_structure",
                    f"Table {node_id!r} parents {list(parent_ids)} "
                    f"must match GIVEN {list(expected_parents)}.",
                    node_id,
                )
            continue
        if states != expected_states:
            if set(states) == set(expected_states):
                add(
                    "wrong_state_order",
                    f"Table {node_id!r} state order {list(states)} "
                    f"must match OUTCOME order {list(expected_states)}.",
                    node_id,
                )
            else:
                add(
                    "wrong_structure",
                    f"Table {node_id!r} states {list(states)} "
                    f"must match OUTCOME {list(expected_states)}.",
                    node_id,
                )
            continue
        if not isinstance(raw_rows, (list, tuple)):
            add("wrong_structure", f"Table {node_id!r} rows must be a list.", node_id)
            continue
        rows_list = list(raw_rows)
        expected_rows = _expected_rows(parent_ids, states_by_var)
        if len(rows_list) < len(expected_rows):
            add(
                "missing_row",
                f"Table {node_id!r} has {len(rows_list)} rows, expected {len(expected_rows)}.",
                node_id,
            )
        elif len(rows_list) > len(expected_rows):
            add(
                "extra_row",
                f"Table {node_id!r} has {len(rows_list)} rows, expected {len(expected_rows)}.",
                node_id,
            )
        # Row checks up to the shared prefix; length mismatch already reported.
        seen_parents: set[tuple[str, ...]] = set()
        built_rows: list[ValidatedCptRow] = []
        row_failed = False
        for i, raw_row in enumerate(rows_list[: len(expected_rows)]):
            expected_parent = expected_rows[i] if i < len(expected_rows) else ()
            if not isinstance(raw_row, Mapping):
                add("wrong_structure", f"Table {node_id!r} row {i} must be a mapping.", node_id, i)
                row_failed = True
                continue
            keys = set(str(k) for k in raw_row.keys())
            required = set(_ROW_REQUIRED)
            for req in sorted(required):
                if req not in keys:
                    add(
                        "missing_field",
                        f"Table {node_id!r} row {i} is missing {req!r}.",
                        node_id,
                        i,
                    )
            for key in sorted(keys):
                if key not in required:
                    add(
                        "extra_field",
                        f"Table {node_id!r} row {i} has extra field {key!r}.",
                        node_id,
                        i,
                    )
            raw_parent_states = raw_row.get("parent_states")
            raw_percentages = raw_row.get("percentages")
            if not isinstance(raw_parent_states, (list, tuple)) or not all(
                isinstance(s, str) for s in raw_parent_states
            ):
                add(
                    "wrong_structure",
                    f"Table {node_id!r} row {i} parent_states must list states.",
                    node_id,
                    i,
                )
                row_failed = True
                continue
            parent_states = tuple(raw_parent_states)
            if parent_states in seen_parents:
                add(
                    "duplicate_row",
                    f"Table {node_id!r} row {i} duplicates {list(parent_states)}.",
                    node_id,
                    i,
                )
                row_failed = True
                continue
            if parent_states != expected_parent:
                if parent_states in expected_rows:
                    add(
                        "wrong_row_order",
                        f"Table {node_id!r} row {i} parent {list(parent_states)} "
                        f"must be {list(expected_parent)} (Cartesian order).",
                        node_id,
                        i,
                    )
                else:
                    add(
                        "wrong_structure",
                        f"Table {node_id!r} row {i} parent {list(parent_states)} "
                        f"must be {list(expected_parent)}.",
                        node_id,
                        i,
                    )
                row_failed = True
                seen_parents.add(parent_states)
                continue
            seen_parents.add(parent_states)
            if not isinstance(raw_percentages, (list, tuple)):
                add(
                    "wrong_structure",
                    f"Table {node_id!r} row {i} percentages must be a list.",
                    node_id,
                    i,
                )
                row_failed = True
                continue
            pct_list = list(raw_percentages)
            if len(pct_list) != len(states):
                add(
                    "wrong_structure",
                    f"Table {node_id!r} row {i} has {len(pct_list)} values, "
                    f"expected {len(states)}.",
                    node_id,
                    i,
                )
                row_failed = True
                continue
            units_row: list[int] = []
            value_failed = False
            for j, token in enumerate(pct_list):
                units, err = _parse_percentage(token)
                if err is not None:
                    add(
                        err,
                        f"Table {node_id!r} row {i} value {j} is {err}: {token!r}.",
                        node_id,
                        i,
                    )
                    value_failed = True
                else:
                    assert units is not None
                    units_row.append(units)
            if value_failed:
                row_failed = True
                continue
            if sum(units_row) != UNITS_FOR_100_PCT:
                add(
                    "inexact_total",
                    f"Table {node_id!r} row {i} totals {sum(units_row)} units, "
                    f"expected {UNITS_FOR_100_PCT}.",
                    node_id,
                    i,
                )
                row_failed = True
                continue
            built_rows.append(
                ValidatedCptRow(
                    parent_states=parent_states,
                    percentages=tuple(pct_list),  # type: ignore[arg-type]
                    units=tuple(units_row),
                )
            )
        if row_failed or len(built_rows) != len(expected_rows):
            continue
        validated[node_id] = ValidatedCptTable(
            node_id=node_id, parent_ids=parent_ids, states=states, rows=tuple(built_rows)
        )

    if errors:
        return CptReport(valid=False, errors=tuple(errors), tables=())
    ordered = tuple(validated[n] for n in var_order)
    return CptReport(valid=True, errors=(), tables=ordered)


def _safe_parser() -> Any:
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)


def build_effective_artifact(
    document: ValidatedXmlbif,
    payload: Mapping[str, Any],
    *,
    query_nodes: Sequence[str] | None = None,
    engine_config: Mapping[str, Any] | None = None,
) -> EffectiveArtifact:
    """Convert validated CPTs into a run-local effective artifact.

    Populates EVERY table in a new XML document preserving fixed structure
    and ordering from the registered source, freezes the effective hash,
    and records the pinned runtime for S23.b replay. The registered
    ``document.source_bytes`` is never mutated. Raises
    :class:`CptValidationError` when CPTs are invalid or the query/source
    does not match; success is pipeline admission only, never an
    executability or clinical-validity claim. No inference is executed.
    """
    report = validate_cpts(document, payload)
    if not report.valid:
        raise CptValidationError("cpt_invalid", "CPT payload failed validation.", report.errors)
    (network,) = document.networks
    if hashlib.sha256(bytes(document.source_bytes)).hexdigest() != document.source_sha256:
        raise CptValidationError(
            "source_changed", "Registered source bytes changed during conversion."
        )

    declared = [v.name for v in network.variables]
    if query_nodes is None:
        query_tuple = tuple(declared)
    else:
        if not isinstance(query_nodes, (list, tuple)) or not all(
            isinstance(q, str) for q in query_nodes
        ):
            raise CptValidationError("wrong_structure", "query_nodes must list declared nodes.")
        query_tuple = tuple(query_nodes)
        for q in query_tuple:
            if q not in set(declared):
                raise CptValidationError(
                    "undeclared_query",
                    f"Query node {q!r} is not declared.",
                    (
                        CptIssue(
                            code="undeclared_query",
                            message=_clip(f"Query node {q!r} is not declared."),
                            node=q,
                        ),
                    ),
                )

    resolved_config: dict[str, Any] = (
        dict(PINNED_ENGINE_CONFIG) if engine_config is None else dict(engine_config)
    )

    by_table = {t.node_id: t for t in report.tables}
    try:
        root = etree.fromstring(bytes(document.source_bytes), _safe_parser())
    except etree.XMLSyntaxError as exc:
        raise CptValidationError("malformed_xml", _clip(str(exc))) from exc
    networks_el = root.findall("NETWORK")
    if len(networks_el) != 1:
        raise CptValidationError("wrong_structure", "Registered source must hold one NETWORK.")
    (network_el,) = networks_el
    for def_el in network_el.findall("DEFINITION"):
        for_el = def_el.find("FOR")
        for_name = (for_el.text or "").strip() if for_el is not None else ""
        table = by_table.get(for_name)
        if table is None:
            raise CptValidationError(
                "missing_table", f"Validated table for {for_name!r} is missing."
            )
        flat: list[str] = []
        for row in table.rows:
            for units in row.units:
                flat.append(_format_fraction(units))
        table_el = def_el.find("TABLE")
        if table_el is None:
            table_el = etree.SubElement(def_el, "TABLE")
        table_el.text = " ".join(flat)

    effective_bytes = etree.tostring(root, encoding="utf-8", xml_declaration=True)
    effective_sha = hashlib.sha256(effective_bytes).hexdigest()

    engine_tables: list[EngineTable] = []
    for table in report.tables:
        n_states = len(table.states)
        n_cols = len(table.rows)
        # Engine order equals XMLBIF Cartesian order: column c is row c,
        # row s is child state s. Evidence order is GIVEN order.
        units_2d: list[tuple[int, ...]] = []
        values_2d: list[tuple[float, ...]] = []
        for s in range(n_states):
            col_units = tuple(table.rows[c].units[s] for c in range(n_cols))
            col_values = tuple(u / UNITS_FOR_100_PCT for u in col_units)
            units_2d.append(col_units)
            values_2d.append(col_values)
        engine_tables.append(
            EngineTable(
                node_id=table.node_id,
                evidence_order=table.parent_ids,
                units_2d=tuple(units_2d),
                values_2d=tuple(values_2d),
            )
        )

    py_v, pgmpy_v, lxml_v = _pinned_versions()
    runtime = RuntimeRecord(
        python_version=py_v,
        pgmpy_version=pgmpy_v,
        lxml_version=lxml_v,
        policy_version=CPT_POLICY_VERSION,
        engine_config=dict(resolved_config),
    )
    return EffectiveArtifact(
        source_sha256=document.source_sha256,
        effective_sha256=effective_sha,
        effective_bytes=bytes(effective_bytes),
        tables=report.tables,
        engine_tables=tuple(engine_tables),
        query_nodes=query_tuple,
        evidence={},
        engine_config=dict(resolved_config),
        runtime=runtime,
    )
