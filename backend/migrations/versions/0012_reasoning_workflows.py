"""0012: ordered workflows and immutable proposals (S46).

Plan.md §§7.1, 8.4, 9 (FR-15, FR-30-35): synthetic five/six-question
workflows execute in pinned order with one eligible question at a time;
the final proposal assembles stored sections plus the pinned DDI report
(no LLM proposal writing). Partial runs stay readable but incomplete.

Tables (new, S46-owned):
- ``proposal_snapshots`` — one immutable row per completed batch: batch FK
  (unique), fingerprint, ordered sections, skipped reasons, coverage
  warnings, pinned DDI report. Immutable (app SELECT+INSERT only);
  incomplete runs have no row (derived incomplete view, never mistaken
  for review-ready).

Columns (S46-owned, on an S40 table):
- ``question_runs.position`` — pinned order index (0-based) within the
  batch. Existing rows default 0 (single-question batches); new workflow
  batches store 0..n-1 in pinned ``packages`` order. Read ordering is
  position-first (then key for stability).

Grants mirror 0009-0011's idempotent DO $$ pattern: proposals app
SELECT+INSERT only (never UPDATE/DELETE), readonly SELECT, migrate ALL.
Downgrade drops the new table and column; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TABLES = ("proposal_snapshots",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.add_column(
        "question_runs",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "proposal_snapshots",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("sections", JSONB(), nullable=False),
        sa.Column("skipped", JSONB(), nullable=False),
        sa.Column("coverage_warnings", JSONB(), nullable=False),
        sa.Column("ddi_report", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("batch_id", name="uq_proposal_snapshots_batch_id"),
        sa.CheckConstraint(
            "char_length(fingerprint) = 64", name="ck_proposal_snapshots_fp_sha256"
        ),
    )
    op.create_index("ix_proposal_snapshots_batch_id", "proposal_snapshots", ["batch_id"])

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
            "GRANT SELECT, INSERT ON proposal_snapshots TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON proposal_snapshots TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON proposal_snapshots TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_proposal_snapshots_batch_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("question_runs", "position")
