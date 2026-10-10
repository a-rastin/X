"""CPT revision persistence + local recalculation (S48a+S48b, seams T1/T5/T8).

Plan.md §9.1 + system-design.md §§5, 8.2-8.4, 9 (FR-50–56, FR-58, FR-43):
S48a persists immutable adjustments with deterministic redistribution;
S48b queues revision-bound local jobs (no provider/MCP), executes only the
fixed network/template/configuration with empty evidence, fences older
responses, and supports explicit baseline-reuse reset + local-only retry.
All persistence uses the caller's transaction (never commits here).
Original baselines and shared XML are read-only (SELECT only, never UPDATE
except the review-state pointer). Calculation state and displayed IDs
derive on read (revisions + results + local jobs); acceptance clearing is
the pointer move (old exact references no longer match; S48c owns the
acceptance table).
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.operations import audit as audit_module
from x_insight.probability_review import redistribution as redistribution_module
from x_insight.probability_review import tables as review_tables

ADJUSTMENT_IDEMPOTENCY_OPERATION = "cpt_adjustments"
ADJUSTMENT_AUDIT_OPERATION = "cpt_adjustment.success"
RESET_IDEMPOTENCY_OPERATION = "cpt_reset"
RESET_AUDIT_OPERATION = "cpt_reset.success"
RETRY_IDEMPOTENCY_OPERATION = "cpt_retry_calculation"
ACCEPTANCE_IDEMPOTENCY_OPERATION = "prob_acceptance"
ACCEPTANCE_AUDIT_OPERATION = "prob_acceptance.success"

RESULT_KIND_BASELINE = "baseline"
RESULT_KIND_CALCULATION = "calculation"

INITIAL_REVIEW_REVISION = 1

CALCULATION_STATES = (
    "unchanged",
    "recalculating",
    "successfully_recalculated",
    "failed",
)


def _fail(status: int, code: str, message: str, field: str) -> contracts.ContractError:
    return contracts.ContractError(status, code, message, {field: [message]})


def adjustment_request_hash(
    run_id: Any,
    expected_review_revision: int,
    node_id: str,
    parent_states: list[str],
    state: str,
    target_percentage: str,
) -> str:
    """Canonical idempotency hash for one adjustment command body."""
    return contracts.canonical_hash(
        {
            "run_id": str(run_id),
            "expected_review_revision": int(expected_review_revision),
            "node_id": str(node_id),
            "parent_states": [str(s) for s in list(parent_states)],
            "state": str(state),
            "target_percentage": str(target_percentage),
        }
    )


def safe_revision(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public revision shape (persisted data only, no secrets)."""
    return {
        "id": str(row.get("id")),
        "question_run_id": str(row.get("question_run_id")),
        "batch_id": str(row.get("batch_id")),
        "parent_revision_id": (
            str(row["parent_revision_id"]) if row.get("parent_revision_id") is not None else None
        ),
        "sequence": int(row.get("sequence", 0)),
        "kind": str(row.get("kind", "adjustment")),
        "cpt_hash": str(row.get("cpt_hash", "")),
        "cpt_artifact": list(row.get("cpt_artifact") or []),
        "direct_edit": dict(row.get("direct_edit") or {}),
        "before_row": dict(row.get("before_row") or {}),
        "after_row": dict(row.get("after_row") or {}),
        "actor_username": str(row.get("actor_username", "")),
        "redistribution_version": str(
            row.get("redistribution_version", redistribution_module.REDISTRIBUTION_RULE_VERSION)
        ),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def safe_review_state(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "question_run_id": str(row.get("question_run_id")),
        "batch_id": str(row.get("batch_id")),
        "current_revision_id": (
            str(row["current_revision_id"]) if row.get("current_revision_id") is not None else None
        ),
        "review_revision": int(row.get("review_revision", INITIAL_REVIEW_REVISION)),
        "updated_at": contracts.serialize_utc(row["updated_at"]),
    }


def get_review_state(session: Session, run_id: Any) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(review_tables.question_review_states).where(
                review_tables.question_review_states.c.question_run_id == run_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_revisions(session: Session, run_id: Any) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(review_tables.cpt_revisions)
            .where(review_tables.cpt_revisions.c.question_run_id == run_id)
            .order_by(review_tables.cpt_revisions.c.sequence.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def get_latest_revision(session: Session, run_id: Any) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(review_tables.cpt_revisions)
            .where(review_tables.cpt_revisions.c.question_run_id == run_id)
            .order_by(review_tables.cpt_revisions.c.sequence.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def current_tables_for_run(
    baseline: Mapping[str, Any] | None, latest_revision: Mapping[str, Any] | None
) -> list[dict[str, Any]] | None:
    """Current committed CPT tables (revision artifact or baseline, else None)."""
    if latest_revision is not None:
        artifact = latest_revision.get("cpt_artifact")
        return [dict(t) for t in list(artifact or [])] if isinstance(artifact, list) else None
    if baseline is None:
        return None
    validated = baseline.get("validated_tables")
    return [dict(t) for t in list(validated or [])] if isinstance(validated, list) else None


def _validate_adjustment_inputs(
    node_id: Any,
    parent_states: Any,
    state: Any,
    target_percentage: Any,
    expected_review_revision: Any,
) -> tuple[str, list[str], str, str, int]:
    if not isinstance(node_id, str) or not node_id.strip():
        raise _fail(422, "VALIDATION_FAILED", "node_id must be non-empty text.", "node_id")
    if not isinstance(parent_states, list) or not all(isinstance(s, str) for s in parent_states):
        raise _fail(
            422, "VALIDATION_FAILED", "parent_states must list parent states.", "parent_states"
        )
    if not isinstance(state, str) or not state.strip():
        raise _fail(422, "VALIDATION_FAILED", "state must be non-empty text.", "state")
    if not isinstance(target_percentage, str) or not target_percentage.strip():
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "target_percentage must be a decimal string.",
            "target_percentage",
        )
    if not isinstance(expected_review_revision, int) or expected_review_revision < 1:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "expected_review_revision must be a positive integer.",
            "expected_review_revision",
        )
    return (
        str(node_id),
        [str(s) for s in parent_states],
        str(state),
        str(target_percentage),
        int(expected_review_revision),
    )


def _require_draft_encounter(session: Session, batch: Mapping[str, Any]) -> None:
    """S49 §4: signed encounters are immutable — no probability mutation.

    Draft routes already 404 for signed; this fences adjustment/reset/retry/
    acceptance after signing (409, no content leak — author already checked).
    """
    from x_insight.cases import tables as cases_tables

    row = (
        session.execute(
            select(cases_tables.encounters).where(
                cases_tables.encounters.c.id == batch.get("encounter_id")
            )
        )
        .mappings()
        .first()
    )
    if row is not None and str(dict(row).get("lifecycle")) != "draft":
        raise contracts.ContractError(409, "ENCOUNTER_SIGNED", "Encounter is signed and immutable.")


def apply_adjustment(
    session: Session,
    *,
    author: Mapping[str, Any],
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    node_id: Any,
    parent_states: Any,
    state: Any,
    target_percentage: Any,
    expected_review_revision: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist one completed slider command (caller's transaction).

    Validates author-fresh inputs, redistributes from the immediately
    preceding committed row via ``redistribution`` (integer units only),
    inserts one immutable ``cpt_revisions`` row, bumps the
    ``question_review_states`` pointer, and audits atomically. Baseline
    rows are never updated. Raises ``ContractError`` (422 invalid,
    412 stale) without repairing input.
    """
    _require_draft_encounter(session, batch)
    clean_node, clean_parents, clean_state, clean_target, expected = _validate_adjustment_inputs(
        node_id, parent_states, state, target_percentage, expected_review_revision
    )
    if baseline is None:
        raise contracts.ContractError(
            422,
            "ADJUSTMENT_NOT_AVAILABLE",
            "No adjustable baseline exists for this question run.",
            {"question_run": ["No successful original baseline to adjust."]},
        )
    # Optimistic pointer with a row lock so multi-tab stale writes conflict.
    locked_state_row = (
        session.execute(
            select(review_tables.question_review_states)
            .where(review_tables.question_review_states.c.question_run_id == run["id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    locked_state = dict(locked_state_row) if locked_state_row is not None else None
    current_review = (
        int(locked_state["review_revision"])
        if locked_state is not None
        else INITIAL_REVIEW_REVISION
    )
    if int(expected) != int(current_review):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The probability review changed. Reload and reconcile your edits.",
            {"expected_review_revision": ["Stale review revision."]},
        )
    latest = get_latest_revision(session, run["id"])
    base_tables = current_tables_for_run(baseline, latest)
    if not isinstance(base_tables, list) or not base_tables:
        raise contracts.ContractError(
            422,
            "ADJUSTMENT_NOT_AVAILABLE",
            "No adjustable baseline exists for this question run.",
            {"question_run": ["No successful original baseline to adjust."]},
        )
    # Locate the target table + row (full parent assignment required).
    table_index: int | None = None
    for index, entry in enumerate(base_tables):
        if isinstance(entry, Mapping) and str(entry.get("node_id")) == clean_node:
            table_index = index
            break
    if table_index is None:
        raise _fail(422, "VALIDATION_FAILED", f"Unknown node {clean_node!r}.", "node_id")
    table = base_tables[table_index]
    if not isinstance(table, Mapping):
        raise _fail(422, "VALIDATION_FAILED", f"Unknown node {clean_node!r}.", "node_id")
    table_states = list(table.get("states") or [])
    table_rows = list(table.get("rows") or [])
    if clean_state not in table_states:
        raise _fail(422, "VALIDATION_FAILED", f"Unknown state {clean_state!r}.", "state")
    row_index: int | None = None
    for index, entry in enumerate(table_rows):
        if (
            isinstance(entry, Mapping)
            and [str(s) for s in list(entry.get("parent_states") or [])] == clean_parents
        ):
            row_index = index
            break
    if row_index is None:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "parent_states must match one complete parent assignment.",
            "parent_states",
        )
    row = table_rows[row_index]
    assert isinstance(row, Mapping)
    preceding_strings = [str(v) for v in list(row.get("percentages") or [])]
    if len(preceding_strings) != len(table_states):
        raise _fail(422, "VALIDATION_FAILED", "Stored row shape drifted.", "parent_states")
    try:
        preceding_units = [
            redistribution_module.parse_percentage_to_units(token) for token in preceding_strings
        ]
        target_units = redistribution_module.parse_percentage_to_units(clean_target)
    except redistribution_module.RedistributionError as exc:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", str(exc), {"target_percentage": [str(exc)]}
        ) from exc
    selected_index = table_states.index(clean_state)
    try:
        new_units = redistribution_module.redistribute_row(
            list(preceding_units), int(selected_index), int(target_units)
        )
    except redistribution_module.RedistributionError as exc:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", str(exc), {"target_percentage": [str(exc)]}
        ) from exc
    new_percentages = [redistribution_module.format_units_to_percentage(u) for u in new_units]
    # Complete new artifact: deep copy, only the target row changes.
    new_tables = copy.deepcopy(base_tables)
    new_table = dict(new_tables[table_index])
    new_rows = [dict(r) if isinstance(r, Mapping) else r for r in list(new_table.get("rows") or [])]
    new_row = dict(new_rows[row_index])
    new_row["parent_states"] = list(clean_parents)
    new_row["percentages"] = list(new_percentages)
    new_rows[row_index] = new_row
    new_table["rows"] = new_rows
    new_tables[table_index] = new_table
    cpt_hash = contracts.canonical_hash(new_tables)
    if latest is None:
        parent_id: Any = None
        sequence = 1
    else:
        parent_id = latest["id"]
        sequence = int(latest.get("sequence", 0)) + 1
    moment = contracts.utcnow()
    revision_id = uuid.uuid4()
    direct_edit = {
        "node_id": clean_node,
        "parent_states": list(clean_parents),
        "state": clean_state,
        "target_percentage": clean_target,
        "target_units": int(target_units),
    }
    before_row = {
        "node_id": clean_node,
        "parent_states": list(clean_parents),
        "percentages": list(preceding_strings),
        "units": list(preceding_units),
    }
    after_row = {
        "node_id": clean_node,
        "parent_states": list(clean_parents),
        "percentages": list(new_percentages),
        "units": list(new_units),
    }
    session.execute(
        insert(review_tables.cpt_revisions).values(
            id=revision_id,
            question_run_id=run["id"],
            batch_id=run["batch_id"],
            parent_revision_id=parent_id,
            sequence=int(sequence),
            kind="adjustment",
            cpt_artifact=list(new_tables),
            cpt_hash=str(cpt_hash),
            direct_edit=dict(direct_edit),
            before_row=dict(before_row),
            after_row=dict(after_row),
            actor_id=author["id"],
            actor_username=str(author.get("username", "")),
            redistribution_version=redistribution_module.REDISTRIBUTION_RULE_VERSION,
            created_at=moment,
        )
    )
    session.flush()
    if locked_state is None:
        session.execute(
            insert(review_tables.question_review_states).values(
                question_run_id=run["id"],
                batch_id=run["batch_id"],
                current_revision_id=revision_id,
                review_revision=int(expected) + 1,
                created_at=moment,
                updated_at=moment,
            )
        )
    else:
        session.execute(
            update(review_tables.question_review_states)
            .where(review_tables.question_review_states.c.question_run_id == run["id"])
            .values(
                current_revision_id=revision_id,
                review_revision=int(expected) + 1,
                updated_at=moment,
            )
        )
    session.flush()
    audit_module.record_audit(
        session,
        operation=ADJUSTMENT_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(batch.get("encounter_id")),
            "batch_id": str(run.get("batch_id")),
            "question_run_id": str(run.get("id")),
            "question_key": str(run.get("question_key", "")),
            "revision_id": str(revision_id),
            "parent_revision_id": str(parent_id) if parent_id is not None else None,
            "sequence": int(sequence),
            "kind": "adjustment",
            "node_id": clean_node,
            "parent_states": list(clean_parents),
            "state": clean_state,
            "target_percentage": clean_target,
            "before_row": dict(before_row),
            "after_row": dict(after_row),
            "cpt_hash": str(cpt_hash),
            "review_revision": int(expected) + 1,
            "redistribution_version": redistribution_module.REDISTRIBUTION_RULE_VERSION,
        },
    )
    # S48b: atomically clear acceptance (pointer move invalidates old
    # exact-revision references; no acceptance table exists until S48c),
    # mark recalculating and queue the exact saved revision/hash. Same
    # transaction as the revision so acknowledgment always has a job.
    # Local jobs need no provider/MCP context and touch no other run.
    from x_insight.reasoning import queue as queue_module

    try:
        deployment = queue_module.get_deployment_generation(session)
    except Exception:
        deployment = 1
    queue_module.insert_local_job(
        session,
        batch_id=run["batch_id"],
        question_run_id=run["id"],
        cpt_revision_id=revision_id,
        cpt_hash=str(cpt_hash),
        deployment_generation=int(deployment),
        now=moment,
    )
    session.flush()
    created = (
        session.execute(
            select(review_tables.cpt_revisions).where(
                review_tables.cpt_revisions.c.id == revision_id
            )
        )
        .mappings()
        .first()
    )
    assert created is not None
    revision = dict(created)
    state_row = (
        session.execute(
            select(review_tables.question_review_states).where(
                review_tables.question_review_states.c.question_run_id == run["id"]
            )
        )
        .mappings()
        .first()
    )
    assert state_row is not None
    new_state = dict(state_row)
    return revision, new_state


# --- S48b result storage + derivation (seams T1/T8) ---


def safe_calculation_result(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public calculation-result shape (persisted data only, no secrets)."""
    reused = row.get("reused_from_baseline_id")
    return {
        "id": str(row.get("id")),
        "question_run_id": str(row.get("question_run_id")),
        "batch_id": str(row.get("batch_id")),
        "cpt_revision_id": str(row.get("cpt_revision_id")),
        "cpt_hash": str(row.get("cpt_hash", "")),
        "network_hash": str(row.get("network_hash", "")),
        "network_version": str(row.get("network_version", "")),
        "template_version": str(row.get("template_version", "")),
        "query_nodes": list(row.get("query_nodes") or []),
        "posteriors": list(row.get("posteriors") or []),
        "section_text": str(row.get("section_text", "")),
        "effective_hash": str(row.get("effective_hash", "")),
        "effective_xml": str(row.get("effective_xml", "")),
        "reused_from_baseline_id": str(reused) if reused is not None else None,
        "provenance": dict(row.get("provenance") or {}),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def list_calculation_results(session: Session, run_id: Any) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(review_tables.calculation_results)
            .where(review_tables.calculation_results.c.question_run_id == run_id)
            .order_by(review_tables.calculation_results.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def get_calculation_result_for_revision(
    session: Session, revision_id: Any
) -> dict[str, Any] | None:
    try:
        want = uuid.UUID(str(revision_id))
    except Exception:
        return None
    row = (
        session.execute(
            select(review_tables.calculation_results).where(
                review_tables.calculation_results.c.cpt_revision_id == want
            )
        )
        .mappings()
        .first()
    )
    if row is not None:
        return dict(row)
    # Fallback string compare for UUID typing edges.
    run_rows = session.execute(select(review_tables.calculation_results)).mappings().all()
    for entry in run_rows:
        if str(dict(entry).get("cpt_revision_id")) == str(revision_id):
            return dict(entry)
    return None


def reset_request_hash(run_id: Any, expected_review_revision: int) -> str:
    return contracts.canonical_hash(
        {"run_id": str(run_id), "expected_review_revision": int(expected_review_revision)}
    )


def retry_request_hash(run_id: Any, expected_review_revision: int, revision_id: Any | None) -> str:
    return contracts.canonical_hash(
        {
            "run_id": str(run_id),
            "expected_review_revision": int(expected_review_revision),
            "cpt_revision_id": str(revision_id) if revision_id is not None else None,
        }
    )


def apply_reset(
    session: Session,
    *,
    author: Mapping[str, Any],
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    expected_review_revision: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create an audited baseline-equal reset revision with verified reuse.

    Restores every CPT row for this run from the immutable baseline,
    clears acceptance via the pointer move, fences older in-flight
    responses (they compare against the new pointer on commit), retains
    history, and immediately binds the reset revision to the retained
    original result (explicit verified reuse, no fresh execution). Other
    runs/batches/DDI/generation are untouched. Stale inputs stay stale
    (reset never regenerates); the caller still blocks acceptance/signing
    on freshness. Raises ContractError (422 no baseline, 412 stale).
    Returns (revision, review_state, calculation_result).
    """
    _require_draft_encounter(session, batch)
    if not isinstance(expected_review_revision, int) or expected_review_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_review_revision must be a positive integer.",
            {"expected_review_revision": ["Must be a positive integer."]},
        )
    expected = int(expected_review_revision)
    if baseline is None:
        raise contracts.ContractError(
            422,
            "ADJUSTMENT_NOT_AVAILABLE",
            "No adjustable baseline exists for this question run.",
            {"question_run": ["No successful original baseline to reset to."]},
        )
    locked_state_row = (
        session.execute(
            select(review_tables.question_review_states)
            .where(review_tables.question_review_states.c.question_run_id == run["id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    locked_state = dict(locked_state_row) if locked_state_row is not None else None
    current_review = (
        int(locked_state["review_revision"])
        if locked_state is not None
        else INITIAL_REVIEW_REVISION
    )
    if int(expected) != int(current_review):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The probability review changed. Reload and reconcile your edits.",
            {"expected_review_revision": ["Stale review revision."]},
        )
    latest = get_latest_revision(session, run["id"])
    validated = baseline.get("validated_tables")
    if not isinstance(validated, list) or not validated:
        raise contracts.ContractError(
            422,
            "ADJUSTMENT_NOT_AVAILABLE",
            "No adjustable baseline exists for this question run.",
            {"question_run": ["No successful original baseline to reset to."]},
        )
    new_tables = copy.deepcopy([dict(t) for t in list(validated)])
    cpt_hash = contracts.canonical_hash(new_tables)
    if latest is None:
        parent_id: Any = None
        sequence = 1
    else:
        parent_id = latest["id"]
        sequence = int(latest.get("sequence", 0)) + 1
    moment = contracts.utcnow()
    revision_id = uuid.uuid4()
    # Verify reuse: reset artifact must equal the baseline tables exactly.
    if contracts.canonical_hash(list(validated)) != cpt_hash:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "Reset artifact does not match the baseline."
        )
    direct_edit = {"reset": True, "baseline_id": str(baseline.get("id"))}
    before_hash = str(latest["cpt_hash"]) if latest is not None else None
    before_row = {"cpt_hash": before_hash, "kind": "previous"}
    after_row = {"cpt_hash": str(cpt_hash), "kind": "baseline_equal"}
    session.execute(
        insert(review_tables.cpt_revisions).values(
            id=revision_id,
            question_run_id=run["id"],
            batch_id=run["batch_id"],
            parent_revision_id=parent_id,
            sequence=int(sequence),
            kind="reset",
            cpt_artifact=list(new_tables),
            cpt_hash=str(cpt_hash),
            direct_edit=dict(direct_edit),
            before_row=dict(before_row),
            after_row=dict(after_row),
            actor_id=author["id"],
            actor_username=str(author.get("username", "")),
            redistribution_version=redistribution_module.REDISTRIBUTION_RULE_VERSION,
            created_at=moment,
        )
    )
    session.flush()
    if locked_state is None:
        session.execute(
            insert(review_tables.question_review_states).values(
                question_run_id=run["id"],
                batch_id=run["batch_id"],
                current_revision_id=revision_id,
                review_revision=int(expected) + 1,
                created_at=moment,
                updated_at=moment,
            )
        )
    else:
        session.execute(
            update(review_tables.question_review_states)
            .where(review_tables.question_review_states.c.question_run_id == run["id"])
            .values(
                current_revision_id=revision_id,
                review_revision=int(expected) + 1,
                updated_at=moment,
            )
        )
    session.flush()
    # Explicit verified reuse: copy the immutable baseline output (same
    # values + same network/template/query) without a new execution.
    baseline_posteriors = list(baseline.get("posteriors") or [])
    baseline_section = str(baseline.get("section_text", ""))
    baseline_effective = str(baseline.get("effective_hash", ""))
    baseline_xml = str(baseline.get("effective_xml", ""))
    baseline_query = list(baseline.get("query_nodes") or [])
    session.execute(
        insert(review_tables.calculation_results).values(
            id=uuid.uuid4(),
            question_run_id=run["id"],
            batch_id=run["batch_id"],
            cpt_revision_id=revision_id,
            cpt_hash=str(cpt_hash),
            network_hash=str(baseline.get("source_hash", "")),
            network_version=str(baseline.get("network_version", "")),
            template_version=str(baseline.get("template_version", "")),
            query_nodes=list(baseline_query),
            posteriors=list(baseline_posteriors),
            section_text=str(baseline_section),
            effective_hash=str(baseline_effective),
            effective_xml=str(baseline_xml),
            reused_from_baseline_id=baseline.get("id"),
            provenance={
                "reuse": "verified_baseline",
                "baseline_id": str(baseline.get("id")),
                "question_key": str(run.get("question_key", "")),
                "actor": str(author.get("username", "")),
            },
            created_at=moment,
        )
    )
    session.flush()
    audit_module.record_audit(
        session,
        operation=RESET_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(batch.get("encounter_id")),
            "batch_id": str(run.get("batch_id")),
            "question_run_id": str(run.get("id")),
            "question_key": str(run.get("question_key", "")),
            "revision_id": str(revision_id),
            "parent_revision_id": str(parent_id) if parent_id is not None else None,
            "sequence": int(sequence),
            "kind": "reset",
            "baseline_id": str(baseline.get("id")),
            "cpt_hash": str(cpt_hash),
            "review_revision": int(expected) + 1,
            "reuse": "verified_baseline",
        },
    )
    created = (
        session.execute(
            select(review_tables.cpt_revisions).where(
                review_tables.cpt_revisions.c.id == revision_id
            )
        )
        .mappings()
        .first()
    )
    assert created is not None
    revision = dict(created)
    state_row = (
        session.execute(
            select(review_tables.question_review_states).where(
                review_tables.question_review_states.c.question_run_id == run["id"]
            )
        )
        .mappings()
        .first()
    )
    assert state_row is not None
    new_state = dict(state_row)
    result_row = (
        session.execute(
            select(review_tables.calculation_results).where(
                review_tables.calculation_results.c.cpt_revision_id == revision_id
            )
        )
        .mappings()
        .first()
    )
    assert result_row is not None
    return revision, new_state, dict(result_row)


def ensure_retry_job(
    session: Session,
    *,
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    expected_review_revision: Any,
    revision_id: Any | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    """Queue (or reuse) the local job for exactly the current revision.

    Validates the optimistic pointer (412 when stale) and, when a revision
    id is supplied, that it equals the current pointer (409 when
    superseded). Targets the current saved revision only; never
    re-estimates CPTs. Idempotent: queued/leased jobs for the revision
    reuse; terminal jobs requeue on the same row. Raises ContractError
    (422 no revision/baseline, 412 stale, 409 superseded).
    """
    from x_insight.reasoning import queue as queue_module

    _require_draft_encounter(session, batch)
    if not isinstance(expected_review_revision, int) or expected_review_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_review_revision must be a positive integer.",
            {"expected_review_revision": ["Must be a positive integer."]},
        )
    expected = int(expected_review_revision)
    moment = now or contracts.utcnow()
    state = get_review_state(session, run["id"])
    current_review = int(state["review_revision"]) if state is not None else INITIAL_REVIEW_REVISION
    if int(expected) != int(current_review):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The probability review changed. Reload and reconcile your edits.",
            {"expected_review_revision": ["Stale review revision."]},
        )
    latest = get_latest_revision(session, run["id"])
    if latest is None:
        raise contracts.ContractError(
            422,
            "RETRY_NOT_AVAILABLE",
            "No adjusted revision exists to retry.",
            {"question_run": ["Nothing to retry."]},
        )
    current_id = str(latest["id"])
    if revision_id is not None and str(revision_id) != current_id:
        raise contracts.ContractError(
            409,
            "REVISION_SUPERSEDED",
            "The revision was superseded. Retry the current revision.",
            {"cpt_revision_id": ["Superseded revision."]},
        )
    existing = queue_module.find_local_job_for_revision(session, run["id"], current_id)
    if existing is not None:
        if str(existing.get("status")) in ("queued", "leased"):
            return existing
        if str(existing.get("status")) == "succeeded":
            # Already solved: idempotent retry reuses (no duplicate work).
            # Only failed/cancelled terminal rows requeue on the same row.
            solved = get_calculation_result_for_revision(session, current_id)
            if solved is not None:
                return existing
        return queue_module.requeue_local_job(session, existing["id"], now=moment)
    try:
        deployment = queue_module.get_deployment_generation(session)
    except Exception:
        deployment = 1
    return queue_module.insert_local_job(
        session,
        batch_id=run["batch_id"],
        question_run_id=run["id"],
        cpt_revision_id=latest["id"],
        cpt_hash=str(latest.get("cpt_hash", "")),
        deployment_generation=int(deployment),
        now=moment,
    )


def derive_calculation_view(
    session: Session,
    *,
    run: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    revisions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    local_jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive truthful calculation state + displayed IDs (no stored columns).

    - ``unchanged``: baseline exists, no revisions (valid corresponding
      result is the baseline itself).
    - ``successfully_recalculated``: current revision has a successful
      matching result.
    - ``recalculating``: current revision is queued/leased (sliders stay
      responsive, acceptance blocked).
    - ``failed``: current revision preserved but unsolved (error in the
      current job, or no job and no success); earlier success stays
      separately labeled via ``displayed_*`` (null when the baseline is
      the displayed success). Never labels an earlier result as solving
      current CPTs (``current_result_matches`` false until solved).
    """
    by_revision: dict[str, dict[str, Any]] = {}
    for entry in results:
        by_revision[str(entry.get("cpt_revision_id"))] = entry
    seq_by_revision: dict[str, int] = {}
    for entry in revisions:
        try:
            seq_by_revision[str(entry.get("id"))] = int(entry.get("sequence", 0))
        except Exception:
            seq_by_revision[str(entry.get("id"))] = 0
    if not revisions:
        if baseline is None:
            status = str(run.get("status", ""))
            if status in ("not_applicable", "needs_clarification"):
                state = "unchanged"
            else:
                state = "failed"
            return {
                "calculation_state": state,
                "displayed_result_revision_id": None,
                "current_result_matches": False if state == "failed" else True,
                "calculation_result": None,
                "displayed_result": None,
            }
        return {
            "calculation_state": "unchanged",
            "displayed_result_revision_id": None,
            "current_result_matches": True,
            "calculation_result": None,
            "displayed_result": None,
        }
    current_id = str(revisions[-1].get("id"))
    current_result = by_revision.get(current_id)
    if current_result is not None:
        safe = safe_calculation_result(current_result)
        return {
            "calculation_state": "successfully_recalculated",
            "displayed_result_revision_id": current_id,
            "current_result_matches": True,
            "calculation_result": safe,
            "displayed_result": safe,
        }
    # Current unsolved: find latest successful revision before current.
    current_seq = seq_by_revision.get(current_id, 0)
    earlier: dict[str, Any] | None = None
    earlier_seq = -1
    for rev_id, result in by_revision.items():
        seq = seq_by_revision.get(rev_id, -1)
        if seq < current_seq and seq > earlier_seq:
            earlier = result
            earlier_seq = seq
    displayed_id = str(earlier.get("cpt_revision_id")) if earlier is not None else None
    displayed_safe = safe_calculation_result(earlier) if earlier is not None else None
    # Queued/leased for current => recalculating, else failed.
    active = False
    for job in local_jobs:
        raw_diag: Any = job.get("diagnostics")
        diag: dict[str, Any] = raw_diag if isinstance(raw_diag, dict) else {}
        if str(diag.get("cpt_revision_id")) != current_id:
            continue
        if str(job.get("status")) in ("queued", "leased"):
            active = True
            break
    return {
        "calculation_state": "recalculating" if active else "failed",
        "displayed_result_revision_id": displayed_id,
        "current_result_matches": False,
        "calculation_result": None,
        "displayed_result": displayed_safe,
    }


def execute_local_revision(
    revision: Mapping[str, Any],
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    worker_id: str,
) -> dict[str, Any]:
    """Run only the fixed network/template/configuration with empty evidence.

    Uses the saved revision CPTs + the run's pinned package (network XML,
    template, query, versions). No provider/MCP access, no DDI, no other
    run. Raises ``ValueError("<CODE>: ...")`` with a stable error code on
    numerical/resource/template failures (caller marks the job failed and
    preserves current CPTs + earlier success).
    """
    from x_insight.models.inference import (
        build_effective_artifact,
        infer_effective,
        validate_cpts,
    )
    from x_insight.reasoning import coordinator as coordinator_module

    package: Any = run.get("pinned_package")
    if not isinstance(package, dict) or not isinstance(package.get("manifest"), Mapping):
        raise ValueError("MISSING_PACKAGE: run has no pinned package")
    try:
        document = coordinator_module.load_document(package)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    manifest = package.get("manifest")
    template = package.get("template")
    if not isinstance(manifest, Mapping) or not isinstance(template, Mapping):
        raise ValueError("PACKAGE_INVALID: manifest/template missing")
    artifact_tables = revision.get("cpt_artifact")
    if not isinstance(artifact_tables, list) or not artifact_tables:
        raise ValueError("CPT_INVALID: revision holds no tables")
    network_hash = str(manifest.get("network_hash", ""))
    payload = {"network_hash": network_hash, "tables": list(artifact_tables)}
    try:
        report = validate_cpts(document, payload)
    except Exception as exc:
        raise ValueError(f"CPT_INVALID: {exc}") from exc
    if not report.valid:
        first = report.errors[0] if report.errors else None
        code = str(getattr(first, "code", "CPT_INVALID") or "CPT_INVALID").upper()
        raise ValueError(f"{code}: validated CPTs failed")
    query_nodes = list(manifest.get("query_nodes", [])) or None
    try:
        artifact = build_effective_artifact(document, payload, query_nodes=query_nodes or None)
    except Exception as exc:
        code = str(getattr(exc, "code", "EFFECTIVE_INVALID") or "EFFECTIVE_INVALID").upper()
        raise ValueError(f"{code}: effective artifact failed") from exc
    try:
        inference = infer_effective(artifact, patient_projection=run.get("projection"))
    except Exception as exc:
        code = str(getattr(exc, "code", "INFERENCE_FAILED") or "INFERENCE_FAILED").upper()
        raise ValueError(f"{code}: local inference failed") from exc
    declared: dict[str, Any] = {}
    variables = manifest.get("variables", [])
    if isinstance(variables, (list, tuple)):
        for entry in variables:
            if isinstance(entry, Mapping) and isinstance(entry.get("node_id"), str):
                states = entry.get("states", [])
                if isinstance(states, (list, tuple)):
                    declared[str(entry["node_id"])] = [str(s) for s in states]
    try:
        section = coordinator_module.render_section(template, list(inference.posteriors), declared)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    posteriors_json: list[dict[str, Any]] = []
    for post in inference.posteriors:
        if isinstance(post, Mapping):
            posteriors_json.append(
                {
                    "node_id": str(post.get("node_id", "")),
                    "states": [str(s) for s in list(post.get("states", []))],
                    "probabilities": [float(v) for v in list(post.get("probabilities", []))],
                }
            )
        else:
            posteriors_json.append(
                {
                    "node_id": str(getattr(post, "node_id", "")),
                    "states": [str(s) for s in list(getattr(post, "states", []))],
                    "probabilities": [float(v) for v in list(getattr(post, "probabilities", []))],
                }
            )
    try:
        effective_text = bytes(artifact.effective_bytes).decode("utf-8")
    except Exception as exc:
        raise ValueError("EFFECTIVE_INVALID: effective bytes not UTF-8") from exc
    query_list = (
        list(query_nodes)
        if isinstance(query_nodes, list) and query_nodes
        else list(artifact.query_nodes)
    )
    return {
        "cpt_hash": str(revision.get("cpt_hash", "")),
        "network_hash": str(network_hash),
        "network_version": str(manifest.get("version", "v1")),
        "template_version": str(manifest.get("template_version", "")),
        "query_nodes": [str(q) for q in list(query_list)],
        "posteriors": posteriors_json,
        "section_text": str(section),
        "effective_hash": str(artifact.effective_sha256),
        "effective_xml": str(effective_text),
        "reused_from_baseline_id": None,
        "provenance": {
            "question_key": str(run.get("question_key", "")),
            "cpt_revision_id": str(revision.get("id")),
            "worker_id": str(worker_id),
            "engine": "local_empty_evidence",
        },
    }


# --- S48c exact current-result acceptance (seam T1) ---


def safe_acceptance(row: Mapping[str, Any], *, question_key: str = "") -> dict[str, Any]:
    """Public acceptance shape (persisted data only, no secrets)."""
    revision_id = row.get("cpt_revision_id")
    return {
        "id": str(row.get("id")),
        "encounter_id": str(row.get("encounter_id")),
        "question_run_id": str(row.get("question_run_id")),
        "batch_id": str(row.get("batch_id")),
        "question_key": str(row.get("question_key", question_key)),
        "baseline_id": str(row.get("baseline_id")),
        "cpt_revision_id": str(revision_id) if revision_id is not None else None,
        "cpt_hash": str(row.get("cpt_hash", "")),
        "result_kind": str(row.get("result_kind", "")),
        "result_id": str(row.get("result_id")),
        "input_hash": str(row.get("input_hash", "")),
        "projection_hash": str(row.get("projection_hash", "")),
        "actor_username": str(row.get("actor_username", "")),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def list_acceptances(session: Session, run_id: Any) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(review_tables.probability_acceptances)
            .where(review_tables.probability_acceptances.c.question_run_id == run_id)
            .order_by(review_tables.probability_acceptances.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def acceptance_request_hash(
    run_id: Any,
    expected_review_revision: int,
    baseline_id: Any,
    cpt_revision_id: Any | None,
    cpt_hash: Any,
    result_id: Any,
    input_hash: Any,
) -> str:
    return contracts.canonical_hash(
        {
            "run_id": str(run_id),
            "expected_review_revision": int(expected_review_revision),
            "baseline_id": str(baseline_id),
            "cpt_revision_id": str(cpt_revision_id) if cpt_revision_id is not None else None,
            "cpt_hash": str(cpt_hash),
            "result_id": str(result_id),
            "input_hash": str(input_hash),
        }
    )


def _parse_acceptance_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            f"{field} must be a UUID.",
            {field: ["Must be a UUID."]},
        ) from exc


def _live_acceptance_point(
    session: Session,
    *,
    run: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Exact current references an acceptance must carry (or why none exists).

    Returns ``{"ok": True, baseline_id, cpt_revision_id|None, cpt_hash,
    result_kind, result_id, question_key}`` for an acceptable current state,
    else ``{"ok": False, "code", "message"}`` with a 409 code/message naming
    the state (no successful original, recalculating, failed). Callers
    compare every client-supplied reference against the ``ok`` point and
    reject the first mismatch (409); the point itself is never trusted from
    the client.
    """
    from x_insight.reasoning import queue as queue_module

    if baseline is None:
        return {
            "ok": False,
            "code": "NO_SUCCESSFUL_RESULT",
            "message": "No successful result exists to accept for this question run.",
        }
    revisions = list_revisions(session, run["id"])
    if not revisions:
        validated = baseline.get("validated_tables")
        if not isinstance(validated, list) or not validated:
            return {
                "ok": False,
                "code": "NO_SUCCESSFUL_RESULT",
                "message": "No successful result exists to accept for this question run.",
            }
        return {
            "ok": True,
            "baseline_id": str(baseline.get("id")),
            "cpt_revision_id": None,
            "cpt_hash": contracts.canonical_hash(list(validated)),
            "result_kind": RESULT_KIND_BASELINE,
            "result_id": str(baseline.get("id")),
            "question_key": str(run.get("question_key", "")),
        }
    current = revisions[-1]
    current_id = str(current.get("id"))
    solved = get_calculation_result_for_revision(session, current["id"])
    if solved is None:
        raw_local_jobs = queue_module.list_local_jobs_for_run(session, run["id"])
        active = any(
            str(job.get("status")) in ("queued", "leased")
            and isinstance(job.get("diagnostics"), dict)
            and str(job["diagnostics"].get("cpt_revision_id")) == current_id
            for job in (dict(j) for j in raw_local_jobs)
        )
        if active:
            message = "The current revision is still recalculating. Wait for it to finish."
        else:
            message = "The current revision failed to calculate. Retry locally or reset first."
        return {"ok": False, "code": "NO_SUCCESSFUL_RESULT", "message": message}
    return {
        "ok": True,
        "baseline_id": str(baseline.get("id")),
        "cpt_revision_id": current_id,
        "cpt_hash": str(current.get("cpt_hash", "")),
        "result_kind": RESULT_KIND_CALCULATION,
        "result_id": str(solved.get("id")),
        "question_key": str(run.get("question_key", "")),
    }


def find_current_acceptance(
    session: Session,
    *,
    run: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    current_fingerprint: str,
) -> dict[str, Any] | None:
    """Latest acceptance exactly matching the live revision/result/inputs.

    Later edits/reset move the revision pointer, relevant patient edits move
    the fingerprint, so older rows stop matching without deletion (history
    stays via ``list_acceptances``). Never labels an earlier result as
    current: only a full exact match counts.
    """
    point = _live_acceptance_point(session, run=run, baseline=baseline)
    if not point.get("ok"):
        return None
    for entry in reversed(list_acceptances(session, run["id"])):
        if (
            str(entry.get("baseline_id")) == point["baseline_id"]
            and (
                str(entry.get("cpt_revision_id"))
                if entry.get("cpt_revision_id") is not None
                else None
            )
            == point["cpt_revision_id"]
            and str(entry.get("cpt_hash")) == point["cpt_hash"]
            and str(entry.get("result_kind")) == point["result_kind"]
            and str(entry.get("result_id")) == point["result_id"]
            and str(entry.get("input_hash")) == str(current_fingerprint)
        ):
            return entry
    return None


def apply_acceptance(
    session: Session,
    *,
    author: Mapping[str, Any],
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    stale: bool,
    current_fingerprint: str,
    expected_review_revision: Any,
    baseline_id: Any,
    cpt_revision_id: Any | None,
    cpt_hash: Any,
    result_id: Any,
    input_hash: Any,
    request_id: str,
) -> dict[str, Any]:
    """Persist one exact current-result acceptance (caller's transaction).

    Validates the optimistic pointer (412 when stale), rejects pending /
    failed / stale-input states (409), rejects any reference that does not
    exactly equal the live baseline/revision/result/input hash (409), and
    inserts one immutable ``probability_acceptances`` row plus the atomic
    ``prob_acceptance.success`` audit. Re-accepting the already-accepted
    exact state returns the existing row (no duplicate, no extra audit).
    Raises ContractError (422 malformed, 412 stale pointer, 409 state).
    """
    _require_draft_encounter(session, batch)
    if not isinstance(expected_review_revision, int) or expected_review_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_review_revision must be a positive integer.",
            {"expected_review_revision": ["Must be a positive integer."]},
        )
    expected = int(expected_review_revision)
    want_baseline = _parse_acceptance_uuid(baseline_id, "baseline_id")
    want_result = _parse_acceptance_uuid(result_id, "result_id")
    want_revision: uuid.UUID | None = None
    if cpt_revision_id is not None:
        want_revision = _parse_acceptance_uuid(cpt_revision_id, "current_cpt_revision_id")
    if not isinstance(cpt_hash, str) or not cpt_hash.strip():
        raise _fail(422, "VALIDATION_FAILED", "cpt_hash must be non-empty text.", "cpt_hash")
    if not isinstance(input_hash, str) or not input_hash.strip():
        raise _fail(422, "VALIDATION_FAILED", "input_hash must be non-empty text.", "input_hash")
    locked_state_row = (
        session.execute(
            select(review_tables.question_review_states)
            .where(review_tables.question_review_states.c.question_run_id == run["id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    locked_state = dict(locked_state_row) if locked_state_row is not None else None
    current_review = (
        int(locked_state["review_revision"])
        if locked_state is not None
        else INITIAL_REVIEW_REVISION
    )
    if int(expected) != int(current_review):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The probability review changed. Reload and reconcile your edits.",
            {"expected_review_revision": ["Stale review revision."]},
        )
    if stale:
        raise contracts.ContractError(
            409,
            "STALE_INPUTS",
            "Patient inputs changed. Regenerate before accepting.",
            {"input_hash": ["Stale patient inputs."]},
        )
    point = _live_acceptance_point(session, run=run, baseline=baseline)
    if not point.get("ok"):
        raise contracts.ContractError(
            409,
            str(point.get("code", "NO_SUCCESSFUL_RESULT")),
            str(point.get("message", "No successful result exists to accept.")),
            {"question_run": [str(point.get("message", "No successful result."))]},
        )
    live_revision = point["cpt_revision_id"]
    if str(want_baseline) != point["baseline_id"]:
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "baseline_id does not match the current baseline.",
            {"baseline_id": ["Does not match the current baseline."]},
        )
    if (str(want_revision) if want_revision is not None else None) != live_revision:
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "current_cpt_revision_id does not match the current revision.",
            {"current_cpt_revision_id": ["Does not match the current revision."]},
        )
    if str(cpt_hash) != point["cpt_hash"]:
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "cpt_hash does not match the current CPT revision.",
            {"cpt_hash": ["Does not match the current CPT revision."]},
        )
    if str(want_result) != point["result_id"]:
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "result_id does not match the current successful result.",
            {"result_id": ["Does not match the current successful result."]},
        )
    if str(input_hash) != str(current_fingerprint):
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "input_hash does not match the current patient inputs.",
            {"input_hash": ["Does not match the current patient inputs."]},
        )
    existing = find_current_acceptance(
        session, run=run, baseline=baseline, current_fingerprint=str(current_fingerprint)
    )
    if existing is not None:
        return existing
    from x_insight.cases import tables as cases_tables

    encounter_row = (
        session.execute(
            select(cases_tables.encounters).where(
                cases_tables.encounters.c.id == batch["encounter_id"]
            )
        )
        .mappings()
        .first()
    )
    patient_id = str(dict(encounter_row).get("patient_id")) if encounter_row is not None else None
    moment = contracts.utcnow()
    acceptance_id = uuid.uuid4()
    session.execute(
        insert(review_tables.probability_acceptances).values(
            id=acceptance_id,
            question_run_id=run["id"],
            batch_id=run["batch_id"],
            encounter_id=batch["encounter_id"],
            baseline_id=want_baseline,
            cpt_revision_id=want_revision,
            cpt_hash=str(point["cpt_hash"]),
            result_kind=str(point["result_kind"]),
            result_id=want_result,
            input_hash=str(current_fingerprint),
            projection_hash=str(run.get("projection_hash", "")),
            actor_id=author["id"],
            actor_username=str(author.get("username", "")),
            created_at=moment,
        )
    )
    session.flush()
    audit_module.record_audit(
        session,
        operation=ACCEPTANCE_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(batch.get("encounter_id")),
            "patient_id": patient_id,
            "batch_id": str(run.get("batch_id")),
            "question_run_id": str(run.get("id")),
            "question_key": str(point.get("question_key", "")),
            "acceptance_id": str(acceptance_id),
            "baseline_id": str(want_baseline),
            "cpt_revision_id": str(want_revision) if want_revision is not None else None,
            "cpt_hash": str(point["cpt_hash"]),
            "result_kind": str(point["result_kind"]),
            "result_id": str(want_result),
            "input_hash": str(current_fingerprint),
            "projection_hash": str(run.get("projection_hash", "")),
            "review_revision": int(expected),
        },
    )
    created = (
        session.execute(
            select(review_tables.probability_acceptances).where(
                review_tables.probability_acceptances.c.id == acceptance_id
            )
        )
        .mappings()
        .first()
    )
    assert created is not None
    stored = dict(created)
    stored["question_key"] = str(point.get("question_key", ""))
    return stored
