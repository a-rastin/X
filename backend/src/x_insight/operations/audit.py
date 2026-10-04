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
) -> list[dict[str, Any]]:
    """Read back audit events in time order, bounded by pagination rules."""
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    rows = session.execute(
        select(audit_events)
        .order_by(audit_events.c.occurred_at, audit_events.c.id)
        .limit(resolved_limit)
        .offset(resolved_offset)
    )
    return [dict(row) for row in rows.mappings()]
