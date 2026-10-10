"""0015: exact current-result acceptances (S48c).

Plan.md §§4.1, 9.2 + system-design.md §§5, 8.5, 10-11 (FR-57, FR-42):
author-only acceptance per question run of the exact current baseline /
CPT revision / successful result / patient-input hash, with actor/time and
an atomic audit event (``prob_acceptance.success``).

Invalidation is an exact-match check against the current revision / result
/ input hash on read (never deletion): a later adjustment/reset moves the
current revision pointer, a relevant patient edit moves the input
fingerprint, so the older row simply stops matching. History is retained
(the full per-run list stays readable). Note-only edits leave the
fingerprint unchanged, so they preserve acceptance.

Schema:

- ``probability_acceptances`` — one immutable row per accepted exact
  state: run/batch/encounter FKs, baseline FK, nullable CPT revision FK
  (null for unchanged originals), CPT hash, polymorphic result reference
  (``result_kind`` baseline/calculation + ``result_id``), input hash,
  projection hash (pinned run inputs, for S49 sign rechecks), actor/time.
  App SELECT+INSERT only; readonly SELECT.

Grants mirror 0013-0014's idempotent DO $$ pattern. Downgrade drops the
table; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

TABLES = ("probability_acceptances",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "probability_acceptances",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("question_run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("encounter_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("cpt_revision_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("cpt_hash", sa.Text(), nullable=False),
        sa.Column("result_kind", sa.Text(), nullable=False),
        sa.Column("result_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("projection_hash", sa.Text(), nullable=False),
        sa.Column("actor_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("actor_username", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["baseline_id"], ["original_baselines.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cpt_revision_id"], ["cpt_revisions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "result_kind IN ('baseline', 'calculation')",
            name="ck_prob_acceptances_kind",
        ),
        sa.CheckConstraint(
            "char_length(cpt_hash) = 64", name="ck_prob_acceptances_cpt_sha256"
        ),
        sa.CheckConstraint(
            "char_length(input_hash) = 64", name="ck_prob_acceptances_input_sha256"
        ),
        sa.CheckConstraint(
            "char_length(projection_hash) = 64", name="ck_prob_acceptances_proj_sha256"
        ),
    )
    op.create_index(
        "ix_prob_acceptances_run_id", "probability_acceptances", ["question_run_id"]
    )
    op.create_index(
        "ix_prob_acceptances_batch_id", "probability_acceptances", ["batch_id"]
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
            "GRANT SELECT, INSERT ON probability_acceptances TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON probability_acceptances TO x_insight_readonly",
            "x_insight_readonly",
        ),
        (
            "GRANT ALL ON probability_acceptances TO x_insight_migrate",
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_prob_acceptances_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_prob_acceptances_run_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
