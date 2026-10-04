"""0004: patient registry + registration encounters (S06).

Plan.md §§2.2-2.3, 4.1 (FR-10, FR-23): patients carry a globally unique
ten-digit text identifier (stored as TEXT, never numeric, so leading zeros
round-trip), Unicode-letter names, sex, age, clinical status, optional phone,
archive flag, and revision. Registration atomically creates one patient plus
one ``encounters`` row (``kind='registration'``, ``lifecycle='draft'``).

Invariants enforced at the database (hold even under simultaneous requests):
- ``UNIQUE(identifier)`` — global, including archived rows (no partial
  predicate), so a duplicate race yields one creation and a conflict.
- ``CHECK`` guards on identifier shape (``^[0-9]{10}$`` ASCII only), sex,
  age range, clinical status, kind, and lifecycle.
- Partial unique index ``one_open_draft_per_patient`` — at most one open
  draft per patient (plan §2.3); the S06 create path inserts the first one.

Grants mirror 0001-0003's idempotent DO $$ pattern: app SELECT+INSERT+UPDATE
(UPDATE for later demographics/archive flows; still no DELETE — there is no
patient deletion in v1), readonly SELECT, migrate ALL. Downgrade drops the
new objects (encounters first for the FK) but keeps roles.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("patients", "encounters")


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "patients",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("given_name", sa.Text(), nullable=False),
        sa.Column("family_name", sa.Text(), nullable=False),
        sa.Column("sex", sa.Text(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("clinical_status", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("identifier", name="uq_patients_identifier"),
        sa.CheckConstraint("identifier ~ '^[0-9]{10}$'", name="ck_patients_identifier"),
        sa.CheckConstraint("sex IN ('M', 'F')", name="ck_patients_sex"),
        sa.CheckConstraint("age BETWEEN 18 AND 99", name="ck_patients_age"),
        sa.CheckConstraint(
            "clinical_status IN ('first_time', 'established')",
            name="ck_patients_clinical_status",
        ),
    )
    op.create_table(
        "encounters",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("patient_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'registration'")),
        sa.Column("author_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
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
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "kind IN ('registration', 'follow_up')", name="ck_encounters_kind"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'signed', 'discarded')", name="ck_encounters_lifecycle"
        ),
    )
    op.create_index("ix_encounters_patient_id", "encounters", ["patient_id"])
    # At most one open draft per patient (plan §2.3);IF NOT EXISTS keeps
    # re-runs idempotent like the 0001-0003 DO blocks.
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_open_draft_per_patient "
            "ON encounters (patient_id) WHERE lifecycle = 'draft'"
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
                    f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                    f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                    "END; END $$;"
                )
            )
    for stmt, role in (
        ("GRANT SELECT, INSERT, UPDATE ON patients TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT, INSERT, UPDATE ON encounters TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON patients TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON encounters TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON patients TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON encounters TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS one_open_draft_per_patient"))
    op.drop_table("encounters")
    op.drop_table("patients")
