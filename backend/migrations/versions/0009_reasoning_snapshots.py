"""0009: reasoning snapshots — generation batches + question runs (S40).

Plan.md §§4.1, 8.1 (FR-16, FR-32, FR-35, NFR-04): starting generation from
a saved author-owned revision freezes allowed facts/version references in
immutable rows; per-question typed allowlisted projections persist before
any provider access. Old snapshots remain author-readable history;
staleness is derived on read (no UPDATE). Queue/provider/MCP tables are
later sessions (S41/S43/S44), not here.

Tables (new, S40-owned):
- ``generation_batches`` — encounter/author, frozen source revision,
  analysis fingerprint + payload, pinned bundle, batch status.
- ``question_runs`` — batch FK, question key, gate status + explicit
  reason, immutable projection + hash, fingerprint copy, pinned versions.
  Unique per batch/key.

Grants mirror 0001-0008's idempotent DO $$ pattern: app SELECT+INSERT only
(immutable, never UPDATE/DELETE), readonly SELECT, migrate ALL.
Downgrade drops the new tables; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLES = (
    "generation_batches",
    "question_runs",
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "generation_batches",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("encounter_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("fingerprint_payload", JSONB(), nullable=False),
        sa.Column("pinned_bundle", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("source_revision >= 1", name="ck_generation_batches_revision_gte_1"),
        sa.CheckConstraint(
            "char_length(fingerprint) = 64", name="ck_generation_batches_fp_sha256"
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'not_applicable', 'needs_clarification')",
            name="ck_generation_batches_status",
        ),
    )
    op.create_index(
        "ix_generation_batches_encounter_id", "generation_batches", ["encounter_id"]
    )
    op.create_table(
        "question_runs",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("gate_reason", sa.Text(), nullable=False),
        sa.Column("projection", JSONB(), nullable=False),
        sa.Column("projection_hash", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("pinned_versions", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("batch_id", "question_key", name="uq_question_runs_batch_key"),
        sa.CheckConstraint(
            "status IN ('ready', 'not_applicable', 'needs_clarification', 'stale')",
            name="ck_question_runs_status",
        ),
        sa.CheckConstraint(
            "char_length(fingerprint) = 64", name="ck_question_runs_fp_sha256"
        ),
        sa.CheckConstraint(
            "char_length(projection_hash) = 64", name="ck_question_runs_proj_sha256"
        ),
    )
    op.create_index("ix_question_runs_batch_id", "question_runs", ["batch_id"])

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
        ("GRANT SELECT, INSERT ON generation_batches TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT, INSERT ON question_runs TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON generation_batches TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON question_runs TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON generation_batches TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON question_runs TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_question_runs_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_generation_batches_encounter_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
