"""Reasoning snapshots storage: batches + question runs (S40, plan.md §§4.1, 8.1).

Tables (new, S40-owned; migration ``0009`` owns the DDL, this module is the
read model for queries):
- ``generation_batches`` — one row per frozen start: encounter/author,
  frozen source revision, analysis fingerprint + payload, pinned bundle,
  batch status, creation time. Immutable (app SELECT+INSERT only).
- ``question_runs`` — one row per projected question: batch FK, question
  key, gate status + explicit reason, immutable projection + hash,
  fingerprint copy, pinned versions. Immutable, unique per batch/key.

No queue/provider/MCP tables here (S41/S43/S44 own them).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,  # noqa: F401  # re-exported for future S44 use, keep import stable
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

metadata = MetaData()

generation_batches = Table(
    "generation_batches",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "encounter_id",
        PG_UUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "author_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_revision", Integer, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("fingerprint_payload", JSONB, nullable=False),
    Column("pinned_bundle", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("source_revision >= 1", name="ck_generation_batches_revision_gte_1"),
    CheckConstraint("char_length(fingerprint) = 64", name="ck_generation_batches_fp_sha256"),
    CheckConstraint(
        "status IN ('ready', 'not_applicable', 'needs_clarification')",
        name="ck_generation_batches_status",
    ),
)

question_runs = Table(
    "question_runs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("question_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("gate_reason", Text, nullable=False),
    Column("projection", JSONB, nullable=False),
    Column("projection_hash", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("pinned_versions", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('ready', 'not_applicable', 'needs_clarification', 'stale')",
        name="ck_question_runs_status",
    ),
    CheckConstraint("char_length(fingerprint) = 64", name="ck_question_runs_fp_sha256"),
    CheckConstraint("char_length(projection_hash) = 64", name="ck_question_runs_proj_sha256"),
)

Index("ix_generation_batches_encounter_id", generation_batches.c.encounter_id)
Index("ix_question_runs_batch_id", question_runs.c.batch_id)
