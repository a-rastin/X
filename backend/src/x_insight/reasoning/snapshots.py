"""Freeze analysis snapshots and project one question's inputs (S40, seams T1/T5).

Plan.md §§4.2, 8.1 (FR-16, FR-32, FR-35, NFR-04):

- Fingerprint (plan §4.2): canonical UTF-8 JSON sorted keys, explicit
  null/missing, schema version. Includes analysis-visible facts
  (history values, reconciled medications, effects, gate, assessment
  answers, baseline), plus pinned content. Excludes notes (separate
  table, never read), names/ID/phone (patient row never read), UI state
  and secondary-plan edits (unallowlisted top-level keys ignored).
  ``notes.exclude_notes_from_analysis`` is the same exclusion by
  construction: this module never queries ``notes`` or ``patients``.
- Projection (plan §8.1): per-question typed allowlisted projection
  persisted before provider access. Each entry holds node ID, patient
  type, observed/not-assessed/missing/conflict status, source
  path/revision and explicit null when unavailable. Only represented
  (mapped) variables appear, in declared order. Notes, names, Patient
  ID, unrelated history, other questions' fields/results and secrets
  never appear: mappings are validated through S25
  (``validate_question_package``) plus runtime allowlist checks, and the
  prompt text is never consulted for inputs.
- Gates: true/false/required-unknown produce ready/not-applicable/
  clarification with persisted explicit reasons. Undeclared
  cross-question result inputs (``posterior``/``questions/``) are
  rejected at start.
- Synthetic scope (tasks.md S40 permits it): source prefixes are the S25
  synthetic allowlist (``synthetic/history|assessments|demographics``).
  ``candidate/...`` histories and the S24 registry pointer are future
  wiring; a package using them fails closed here as ``unknown_source``.
  Demographic paths that name identifiers/phones are rejected, so
  sentinel names/ID/phone can never enter a projection. Queue execution
  is S44; this module only freezes and reads.

All persistence uses the caller's transaction; this module never commits.
Batches/runs are immutable: staleness is derived on read by comparing
the stored fingerprint with the current fingerprint, never by UPDATE.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.models import question_package as package_module
from x_insight.operations import audit as audit_module
from x_insight.reasoning import tables as reasoning_tables

FINGERPRINT_SCHEMA_VERSION = "analysis-fingerprint-v1"
PROJECTION_SCHEMA_VERSION = "question-projection-v1"

GENERATION_START_OPERATION = "generation.start"

READY = "ready"
NOT_APPLICABLE = "not_applicable"
NEEDS_CLARIFICATION = "needs_clarification"

BATCH_STATUSES = (READY, NOT_APPLICABLE, NEEDS_CLARIFICATION)

# S46 ordered workflows: batches hold 1..11 questions in pinned ``packages`` order.
# Single-question starts stay backward compatible (``package`` wraps to one).
MAX_PROJECTION_VARIABLES = 64
MAX_WORKFLOW_QUESTIONS = 11


def _is_note_path(path: str) -> bool:
    for segment in path.replace("\\", "/").lower().split("/"):
        if segment in ("note", "notes") or segment.startswith(("note_", "notes_", "page_note")):
            return True
    return False


def _is_posterior_path(path: str) -> bool:
    lowered = path.lower()
    return "posterior" in lowered or "questions/" in lowered


def _is_identifying_demographic(path: str) -> bool:
    lowered = path.lower()
    for marker in ("name", "identifier", "patient_id", "phone"):
        if marker in lowered:
            return True
    return False


def _fail(status: int, code: str, message: str, field: str = "package") -> contracts.ContractError:
    return contracts.ContractError(status, code, message, {field: [message]})


def _get_history_values(draft_data: Any) -> dict[str, Any]:
    if not isinstance(draft_data, dict):
        return {}
    section = draft_data.get("history")
    if not isinstance(section, dict):
        return {}
    values = section.get("values")
    if isinstance(values, dict):
        return dict(values)
    # Synthetic direct shape: {"history": {"h_a": "yes"}} (PATCH-permissive).
    return {
        key: value
        for key, value in section.items()
        if key not in ("provenance", "reconciliation", "phone_update")
    }


def _get_medication_ids(draft_data: Any) -> list[str]:
    if not isinstance(draft_data, dict):
        return []
    section = draft_data.get("medications")
    if not isinstance(section, dict):
        return []
    entries = section.get("entries")
    if not isinstance(entries, list):
        return []
    ids: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("catalog_drug_id"), str):
            ids.add(entry["catalog_drug_id"])
    return sorted(ids)


def _get_medication_reconciliation(draft_data: Any) -> str | None:
    if not isinstance(draft_data, dict):
        return None
    section = draft_data.get("medications")
    if not isinstance(section, dict):
        return None
    reconciliation = section.get("reconciliation")
    if isinstance(reconciliation, dict) and isinstance(reconciliation.get("status"), str):
        return str(reconciliation["status"])
    return None


def _get_effects_map(draft_data: Any) -> dict[str, Any]:
    if not isinstance(draft_data, dict):
        return {}
    section = draft_data.get("effects")
    if not isinstance(section, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key in sorted(section):
        value = section[key]
        if isinstance(value, dict):
            normalized[key] = {"status": value.get("status"), "severity": value.get("severity")}
        else:
            normalized[key] = value
    return normalized


def _get_gate_value(draft_data: Any) -> str | None:
    if not isinstance(draft_data, dict):
        return None
    gate = draft_data.get("gate")
    return gate if isinstance(gate, str) else None


def _get_answers(draft_data: Any, key: str) -> dict[str, Any]:
    if not isinstance(draft_data, dict):
        return {}
    section = draft_data.get(key)
    if not isinstance(section, dict):
        return {}
    answers = section.get("answers")
    return dict(answers) if isinstance(answers, dict) else {}


def compute_analysis_fingerprint(
    draft_data: Any, pinned: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Canonical analysis fingerprint over allowlisted facts + pinned content.

    Never reads ``notes`` or ``patients``: notes/names/ID/phone cannot
    enter the digest by construction. Unallowlisted top-level draft keys
    (secondary plan, UI state, arbitrary client keys) are ignored, so
    note-only and plan-only edits leave the fingerprint unchanged.
    """
    values = _get_history_values(draft_data)
    payload: dict[str, Any] = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "history": values,
        "medications": _get_medication_ids(draft_data),
        "medications_reconciliation": _get_medication_reconciliation(draft_data),
        "effects": _get_effects_map(draft_data),
        "gate": _get_gate_value(draft_data),
        "diagnosis_answers": _get_answers(draft_data, "diagnosis"),
        "panss_answers": _get_answers(draft_data, "panss"),
        "cssrs_answers": _get_answers(draft_data, "cssrs"),
        "baseline": draft_data.get("followup_baseline") if isinstance(draft_data, dict) else None,
        "pinned": dict(pinned),
    }
    if payload["baseline"] is None:
        payload["baseline"] = None
    return contracts.canonical_hash(payload), payload


def _check_source_path(path: Any) -> str:
    if not isinstance(path, str) or not path:
        raise _fail(422, "unknown_source_path", f"Source path {path!r} is not allowlisted.")
    if _is_note_path(path):
        raise _fail(422, "note_source_path", f"Source path {path!r} references notes.")
    if _is_posterior_path(path):
        raise _fail(422, "posterior_chaining", f"Source path {path!r} chains another result.")
    if _is_identifying_demographic(path):
        raise _fail(422, "identifying_source_path", f"Source path {path!r} is identifying.")
    allowed = package_module.KNOWN_SOURCE_PREFIXES
    if not any(path.startswith(prefix) for prefix in allowed):
        raise _fail(422, "unknown_source_path", f"Source path {path!r} is not allowlisted.")
    return path


def _resolve_raw_value(source_path: str, draft_data: Any) -> Any:
    """Resolve one allowlisted source path against saved draft facts.

    ``synthetic/history/<field>`` reads ``history.values[field]``;
    ``synthetic/assessments/<field>`` searches diagnosis/panss/cssrs
    answers; ``synthetic/demographics/*`` is rejected in this synthetic
    slice (demographics never enter model projections here). Missing
    fields return the ``MISSING`` sentinel (absent, never null-coerced).
    """
    _check_source_path(source_path)
    if source_path.startswith("synthetic/history/"):
        field = source_path[len("synthetic/history/") :]
        if not field:
            raise _fail(422, "unknown_source_path", f"Source path {source_path!r} is empty.")
        values = _get_history_values(draft_data)
        return values[field] if field in values else _MISSING
    if source_path.startswith("synthetic/assessments/"):
        field = source_path[len("synthetic/assessments/") :]
        if not field:
            raise _fail(422, "unknown_source_path", f"Source path {source_path!r} is empty.")
        for key in ("diagnosis", "panss", "cssrs"):
            answers = _get_answers(draft_data, key)
            if field in answers:
                return answers[field]
        values = _get_history_values(draft_data)
        return values[field] if field in values else _MISSING
    raise _fail(422, "unknown_source_path", f"Source path {source_path!r} is not projected in S40.")


class _MissingType:
    pass


_MISSING = _MissingType()


def _typed_status(raw: Any) -> tuple[str, Any]:
    """Map a raw saved fact to (status, model value or None).

    - list or ``"conflict"`` → conflict (explicit null)
    - ``"not_assessed"`` → not_assessed (explicit null)
    - missing sentinel, None, ``"unknown"`` → missing (explicit null)
    - anything else → observed with the raw value verbatim
    """
    if isinstance(raw, _MissingType):
        return "missing", None
    if isinstance(raw, list):
        return "conflict", None
    if raw is None:
        return "missing", None
    if isinstance(raw, str) and raw == "conflict":
        return "conflict", None
    if isinstance(raw, str) and raw == "not_assessed":
        return "not_assessed", None
    if isinstance(raw, str) and raw == "unknown":
        return "missing", None
    return "observed", raw


def build_question_projection(
    manifest: Mapping[str, Any], draft_data: Any, source_revision: int
) -> tuple[dict[str, Any], str]:
    """Build the immutable per-question projection (prompt text ignored).

    Only mapped (represented) variables appear, in declared order. Each
    entry carries node ID, patient type, typed status, explicit value or
    null, source path and frozen source revision. Note/unrelated paths
    raise 422 regardless of what the prompt instructs.
    """
    declared = manifest.get("declared_node_order")
    variables = manifest.get("variables")
    mappings = manifest.get("patient_mappings")
    if not isinstance(declared, (list, tuple)) or not declared:
        raise _fail(422, "bad_manifest", "Manifest needs declared_node_order.")
    if not isinstance(variables, (list, tuple)) or not variables:
        raise _fail(422, "bad_manifest", "Manifest needs variables.")
    if not isinstance(mappings, (list, tuple)) or not mappings:
        raise _fail(422, "bad_manifest", "Manifest needs patient_mappings.")
    type_by_node: dict[str, str] = {}
    for entry in variables:
        if not isinstance(entry, Mapping):
            continue
        node = entry.get("node_id")
        if isinstance(node, str) and node:
            type_by_node[node] = str(entry.get("patient_value_type", "categorical"))
    mapping_by_node: dict[str, Mapping[str, Any]] = {}
    for entry in mappings:
        if not isinstance(entry, Mapping):
            raise _fail(422, "bad_manifest", "patient_mappings entries must map.")
        node = entry.get("node_id")
        if not isinstance(node, str) or not node:
            raise _fail(422, "undeclared_variable", "Mapping has no node_id.")
        if node in mapping_by_node:
            raise _fail(422, "duplicate_variable", f"Mapping for {node!r} appears twice.")
        if node not in type_by_node:
            raise _fail(422, "undeclared_variable", f"Mapping node {node!r} is not declared.")
        if entry.get("usage") != "cpt_context":
            raise _fail(422, "evidence_mapping", f"Mapping for {node!r} must be cpt_context.")
        paths = entry.get("allowed_source_paths")
        if not isinstance(paths, (list, tuple)) or not paths:
            raise _fail(422, "bad_source_refs", f"Mapping for {node!r} needs source paths.")
        for path in paths:
            _check_source_path(path)
        mapping_by_node[node] = entry
    if len(mapping_by_node) > MAX_PROJECTION_VARIABLES:
        raise _fail(422, "too_many_variables", "Too many projected variables.")
    question_key = str(manifest.get("question_key", ""))
    entries: list[dict[str, Any]] = []
    for node in declared:
        mapping = mapping_by_node.get(str(node))
        if mapping is None:
            continue  # latent node: represented set is the mapped subset only.
        paths = mapping["allowed_source_paths"]
        assert isinstance(paths, (list, tuple)) and paths
        source_path = str(paths[0])
        raw = _resolve_raw_value(source_path, draft_data)
        status, value = _typed_status(raw)
        entries.append(
            {
                "node_id": str(node),
                "patient_type": type_by_node.get(str(node), "categorical"),
                "status": status,
                "value": value,
                "source_path": source_path,
                "source_revision": int(source_revision),
            }
        )
    projection: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "question_key": question_key,
        "source_revision": int(source_revision),
        "variables": entries,
    }
    return projection, contracts.canonical_hash(projection)


def evaluate_gate(manifest: Mapping[str, Any], draft_data: Any) -> tuple[str, str]:
    """Evaluate applicability against saved facts (three-valued gate).

    ``true`` is always applicable; ``gate == 'X'`` compares the saved
    ``draft_data["gate"]`` fact. True + all required fields observed →
    ready; false → not_applicable; unknown gate or any required
    missing/conflict/not_assessed → needs_clarification. Returns
    (status, explicit reason). Cross-question references raise 422.
    """
    applicability = manifest.get("applicability")
    if not isinstance(applicability, Mapping):
        raise _fail(422, "bad_applicability", "Manifest needs applicability.")
    expression = applicability.get("expression")
    if not isinstance(expression, str) or expression not in package_module.SAFE_EXPRESSIONS:
        raise _fail(422, "unsafe_expression", f"Expression {expression!r} is not allowlisted.")
    required = applicability.get("required_fields")
    if not isinstance(required, (list, tuple)) or not all(
        isinstance(item, str) for item in required
    ):
        raise _fail(422, "bad_required_fields", "required_fields must list strings.")
    unknown_policy = applicability.get("unknown_policy")
    if unknown_policy not in ("needs_clarification", "not_applicable"):
        raise _fail(422, "bad_unknown_policy", "unknown_policy must be allowlisted.")
    for field in required:
        assert isinstance(field, str)
        if _is_posterior_path(field):
            raise _fail(422, "posterior_chaining", f"Required field {field!r} chains a result.")
        if _is_note_path(field):
            raise _fail(422, "note_source_path", f"Required field {field!r} references notes.")
    gate = _get_gate_value(draft_data)
    if expression == "true":
        gate_state: str | None = "true"
    elif expression == "gate == 'true'":
        gate_state = "true" if gate == "true" else ("false" if gate == "false" else "unknown")
    elif expression == "gate == 'false'":
        gate_state = "true" if gate == "false" else ("false" if gate == "true" else "unknown")
    elif expression == "gate == 'unknown'":
        gate_state = (
            "true" if gate == "unknown" else ("false" if gate in ("true", "false") else "unknown")
        )
    else:  # pragma: no cover — allowlist above is exhaustive.
        raise _fail(422, "unsafe_expression", f"Expression {expression!r} is not allowlisted.")
    if gate_state == "false":
        return NOT_APPLICABLE, f"gate false; expression {expression!r} is not satisfied"
    if gate_state == "unknown":
        return (
            NEEDS_CLARIFICATION,
            f"gate unknown; expression {expression!r} needs clarification",
        )
    for field in required:
        assert isinstance(field, str)
        try:
            raw = _resolve_raw_value(field, draft_data)
        except contracts.ContractError:
            return NEEDS_CLARIFICATION, f"required field {field} is not allowlisted"
        status, _ = _typed_status(raw)
        if status != "observed":
            return NEEDS_CLARIFICATION, f"required field {field} is {status}"
    count = len(list(required))
    return READY, f"gate true; all {count} required fields observed"


def idempotency_request_hash(
    target_id: uuid.UUID, expected_revision: int, package: dict[str, Any]
) -> str:
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision, "package": package}
    )


def idempotency_workflow_hash(
    target_id: uuid.UUID, expected_revision: int, packages: list[dict[str, Any]]
) -> str:
    """Idempotency hash for ordered workflow starts (pinned package order matters)."""
    return contracts.canonical_hash(
        {
            "target_id": str(target_id),
            "expected_revision": expected_revision,
            "packages": list(packages),
        }
    )


def safe_batch(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "encounter_id": str(row["encounter_id"]),
        "author_id": str(row["author_id"]),
        "source_revision": int(row["source_revision"]),
        "fingerprint": str(row["fingerprint"]),
        "status": str(row["status"]),
        "pinned_bundle": dict(row["pinned_bundle"])
        if isinstance(row["pinned_bundle"], dict)
        else {},
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def safe_run(row: dict[str, Any]) -> dict[str, Any]:
    projection = row["projection"]
    return {
        "id": str(row["id"]),
        "batch_id": str(row["batch_id"]),
        "question_key": str(row["question_key"]),
        "status": str(row["status"]),
        "gate_reason": str(row["gate_reason"]),
        "projection": dict(projection) if isinstance(projection, dict) else {},
        "projection_hash": str(row["projection_hash"]),
        "fingerprint": str(row["fingerprint"]),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def _validate_workflow_packages(
    packages: list[dict[str, Any]],
) -> list[Any]:
    """Validate ordered packages (S25 each) with distinct keys in pinned order."""
    if not isinstance(packages, list) or not packages:
        raise _fail(422, "missing_package", "No question packages supplied.")
    if len(packages) > MAX_WORKFLOW_QUESTIONS:
        raise _fail(422, "too_many_questions", "Too many questions in one workflow.")
    seen: set[str] = set()
    loaded_list: list[Any] = []
    for package in packages:
        if not isinstance(package, dict):
            raise _fail(422, "missing_package", "No question package supplied.")
        report = package_module.validate_question_package(package, None)
        if not report.valid:
            first = report.errors[0] if report.errors else None
            code = first.code if first is not None else "invalid_package"
            detail = first.message if first is not None else "Invalid package."
            raise contracts.ContractError(422, code, f"Invalid question package: {detail}")
        try:
            loaded = package_module.load_question_package(package, None)
        except package_module.QuestionPackageError as exc:
            raise contracts.ContractError(
                422, exc.code, f"Invalid question package: {exc.message}"
            ) from exc
        if loaded.question_key in seen:
            raise _fail(422, "duplicate_question", f"Duplicate question {loaded.question_key!r}.")
        seen.add(loaded.question_key)
        loaded_list.append(loaded)
    return loaded_list


def _pin_ddi_for_start(session: Session, draft_data: Any) -> dict[str, Any]:
    """Pin the DDI dataset/catalog/fingerprint/report for one batch start.

    Reuses the S19 ``checker.check`` seam (engine DI). Synthetic
    limited-coverage fixtures are acceptable mechanics here; S18 gates real
    data. Valid reports pin ``ddi_status='valid'`` with the full report;
    missing/broken datasets or unknown catalog IDs pin
    ``ddi_status='unavailable'`` with the error code (proposal stays
    incomplete, questions still execute). Never raises: start succeeds so
    partial sections stay readable; proposal assembly enforces validity.
    """
    from x_insight import db as db_module
    from x_insight.ddi import checker as checker_module

    med_ids = _get_medication_ids(draft_data)
    stored_version: str | None = None
    if isinstance(draft_data, dict):
        section = draft_data.get("medications")
        if isinstance(section, dict) and isinstance(section.get("dataset_version"), str):
            stored_version = str(section["dataset_version"]).strip() or None
    try:
        engine = session.get_bind()
    except Exception:
        engine = None
    if engine is None:
        try:
            engine = db_module.get_engine()
        except Exception:
            engine = None
    dataset_version = stored_version
    if not dataset_version and engine is not None:
        try:
            dataset_version = checker_module.latest_release_version(engine)
        except Exception:
            dataset_version = None
    if engine is None or not dataset_version:
        return {
            "dataset_version": dataset_version,
            "catalog_version": None,
            "medication_fingerprint": None,
            "ddi_status": "unavailable",
            "ddi_error": "DATASET_UNAVAILABLE",
            "ddi_report": None,
        }
    try:
        ddi_report = checker_module.check(engine, list(med_ids), str(dataset_version))
    except checker_module.CheckError as exc:
        return {
            "dataset_version": str(dataset_version),
            "catalog_version": None,
            "medication_fingerprint": None,
            "ddi_status": "unavailable",
            "ddi_error": str(exc.code),
            "ddi_report": None,
        }
    except Exception:
        return {
            "dataset_version": str(dataset_version),
            "catalog_version": None,
            "medication_fingerprint": None,
            "ddi_status": "unavailable",
            "ddi_error": "DATASET_UNAVAILABLE",
            "ddi_report": None,
        }
    return {
        "dataset_version": str(ddi_report.get("dataset_version")),
        "catalog_version": str(ddi_report.get("catalog_version") or ""),
        "medication_fingerprint": str(ddi_report.get("medication_fingerprint") or ""),
        "ddi_status": "valid",
        "ddi_error": None,
        "ddi_report": dict(ddi_report),
    }


def _workflow_batch_status(statuses: list[str]) -> str:
    """Overall batch status from ordered per-question gates.

    Skips ``not_applicable`` (no job, recorded skipped); the first
    ``needs_clarification`` blocks successors (batch clarification); the
    first ``ready`` makes the batch ready; all skipped → not_applicable.
    """
    for status in statuses:
        if status == NOT_APPLICABLE:
            continue
        if status == NEEDS_CLARIFICATION:
            return NEEDS_CLARIFICATION
        if status == READY:
            return READY
    # Either all skipped or empty (empty rejected earlier); all skipped.
    return NOT_APPLICABLE


def _first_eligible_index(statuses: list[str], skip: set[int] | None = None) -> int | None:
    """Index of the first eligible ready run (skips not_applicable, stops on clarification).

    ``skip`` holds positions already solved (S47 carried baselines): they
    need no job, and successors after them stay eligible.
    """
    skipped = skip or set()
    for index, status in enumerate(statuses):
        if index in skipped:
            continue
        if status == NOT_APPLICABLE:
            continue
        if status == NEEDS_CLARIFICATION:
            return None
        if status == READY:
            return index
    return None


def _carried_baselines(
    session: Session,
    encounter_id: uuid.UUID,
    fingerprint: str,
    question_keys: list[str],
) -> dict[str, dict[str, Any]]:
    """Latest accepted baseline per question under the same fingerprint (S47).

    Author retry of an unchanged failed stage starts a new bounded batch;
    questions already solved under identical facts + pinned content carry
    their immutable baselines forward, so the new batch resumes at the
    failed stage with no new provider request for earlier questions.
    Returns {question_key: baseline row} (at most one per key, newest first).
    """
    wanted = set(question_keys)
    carried: dict[str, dict[str, Any]] = {}
    if not wanted:
        return carried
    prior_ids = [
        row[0]
        for row in session.execute(
            select(reasoning_tables.generation_batches.c.id)
            .where(
                reasoning_tables.generation_batches.c.encounter_id == encounter_id,
                reasoning_tables.generation_batches.c.fingerprint == fingerprint,
            )
            .order_by(reasoning_tables.generation_batches.c.created_at.desc())
        ).all()
    ]
    for batch_id in prior_ids:
        if len(carried) >= len(wanted):
            break
        runs = (
            session.execute(
                select(
                    reasoning_tables.question_runs.c.id,
                    reasoning_tables.question_runs.c.question_key,
                ).where(reasoning_tables.question_runs.c.batch_id == batch_id)
            )
            .mappings()
            .all()
        )
        for entry in runs:
            key = str(entry["question_key"])
            if key not in wanted or key in carried:
                continue
            stored = (
                session.execute(
                    select(reasoning_tables.original_baselines).where(
                        reasoning_tables.original_baselines.c.question_run_id == entry["id"]
                    )
                )
                .mappings()
                .first()
            )
            if stored is not None:
                carried[key] = dict(stored)
    return carried


def start_generation_batch(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    package: dict[str, Any] | None = None,
    packages: list[dict[str, Any]] | None = None,
    request_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Freeze ordered snapshots + projections and enqueue the first eligible job.

    Backward compatible single-question path: ``package={...}`` wraps to a
    one-element workflow. Ordered path: ``packages=[...]`` in pinned order
    (distinct ``question_key`` values, S46 five/six-question synthetic
    workflows). Privacy first (404/403), then revision fence (412), then
    S25 validation per package (422 changes nothing). S44 admission (single
    active generation per encounter, fingerprint reuse, 100-queued cap)
    runs under the encounter + deployment locks. Only a fully valid start
    inserts the immutable batch + ordered run rows plus the first eligible
    queued job atomically — failures create no partial rows. Repeated
    same-fingerprint triggers reuse the existing batch (no duplicate,
    backfilling the first eligible job when missing).
    """
    if packages is not None and package is not None:
        raise _fail(422, "missing_package", "Supply package or packages, not both.")
    if packages is None:
        if not isinstance(package, dict):
            raise _fail(422, "missing_package", "No question package supplied.")
        ordered_packages: list[dict[str, Any]] = [package]
    else:
        if not isinstance(packages, list):
            raise _fail(422, "missing_package", "No question packages supplied.")
        ordered_packages = list(packages)
    loaded_list = _validate_workflow_packages(ordered_packages)
    manifests: list[Mapping[str, Any]] = []
    for entry, loaded in zip(ordered_packages, loaded_list):
        manifest = entry.get("manifest")
        if not isinstance(manifest, Mapping):
            raise _fail(422, "missing_package", "No question package supplied.")
        manifests.append(manifest)

    row = (
        session.execute(
            select(encounters_service.tables.encounters)
            .where(encounters_service.tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    encounters_service.require_draft_lifecycle(encounter)
    encounters_service.require_author(encounter, author)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412, "STALE_REVISION", "The draft changed. Reload and reconcile your edits."
        )
    draft_data = encounter.get("draft_data") or {}
    # S46 DDI pin (S19 checker seam): dataset/catalog/fingerprint/report per batch.
    ddi_pin = _pin_ddi_for_start(session, draft_data)
    # Workflow-level pinned bundle: ordered keys + per-question hashes for the
    # fingerprint, plus legacy single-question keys for T8 compat (first
    # question) and the DDI pin. Fingerprint covers ordered content + DDI.
    first_manifest = manifests[0]
    first_loaded = loaded_list[0]
    per_question: dict[str, Any] = {}
    for loaded, manifest in zip(loaded_list, manifests):
        per_question[loaded.question_key] = {
            "package_hash": loaded.package_hash,
            "network_hash": str(manifest.get("network_hash", "")),
            "prompt_version": str(manifest.get("prompt_version", "")),
            "template_version": str(manifest.get("template_version", "")),
            "package_version": str(manifest.get("version", "")),
        }
    pinned: dict[str, Any] = {
        "question_key": first_loaded.question_key,
        "package_hash": first_loaded.package_hash,
        "network_hash": str(first_manifest.get("network_hash", "")),
        "prompt_version": str(first_manifest.get("prompt_version", "")),
        "template_version": str(first_manifest.get("template_version", "")),
        "package_version": str(first_manifest.get("version", "")),
        "workflow": str(first_manifest.get("workflow", "registration")),
        "question_keys": [loaded.question_key for loaded in loaded_list],
        "per_question": per_question,
        "ddi_dataset_version": ddi_pin.get("dataset_version"),
        "ddi_catalog_version": ddi_pin.get("catalog_version"),
        "ddi_medication_fingerprint": ddi_pin.get("medication_fingerprint"),
        "ddi_status": ddi_pin.get("ddi_status"),
    }
    fingerprint, payload = compute_analysis_fingerprint(draft_data, pinned)
    # Per-question projections/gates in pinned order (S40 reuse, no second path).
    projections: list[dict[str, Any]] = []
    projection_hashes: list[str] = []
    statuses: list[str] = []
    reasons: list[str] = []
    for manifest in manifests:
        projection, projection_hash = build_question_projection(
            manifest, draft_data, expected_revision
        )
        status, reason = evaluate_gate(manifest, draft_data)
        projections.append(projection)
        projection_hashes.append(projection_hash)
        statuses.append(status)
        reasons.append(reason)
    batch_status = _workflow_batch_status(statuses)

    # S44 admission (lazy import: queue imports snapshots for eligibility).
    from x_insight.reasoning import queue as queue_module

    moment = contracts.utcnow()
    deployment_generation = queue_module.lock_deployment(session)
    requested_keys = [loaded.question_key for loaded in loaded_list]
    reusable = queue_module.check_start_admission(
        session,
        encounter_id=encounter_id,
        fingerprint=fingerprint,
        question_key=requested_keys[0] if len(requested_keys) == 1 else None,
        question_keys=requested_keys,
    )
    if reusable is not None:
        queue_module.ensure_job_for_reused_batch(
            session,
            batch=reusable,
            question_key=requested_keys[0] if len(requested_keys) == 1 else None,
            question_keys=requested_keys,
            deployment_generation=int(deployment_generation),
            now=moment,
        )
        batch = _get_batch(session, reusable["id"])
        assert batch is not None
        runs = _list_runs(session, reusable["id"])
        return batch, runs

    # S47 author retry: identical facts + pinned content carry already
    # accepted baselines into the new bounded batch, so it resumes at the
    # failed stage (earlier questions cost no new provider request).
    carried = _carried_baselines(session, encounter_id, fingerprint, requested_keys)
    carried_positions = {index for index, key in enumerate(requested_keys) if key in carried}
    first_eligible = _first_eligible_index(statuses, carried_positions)

    batch_id = uuid.uuid4()
    session.execute(
        insert(reasoning_tables.generation_batches).values(
            id=batch_id,
            encounter_id=encounter_id,
            author_id=author["id"],
            source_revision=expected_revision,
            fingerprint=fingerprint,
            fingerprint_payload=payload,
            pinned_bundle=pinned,
            status=batch_status,
            created_at=moment,
        )
    )
    # S45+S46: freeze each full package verbatim in pinned order with an
    # explicit position (never read from mutable files at execution time).
    # Activating new content never UPDATEs these rows (old batches immutable).
    run_ids: list[uuid.UUID] = []
    for position, (entry, loaded, projection, projection_hash, status, reason) in enumerate(
        zip(ordered_packages, loaded_list, projections, projection_hashes, statuses, reasons)
    ):
        run_id = uuid.uuid4()
        run_ids.append(run_id)
        manifest = manifests[position]
        run_pinned: dict[str, Any] = {
            "question_key": loaded.question_key,
            "package_hash": loaded.package_hash,
            "network_hash": str(manifest.get("network_hash", "")),
            "prompt_version": str(manifest.get("prompt_version", "")),
            "template_version": str(manifest.get("template_version", "")),
            "package_version": str(manifest.get("version", "")),
            "position": int(position),
        }
        frozen_package = dict(entry) if isinstance(entry, dict) else {}
        session.execute(
            insert(reasoning_tables.question_runs).values(
                id=run_id,
                batch_id=batch_id,
                question_key=loaded.question_key,
                status=status,
                gate_reason=reason,
                projection=projection,
                projection_hash=projection_hash,
                fingerprint=fingerprint,
                pinned_versions=run_pinned,
                pinned_package=frozen_package,
                position=int(position),
                created_at=moment,
            )
        )
    session.flush()
    # S47 carried baselines: copy accepted rows onto the new runs (new
    # identities, identical artifact data, retry provenance). Old batches
    # stay immutable history; the new batch resumes at the failed stage.
    for position, run_id in enumerate(run_ids):
        old = carried.get(requested_keys[position])
        if old is None:
            continue
        provenance = dict(old.get("provenance") or {})
        provenance["retried_from_baseline_id"] = str(old.get("id"))
        session.execute(
            insert(reasoning_tables.original_baselines).values(
                id=uuid.uuid4(),
                question_run_id=run_id,
                batch_id=batch_id,
                source_hash=str(old.get("source_hash")),
                effective_xml=str(old.get("effective_xml")),
                effective_hash=str(old.get("effective_hash")),
                raw_response=dict(old.get("raw_response") or {}),
                validated_tables=list(old.get("validated_tables") or []),
                query_nodes=list(old.get("query_nodes") or []),
                posteriors=list(old.get("posteriors") or []),
                section_text=str(old.get("section_text")),
                template_version=str(old.get("template_version")),
                prompt_version=str(old.get("prompt_version")),
                network_version=str(old.get("network_version")),
                provider_model=str(old.get("provider_model")),
                projection_hash=str(old.get("projection_hash")),
                provenance=provenance,
                created_at=moment,
            )
        )
    session.flush()
    # Enqueue only the first eligible ready run (ordered eligibility;
    # not_applicable needs no job, needs_clarification blocks successors;
    # carried runs are already solved).
    if first_eligible is not None:
        queue_module.insert_initial_job(
            session,
            batch_id=batch_id,
            question_run_id=run_ids[first_eligible],
            deployment_generation=int(deployment_generation),
            now=moment,
        )
        session.flush()
    elif batch_status == READY and all(
        position in carried_positions or status != READY for position, status in enumerate(statuses)
    ):
        # Full retry: every applicable question already solved under this
        # fingerprint — assemble the immutable proposal now (same rules as
        # the worker path, no LLM writing).
        from x_insight.reasoning import coordinator as coordinator_module

        coordinator_module.try_assemble_proposal(session, batch_id, moment)
        session.flush()
    # Full DDI report is NOT stored in pinned_bundle (it carries
    # ``generated_at`` which would destabilize the fingerprint). The stable
    # pin (dataset/catalog/medication fingerprint/status) lives in
    # ``pinned_bundle`` above and in the fingerprint; proposal assembly
    # recomputes the full report from frozen payload meds + pinned dataset
    # (never live chart) and stores it immutably in the proposal row.
    audit_module.record_audit(
        session,
        operation="generation.start.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "batch_id": str(batch_id),
            "question_keys": requested_keys,
            "carried_question_keys": sorted(carried),
            "source_revision": expected_revision,
            "status": batch_status,
        },
    )
    batch = _get_batch(session, batch_id)
    assert batch is not None
    runs = _list_runs(session, batch_id)
    return batch, runs


def _get_batch(session: Session, batch_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(reasoning_tables.generation_batches).where(
                reasoning_tables.generation_batches.c.id == batch_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _list_runs(session: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    # S46 pinned order first (position), then key/created for stability.
    # Legacy single-question rows all carry position 0, preserving old order.
    rows = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
                reasoning_tables.question_runs.c.position.asc(),
                reasoning_tables.question_runs.c.question_key.asc(),
                reasoning_tables.question_runs.c.created_at.asc(),
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def get_generation_batch(
    session: Session, batch_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Author-only read of an immutable snapshot plus derived freshness.

    404 when missing, 403 for strangers (no projection content). Old
    snapshots stay readable after later edits; ``freshness.stale`` is
    derived by comparing the stored fingerprint with the current draft
    fingerprint (note-only edits stay fresh, relevant edits go stale).
    Rows are never updated here.
    """
    batch = _get_batch(session, batch_id)
    if batch is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Generation batch not found.")
    if str(batch.get("author_id")) != str(user.get("id")):
        raise contracts.ContractError(
            403, "FORBIDDEN", "Only the draft author can access this batch."
        )
    runs = _list_runs(session, batch_id)
    encounter = encounters_service.get_encounter(session, batch["encounter_id"])
    pinned = batch.get("pinned_bundle") or {}
    if encounter is None or not isinstance(encounter.get("draft_data"), dict):
        freshness = {
            "stale": False,
            "reason": "current",
            "current_fingerprint": str(batch.get("fingerprint")),
        }
        return batch, runs, freshness
    current_fp, _ = compute_analysis_fingerprint(encounter.get("draft_data"), pinned)
    stored_fp = str(batch.get("fingerprint"))
    if current_fp == stored_fp:
        freshness = {
            "stale": False,
            "reason": "current",
            "current_fingerprint": current_fp,
        }
    else:
        freshness = {
            "stale": True,
            "reason": "analysis facts changed since freeze",
            "current_fingerprint": current_fp,
        }
    return batch, runs, freshness
