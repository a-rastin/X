"""Safe XMLBIF draft import and inspection (S21, seam T5; plan.md §7.3, FR-31/37/NFR-05).

Public interface (seam T5 — tests and callers use the same functions):

- :func:`validate` — parse ``bytes`` safely, run the supplied XSD, return an
  ordered draft document plus a separate XSD report.
- :func:`display_graph` — read-only graph for one network; edges come only
  from ``DEFINITION FOR/GIVEN`` in source order. ``proposed_parent``
  ``PROPERTY`` values are metadata and never become edges.
- :func:`export_source` — return the exact preserved source bytes.

What this module does and does not promise:

- XSD success means "structurally valid against the supplied profile". It is
  never labeled executable or clinically valid; full semantic/admission checks
  (cycles, dimensions, normalization, mappings, review) belong to S22+.
- Drafts with missing ``DEFINITION``\\ s (e.g. BN-04/BN-08) remain storable
  and inspectable; they are reported nonactivatable under v1 via
  ``missing_definitions``.
- A document with several ``NETWORK``\\ s or any ``decision``/``utility``
  variable is XSD-valid but nonactivatable under v1 (``multiple_networks`` /
  ``unsupported_kind``). All networks are returned; none is silently
  selected or dropped.
- Order is preserved as in source throughout: network, variable, outcome,
  parent (``GIVEN``), definition, and ``PROPERTY`` order; ``TABLE`` tokens
  keep source order as raw strings.

Safety (Slice 2):

- The lxml parser is configured explicitly on every parse — never defaults::

      XMLParser(resolve_entities=False, load_dtd=False,
                no_network=True, huge_tree=False)

  ``DOCTYPE``/``ENTITY`` markers are rejected before parsing so no entity is
  expanded and no remote document is fetched. ``xinclude()`` is never called.
- Engineering guardrails (``MAX_*`` below) bound input size, network/variable/
  outcome counts, and per-definition table cells. They are resource limits,
  not clinical thresholds. Oversize or malformed input raises
  :class:`XmlValidationError` with a bounded message and a line/column
  location; XSD content violations instead return ``xsd.valid is False``
  with bounded per-error locations.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

# --- Engineering guardrails (resource limits, not clinical thresholds) ---

MAX_XML_BYTES = 1_048_576  # 1 MiB; largest supplied draft is ~57 KiB.
MAX_NETWORKS = 16  # >1 is nonactivatable under v1; >16 is rejected as abuse.
MAX_VARIABLES_PER_NETWORK = 256  # Supplied drafts peak at 32 variables.
MAX_OUTCOMES_PER_VARIABLE = 64  # Supplied peak is 22 states (oral meds).
MAX_TABLE_VALUES_PER_DEFINITION = 131_072  # Covers 1 584-cell drafts w/ headroom.
MAX_XSD_ERRORS = 20
MAX_MESSAGE_CHARS = 500
MAX_REASON_ITEMS = 10

SCHEMA_RELATIVE = Path("project-documents/bayesian-networks/schema.xml")


class XmlValidationError(ValueError):
    """Safe hard failure: unsafe, malformed, or resource-abusive input.

    Attributes: ``code`` (stable short string), ``message`` (bounded to
    ``MAX_MESSAGE_CHARS``), ``line``/``column`` (0 when unknown).
    """

    def __init__(self, code: str, message: str, line: int = 0, column: int = 0) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message[:MAX_MESSAGE_CHARS]
        self.line = line
        self.column = column


@dataclass(frozen=True)
class XsdIssue:
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class XsdReport:
    valid: bool
    errors: tuple[XsdIssue, ...]


@dataclass(frozen=True)
class XmlbifVariable:
    name: str
    kind: str  # "nature" | "decision" | "utility"; absent TYPE defaults to nature.
    states: tuple[str, ...]  # OUTCOME order as in source.
    properties: tuple[str, ...]  # PROPERTY order as in source.


@dataclass(frozen=True)
class XmlbifDefinition:
    for_node: str
    parents: tuple[str, ...]  # GIVEN order as in source.
    table: tuple[str, ...]  # Raw TABLE tokens in source order (no float coercion).
    properties: tuple[str, ...]  # PROPERTY order as in source.


@dataclass(frozen=True)
class XmlbifNetwork:
    name: str
    properties: tuple[str, ...]  # NETWORK-level PROPERTY order as in source.
    variables: tuple[XmlbifVariable, ...]  # VARIABLE order as in source.
    definitions: tuple[XmlbifDefinition, ...]  # DEFINITION order as in source.


@dataclass(frozen=True)
class DisplayGraph:
    network_name: str
    nodes: tuple[str, ...]  # Variable order as in source.
    edges: tuple[tuple[str, str], ...]  # (parent, child) in DEFINITION/GIVEN order.


@dataclass(frozen=True)
class ValidatedXmlbif:
    source_sha256: str
    source_bytes: bytes  # Exact preserved input bytes.
    xsd: XsdReport
    networks: tuple[XmlbifNetwork, ...]  # All networks in source order; never subset.
    activatable_v1: bool  # Structural v1 profile only; never means executable/valid.
    nonactivatable_reasons: tuple[str, ...]


def _truncate(text: str) -> str:
    return text[:MAX_MESSAGE_CHARS]


def _safe_parser() -> Any:
    # All four flags explicit; never rely on lxml defaults.
    return etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)


_SCHEMA: Any | None = None


def _schema_path() -> Path:
    candidates = list(Path(__file__).resolve().parents) + list(Path.cwd().resolve().parents)
    for parent in [Path(__file__).resolve().parent, *candidates, Path.cwd().resolve()]:
        candidate = parent / SCHEMA_RELATIVE
        if candidate.is_file():
            return candidate
    raise XmlValidationError("schema_unavailable", "Supplied schema.xml not found.")


def _load_schema() -> Any:
    global _SCHEMA
    if _SCHEMA is None:
        path = _schema_path()
        try:
            _SCHEMA = etree.XMLSchema(etree.parse(str(path), _safe_parser()))
        except etree.XMLSchemaParseError as exc:
            raise XmlValidationError("schema_unavailable", _truncate(str(exc))) from exc
    return _SCHEMA


def _fail(code: str, message: str, line: int = 0, column: int = 0) -> XmlValidationError:
    return XmlValidationError(code, _truncate(message), line, column)


def _text(element: Any) -> str:
    return ((element.text or "") if element is not None else "").strip()


def _parse_networks(root: Any) -> tuple[XmlbifNetwork, ...]:
    networks: list[XmlbifNetwork] = []
    network_elements = root.findall("NETWORK")
    if len(network_elements) > MAX_NETWORKS:
        raise _fail(
            "too_many_networks",
            f"Document has {len(network_elements)} NETWORK elements (limit {MAX_NETWORKS}).",
        )
    for network_el in network_elements:
        name_el = network_el.find("NAME")
        name = _text(name_el)
        properties = tuple(_text(p) for p in network_el.findall("PROPERTY"))
        variable_elements = network_el.findall("VARIABLE")
        if len(variable_elements) > MAX_VARIABLES_PER_NETWORK:
            raise _fail(
                "too_many_variables",
                f"Network {name!r} has {len(variable_elements)} variables "
                f"(limit {MAX_VARIABLES_PER_NETWORK}).",
            )
        variables: list[XmlbifVariable] = []
        for var_el in variable_elements:
            var_name = _text(var_el.find("NAME"))
            kind = var_el.get("TYPE", "nature")
            outcomes = tuple(_text(o) for o in var_el.findall("OUTCOME"))
            if len(outcomes) > MAX_OUTCOMES_PER_VARIABLE:
                raise _fail(
                    "too_many_outcomes",
                    f"Variable {var_name!r} has {len(outcomes)} outcomes "
                    f"(limit {MAX_OUTCOMES_PER_VARIABLE}).",
                )
            var_props = tuple(_text(p) for p in var_el.findall("PROPERTY"))
            variables.append(
                XmlbifVariable(name=var_name, kind=kind, states=outcomes, properties=var_props)
            )
        definitions: list[XmlbifDefinition] = []
        for def_el in network_el.findall("DEFINITION"):
            for_node = _text(def_el.find("FOR"))
            parents = tuple(_text(g) for g in def_el.findall("GIVEN"))
            table_el = def_el.find("TABLE")
            raw = (table_el.text or "") if table_el is not None else ""
            tokens = tuple(raw.split())
            if len(tokens) > MAX_TABLE_VALUES_PER_DEFINITION:
                raise _fail(
                    "table_too_large",
                    f"Definition for {for_node!r} has {len(tokens)} table values "
                    f"(limit {MAX_TABLE_VALUES_PER_DEFINITION}).",
                )
            def_props = tuple(_text(p) for p in def_el.findall("PROPERTY"))
            definitions.append(
                XmlbifDefinition(
                    for_node=for_node, parents=parents, table=tokens, properties=def_props
                )
            )
        networks.append(
            XmlbifNetwork(
                name=name,
                properties=properties,
                variables=tuple(variables),
                definitions=tuple(definitions),
            )
        )
    return tuple(networks)


def _xsd_report(schema: Any, root: Any) -> XsdReport:
    valid = bool(schema.validate(root))
    issues: list[XsdIssue] = []
    if not valid:
        for entry in list(schema.error_log)[:MAX_XSD_ERRORS]:
            try:
                line = int(getattr(entry, "line", 0) or 0)
            except (TypeError, ValueError):
                line = 0
            try:
                column = int(getattr(entry, "column", 0) or 0)
            except (TypeError, ValueError):
                column = 0
            issues.append(XsdIssue(line=line, column=column, message=_truncate(str(entry.message))))
    return XsdReport(valid=valid, errors=tuple(issues))


def _activation(
    networks: tuple[XmlbifNetwork, ...], xsd: XsdReport
) -> tuple[bool, tuple[str, ...]]:
    if len(networks) != 1:
        return False, ("multiple_networks" if networks else "no_network",)
    if not xsd.valid:
        return False, ("xsd_invalid",)
    network = networks[0]
    if not network.variables:
        return False, ("empty_network",)
    bad_kinds = sorted({v.kind for v in network.variables if v.kind != "nature"})
    if bad_kinds:
        return False, tuple(f"unsupported_kind:{kind}" for kind in bad_kinds[:MAX_REASON_ITEMS])
    defined = {d.for_node for d in network.definitions if d.for_node}
    missing = [v.name for v in network.variables if v.name not in defined]
    if missing:
        shown = ", ".join(missing[:MAX_REASON_ITEMS])
        suffix = "…" if len(missing) > MAX_REASON_ITEMS else ""
        return False, (f"missing_definitions:{shown}{suffix}",)
    return True, ()


def validate(source: bytes | bytearray) -> ValidatedXmlbif:
    """Safely import one XMLBIF document and inspect it as a draft.

    Returns a :class:`ValidatedXmlbif` with exact ``source_bytes``/SHA-256,
    a separate ``xsd`` report, all ``networks`` in source order, and a
    structural ``activatable_v1`` flag (never an executability claim).

    Raises :class:`XmlValidationError` only for input that cannot be safely
    inspected: non-bytes, empty, oversized, ``DOCTYPE``/``ENTITY``, malformed
    XML, or over-limit node/table counts. Inner XSD violations instead yield
    ``xsd.valid is False`` with bounded errors.
    """
    if not isinstance(source, (bytes, bytearray)):
        raise _fail("not_bytes", "Input must be bytes.")
    data = bytes(source)
    if not data.strip():
        raise _fail("empty_input", "Input is empty.")
    if len(data) > MAX_XML_BYTES:
        raise _fail(
            "too_large",
            f"Input is {len(data)} bytes (limit {MAX_XML_BYTES}).",
        )
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise _fail(
            "unsafe_doctype",
            "DOCTYPE/ENTITY declarations are not allowed.",
        )
    try:
        root = etree.fromstring(data, _safe_parser())
    except etree.XMLSyntaxError as exc:
        line = int(getattr(exc, "lineno", 0) or 0)
        position = getattr(exc, "position", (0, 0))
        column = 0
        try:
            column = int(position[1] if len(position) > 1 else 0) or 0
        except (TypeError, ValueError):
            column = 0
        raise _fail("malformed_xml", str(exc), line, column) from exc
    if root.tag != "BIF":
        report = XsdReport(
            valid=False,
            errors=(XsdIssue(line=0, column=0, message=_truncate("Root must be BIF.")),),
        )
        digest = hashlib.sha256(data).hexdigest()
        return ValidatedXmlbif(
            source_sha256=digest,
            source_bytes=data,
            xsd=report,
            networks=(),
            activatable_v1=False,
            nonactivatable_reasons=("unsupported_content",),
        )
    networks = _parse_networks(root)
    schema = _load_schema()
    report = _xsd_report(schema, root)
    activatable, reasons = _activation(networks, report)
    digest = hashlib.sha256(data).hexdigest()
    return ValidatedXmlbif(
        source_sha256=digest,
        source_bytes=data,
        xsd=report,
        networks=networks,
        activatable_v1=activatable,
        nonactivatable_reasons=reasons,
    )


def display_graph(network: XmlbifNetwork) -> DisplayGraph:
    """Read-only graph for one network in source order.

    Nodes follow ``VARIABLE`` order; edges follow ``DEFINITION`` order with
    each definition's ``GIVEN`` order preserved. Only ``FOR``/``GIVEN``
    pairs become edges — ``proposed_parent`` properties are ignored.
    """
    nodes = tuple(v.name for v in network.variables)
    edges: list[tuple[str, str]] = []
    for definition in network.definitions:
        if not definition.for_node:
            continue
        for parent in definition.parents:
            if parent:
                edges.append((parent, definition.for_node))
    return DisplayGraph(network_name=network.name, nodes=nodes, edges=tuple(edges))


def export_source(document: ValidatedXmlbif) -> bytes:
    """Return the exact preserved source bytes (order/metadata intact by identity)."""
    return bytes(document.source_bytes)
