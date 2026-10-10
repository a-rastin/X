"""0014: local calculation results + generation-only uniqueness (S48b).

Plan.md §9.1 + system-design.md §§5, 8.3-8.4, 9 (FR-53, FR-55-56, FR-58,
FR-43, NFR-04, NFR-06): completed adjustments queue revision-bound local
jobs (``reasoning_jobs`` with ``job_class='local_calculation'``,
revision/hash in ``diagnostics``) and successful local executions persist
one immutable ``calculation_results`` row per CPT revision (fixed network/
template/query + empty-evidence posteriors/section). Failed locals create
no result row (no adjustable success), mirroring S45 baselines; the job
row + attempt ledger carry the error. Reset reuses the verified baseline
result explicitly (``reused_from_baseline_id``) without a new execution.

Schema:

- ``calculation_results`` — one immutable row per successfully calculated
  revision: run/batch FKs, ``cpt_revision_id`` unique, CPT/network hashes,
  versions, query/posteriors/section/effective artifact, optional baseline
  reuse link, provenance. App SELECT+INSERT only; readonly SELECT.
- ``reasoning_jobs`` uniqueness: drop ``uq_reasoning_jobs_run_id`` (one
  job per run blocked generation+local sharing) and replace with a partial
  unique for generation only (``WHERE job_class='generation'``). Local jobs
  share the run (multiple revisions) with app-level coalescing + the
  results-table uniqueness as the backstop; no new columns (revision
  binding lives in the existing ``diagnostics`` JSONB).

Calculation state (unchanged/recalculating/successfully_recalculated/
failed) and displayed/current revision IDs are derived on read from
revisions + results + local jobs (no new pointer columns); input
freshness reuses the S40 fingerprint derivation. Downgrade drops the new
table/index and restores the old unique (fails if local rows share a run;
S48b data requires the new shape).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLES = ("calculation_results",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "calculation_results",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("cpt_revision_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("cpt_hash", sa.Text(), nullable=False),
        sa.Column("network_hash", sa.Text(), nullable=False),
        sa.Column("network_version", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Text(), nullable=False),
        sa.Column("query_nodes", JSONB(), nullable=False),
        sa.Column("posteriors", JSONB(), nullable=False),
        sa.Column("section_text", sa.Text(), nullable=False),
        sa.Column("effective_hash", sa.Text(), nullable=False),
        sa.Column("effective_xml", sa.Text(), nullable=False),
        sa.Column("reused_from_baseline_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("provenance", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["question_run_id"], ["question_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["generation_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cpt_revision_id"], ["cpt_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reused_from_baseline_id"], ["original_baselines.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("cpt_revision_id", name="uq_calculation_results_revision"),
        sa.CheckConstraint("char_length(cpt_hash) = 64", name="ck_calc_results_cpt_sha256"),
        sa.CheckConstraint(
            "char_length(network_hash) = 64", name="ck_calc_results_net_sha256"
        ),
        sa.CheckConstraint(
            "char_length(effective_hash) = 64", name="ck_calc_results_eff_sha256"
        ),
    )
    op.create_index(
        "ix_calculation_results_run_id", "calculation_results", ["question_run_id"]
    )
    op.create_index(
        "ix_calculation_results_batch_id", "calculation_results", ["batch_id"]
    )
    # Allow generation + locals to share a run; keep one generation per run.
    op.execute(sa.text("ALTER TABLE reasoning_jobs DROP CONSTRAINT IF EXISTS uq_reasoning_jobs_run_id"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_reasoning_jobs_generation_run "
            "ON reasoning_jobs (question_run_id) WHERE job_class = 'generation'"
        )
    )

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
                    "WHEN undefined_object THEN RAISE NOTICE "
                    f"'skipping REVOKE for missing role {role}'; "
                    "WHEN insufficient_privilege THEN RAISE NOTICE "
                    f"'skipping REVOKE for role {role}: "
                    "insufficient privilege'; "
                    "END; END $$;"
                )
            )
    for stmt, role in (
        (
            "GRANT SELECT, INSERT ON calculation_results TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON calculation_results TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON calculation_results TO x_insight_migrate", "x_insight_migrate"),
    ):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                "BEGIN "
                f"{stmt}; "
                "EXCEPTION "
                "WHEN insufficient_privilege THEN RAISE NOTICE "
                f"'skipping {stmt}: insufficient privilege'; "
                "END; "
                "ELSE "
                f"RAISE NOTICE 'skipping {stmt}: role {role} missing'; "
                "END IF; END $$;"
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_reasoning_jobs_generation_run"))
    # Restores the old one-job-per-run shape; fails when local rows share a run.
    op.execute(
        sa.text(
            "ALTER TABLE reasoning_jobs ADD CONSTRAINT uq_reasoning_jobs_run_id "
            "UNIQUE (question_run_id)"
        )
    )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_calculation_results_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_calculation_results_run_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
