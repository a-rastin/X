"""0010: durable leased jobs and global admission (S44).

Plan.md §§8.4-8.5 (FR-35-36, FR-43, NFR-04, NFR-06): run creation and its
first eligible job are atomic; global provider slots (2), queued cap (100),
short claim transactions with SKIP LOCKED, lease/heartbeat (15s/120s),
fencing token + deployment generation, fair rotation state, and minimal
grant storage for S41 fencing.

Tables (new, S44-owned):
- ``reasoning_jobs`` — one mutable row per eligible question run: batch FK,
  question-run FK (unique, one job per run), job_class
  (generation|local_calculation, separate capacity reserved for S48b),
  status (queued|leased|succeeded|failed|cancelled), attempt counters,
  lease token/deadline/heartbeat, deployment generation, eligibility,
  diagnostics, terminal result. Mutable (leases); terminal rows retained.
- ``reasoning_job_attempts`` — immutable attempt ledger per job:
  (job_id, attempt_index) unique, stage, fencing token, deployment
  generation, started/finished times, outcome, diagnostics, result.
  Retained across restarts; old tokens never commit.
- ``reasoning_grants`` — minimal fencing/MCP grant storage for S41:
  job/batch/run binding, token hash, deployment generation, revoke/expiry.
  One row per claim; revoked on terminal/reclaim. S41 adds transport use.
- ``reasoning_fairness`` — persistent round-robin state per
  (job_class, author): last grant time. Claim orders authors by oldest
  grant then oldest job (FIFO within physician).
- ``reasoning_deployment`` — single-row deployment generation (id=1).
  Claim/commit checks equality; restores bump it to fence old workers.

Grants mirror prior migrations' idempotent DO $$ pattern: jobs/grants/
fairness/deployment app SELECT+INSERT+UPDATE (mutable leases), attempts
app SELECT+INSERT+UPDATE (finish updates), readonly SELECT, migrate ALL.
Downgrade drops the new tables; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TABLES = (
    "reasoning_jobs",
    "reasoning_job_attempts",
    "reasoning_grants",
    "reasoning_fairness",
    "reasoning_deployment",
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "reasoning_jobs",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("job_class", sa.Text(), nullable=False, server_default="generation"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempt_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_token", sa.Text(), nullable=True),
        sa.Column("lease_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deployment_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diagnostics", JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_run_id"], ["question_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("question_run_id", name="uq_reasoning_jobs_run_id"),
        sa.CheckConstraint(
            "job_class IN ('generation', 'local_calculation')",
            name="ck_reasoning_jobs_class",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
            name="ck_reasoning_jobs_status",
        ),
        sa.CheckConstraint("attempt_index >= 0", name="ck_reasoning_jobs_attempt_gte_0"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_reasoning_jobs_max_attempts_gte_1"),
        sa.CheckConstraint("deployment_generation >= 1", name="ck_reasoning_jobs_deploy_gte_1"),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_token IS NOT NULL AND lease_deadline IS NOT NULL)"
            " OR (status != 'leased' AND lease_token IS NULL)",
            name="ck_reasoning_jobs_lease_consistency",
        ),
    )
    op.create_index("ix_reasoning_jobs_batch_id", "reasoning_jobs", ["batch_id"])
    op.create_index(
        "ix_reasoning_jobs_claim",
        "reasoning_jobs",
        ["job_class", "status", "created_at"],
    )
    op.create_index("ix_reasoning_jobs_lease_deadline", "reasoning_jobs", ["lease_deadline"])

    op.create_table(
        "reasoning_job_attempts",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False, server_default="preparing_question"),
        sa.Column("lease_token", sa.Text(), nullable=False),
        sa.Column("deployment_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False, server_default="started"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("diagnostics", JSONB(), nullable=False, server_default="{}"),
        sa.Column("result", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["reasoning_jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "attempt_index", name="uq_reasoning_attempts_job_index"),
        sa.CheckConstraint("attempt_index >= 0", name="ck_reasoning_attempts_index_gte_0"),
        sa.CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed')",
            name="ck_reasoning_attempts_outcome",
        ),
        sa.CheckConstraint("deployment_generation >= 1", name="ck_reasoning_attempts_deploy_gte_1"),
    )
    op.create_index("ix_reasoning_attempts_job_id", "reasoning_job_attempts", ["job_id"])

    op.create_table(
        "reasoning_grants",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("grant_token_hash", sa.Text(), nullable=False),
        sa.Column("deployment_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["reasoning_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_run_id"], ["question_runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "char_length(grant_token_hash) = 64", name="ck_reasoning_grants_hash_sha256"
        ),
        sa.CheckConstraint("deployment_generation >= 1", name="ck_reasoning_grants_deploy_gte_1"),
    )
    op.create_index("ix_reasoning_grants_job_id", "reasoning_grants", ["job_id"])

    op.create_table(
        "reasoning_fairness",
        sa.Column("job_class", sa.Text(), nullable=False),
        sa.Column("author_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("last_granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_class", "author_id"),
        sa.CheckConstraint(
            "job_class IN ('generation', 'local_calculation')",
            name="ck_reasoning_fairness_class",
        ),
    )

    op.create_table(
        "reasoning_deployment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="ck_reasoning_deployment_singleton"),
        sa.CheckConstraint("generation >= 1", name="ck_reasoning_deployment_gte_1"),
    )
    op.execute(sa.text("INSERT INTO reasoning_deployment (id, generation) VALUES (1, 1)"))

    for table in TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        for table in TABLES:
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "BEGIN "
                    f"REVOKE ALL ON {table} FROM {role}; "
                    "EXCEPTION "
                    f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                    f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                    "END; END $$;"
                )
            )
    for stmt, role in (
        ("GRANT SELECT, INSERT, UPDATE ON reasoning_jobs TO x_insight_app", "x_insight_app"),
        (
            "GRANT SELECT, INSERT, UPDATE ON reasoning_job_attempts TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, INSERT, UPDATE ON reasoning_grants TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, INSERT, UPDATE ON reasoning_fairness TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, UPDATE ON reasoning_deployment TO x_insight_app",
            "x_insight_app",
        ),
        ("GRANT SELECT ON reasoning_jobs TO x_insight_readonly", "x_insight_readonly"),
        (
            "GRANT SELECT ON reasoning_job_attempts TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT SELECT ON reasoning_grants TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON reasoning_fairness TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON reasoning_deployment TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON reasoning_jobs TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON reasoning_job_attempts TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON reasoning_grants TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON reasoning_fairness TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON reasoning_deployment TO x_insight_migrate", "x_insight_migrate"),
    ):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                "BEGIN "
                f"{stmt}; "
                "EXCEPTION "
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping {stmt}: insufficient privilege'; "  # noqa: E501
                "END; "
                "ELSE "
                f"RAISE NOTICE 'skipping {stmt}: role {role} missing'; "
                "END IF; END $$;"
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_reasoning_grants_job_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_reasoning_attempts_job_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_reasoning_jobs_lease_deadline"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_reasoning_jobs_claim"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_reasoning_jobs_batch_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
