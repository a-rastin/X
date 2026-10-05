"""0006: attributed page notes separate from draft content (S13).

Plan.md §§2.3, 4.1 (FR-16, FR-22): page notes carry page, author snapshot,
server timestamp, and text. They live in their own ``notes`` table — never
inside ``encounters.draft_data`` and never in the analysis-visible history
channel (plan §4.2 excludes notes from the analysis fingerprint; the explicit
serializer exclusion lives in ``B/cases/notes.py`` for future S40 snapshots).

Invariants:
- One row per note: opaque UUID PK, ``encounter_id`` FK → encounters
  (CASCADE — discarded rows keep their notes rows until the encounter row
  itself goes), ``page`` TEXT (allowlist enforced in the service so the page
  set can grow without DDL), ``author_id`` FK → users (CASCADE),
  ``author_display`` TEXT (username snapshot at write time), ``created_at``
  TIMESTAMPTZ, ``text`` TEXT verbatim (literal markup preserved, no
  stripping). Length bounds are CHECKed here as a backstop; the 422 contract
  (non-empty, max ~2000 chars) lives in the service.
- Append-only: the application role gets SELECT+INSERT only (never
  UPDATE/DELETE — correction is a new note, there is no edit/delete
  endpoint). Encounter revision still bumps on note create via the encounters
  UPDATE grant from 0004/0005, so S07 autosave stays coherent.

Grants mirror 0001-0005's idempotent DO $$ pattern: app SELECT+INSERT,
readonly SELECT, migrate ALL. Downgrade drops the notes table; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "notes",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("encounter_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("page", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "char_length(page) BETWEEN 1 AND 64", name="ck_notes_page_nonempty"
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 2000", name="ck_notes_text_bounds"
        ),
    )
    op.create_index("ix_notes_encounter_id", "notes", ["encounter_id"])
    op.create_index("ix_notes_encounter_page", "notes", ["encounter_id", "page"])

    op.execute(sa.text("REVOKE ALL ON notes FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                "BEGIN "
                f"REVOKE ALL ON notes FROM {role}; "
                "EXCEPTION "
                f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                "END; END $$;"
            )
        )
    for stmt, role in (
        ("GRANT SELECT, INSERT ON notes TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON notes TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON notes TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_notes_encounter_page"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_notes_encounter_id"))
    op.drop_table("notes")
