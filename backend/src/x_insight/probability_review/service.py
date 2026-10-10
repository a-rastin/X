"""CPT revision persistence + review snapshot (S48a, seams T1/T5).

Plan.md §9.1 + system-design.md §§5, 8.2-8.3 (FR-50–52, FR-56, FR-42):
completed slider commands persist one immutable complete ``cpt_revisions``
row (full artifact/hash, parent/sequence, kind=adjustment, direct edit +
redistributed before/after, actor/time) plus a mutable
``question_review_states`` pointer, with atomic audit. All persistence uses
the caller's transaction (never commits here). Original baselines and
shared XML are read-only here (SELECT only, never UPDATE).

Redistribution itself is pure ``redistribution.py`` (seam T5, integer
units only); this module only looks up the target row, calls it, and
persists the complete new artifact (other rows byte-for-byte unchanged).
Local jobs/results, reset/retry, acceptance, freshness and signing arrive
in S48b/S48c/S48d/S49 — none of those are created here.
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

INITIAL_REVIEW_REVISION = 1


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
