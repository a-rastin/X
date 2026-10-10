"""Probability-review tables (S48a-owned; migration 0013 owns the DDL).

- ``cpt_revisions`` — one immutable row per completed slider command:
  run/batch FKs, parent revision, per-run sequence, kind ``adjustment``
  (``reset`` reserved for S48b), complete CPT artifact + hash, direct edit
  and redistributed before/after row values, actor/time. Immutable
  (app SELECT+INSERT only).
- ``question_review_states`` — one mutable pointer row per question run:
  current revision (null when baseline), optimistic ``review_revision``
  (starts at 1, +1 per adjustment), timestamps. Mutable pointer only;
  history lives in ``cpt_revisions``.
"""

from __future__ import annotations

from sqlalchemy import (
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

cpt_revisions = Table(
    "cpt_revisions",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "question_run_id",
        PG_UUID(as_uuid=True),
        ForeignKey("question_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("parent_revision_id", PG_UUID(as_uuid=True), nullable=True),
    Column("sequence", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("cpt_artifact", JSONB, nullable=False),
    Column("cpt_hash", Text, nullable=False),
    Column("direct_edit", JSONB, nullable=False),
    Column("before_row", JSONB, nullable=False),
    Column("after_row", JSONB, nullable=False),
    Column("actor_id", PG_UUID(as_uuid=True), nullable=False),
    Column("actor_username", Text, nullable=False),
    Column("redistribution_version", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("sequence >= 1", name="ck_cpt_revisions_seq_gte_1"),
    CheckConstraint(
        "kind IN ('adjustment', 'reset')",
        name="ck_cpt_revisions_kind",
    ),
    CheckConstraint("char_length(cpt_hash) = 64", name="ck_cpt_revisions_hash_sha256"),
)

question_review_states = Table(
    "question_review_states",
    metadata,
    Column(
        "question_run_id",
        PG_UUID(as_uuid=True),
        ForeignKey("question_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("current_revision_id", PG_UUID(as_uuid=True), nullable=True),
    Column("review_revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("review_revision >= 1", name="ck_review_states_rev_gte_1"),
)

Index("ix_cpt_revisions_run_id", cpt_revisions.c.question_run_id)
Index("ix_cpt_revisions_batch_id", cpt_revisions.c.batch_id)
