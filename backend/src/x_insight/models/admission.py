"""Model semantics and admission gating (S22, seam T5; plan.md §7.3, FR-31–32/37, NFR-04).

Public interface (seam T5 — tests and callers use the same functions):

- :func:`check_semantics` — semantic shape of one validated draft: cycles,
  missing CPTs, wrong TABLE dimensions, invalid normalization, empty outcomes.
- :func:`validate_package_contract` — content/activation hook S25 will fill;
  rejects unknown paths/expressions by default, never invents patient mappings.
- :func:`admission_decision` — import-versus-activation gate: ``validate`` is
  structural import, this is executable? — never clinically valid.

What this module does and does not promise:

- XSD success stays structural only. Semantic validity means "shape fits the
  v1 execution profile" (single ``nature``-only network, acyclic, complete
  finite normalized tables). Executable means "admitted for the inference
  pipeline subject to its own exact checks (S23)". Neither label is an
  executability claim beyond the pipeline nor clinical validity.
- Decision/utility nodes stay drafts: they are inspectable via S21 but never
  executable under v1 (``unsupported_kind``).
- A JSON ``reviewed=true`` flag alone is NEVER sufficient for activation: an
  explicit review record object (reviewer + approved decision + date) is
  required (``unreviewed_package`` otherwise).
- Note source paths are explicitly rejected: page notes never influence
  analysis (plan.md §2.3), so any ``notes``/``note`` mapping is
  ``note_source_path``.

Engineering limits (resource/diagnostic bounds, NOT clinical thresholds):

- Reuses S21 ``MAX_*`` guardrails (see :data:`ENGINEERING_LIMITS`), sized from
  actual model measurements: largest supplied draft ~57 KiB (BN-04, 57 652 B),
  32 variables peak, ``MAX_TABLE_VALUES_PER_DEFINITION`` covering 1 584-cell
  drafts with headroom. S22 extends them with diagnostic caps below.
- Diagnostics are deterministic: fixed check order, source order inside each
  check, bounded counts (``MAX_SEMANTIC_ERRORS`` / ``MAX_CONTRACT_ERRORS`` /
  ``MAX_ADMISSION_DIAGNOSTICS``) and bounded messages (``MAX_MESSAGE_CHARS``).
- Row normalization uses a tight float gate (``NORMALIZATION_TOLERANCE``);
  the exact integer-unit policy (100% = 100,000,000) is owned by S23 and is
  strictly stronger.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import fsum, isfinite
from typing import Any

from x_insight.models.question_package import (
    KNOWN_SOURCE_PREFIXES as _REGISTRY_PREFIXES,
)
from x_insight.models.question_package import (
    SAFE_EXPRESSIONS as _REGISTRY_EXPRESSIONS,
)
from x_insight.models.validation import (
    MAX_MESSAGE_CHARS,
    MAX_NETWORKS,
    MAX_OUTCOMES_PER_VARIABLE,
    MAX_REASON_ITEMS,
    MAX_TABLE_VALUES_PER_DEFINITION,
    MAX_VARIABLES_PER_NETWORK,
    MAX_XML_BYTES,
    MAX_XSD_ERRORS,
    ValidatedXmlbif,
)

# --- Engineering guardrails (diagnostic bounds, not clinical thresholds) ---

MAX_SEMANTIC_ERRORS = 50
MAX_CONTRACT_ERRORS = 50
MAX_ADMISSION_DIAGNOSTICS = 100

#: Tight float gate for row sums; S23 exact integer-unit policy is stronger.
NORMALIZATION_TOLERANCE = 1e-9

#: Reused S21 resource limits plus S22 diagnostic caps, in one place.
ENGINEERING_LIMITS: Mapping[str, int | float] = {
    "MAX_XML_BYTES": MAX_XML_BYTES,
    "MAX_NETWORKS": MAX_NETWORKS,
    "MAX_VARIABLES_PER_NETWORK": MAX_VARIABLES_PER_NETWORK,
    "MAX_OUTCOMES_PER_VARIABLE": MAX_OUTCOMES_PER_VARIABLE,
    "MAX_TABLE_VALUES_PER_DEFINITION": MAX_TABLE_VALUES_PER_DEFINITION,
    "MAX_XSD_ERRORS": MAX_XSD_ERRORS,
    "MAX_MESSAGE_CHARS": MAX_MESSAGE_CHARS,
    "MAX_REASON_ITEMS": MAX_REASON_ITEMS,
    "MAX_SEMANTIC_ERRORS": MAX_SEMANTIC_ERRORS,
    "MAX_CONTRACT_ERRORS": MAX_CONTRACT_ERRORS,
    "MAX_ADMISSION_DIAGNOSTICS": MAX_ADMISSION_DIAGNOSTICS,
    "NORMALIZATION_TOLERANCE": NORMALIZATION_TOLERANCE,
}

#: S25 registry (explicit synthetic allowlists, default-deny); S22 hooks share it.
KNOWN_SOURCE_PREFIXES: tuple[str, ...] = _REGISTRY_PREFIXES
SAFE_EXPRESSIONS: frozenset[str] = _REGISTRY_EXPRESSIONS


@dataclass(frozen=True)
class SemanticIssue:
    code: str
    message: str
    node: str = ""


@dataclass(frozen=True)
class SemanticReport:
    valid: bool
    errors: tuple[SemanticIssue, ...]


@dataclass(frozen=True)
class ContractIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ContractReport:
    valid: bool
    errors: tuple[ContractIssue, ...]


@dataclass(frozen=True)
class AdmissionDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class AdmissionDecision:
    executable: bool
    diagnostics: tuple[AdmissionDiagnostic, ...]  # Ordered, bounded, stable.
    semantic: SemanticReport
    contract: ContractReport


def _clip(text: str) -> str:
    return text[:MAX_MESSAGE_CHARS]


def _find_cycle(
    nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
) -> tuple[str, ...] | None:
    """First directed cycle in source order, or None. Self-loops count."""
    successors: dict[str, list[str]] = {name: [] for name in nodes}
    for parent, child in edges:
        if parent in successors and child in successors and child not in successors[parent]:
            successors[parent].append(child)
    color: dict[str, int] = dict.fromkeys(nodes, 0)  # 0 unvisited, 1 on path, 2 done.
    path: list[str] = []
    found: list[str] | None = None

    def visit(node: str) -> None:
        nonlocal found
        color[node] = 1
        path.append(node)
        for nxt in successors[node]:
            if found is not None:
                break
            if color[nxt] == 1:
                found = path[path.index(nxt) :] + [nxt]
            elif color[nxt] == 0:
                visit(nxt)
        path.pop()
        color[node] = 2

    for start in nodes:
        if found is not None or color[start] != 0:
            continue
        visit(start)
    return tuple(found) if found is not None else None


def check_semantics(document: ValidatedXmlbif) -> SemanticReport:
    """Check semantic shape after structure validation (fixed check order).

    Order: ``xsd_invalid`` → ``no_network``/``multiple_networks`` →
    ``empty_network`` → ``unsupported_kind`` → ``empty_outcomes`` →
    ``missing_definition`` → ``cycle_detected`` → ``wrong_dimensions`` →
    ``non_finite_value`` → ``probability_out_of_range`` →
    ``row_not_normalized``. Within a check, source order. Bounded to
    ``MAX_SEMANTIC_ERRORS``.
    """
    errors: list[SemanticIssue] = []

    def add(code: str, message: str, node: str = "") -> None:
        if len(errors) < MAX_SEMANTIC_ERRORS:
            errors.append(SemanticIssue(code=code, message=_clip(message), node=node))

    if not document.xsd.valid:
        add("xsd_invalid", "Document is not XSD-valid; semantic checks need structure first.")
        return SemanticReport(valid=False, errors=tuple(errors))
    if len(document.networks) != 1:
        code = "no_network" if not document.networks else "multiple_networks"
        add(code, f"Expected one NETWORK for v1 semantics, found {len(document.networks)}.")
        return SemanticReport(valid=False, errors=tuple(errors))

    (network,) = document.networks
    if not network.variables:
        add("empty_network", "Network declares no variables.")
        return SemanticReport(valid=False, errors=tuple(errors))

    bad_kinds = sorted({v.kind for v in network.variables if v.kind != "nature"})
    if bad_kinds:
        add(
            "unsupported_kind",
            f"Decision/utility nodes stay drafts under v1: {', '.join(bad_kinds)}.",
        )

    states: dict[str, tuple[str, ...]] = {v.name: v.states for v in network.variables}
    kinds: dict[str, str] = {v.name: v.kind for v in network.variables}
    for variable in network.variables:
        if variable.kind in ("nature", "decision") and not variable.states:
            add(
                "empty_outcomes", f"Variable {variable.name!r} declares no outcomes.", variable.name
            )

    defined = {d.for_node for d in network.definitions if d.for_node}
    for variable in network.variables:
        if variable.kind == "nature" and variable.name not in defined:
            add(
                "missing_definition",
                f"Nature variable {variable.name!r} has no DEFINITION (no CPT).",
                variable.name,
            )

    edge_list: list[tuple[str, str]] = []
    for definition in network.definitions:
        for parent in definition.parents:
            if definition.for_node and parent:
                edge_list.append((parent, definition.for_node))
    cycle = _find_cycle(tuple(v.name for v in network.variables), tuple(edge_list))
    if cycle is not None:
        add("cycle_detected", f"Directed cycle: {' -> '.join(cycle)}.")

    well_formed: set[str] = set()
    for definition in network.definitions:
        node = definition.for_node
        if not node or node not in states or kinds.get(node) != "nature":
            continue  # Non-nature tables are not probability tables under v1.
        if any(parent not in states for parent in definition.parents):
            continue  # Unreachable when XSD is valid; XSD keyref guarantees parents.
        if not states[node]:
            continue  # Already reported as empty_outcomes; no rows can form.
        product = 1
        for parent in definition.parents:
            product *= len(states[parent])
        expected = len(states[node]) * product
        if len(definition.table) != expected:
            add(
                "wrong_dimensions",
                f"TABLE for {node!r} has {len(definition.table)} values, "
                f"expected {expected} ({len(states[node])} states "
                f"x parent product {product}).",
                node,
            )
        else:
            well_formed.add(node)
    for definition in network.definitions:
        node = definition.for_node
        if node not in well_formed or not states.get(node):
            continue
        size = len(states[node])
        values: list[float] = []
        token_ok = True
        for token in definition.table:
            try:
                value = float(token)
            except (TypeError, ValueError, OverflowError):
                add(
                    "non_finite_value", f"TABLE token {token!r} for {node!r} is not a number.", node
                )
                token_ok = False
                break
            if not isfinite(value):
                add(
                    "non_finite_value",
                    f"TABLE token {token!r} for {node!r} is not finite.",
                    node,
                )
                token_ok = False
                break
            if not 0.0 <= value <= 1.0:
                add(
                    "probability_out_of_range",
                    f"TABLE token {token!r} for {node!r} is outside [0, 1].",
                    node,
                )
                token_ok = False
                break
            values.append(value)
        if not token_ok:
            continue  # No meaningful row sums once a token already failed.
        for row in range(len(values) // size):
            total = fsum(values[row * size : (row + 1) * size])
            if abs(total - 1.0) > NORMALIZATION_TOLERANCE:
                add(
                    "row_not_normalized",
                    f"Row {row} for {node!r} totals {total!r}, expected 1.0 (100%).",
                    node,
                )
    return SemanticReport(valid=not errors, errors=tuple(errors))


def _is_note_path(path: str) -> bool:
    for segment in path.replace("\\", "/").lower().split("/"):
        if segment in ("note", "notes") or segment.startswith(("note_", "notes_", "page_note")):
            return True
    return False


def _candidate_expressions(package: Mapping[str, Any]) -> list[Any]:
    candidates: list[Any] = []
    raw = package.get("expressions")
    if isinstance(raw, (list, tuple)):
        candidates.extend(raw)
    elif raw is not None:
        candidates.append(raw)
    applicability = package.get("applicability")
    if isinstance(applicability, str):
        candidates.append(applicability)
    elif isinstance(applicability, Mapping):
        for key in sorted(applicability.keys()):
            value = applicability[key]
            if isinstance(value, str):
                candidates.append(value)
    return candidates


def validate_package_contract(
    package: Mapping[str, Any] | None,
    *,
    known_source_prefixes: tuple[str, ...] = KNOWN_SOURCE_PREFIXES,
    safe_expressions: frozenset[str] = SAFE_EXPRESSIONS,
) -> ContractReport:
    """Content/activation hook S25 will fill (fixed check order, default-deny).

    Minimal complete package shape (synthetic fixtures use exactly this)::

        {"question_mappings": [...], "prompts": ..., "templates": ...,
         "review": {"reviewer": ..., "decision": "approved", "date": ...}}

    Optional ``source_paths`` / ``expressions`` / ``applicability`` /
    ``query_nodes`` / ``query_states`` are checked when present; unknown
    source paths and expressions are rejected until S25 registers them via
    ``known_source_prefixes`` / ``safe_expressions``. No patient mappings
    are invented here.
    """
    errors: list[ContractIssue] = []

    def add(code: str, message: str) -> None:
        if len(errors) < MAX_CONTRACT_ERRORS:
            errors.append(ContractIssue(code=code, message=_clip(message)))

    if not isinstance(package, Mapping):
        add("missing_package", "No question package supplied; activation needs one.")
        return ContractReport(valid=False, errors=tuple(errors))
    if not package.get("question_mappings"):
        add("missing_question_mappings", "Package has no question mappings.")
    if not package.get("prompts"):
        add("missing_prompts", "Package has no prompts.")
    if not package.get("templates"):
        add("missing_templates", "Package has no templates.")

    raw_paths = package.get("source_paths")
    if raw_paths:
        paths = list(raw_paths) if isinstance(raw_paths, (list, tuple)) else [raw_paths]
        for entry in paths:
            if not isinstance(entry, str) or not entry:
                add("unknown_source_path", f"Source path {entry!r} is not a known path.")
            elif _is_note_path(entry):
                add(
                    "note_source_path",
                    f"Source path {entry!r} references notes; notes never feed models.",
                )
            elif not any(entry.startswith(prefix) for prefix in known_source_prefixes):
                add("unknown_source_path", f"Source path {entry!r} is not a known path.")

    review = package.get("review")
    reviewed_ok = (
        isinstance(review, Mapping)
        and isinstance(review.get("reviewer"), str)
        and bool(review.get("reviewer", "").strip())
        and review.get("decision") == "approved"
        and isinstance(review.get("date"), str)
        and bool(review.get("date", "").strip())
    )
    if not reviewed_ok:
        add(
            "unreviewed_package",
            "Package has no explicit review record "
            "(reviewer + decision 'approved' + date); "
            "a 'reviewed' flag alone is never sufficient.",
        )

    for expression in _candidate_expressions(package):
        if not isinstance(expression, str) or expression not in safe_expressions:
            add("unsafe_expression", f"Expression {expression!r} is not an allowlisted query.")
    return ContractReport(valid=not errors, errors=tuple(errors))


def admission_decision(
    document: ValidatedXmlbif,
    package: Mapping[str, Any] | None = None,
    *,
    known_source_prefixes: tuple[str, ...] = KNOWN_SOURCE_PREFIXES,
    safe_expressions: frozenset[str] = SAFE_EXPRESSIONS,
) -> AdmissionDecision:
    """Import-versus-activation gate: semantic errors, then contract, then refs.

    Diagnostics keep that stable order and are bounded to
    ``MAX_ADMISSION_DIAGNOSTICS``. ``executable`` is True only when the
    single-network structure, all semantic checks, the package contract, and
    every query/state reference pass. It never means clinically valid.
    """
    semantic = check_semantics(document)
    contract = validate_package_contract(
        package,
        known_source_prefixes=known_source_prefixes,
        safe_expressions=safe_expressions,
    )
    cross: list[AdmissionDiagnostic] = []
    if isinstance(package, Mapping) and len(document.networks) == 1:
        declared = {v.name: set(v.states) for v in document.networks[0].variables}
        raw_nodes = package.get("query_nodes")
        if raw_nodes:
            nodes = list(raw_nodes) if isinstance(raw_nodes, (list, tuple)) else [raw_nodes]
            for entry in nodes:
                if entry not in declared:
                    cross.append(
                        AdmissionDiagnostic(
                            code="undeclared_query",
                            message=_clip(f"Query node {entry!r} is not a declared variable."),
                        )
                    )
        raw_states = package.get("query_states")
        if isinstance(raw_states, Mapping):
            for node in raw_states:
                states = raw_states[node]
                candidates = list(states) if isinstance(states, (list, tuple)) else [states]
                if node not in declared:
                    cross.append(
                        AdmissionDiagnostic(
                            code="undeclared_query",
                            message=_clip(f"Query node {node!r} is not a declared variable."),
                        )
                    )
                    continue
                for state in candidates:
                    if state not in declared[node]:
                        cross.append(
                            AdmissionDiagnostic(
                                code="undeclared_state",
                                message=_clip(f"State {state!r} is not declared for {node!r}."),
                            )
                        )
        elif raw_states is not None:
            cross.append(
                AdmissionDiagnostic(
                    code="undeclared_state",
                    message=_clip("query_states must map declared nodes to declared states."),
                )
            )

    diagnostics = (
        [AdmissionDiagnostic(code=e.code, message=e.message) for e in semantic.errors]
        + [AdmissionDiagnostic(code=e.code, message=e.message) for e in contract.errors]
        + cross
    )[:MAX_ADMISSION_DIAGNOSTICS]
    executable = semantic.valid and contract.valid and not cross
    return AdmissionDecision(
        executable=executable,
        diagnostics=tuple(diagnostics),
        semantic=semantic,
        contract=contract,
    )
