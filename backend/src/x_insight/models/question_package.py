"""Reusable question-package contract and review harness (S25, seam T5).

Public interface (seam T5 — tests and S22/S23 harnesses use the same functions):

- :func:`validate_question_package` — validate one synthetic question package
  (manifest/prompt/template/examples/review, plus query/CPT references)
  against an optional admitted ``ValidatedXmlbif`` document.
- :func:`load_question_package` — validate and freeze a canonical package
  hash for handoff to S27/S29-S38; raises :class:`QuestionPackageError`.
- Registry allowlists ``KNOWN_SOURCE_PREFIXES`` / ``SAFE_EXPRESSIONS`` —
  explicit, synthetic, default-deny. ``admission.py`` imports these so S22
  hooks and S25 share one registry without inventing patient mappings.

What this module does and does not promise:

- Success means "package shape fits the reusable v1 contract". It is never
  labeled executable, clinically valid, or owner-approved. Draft packages
  validate without activation; owner review is a separate recorded decision
  (``review.json`` presence/shape checked, never auto-approved).
- All patient mappings are CPT-estimation context only (``usage`` must be
  ``cpt_context``). Execution evidence is always empty; review cannot enable
  evidence under the confirmed contract. Any evidence mapping, posterior
  chaining, note path, unknown path, or arbitrary expression is rejected.
- Templates hold escaped-value slots (``{variable}``) plus explicit branches
  on declared output states with allowlisted operators only. No LLM prose,
  no unreviewed argmax treatment selection. Runtime section rendering is S45
  and is NOT implemented here — only shape is validated.
- CPT completeness reuses the S23 contract: ``cpt_contract`` must cover every
  declared node in order, and an optional ``cpt_tables`` payload is checked
  with :func:`~x_insight.models.inference.validate_cpts` without changing
  S23 behavior. S22 behavior is unchanged: this module never relaxes S22 and
  S22 ignores S25-only keys.

Safety/bounds: deterministic fixed check order, source order inside each
check, bounded counts (``MAX_PACKAGE_ERRORS``) and bounded messages
(``MAX_MESSAGE_CHARS``). Engineering bounds only, not clinical thresholds.
Synthetic fixtures only; no BN/clinical content invented.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# --- Manifest registry (explicit synthetic allowlists, default-deny) ---
#
# Synthetic only: no real patient field is claimed. Unknown paths/expressions
# are denied until a future content session registers them here with provenance.

#: Single package schema version; bump only with a migration.
MANIFEST_SCHEMA_VERSION = "question-package-v1"

#: Allowlisted synthetic source prefixes for ``allowed_source_paths``.
KNOWN_SOURCE_PREFIXES: tuple[str, ...] = (
    "synthetic/history/",
    "synthetic/assessments/",
    "synthetic/demographics/",
)

#: Allowlisted applicability expressions (gate language, nothing else).
SAFE_EXPRESSIONS: frozenset[str] = frozenset(
    {
        "true",
        "gate == 'true'",
        "gate == 'false'",
        "gate == 'unknown'",
    }
)

SUPPORTED_PATIENT_VALUE_TYPES: frozenset[str] = frozenset(
    {"tristate", "tristate_plus_not_assessed", "boolean", "categorical"}
)
SUPPORTED_TRANSFORMS: frozenset[str] = frozenset({"copy", "map_tristate"})
SUPPORTED_TIME_WINDOWS: frozenset[str] = frozenset(
    {"current_encounter", "lifetime", "past_4_weeks", "past_3_months"}
)
SUPPORTED_MISSING_POLICIES: frozenset[str] = frozenset(
    {"needs_clarification", "not_applicable", "use_unknown_state"}
)
SUPPORTED_UNKNOWN_POLICIES: frozenset[str] = frozenset({"needs_clarification", "not_applicable"})
SUPPORTED_WORKFLOWS: frozenset[str] = frozenset({"registration", "followup"})
SUPPORTED_REVIEW_STATUSES: frozenset[str] = frozenset({"draft", "awaiting_review", "approved"})
SUPPORTED_REVIEW_DECISIONS: frozenset[str] = frozenset(
    {"approved", "awaiting_review", "draft", "needs_changes", "rejected"}
)
SUPPORTED_TEMPLATE_OPERATORS: frozenset[str] = frozenset({"==", "!=", "in", "and", "or", "not"})

#: Review dossier sections required by S27/S29-S38 (presence/shape only;
#: clinical correctness stays owner review, never self-validated).
DOSSIER_FIELDS: tuple[str, ...] = (
    "source_comparison",
    "explicit_graph",
    "reference_table_provenance",
    "estimation_instructions",
    "result_mapping",
    "numerical_examples",
    "clinical_examples",
    "admission_measurements",
    "open_assumptions",
)

MAX_PACKAGE_ERRORS = 50
MAX_MESSAGE_CHARS = 500

_QUESTION_KEY_RE = re.compile(r"^[a-z0-9_]{3,64}$")
_SLOT_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
_POSTERIOR_MARKERS = ("posterior", "questions/")
_FORBIDDEN_PROMPT_MARKERS = (
    "choose applicability",
    "write a plan",
    "full record",
    "execute the network",
    "modify the structure",
)
_ARGMAX_MARKERS = ("argmax", "largest posterior", "highest posterior", "best posterior")


@dataclass(frozen=True)
class QuestionPackageIssue:
    code: str
    message: str


@dataclass(frozen=True)
class QuestionPackageReport:
    valid: bool
    errors: tuple[QuestionPackageIssue, ...]


@dataclass(frozen=True)
class LoadedQuestionPackage:
    question_key: str
    network_hash: str
    package_hash: str
    declared_node_order: tuple[str, ...]
    query_nodes: tuple[str, ...]


class QuestionPackageError(ValueError):
    """Hard failure for the loader: package shape does not fit the contract."""

    def __init__(
        self, code: str, message: str, errors: tuple[QuestionPackageIssue, ...] = ()
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message[:MAX_MESSAGE_CHARS]
        self.errors = errors


def _clip(text: str) -> str:
    return text[:MAX_MESSAGE_CHARS]


def _is_note_path(path: str) -> bool:
    for segment in path.replace("\\", "/").lower().split("/"):
        if segment in ("note", "notes") or segment.startswith(("note_", "notes_", "page_note")):
            return True
    return False


def _is_posterior_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in _POSTERIOR_MARKERS)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_question_package(
    package: Mapping[str, Any] | None,
    document: Any | None = None,
    *,
    known_source_prefixes: tuple[str, ...] = KNOWN_SOURCE_PREFIXES,
    safe_expressions: frozenset[str] = SAFE_EXPRESSIONS,
) -> QuestionPackageReport:
    """Validate one reusable question package (fixed check order, default-deny).

    Fixed order: manifest envelope → variables/order vs document →
    patient mappings (note/chaining/evidence/prefix) → applicability/gate →
    CPT contract (+ optional S23 ``cpt_tables``) → query/evidence →
    prompt → template (slots/branches/operators/argmax) →
    examples → review dossier/shape. Within a check, manifest order.
    Bounded to ``MAX_PACKAGE_ERRORS``.

    ``document`` is an S21 ``ValidatedXmlbif`` or None. Document-dependent
    checks (node order, states, parents, hashes) run only when a single-network
    document is supplied; shape checks always run. Draft ``review_status``
    (``draft``/``awaiting_review``) validates the same as ``approved`` —
    approval is a separate recorded decision, never implied by ``valid``.
    """
    errors: list[QuestionPackageIssue] = []

    def add(code: str, message: str) -> None:
        if len(errors) < MAX_PACKAGE_ERRORS:
            errors.append(QuestionPackageIssue(code=code, message=_clip(message)))

    if not isinstance(package, Mapping):
        add("missing_package", "No question package supplied.")
        return QuestionPackageReport(valid=False, errors=tuple(errors))

    manifest = package.get("manifest")
    if not isinstance(manifest, Mapping):
        add("missing_manifest", "Package has no manifest mapping.")
        return QuestionPackageReport(valid=False, errors=tuple(errors))

    # --- Manifest envelope ---
    schema_version = manifest.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        add("bad_schema_version", f"schema_version must be {MANIFEST_SCHEMA_VERSION!r}.")
    question_key = manifest.get("question_key")
    if not isinstance(question_key, str) or not _QUESTION_KEY_RE.match(question_key):
        add("bad_question_key", "question_key must match ^[a-z0-9_]{3,64}$.")
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        add("bad_manifest", "manifest needs a non-empty title.")
    workflow = manifest.get("workflow")
    if workflow not in SUPPORTED_WORKFLOWS:
        add("bad_workflow", f"workflow must be one of {sorted(SUPPORTED_WORKFLOWS)}.")
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        add("bad_manifest", "manifest needs a non-empty version.")
    review_status = manifest.get("review_status")
    if review_status not in SUPPORTED_REVIEW_STATUSES:
        add(
            "bad_review_status",
            f"review_status must be one of {sorted(SUPPORTED_REVIEW_STATUSES)}.",
        )
    network_file = manifest.get("network_file")
    if not isinstance(network_file, str) or not network_file.strip():
        add("bad_manifest", "manifest needs a non-empty network_file.")
    network_hash = manifest.get("network_hash")
    if not isinstance(network_hash, str) or not network_hash.strip():
        add("bad_network_hash", "manifest needs a non-empty network_hash.")
    source_refs = manifest.get("source_refs")
    if not isinstance(source_refs, (list, tuple)) or not source_refs:
        add("bad_source_refs", "manifest needs a non-empty source_refs list.")
    elif not all(isinstance(s, str) and s.strip() for s in source_refs):
        add("bad_source_refs", "source_refs must list non-empty strings.")
    declared = manifest.get("declared_node_order")
    if (
        not isinstance(declared, (list, tuple))
        or not declared
        or not all(isinstance(n, str) and n for n in declared)
    ):
        add("bad_manifest", "manifest needs a non-empty declared_node_order.")
        declared_order: tuple[str, ...] = ()
    else:
        declared_order = tuple(declared)
        if len(set(declared_order)) != len(declared_order):
            add("bad_manifest", "declared_node_order holds duplicates.")

    # Document-derived truth (single network only; None skips these checks).
    doc_states: dict[str, tuple[str, ...]] = {}
    doc_parents: dict[str, tuple[str, ...]] = {}
    doc_kinds: dict[str, str] = {}
    doc_order: tuple[str, ...] = ()
    doc_hash = ""
    doc_available = False
    if document is not None:
        try:
            networks = getattr(document, "networks", ())
            source_sha = str(getattr(document, "source_sha256", ""))
        except Exception:
            networks = ()
            source_sha = ""
        if isinstance(networks, (list, tuple)) and len(networks) == 1:
            network = networks[0]
            try:
                doc_variables = tuple(getattr(network, "variables", ()))
                doc_definitions = tuple(getattr(network, "definitions", ()))
            except Exception:
                doc_variables = ()
                doc_definitions = ()
            names = [str(getattr(v, "name", "")) for v in doc_variables]
            if names and all(names):
                doc_available = True
                doc_order = tuple(names)
                doc_hash = source_sha
                for v in doc_variables:
                    name = str(getattr(v, "name", ""))
                    doc_var_states = tuple(str(s) for s in (getattr(v, "states", ()) or ()))
                    doc_states[name] = doc_var_states
                    doc_kinds[name] = str(getattr(v, "kind", "nature"))
                parents_map: dict[str, tuple[str, ...]] = {n: () for n in doc_order}
                for d in doc_definitions:
                    doc_node = str(getattr(d, "for_node", "") or "")
                    if doc_node in parents_map and parents_map[doc_node] == ():
                        try:
                            given = tuple(str(p) for p in (getattr(d, "parents", ()) or ()))
                        except Exception:
                            given = ()
                        parents_map[doc_node] = given
                doc_parents = parents_map

    if doc_available:
        if declared_order and declared_order != doc_order:
            add(
                "wrong_node_order",
                f"declared_node_order {list(declared_order)} must match "
                f"document order {list(doc_order)}.",
            )
        if isinstance(network_hash, str) and network_hash and network_hash != doc_hash:
            add("bad_network_hash", "manifest network_hash must match the document hash.")

    # --- Variables (fixed types/states/order) ---
    manifest_variables = manifest.get("variables")
    var_by_id: dict[str, Mapping[str, Any]] = {}
    if not isinstance(manifest_variables, (list, tuple)) or not manifest_variables:
        add("bad_manifest", "manifest needs a non-empty variables list.")
    else:
        seen_vars: set[str] = set()
        for idx, entry in enumerate(manifest_variables):
            if not isinstance(entry, Mapping):
                add("bad_manifest", f"variables[{idx}] must be a mapping.")
                continue
            node_id = entry.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                add("undeclared_variable", f"variables[{idx}] has no node_id.")
                continue
            if node_id in seen_vars:
                add("duplicate_variable", f"Variable {node_id!r} appears twice.")
                continue
            seen_vars.add(node_id)
            var_by_id[node_id] = entry
            if declared_order and node_id not in declared_order:
                add("undeclared_variable", f"Variable {node_id!r} is not declared.")
            kind = entry.get("kind")
            if kind != "nature":
                add("wrong_kind", f"Variable {node_id!r} kind must be 'nature'.")
            patient_value_type = entry.get("patient_value_type")
            if patient_value_type not in SUPPORTED_PATIENT_VALUE_TYPES:
                add(
                    "bad_patient_value_type",
                    f"Variable {node_id!r} type must be one of "
                    f"{sorted(SUPPORTED_PATIENT_VALUE_TYPES)}.",
                )
            var_states = entry.get("states")
            if (
                not isinstance(var_states, (list, tuple))
                or not var_states
                or not all(isinstance(s, str) and s for s in var_states)
            ):
                add("wrong_states", f"Variable {node_id!r} needs non-empty states.")
            elif (
                doc_available and node_id in doc_states and tuple(var_states) != doc_states[node_id]
            ):
                if set(var_states) == set(doc_states[node_id]):
                    add(
                        "wrong_state_order",
                        f"Variable {node_id!r} states {list(var_states)} must match "
                        f"document order {list(doc_states[node_id])}.",
                    )
                else:
                    add(
                        "wrong_states",
                        f"Variable {node_id!r} states {list(var_states)} must match "
                        f"document {list(doc_states[node_id])}.",
                    )
            var_parents = entry.get("ordered_parents")
            if not isinstance(var_parents, (list, tuple)) or not all(
                isinstance(p, str) for p in var_parents
            ):
                add("wrong_parents", f"Variable {node_id!r} ordered_parents must list strings.")
            elif (
                doc_available
                and node_id in doc_parents
                and tuple(var_parents) != doc_parents[node_id]
            ):
                expected = list(doc_parents[node_id])
                if set(var_parents) == set(expected):
                    add(
                        "wrong_parent_order",
                        f"Variable {node_id!r} parent order {list(var_parents)} must match "
                        f"GIVEN order {expected}.",
                    )
                else:
                    add(
                        "wrong_parents",
                        f"Variable {node_id!r} parents {list(var_parents)} must match "
                        f"GIVEN {expected}.",
                    )
        if declared_order and set(seen_vars) != set(declared_order):
            missing = sorted(set(declared_order) - seen_vars)
            extra = sorted(seen_vars - set(declared_order))
            if missing:
                add("undeclared_variable", f"Variables omit declared nodes {missing}.")
            if extra:
                add("undeclared_variable", f"Variables hold undeclared nodes {extra}.")
        if declared_order and [
            str(e.get("node_id", "")) for e in manifest_variables if isinstance(e, Mapping)
        ] != list(declared_order):
            # Same set but different order is still a fixed-order violation.
            ordered_ids = [
                str(e.get("node_id", "")) for e in manifest_variables if isinstance(e, Mapping)
            ]
            if set(ordered_ids) == set(declared_order):
                add("wrong_node_order", "variables must follow declared_node_order.")

    # --- Patient mappings (estimation context only) ---
    mappings = manifest.get("patient_mappings")
    if not isinstance(mappings, (list, tuple)) or not mappings:
        add("bad_manifest", "manifest needs a non-empty patient_mappings list.")
    else:
        seen_mapping_nodes: set[str] = set()
        for idx, entry in enumerate(mappings):
            if not isinstance(entry, Mapping):
                add("bad_manifest", f"patient_mappings[{idx}] must be a mapping.")
                continue
            node_id = entry.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                add("undeclared_variable", f"patient_mappings[{idx}] has no node_id.")
                continue
            if declared_order and node_id not in declared_order:
                add("undeclared_variable", f"Mapping node {node_id!r} is not declared.")
            if node_id in seen_mapping_nodes:
                add("duplicate_variable", f"Mapping for {node_id!r} appears twice.")
                continue
            seen_mapping_nodes.add(node_id)
            usage = entry.get("usage")
            if usage != "cpt_context":
                add(
                    "evidence_mapping",
                    f"Mapping for {node_id!r} must use 'cpt_context'; "
                    "patient mappings are estimation context only, never evidence.",
                )
            transform = entry.get("transform")
            if transform not in SUPPORTED_TRANSFORMS:
                add(
                    "bad_transform",
                    f"Mapping for {node_id!r} transform must be one of "
                    f"{sorted(SUPPORTED_TRANSFORMS)}.",
                )
            time_window = entry.get("time_window")
            if time_window not in SUPPORTED_TIME_WINDOWS:
                add(
                    "bad_time_window",
                    f"Mapping for {node_id!r} time_window must be one of "
                    f"{sorted(SUPPORTED_TIME_WINDOWS)}.",
                )
            missing_policy = entry.get("missing_policy")
            if missing_policy not in SUPPORTED_MISSING_POLICIES:
                add(
                    "bad_missing_policy",
                    f"Mapping for {node_id!r} missing_policy must be one of "
                    f"{sorted(SUPPORTED_MISSING_POLICIES)}.",
                )
            paths = entry.get("allowed_source_paths")
            if (
                not isinstance(paths, (list, tuple))
                or not paths
                or not all(isinstance(p, str) and p for p in paths)
            ):
                add(
                    "bad_source_refs",
                    f"Mapping for {node_id!r} needs non-empty allowed_source_paths.",
                )
                continue
            for path in paths:
                assert isinstance(path, str)
                if _is_note_path(path):
                    add(
                        "note_source_path",
                        f"Source path {path!r} references notes; notes never feed models.",
                    )
                elif _is_posterior_path(path):
                    add(
                        "posterior_chaining",
                        f"Source path {path!r} chains another result/posterior; "
                        "no implicit posterior chaining.",
                    )
                elif not any(path.startswith(prefix) for prefix in known_source_prefixes):
                    add("unknown_source_path", f"Source path {path!r} is not a known path.")

    # --- Applicability gate / missingness ---
    applicability = manifest.get("applicability")
    if not isinstance(applicability, Mapping):
        add("bad_applicability", "manifest needs an applicability mapping.")
    else:
        expression = applicability.get("expression")
        if not isinstance(expression, str) or expression not in safe_expressions:
            add("unsafe_expression", f"Expression {expression!r} is not an allowlisted query.")
        required_fields = applicability.get("required_fields")
        if not isinstance(required_fields, (list, tuple)) or not all(
            isinstance(f, str) for f in required_fields
        ):
            add("bad_required_fields", "applicability required_fields must list strings.")
        unknown_policy = applicability.get("unknown_policy")
        if unknown_policy not in SUPPORTED_UNKNOWN_POLICIES:
            add(
                "bad_unknown_policy",
                f"unknown_policy must be one of {sorted(SUPPORTED_UNKNOWN_POLICIES)}.",
            )

    # --- All-CPT contract ---
    cpt_contract = manifest.get("cpt_contract")
    if not isinstance(cpt_contract, Mapping):
        add("incomplete_cpt_contract", "manifest needs a cpt_contract mapping.")
    else:
        nodes = cpt_contract.get("nodes")
        if not isinstance(nodes, (list, tuple)) or not nodes:
            add("incomplete_cpt_contract", "cpt_contract needs a non-empty nodes list.")
        else:
            contract_ids = [str(e.get("node_id", "")) for e in nodes if isinstance(e, Mapping)]
            if declared_order:
                if set(contract_ids) != set(declared_order):
                    add(
                        "incomplete_cpt_contract",
                        f"cpt_contract covers {sorted(set(contract_ids))}, "
                        f"must cover all of {list(declared_order)}.",
                    )
                elif contract_ids != list(declared_order):
                    add(
                        "wrong_cpt_order",
                        f"cpt_contract order {contract_ids} must match "
                        f"declared order {list(declared_order)}.",
                    )
            for entry in nodes:
                if not isinstance(entry, Mapping):
                    add("incomplete_cpt_contract", "cpt_contract node must be a mapping.")
                    continue
                node_id = entry.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    add("incomplete_cpt_contract", "cpt_contract node has no node_id.")
                    continue
                parent_ids = entry.get("parent_ids")
                contract_states = entry.get("states")
                if not isinstance(parent_ids, (list, tuple)) or not all(
                    isinstance(p, str) for p in parent_ids
                ):
                    add(
                        "incomplete_cpt_contract",
                        f"cpt_contract {node_id!r} parent_ids must list strings.",
                    )
                if not isinstance(contract_states, (list, tuple)) or not all(
                    isinstance(s, str) for s in contract_states
                ):
                    add(
                        "incomplete_cpt_contract",
                        f"cpt_contract {node_id!r} states must list strings.",
                    )
                if isinstance(entry, Mapping) and node_id in var_by_id:
                    want_parents = list(var_by_id[node_id].get("ordered_parents", ()))
                    want_states = list(var_by_id[node_id].get("states", ()))
                    if isinstance(parent_ids, (list, tuple)) and list(parent_ids) != want_parents:
                        add(
                            "incomplete_cpt_contract",
                            f"cpt_contract {node_id!r} parents {list(parent_ids)} "
                            f"must match variables {want_parents}.",
                        )
                    if (
                        isinstance(contract_states, (list, tuple))
                        and list(contract_states) != want_states
                    ):
                        add(
                            "incomplete_cpt_contract",
                            f"cpt_contract {node_id!r} states {list(contract_states)} "
                            f"must match variables {want_states}.",
                        )
    cpt_tables = package.get("cpt_tables")
    if cpt_tables is not None:
        if not isinstance(cpt_tables, Mapping):
            add("incomplete_cpt_contract", "cpt_tables must be a mapping.")
        elif document is None or not doc_available:
            add("incomplete_cpt_contract", "cpt_tables need a single-network document.")
        else:
            try:
                from x_insight.models.inference import validate_cpts as _validate_cpts

                report = _validate_cpts(document, cpt_tables)
            except Exception as exc:
                add("incomplete_cpt_contract", f"cpt_tables check failed: {exc}.")
            else:
                if not report.valid:
                    first = report.errors[0] if report.errors else None
                    detail = f" ({first.code})" if first is not None else ""
                    add("incomplete_cpt_contract", f"cpt_tables invalid{detail}; see S23 report.")

    # --- Query + execution evidence (always empty) ---
    query_nodes = manifest.get("query_nodes")
    if (
        not isinstance(query_nodes, (list, tuple))
        or not query_nodes
        or not all(isinstance(q, str) and q for q in query_nodes)
    ):
        add("bad_manifest", "manifest needs a non-empty query_nodes list.")
    elif declared_order and any(q not in declared_order for q in query_nodes):
        bad = sorted({str(q) for q in query_nodes if q not in declared_order})
        add("undeclared_query", f"Query nodes {bad} are not declared.")
    execution_evidence = manifest.get("execution_evidence")
    if isinstance(execution_evidence, Mapping):
        if dict(execution_evidence) != {}:
            add(
                "evidence_not_empty",
                "execution_evidence must always be empty; review cannot enable evidence.",
            )
    elif execution_evidence is not None:
        add("evidence_not_empty", "execution_evidence must always be empty.")
    prompt_version = manifest.get("prompt_version")
    template_version = manifest.get("template_version")
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        add("bad_manifest", "manifest needs a non-empty prompt_version.")
    if not isinstance(template_version, str) or not template_version.strip():
        add("bad_manifest", "manifest needs a non-empty template_version.")

    # --- Prompt (estimation only) ---
    prompt = package.get("prompt")
    if not isinstance(prompt, Mapping):
        add("missing_prompt", "Package has no prompt mapping.")
    else:
        if isinstance(prompt_version, str) and prompt.get("version") != prompt_version:
            add("bad_prompt_reference", "prompt version must match manifest prompt_version.")
        text = prompt.get("text")
        if not isinstance(text, str) or not text.strip():
            add("missing_prompt", "prompt needs non-empty text.")
        else:
            lowered = text.lower()
            if any(marker in lowered for marker in _FORBIDDEN_PROMPT_MARKERS):
                add(
                    "forbidden_prompt_directive",
                    "prompt must estimate CPTs only; no plan/record/execution choice.",
                )
            if "estimate" not in lowered or ("cpt" not in lowered and "percentage" not in lowered):
                add(
                    "bad_prompt_content",
                    "prompt must instruct CPT/percentage estimation from supplied inputs.",
                )
            if "argmax" in lowered:
                add("argmax_selection", "prompt must not select treatment by argmax.")

    # --- Template (escaped slots + explicit branches, no rendering here) ---
    template = package.get("template")
    declared_state_map: dict[str, tuple[str, ...]] = {}
    for map_node_id, map_entry in var_by_id.items():
        map_states = map_entry.get("states", ())
        if isinstance(map_states, (list, tuple)):
            declared_state_map[map_node_id] = tuple(str(s) for s in map_states)
    if declared_order:
        for map_node_id in declared_order:
            declared_state_map.setdefault(map_node_id, ())
    if doc_available:
        for map_node_id, map_states_tuple in doc_states.items():
            declared_state_map[map_node_id] = map_states_tuple
    if not isinstance(template, Mapping):
        add("missing_template", "Package has no template mapping.")
    else:
        if isinstance(template_version, str) and template.get("version") != template_version:
            add("bad_template_reference", "template version must match manifest template_version.")
        if "prose" in template or "free_text" in template:
            add("llm_prose", "template must not hold free LLM prose; branches only.")
        branches = template.get("branches")
        if not isinstance(branches, (list, tuple)) or not branches:
            add("missing_template", "template needs a non-empty branches list.")
        else:
            for idx, branch in enumerate(branches):
                if not isinstance(branch, Mapping):
                    add("missing_template", f"template branches[{idx}] must be a mapping.")
                    continue
                when = branch.get("when")
                branch_text = branch.get("text")
                if not isinstance(when, Mapping):
                    add("missing_template", f"template branches[{idx}] needs a when mapping.")
                    continue
                if not isinstance(branch_text, str) or not branch_text.strip():
                    add("missing_template", f"template branches[{idx}] needs text.")
                    continue
                branch_node = when.get("node")
                branch_state = when.get("state")
                operator = when.get("operator", "==")
                if not isinstance(branch_node, str) or branch_node not in declared_state_map:
                    add(
                        "undeclared_query",
                        f"template branches[{idx}] node {branch_node!r} undeclared.",
                    )
                    continue
                if not isinstance(branch_state, str) or branch_state not in declared_state_map.get(
                    branch_node, ()
                ):
                    add(
                        "undeclared_output_state",
                        f"template branches[{idx}] state {branch_state!r} "
                        f"is not declared for {branch_node!r}.",
                    )
                if operator not in SUPPORTED_TEMPLATE_OPERATORS:
                    add(
                        "unsupported_operator",
                        f"template branches[{idx}] operator {operator!r} "
                        f"must be one of {sorted(SUPPORTED_TEMPLATE_OPERATORS)}.",
                    )
                lowered_text = branch_text.lower()
                if any(marker in lowered_text for marker in _ARGMAX_MARKERS):
                    add(
                        "argmax_selection",
                        f"template branches[{idx}] must not select treatment by argmax.",
                    )
                if "llm" in lowered_text and "decide" in lowered_text:
                    add("llm_prose", f"template branches[{idx}] must not hold LLM prose.")
                if "{" in branch_text or "}" in branch_text:
                    for token in _SLOT_RE.findall(branch_text):
                        if token not in declared_state_map:
                            add(
                                "bad_template_slot",
                                f"template branches[{idx}] slot {{{token}}} is not declared.",
                            )
                    # Unbalanced braces with no parseable slot is still a slot error.
                    if not _SLOT_RE.search(branch_text):
                        add(
                            "bad_template_slot",
                            f"template branches[{idx}] has unbalanced value slots.",
                        )
                else:
                    add(
                        "bad_template_slot",
                        f"template branches[{idx}] needs an escaped-value slot like {{node}}.",
                    )

    # --- Examples (independent numerical + clinical, review candidates) ---
    examples = package.get("examples")
    if not isinstance(examples, Mapping):
        add("missing_examples", "Package has no examples mapping.")
    else:
        numerical = examples.get("numerical")
        clinical = examples.get("clinical")
        if not isinstance(numerical, (list, tuple)) or not numerical:
            add("missing_examples", "examples need a non-empty numerical list.")
        else:
            for idx, entry in enumerate(numerical):
                if not isinstance(entry, Mapping) or "expected" not in entry:
                    add(
                        "bad_numerical_examples",
                        f"examples numerical[{idx}] needs an expected mapping.",
                    )
        if not isinstance(clinical, (list, tuple)) or not clinical:
            add("missing_examples", "examples need a non-empty clinical list.")
        else:
            for idx, entry in enumerate(clinical):
                if not isinstance(entry, Mapping) or "expected_for_review" not in entry:
                    add(
                        "bad_clinical_examples",
                        f"examples clinical[{idx}] needs expected_for_review "
                        "(review candidate, never self-validating).",
                    )

    # --- Review dossier (presence/shape only, never approval) ---
    review = package.get("review")
    if not isinstance(review, Mapping):
        add("missing_review", "Package has no review mapping.")
    else:
        reviewer = review.get("reviewer")
        decision = review.get("decision")
        date = review.get("date")
        if not isinstance(reviewer, str) or not reviewer.strip():
            add("bad_review_shape", "review needs a non-empty reviewer.")
        if decision not in SUPPORTED_REVIEW_DECISIONS:
            add(
                "bad_review_shape",
                f"review decision must be one of {sorted(SUPPORTED_REVIEW_DECISIONS)}.",
            )
        if not isinstance(date, str) or not date.strip():
            add("bad_review_shape", "review needs a non-empty date.")
        source_hashes = review.get("source_hashes")
        if not isinstance(source_hashes, Mapping) or not source_hashes:
            add("bad_review_shape", "review needs a non-empty source_hashes mapping.")
        elif doc_available:
            want = source_hashes.get("network.xml", source_hashes.get("network_hash", ""))
            if want != doc_hash:
                add("bad_source_hash", "review source_hashes must pin the document hash.")
        assumptions = review.get("assumptions")
        if isinstance(assumptions, (list, tuple)):
            if not assumptions or not all(isinstance(a, str) and a.strip() for a in assumptions):
                add("bad_review_shape", "review assumptions must list non-empty strings.")
        elif not isinstance(assumptions, str) or not assumptions.strip():
            add("incomplete_dossier", "review needs explicit assumptions.")
        for field in DOSSIER_FIELDS:
            value = review.get(field)
            if not isinstance(value, str) or not value.strip():
                add("incomplete_dossier", f"review dossier needs non-empty {field}.")

    return QuestionPackageReport(valid=not errors, errors=tuple(errors))


def load_question_package(
    package: Mapping[str, Any] | None,
    document: Any | None = None,
    *,
    known_source_prefixes: tuple[str, ...] = KNOWN_SOURCE_PREFIXES,
    safe_expressions: frozenset[str] = SAFE_EXPRESSIONS,
) -> LoadedQuestionPackage:
    """Validate and freeze one reusable package for S27/S29-S38 handoff.

    Returns :class:`LoadedQuestionPackage` with the manifest ``question_key``,
    ``network_hash``, ordered nodes/query, and a canonical ``package_hash``
    over ``manifest``/``prompt``/``template``/``examples``/``review`` (sorted
    keys). Raises :class:`QuestionPackageError` when the shape is invalid.
    Never activates, never approves, never renders template sections (S45).
    """
    report = validate_question_package(
        package,
        document,
        known_source_prefixes=known_source_prefixes,
        safe_expressions=safe_expressions,
    )
    if not report.valid:
        first = report.errors[0] if report.errors else None
        code = first.code if first is not None else "invalid_package"
        raise QuestionPackageError(code, "Question package failed validation.", report.errors)
    assert isinstance(package, Mapping)
    manifest = package["manifest"]
    assert isinstance(manifest, Mapping)
    question_key = str(manifest["question_key"])
    network_hash = str(manifest["network_hash"])
    declared_node_order = tuple(str(n) for n in manifest["declared_node_order"])
    query_nodes = tuple(str(q) for q in manifest["query_nodes"])
    core = {
        "manifest": dict(manifest),
        "prompt": dict(package["prompt"]) if isinstance(package.get("prompt"), Mapping) else {},
        "template": dict(package["template"])
        if isinstance(package.get("template"), Mapping)
        else {},
        "examples": dict(package["examples"])
        if isinstance(package.get("examples"), Mapping)
        else {},
        "review": dict(package["review"]) if isinstance(package.get("review"), Mapping) else {},
    }
    return LoadedQuestionPackage(
        question_key=question_key,
        network_hash=network_hash,
        package_hash=_canonical_hash(core),
        declared_node_order=declared_node_order,
        query_nodes=query_nodes,
    )
