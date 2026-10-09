"""0011: original baselines for one full synthetic question (S45).

Plan.md §§7.4, 8.4-8.5, 9 (FR-32-35): after real MCP + controlled provider +
all-CPT validation + effective XML + empty-evidence inference + template
rendering, atomically create one immutable OriginalBaseline per question run.
Failed originals create no row (no adjustable baseline). Registered source
bytes stay unchanged; the effective artifact is a new run-local document.

Tables (new, S45-owned):
- ``original_baselines`` — one immutable row per successful question run:
  run/batch FKs (run unique), source/effective hashes, raw/validated CPTs,
  effective XML, query/posteriors, rendered section, versions/model,
  projection hash, provenance. Immutable (app SELECT+INSERT only).

Columns (S45-owned, on an S40 table):
- ``question_runs.pinned_package`` — frozen full question package
  (manifest/prompt/template/examples/review + ``network_xml``) stored at
  start for the worker. Nullable for pre-S45 rows (worker fails closed
  without it); new starts always fill it.

Grants mirror 0009-0010's idempotent DO $$ pattern: baselines app
SELECT+INSERT only (never UPDATE/DELETE), readonly SELECT, migrate ALL.
Downgrade drops the new table and column; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TABLES = ("original_baselines",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.add_column(
        "question_runs",
        sa.Column("pinned_package", JSONB(), nullable=True),
    )
    op.create_table(
        "original_baselines",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("effective_xml", sa.Text(), nullable=False),
        sa.Column("effective_hash", sa.Text(), nullable=False),
        sa.Column("raw_response", JSONB(), nullable=False),
        sa.Column("validated_tables", JSONB(), nullable=False),
        sa.Column("query_nodes", JSONB(), nullable=False),
        sa.Column("posteriors", JSONB(), nullable=False),
        sa.Column("section_text", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("network_version", sa.Text(), nullable=False),
        sa.Column("provider_model", sa.Text(), nullable=False),
        sa.Column("projection_hash", sa.Text(), nullable=False),
        sa.Column("provenance", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["question_run_id"], ["question_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("question_run_id", name="uq_original_baselines_run_id"),
        sa.CheckConstraint(
            "char_length(source_hash) = 64", name="ck_original_baselines_src_sha256"
        ),
        sa.CheckConstraint(
            "char_length(effective_hash) = 64", name="ck_original_baselines_eff_sha256"
        ),
        sa.CheckConstraint(
            "char_length(projection_hash) = 64", name="ck_original_baselines_proj_sha256"
        ),
    )
    op.create_index("ix_original_baselines_batch_id", "original_baselines", ["batch_id"])

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
            "GRANT SELECT, INSERT ON original_baselines TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON original_baselines TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON original_baselines TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_original_baselines_batch_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("question_runs", "pinned_package")
