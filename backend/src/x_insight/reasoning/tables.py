"""Reasoning snapshots + durable queue storage (S40, S44; plan.md §§4.1, 8.1, 8.4-8.5).

Tables (S40-owned; migration ``0009`` owns the DDL, this module is the
read model for queries):
- ``generation_batches`` — one row per frozen start: encounter/author,
  frozen source revision, analysis fingerprint + payload, pinned bundle,
  batch status, creation time. Immutable (app SELECT+INSERT only).
- ``question_runs`` — one row per projected question: batch FK, question
  key, gate status + explicit reason, immutable projection + hash,
  fingerprint copy, pinned versions. Immutable, unique per batch/key.

Tables (S44-owned; migration ``0010`` owns the DDL):
- ``reasoning_jobs`` — one mutable row per eligible run: batch/run FKs,
  job_class (generation|local_calculation, separate capacity reserved for
  S48b), status (queued|leased|succeeded|failed|cancelled), attempt
  counters, lease token/deadline/heartbeat, deployment generation,
  eligibility, diagnostics, terminal result. Mutable leases; terminal rows
  retained across restarts.
- ``reasoning_job_attempts`` — immutable attempt ledger: (job, index)
  unique, stage, fencing token, deployment generation, times, outcome,
  diagnostics, result. Old tokens never commit.
- ``reasoning_grants`` — minimal fencing/MCP grant storage for S41: job/
  batch/run binding, token hash, deployment generation, revoke/expiry.
- ``reasoning_fairness`` — persistent round-robin state per
  (job_class, author): last grant time. Claim orders authors by oldest
  grant then oldest job (FIFO within physician).
- ``reasoning_deployment`` — singleton deployment generation (id=1).
  Claim/commit checks equality; restores bump it to fence old workers.
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

reasoning_jobs = Table(
    "reasoning_jobs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "question_run_id",
        PG_UUID(as_uuid=True),
        ForeignKey("question_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column("job_class", Text, nullable=False, default="generation"),
    Column("status", Text, nullable=False, default="queued"),
    Column("attempt_index", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("lease_token", Text, nullable=True),
    Column("lease_deadline", DateTime(timezone=True), nullable=True),
    Column("last_heartbeat", DateTime(timezone=True), nullable=True),
    Column("deployment_generation", Integer, nullable=False, default=1),
    Column("next_eligible_at", DateTime(timezone=True), nullable=True),
    Column("diagnostics", JSONB, nullable=False, default=dict),
    Column("result", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "job_class IN ('generation', 'local_calculation')",
        name="ck_reasoning_jobs_class",
    ),
    CheckConstraint(
        "status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
        name="ck_reasoning_jobs_status",
    ),
)

reasoning_job_attempts = Table(
    "reasoning_job_attempts",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "job_id",
        PG_UUID(as_uuid=True),
        ForeignKey("reasoning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("attempt_index", Integer, nullable=False),
    Column("stage", Text, nullable=False, default="preparing_question"),
    Column("lease_token", Text, nullable=False),
    Column("deployment_generation", Integer, nullable=False, default=1),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("outcome", Text, nullable=False, default="started"),
    Column("error_code", Text, nullable=True),
    Column("diagnostics", JSONB, nullable=False, default=dict),
    Column("result", JSONB, nullable=True),
    CheckConstraint(
        "outcome IN ('started', 'succeeded', 'failed')",
        name="ck_reasoning_attempts_outcome",
    ),
)

reasoning_grants = Table(
    "reasoning_grants",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "job_id",
        PG_UUID(as_uuid=True),
        ForeignKey("reasoning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "question_run_id",
        PG_UUID(as_uuid=True),
        ForeignKey("question_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("grant_token_hash", Text, nullable=False),
    Column("deployment_generation", Integer, nullable=False, default=1),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

reasoning_fairness = Table(
    "reasoning_fairness",
    metadata,
    Column("job_class", Text, primary_key=True),
    Column(
        "author_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("last_granted_at", DateTime(timezone=True), nullable=False),
)

reasoning_deployment = Table(
    "reasoning_deployment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("generation", Integer, nullable=False, default=1),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Index("ix_reasoning_jobs_batch_id", reasoning_jobs.c.batch_id)
Index(
    "ix_reasoning_jobs_claim",
    reasoning_jobs.c.job_class,
    reasoning_jobs.c.status,
    reasoning_jobs.c.created_at,
)
Index("ix_reasoning_jobs_lease_deadline", reasoning_jobs.c.lease_deadline)
Index("ix_reasoning_attempts_job_id", reasoning_job_attempts.c.job_id)
Index("ix_reasoning_grants_job_id", reasoning_grants.c.job_id)
