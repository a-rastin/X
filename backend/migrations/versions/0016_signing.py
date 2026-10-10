"""0016: secondary plans + signed snapshots + addenda (S49).

Plan.md §§4.1, 9.2 (FR-15, FR-22, FR-57, FR-42, NFR-04): the secondary plan
is a separately revisioned physician edit (own If-Match fence, never in the
analysis fingerprint); signing freezes the full record (original proposal /
CPTs / results, final accepted CPTs / results, adjusted recommendations,
plan edits, inputs / versions / configuration, acceptor / signer /
timestamps) in one immutable snapshot, releases the single-draft slot
(draft -> signed), and audits atomically; any active physician may append
an attributed dated addendum (append-only, server-derived actor/time).

Schema:

- ``secondary_plans`` — one mutable row per encounter (encounter PK, text,
  revision >= 1, timestamps). App SELECT+INSERT+UPDATE; readonly SELECT.
- ``signed_encounter_snapshots`` — one immutable row per signed encounter
  (encounter UNIQUE, patient/batch/proposal FKs, plan revision+text,
  snapshot JSONB + sha256 hash, signer, signed_at, encounter revision).
  App SELECT+INSERT only; readonly SELECT.
- ``encounter_addenda`` — append-only rows per signed encounter (encounter
  FK, server-derived author, 1..2000 text). App SELECT+INSERT only.

Grants mirror 0013-0015's idempotent DO $$ pattern. Downgrade drops the new
tables/indexes; roles kept. S51 owns concurrent-race fencing beyond the
single-transaction row locks + UNIQUE(encounter_id) used here (hook
documented in signing.py, not built here).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TABLES = ("secondary_plans", "signed_encounter_snapshots", "encounter_addenda")


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "secondary_plans",
        sa.Column("encounter_id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
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
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision >= 1", name="ck_secondary_plans_rev_gte_1"),
    )
    op.create_table(
        "signed_encounter_snapshots",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("encounter_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("secondary_plan_revision", sa.Integer(), nullable=False),
        sa.Column("secondary_plan_text", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column("signer_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("signer_username", sa.Text(), nullable=False),
        sa.Column(
            "signed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("encounter_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["generation_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposal_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signer_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("encounter_id", name="uq_signed_snapshots_encounter"),
        sa.CheckConstraint("secondary_plan_revision >= 1", name="ck_signed_plan_rev_gte_1"),
        sa.CheckConstraint("encounter_revision >= 1", name="ck_signed_enc_rev_gte_1"),
        sa.CheckConstraint("char_length(snapshot_hash) = 64", name="ck_signed_snapshot_sha256"),
    )
    op.create_index("ix_signed_snapshots_patient_id", "signed_encounter_snapshots", ["patient_id"])
    op.create_index("ix_signed_snapshots_batch_id", "signed_encounter_snapshots", ["batch_id"])
    op.create_table(
        "encounter_addenda",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("encounter_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("author_display", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="ck_addenda_text_bounds"),
    )
    op.create_index("ix_encounter_addenda_encounter_id", "encounter_addenda", ["encounter_id"])

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
            "GRANT SELECT, INSERT, UPDATE ON secondary_plans TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, INSERT ON signed_encounter_snapshots TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT, INSERT ON encounter_addenda TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON secondary_plans TO x_insight_readonly",
            "x_insight_readonly",
        ),
        (
            "GRANT SELECT ON signed_encounter_snapshots TO x_insight_readonly",
            "x_insight_readonly",
        ),
        (
            "GRANT SELECT ON encounter_addenda TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON secondary_plans TO x_insight_migrate", "x_insight_migrate"),
        (
            "GRANT ALL ON signed_encounter_snapshots TO x_insight_migrate",
            "x_insight_migrate",
        ),
        ("GRANT ALL ON encounter_addenda TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_encounter_addenda_encounter_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_signed_snapshots_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_signed_snapshots_patient_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
