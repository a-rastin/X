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

# ponytail: single-question proof; multi-question ordered batches land with S46 workflows.
MAX_PROJECTION_VARIABLES = 64


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


def start_generation_batch(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    package: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Freeze one question's snapshot + projection and enqueue its first job.

    Privacy first (404 missing/released, 403 stranger), then the revision
    fence (412 stale changes nothing), then S25 package validation (422
    changes nothing). S44 admission (single active generation per encounter,
    fingerprint reuse, 100-queued cap) runs under the encounter lock plus
    the deployment global lock, so simultaneous triggers serialize. Only a
    fully valid start inserts the immutable batch + run rows plus the first
    eligible queued job atomically — failures create no partial rows.
    Repeated same-fingerprint triggers reuse the existing run (no duplicate
    batch, backfilling a queued job for pre-S44 batches when ready).
    """
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
    manifest = package["manifest"]
    assert isinstance(manifest, Mapping)

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
    pinned: dict[str, Any] = {
        "question_key": loaded.question_key,
        "package_hash": loaded.package_hash,
        "network_hash": str(manifest.get("network_hash", "")),
        "prompt_version": str(manifest.get("prompt_version", "")),
        "template_version": str(manifest.get("template_version", "")),
        "package_version": str(manifest.get("version", "")),
    }
    fingerprint, payload = compute_analysis_fingerprint(draft_data, pinned)
    projection, projection_hash = build_question_projection(manifest, draft_data, expected_revision)
    status, reason = evaluate_gate(manifest, draft_data)

    # S44 admission (lazy import: queue imports snapshots for eligibility).
    from x_insight.reasoning import queue as queue_module

    moment = contracts.utcnow()
    deployment_generation = queue_module.lock_deployment(session)
    reusable = queue_module.check_start_admission(
        session,
        encounter_id=encounter_id,
        fingerprint=fingerprint,
        question_key=loaded.question_key,
    )
    if reusable is not None:
        queue_module.ensure_job_for_reused_batch(
            session,
            batch=reusable,
            question_key=loaded.question_key,
            deployment_generation=int(deployment_generation),
            now=moment,
        )
        batch = _get_batch(session, reusable["id"])
        assert batch is not None
        runs = _list_runs(session, reusable["id"])
        return batch, runs

    batch_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session.execute(
        insert(reasoning_tables.generation_batches).values(
            id=batch_id,
            encounter_id=encounter_id,
            author_id=author["id"],
            source_revision=expected_revision,
            fingerprint=fingerprint,
            fingerprint_payload=payload,
            pinned_bundle=pinned,
            status=status,
            created_at=moment,
        )
    )
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
            pinned_versions=pinned,
            created_at=moment,
        )
    )
    session.flush()
    if status == READY:
        queue_module.insert_initial_job(
            session,
            batch_id=batch_id,
            question_run_id=run_id,
            deployment_generation=int(deployment_generation),
            now=moment,
        )
        session.flush()
    audit_module.record_audit(
        session,
        operation="generation.start.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "batch_id": str(batch_id),
            "question_key": loaded.question_key,
            "source_revision": expected_revision,
            "status": status,
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
    rows = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
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
