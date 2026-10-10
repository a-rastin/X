"""0018: restore staging jobs for S55 validate-only restores.

Plan.md §§4.1, 10.3 steps 1-3 (FR-41; tasks.md S55 §§1-4 staging only):
one durable row per validate-to-staging attempt — identity, terminal
status, staged archive location/hash, source manifest/report, immutable
confirmation digest, expiry, failure cause. Staging-only: no live-mutation
columns, no commit/switch/rollback state (S56 owns those, never this
table). ``restore_jobs`` rows are ephemeral staging metadata and are
deliberately EXCLUDED from S54 backups (like sessions): portable archives
never carry staging state.

Restricted-role note (reuse-first, ponytail: no new DB role): staging
inspection reuses the existing ``x_insight_readonly`` SELECT-only role for
live impact reads; the app role only writes ``restore_jobs`` + audit.
Creating ``x_insight_restore`` would add privilege sprawl for no stronger
guarantee (staging never executes uploaded SQL — it only parses JSON/XML
via safe stdlib/lxml helpers) and would break the S02 role-count contract
(``test_audit_roles_and_append_only_grants`` expects exactly the three
0001 roles). Grants below mirror 0017's idempotent DO $$ pattern: app
SELECT+INSERT+UPDATE (status/expiry transitions only, never DELETE),
readonly SELECT, migrate ALL. Downgrade drops the table/index; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TABLES = ("restore_jobs",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "restore_jobs",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_by_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_username", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("backup_created_at", sa.Text(), nullable=True),
        sa.Column("app_schema_version", sa.Text(), nullable=True),
        sa.Column("archive_path", sa.Text(), nullable=True),
        sa.Column("archive_sha256", sa.Text(), nullable=True),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=True),
        sa.Column("manifest", JSONB(), nullable=True),
        sa.Column("report", JSONB(), nullable=True),
        sa.Column("confirmation_digest", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_restore_jobs_status",
        ),
        sa.CheckConstraint(
            "archive_sha256 IS NULL OR char_length(archive_sha256) = 64",
            name="ck_restore_jobs_sha256",
        ),
        sa.CheckConstraint(
            "confirmation_digest IS NULL OR char_length(confirmation_digest) = 64",
            name="ck_restore_jobs_digest_sha256",
        ),
    )
    op.create_index("ix_restore_jobs_created_at", "restore_jobs", ["created_at"])

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
            "GRANT SELECT, INSERT, UPDATE ON restore_jobs TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON restore_jobs TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON restore_jobs TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_restore_jobs_created_at"))
    for table in reversed(TABLES):
        op.drop_table(table)
