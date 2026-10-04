"""0005: encounter draft content for author-owned autosave (S07).

Plan.md §§2.3, 4.1 (FR-16, FR-22): encounters gain a minimal JSONB
``draft_data`` clinical body (empty object by default) so an author can save
and resume private draft content. Existing S06 rows backfill to ``'{}'`` via
the column default — no data migration touches patient demographics.

Invariants:
- ``draft_data`` is always a JSON object (``jsonb_typeof = 'object'`` CHECK),
  never a bare scalar/array/null; shape validation beyond that lives in
  ``B/cases/encounters.py`` (service), not in DDL.
- Lifecycle/revision/slot mechanics are unchanged: the partial unique index
  ``one_open_draft_per_patient`` still owns the single-open-draft slot;
  ``lifecycle`` stays the persistence state (``draft``/``signed``/
  ``discarded``), distinct from any calculation/readiness state (no such
  column is added here — S07 explicitly keeps them separate).
- Discard releases the slot by moving lifecycle to ``discarded``; the row
  (including ``draft_data``) is retained as-is. No retention promise is made
  here — physical draft-content retention remains proposed per plan §1.3.

Grants mirror 0001-0004's idempotent DO $$ pattern: app keeps
SELECT+INSERT+UPDATE (UPDATE for later autosave/discard flows; still no
DELETE — no encounter deletion in v1), readonly SELECT, migrate ALL.
Downgrade drops the constraint then the column; roles are kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    # Idempotent-safe like 0001-0004's DO blocks: re-runs keep existing rows.
    op.execute(
        sa.text(
            "ALTER TABLE encounters "
            "ADD COLUMN IF NOT EXISTS draft_data JSONB "
            "NOT NULL DEFAULT '{}'::jsonb"
        )
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_constraint "
            "WHERE conname = 'ck_encounters_draft_data_object') THEN "
            "ALTER TABLE encounters ADD CONSTRAINT ck_encounters_draft_data_object "
            "CHECK (jsonb_typeof(draft_data) = 'object'); "
            "END IF; END $$;"
        )
    )

    op.execute(sa.text("REVOKE ALL ON encounters FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                "BEGIN "
                f"REVOKE ALL ON encounters FROM {role}; "
                "EXCEPTION "
                f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                "END; END $$;"
            )
        )
    for stmt, role in (
        ("GRANT SELECT, INSERT, UPDATE ON encounters TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON encounters TO x_insight_readonly", "x_insight_readonly"),
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
    op.execute(sa.text("ALTER TABLE encounters DROP CONSTRAINT IF EXISTS ck_encounters_draft_data_object"))  # noqa: E501
    op.execute(sa.text("ALTER TABLE encounters DROP COLUMN IF EXISTS draft_data"))
