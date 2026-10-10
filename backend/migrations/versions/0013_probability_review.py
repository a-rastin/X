"""0013: CPT revisions + review-state pointers for S48a adjustments.

Plan.md §9.1 + system-design.md §§5, 8.2-8.3 (FR-50–52, FR-56, FR-42,
NFR-04–05): completed slider commands persist one immutable complete
CPTRevision (full artifact/hash, parent/sequence, kind=adjustment,
direct edit + redistributed before/after, actor/time) plus a mutable
QuestionReviewState pointer (current revision, optimistic review_revision).
Original baselines and shared XML are never updated here. S48b owns
local-calculation jobs/results, reset/retry routes, and acceptance;
S48d owns freshness regeneration; S49 owns signing — none of those tables
are created here.

Grants mirror 0009-0012's idempotent DO $$ pattern: revisions app
SELECT+INSERT only (never UPDATE/DELETE), review states app
SELECT+INSERT+UPDATE (mutable pointer), readonly SELECT, migrate ALL.
Downgrade drops both tables; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLES = ("cpt_revisions", "question_review_states")


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "cpt_revisions",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("parent_revision_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("cpt_artifact", JSONB(), nullable=False),
        sa.Column("cpt_hash", sa.Text(), nullable=False),
        sa.Column("direct_edit", JSONB(), nullable=False),
        sa.Column("before_row", JSONB(), nullable=False),
        sa.Column("after_row", JSONB(), nullable=False),
        sa.Column("actor_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("actor_username", sa.Text(), nullable=False),
        sa.Column("redistribution_version", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("question_run_id", "sequence", name="uq_cpt_revisions_run_seq"),
        sa.CheckConstraint("sequence >= 1", name="ck_cpt_revisions_seq_gte_1"),
        sa.CheckConstraint(
            "kind IN ('adjustment', 'reset')", name="ck_cpt_revisions_kind"
        ),
        sa.CheckConstraint(
            "char_length(cpt_hash) = 64", name="ck_cpt_revisions_hash_sha256"
        ),
    )
    op.create_index("ix_cpt_revisions_run_id", "cpt_revisions", ["question_run_id"])
    op.create_index("ix_cpt_revisions_batch_id", "cpt_revisions", ["batch_id"])
    op.create_table(
        "question_review_states",
        sa.Column("question_run_id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("current_revision_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("review_revision", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["question_run_id"], ["question_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["generation_batches.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("review_revision >= 1", name="ck_review_states_rev_gte_1"),
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
            "GRANT SELECT, INSERT ON cpt_revisions TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, INSERT, UPDATE ON question_review_states TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON cpt_revisions TO x_insight_readonly",
            "x_insight_readonly",
        ),
        (
            "GRANT SELECT ON question_review_states TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON cpt_revisions TO x_insight_migrate", "x_insight_migrate"),
        (
            "GRANT ALL ON question_review_states TO x_insight_migrate",
            "x_insight_migrate",
        ),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cpt_revisions_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_cpt_revisions_run_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
