"""Append-only audit events, transaction-scoped (S02, plan.md §§4.1, 10.1).

The owning module for audit rows: HTTP/worker code calls :func:`record_audit`
with an already-open session and reads back via :func:`list_audit_events`.
This helper never commits or rolls back by itself — the caller's
:func:`x_insight.db.session_scope` transaction owns the commit, so a failed
mutation and its audit row land (or roll back) together. No generic
repository layer: only these two operations exist.

``payload_hash`` is the canonical-hash actual public use (contracts): the
SHA-256 over canonical JSON of the versioned ``{"schema_version", "details"}``
envelope, so later readers can detect tampering without trusting
presentation. ``occurred_at`` is stamped with
UTC helpers. No credentials or clinical payloads belong in ``details`` —
actor/operation/request correlation only (S03+ adds its own columns via new
migrations; this table is append-only and never updated in place).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.contracts import ContractError

# Audit payload hash schema version (plan.md §4.2): part of the hashed
# envelope so future detail-shape changes invalidate old digests.
SCHEMA_VERSION = 1

# S52 stable operation names (plan.md §10.1). Workers + HTTP share these
# strings so admin filters stay stable across S53-S56. Backup/restore and
# export names are reserved hooks only (no routes implement them here).
ORIGINAL_RUN_SUCCESS_OPERATION = "generation.run.success"
ORIGINAL_RUN_FAILED_OPERATION = "generation.run.failed"
LOCAL_RETRY_REQUESTED_OPERATION = "cpt_retry.requested"
BACKUP_CREATE_OPERATION = "backups.create"
BACKUP_CREATE_SUCCESS_OPERATION = "backups.create.success"
BACKUP_CREATE_FAILED_OPERATION = "backups.create.failed"
RESTORE_VALIDATE_OPERATION = "restores.validate"
RESTORE_VALIDATE_SUCCESS_OPERATION = "restores.validate.success"
RESTORE_VALIDATE_FAILED_OPERATION = "restores.validate.failed"
RESTORE_COMMIT_OPERATION = "restores.commit"
RESTORE_COMMIT_SUCCESS_OPERATION = "restores.commit.success"
RESTORE_COMMIT_FAILED_OPERATION = "restores.commit.failed"
EXPORT_PATIENTS_SUCCESS_OPERATION = "exports.patients.success"
EXPORT_PHYSICIANS_SUCCESS_OPERATION = "exports.physicians.success"
PATIENT_REPORT_SUCCESS_OPERATION = "reports.patient.success"


def audit_payload_hash(details: dict[str, Any]) -> str:
    """SHA-256 over the versioned envelope (never the bare details)."""
    return contracts.canonical_hash({"schema_version": SCHEMA_VERSION, "details": details})


metadata = MetaData()

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor", Text, nullable=True),
    Column("operation", Text, nullable=False),
    Column("request_id", Text, nullable=True),
    Column("details", JSONB, nullable=False),
    Column("payload_hash", Text, nullable=False),
)


def record_audit(
    session: Session,
    *,
    operation: str,
    actor: str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
    occurred_at: Any = None,
) -> uuid.UUID:
    """Insert one audit event in the caller's transaction (flush, no commit).

    Raises ``ContractError`` (422) for an empty operation; database NOT NULL
    and privilege failures surface as transaction errors for the caller to
    roll back. Returns the event UUID.
    """
    if not operation or not operation.strip():
        raise ContractError(
            422,
            "INVALID_AUDIT_OPERATION",
            "Audit operation must be a non-empty string.",
            {"operation": ["Must be a non-empty string."]},
        )
    payload = dict(details or {})
    event_id = uuid.uuid4()
    session.execute(
        insert(audit_events).values(
            id=event_id,
            occurred_at=occurred_at or contracts.utcnow(),
            actor=actor,
            operation=operation,
            request_id=request_id,
            details=payload,
            payload_hash=audit_payload_hash(payload),
        )
    )
    session.flush()
    return event_id


def list_audit_events(
    session: Session,
    *,
    limit: int | None = None,
    offset: int | None = None,
    actor: str | None = None,
    operation: str | None = None,
    since: Any = None,
    until: Any = None,
    patient_id: Any = None,
    encounter_id: Any = None,
    question_run_id: Any = None,
    batch_id: Any = None,
    question_key: str | None = None,
) -> list[dict[str, Any]]:
    """Read back audit events in stable time order, bounded by pagination rules.

    Filters are exact except ``actor`` (display-substring or exact immutable
    id in ``details``). Target filters match the ``details`` JSONB keys the
    command paths store (``patient_id``/``encounter_id``/``question_run_id``/
    ``batch_id``/``question_key``); events without that key simply do not
    match. Stable order is (``occurred_at``, ``id``).
    """
    from sqlalchemy import and_, or_

    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    clauses: list[Any] = []
    if actor is not None and str(actor).strip() != "":
        wanted = str(actor).strip()
        clauses.append(
            or_(
                audit_events.c.actor.ilike(f"%{wanted}%"),
                audit_events.c.details["actor_id"].astext == wanted,
                audit_events.c.details["actor_display"].astext.ilike(f"%{wanted}%"),
            )
        )
    if operation is not None and str(operation).strip() != "":
        clauses.append(audit_events.c.operation == str(operation).strip())
    if since is not None:
        clauses.append(audit_events.c.occurred_at >= since)
    if until is not None:
        clauses.append(audit_events.c.occurred_at <= until)
    targets = (
        ("patient_id", patient_id),
        ("encounter_id", encounter_id),
        ("question_run_id", question_run_id),
        ("batch_id", batch_id),
    )
    for key, value in targets:
        if value is not None and str(value).strip() != "":
            clauses.append(audit_events.c.details[key].astext == str(value).strip())
    if question_key is not None and str(question_key).strip() != "":
        clauses.append(audit_events.c.details["question_key"].astext == str(question_key).strip())
    query = select(audit_events)
    if clauses:
        query = query.where(and_(*clauses))
    rows = session.execute(
        query.order_by(audit_events.c.occurred_at, audit_events.c.id)
        .limit(resolved_limit)
        .offset(resolved_offset)
    )
    return [dict(row) for row in rows.mappings()]


def count_audit_events(
    session: Session,
    *,
    actor: str | None = None,
    operation: str | None = None,
    since: Any = None,
    until: Any = None,
    patient_id: Any = None,
    encounter_id: Any = None,
    question_run_id: Any = None,
    batch_id: Any = None,
    question_key: str | None = None,
) -> int:
    """Count events matching the same filters as :func:`list_audit_events`."""
    from sqlalchemy import and_, func, or_, select

    clauses: list[Any] = []
    if actor is not None and str(actor).strip() != "":
        wanted = str(actor).strip()
        clauses.append(
            or_(
                audit_events.c.actor.ilike(f"%{wanted}%"),
                audit_events.c.details["actor_id"].astext == wanted,
                audit_events.c.details["actor_display"].astext.ilike(f"%{wanted}%"),
            )
        )
    if operation is not None and str(operation).strip() != "":
        clauses.append(audit_events.c.operation == str(operation).strip())
    if since is not None:
        clauses.append(audit_events.c.occurred_at >= since)
    if until is not None:
        clauses.append(audit_events.c.occurred_at <= until)
    targets = (
        ("patient_id", patient_id),
        ("encounter_id", encounter_id),
        ("question_run_id", question_run_id),
        ("batch_id", batch_id),
    )
    for key, value in targets:
        if value is not None and str(value).strip() != "":
            clauses.append(audit_events.c.details[key].astext == str(value).strip())
    if question_key is not None and str(question_key).strip() != "":
        clauses.append(audit_events.c.details["question_key"].astext == str(question_key).strip())
    query = select(func.count()).select_from(audit_events)
    if clauses:
        query = query.where(and_(*clauses))
    return int(session.execute(query).scalar_one())


def safe_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public audit shape: stable attribution surfaced without digging.

    ``actor`` stays the snapshot display name stored at event time;
    ``actor_id``/``actor_display`` come from ``details`` when the command
    path recorded them (None/actor fallback for older rows). Times are
    explicit UTC strings; ids are strings.
    """
    from collections.abc import Mapping as _Mapping

    details = row.get("details")
    safe_details = dict(details) if isinstance(details, _Mapping) else {}
    occurred = row.get("occurred_at")
    try:
        occurred_text = contracts.serialize_utc(occurred)
    except Exception:
        occurred_text = str(occurred)
    actor_display = safe_details.get("actor_display")
    return {
        "id": str(row.get("id")),
        "occurred_at": occurred_text,
        "actor": row.get("actor"),
        "actor_id": safe_details.get("actor_id"),
        "actor_display": str(actor_display) if actor_display is not None else row.get("actor"),
        "operation": str(row.get("operation", "")),
        "request_id": row.get("request_id"),
        "details": safe_details,
        "payload_hash": str(row.get("payload_hash", "")),
    }
