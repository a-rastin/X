"""0017: backup jobs for S54 consistent full backups.

Plan.md §§4.1, 10.2 (FR-41–42): one durable row per backup attempt —
identity, status (pending/running/succeeded/failed), manifest/checksums,
archive location, uploader, failure cause. No restore/staging columns here;
S55 owns its own staging table (never reuse this one for live mutation).

Grants mirror 0013-0016's idempotent DO $$ pattern: app SELECT+INSERT+UPDATE
(status transitions only, never DELETE), readonly SELECT, migrate ALL.
Downgrade drops the table/index; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = ("backup_jobs",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "backup_jobs",
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
        sa.Column("manifest", JSONB(), nullable=True),
        sa.Column("archive_path", sa.Text(), nullable=True),
        sa.Column("archive_sha256", sa.Text(), nullable=True),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_backup_jobs_status",
        ),
        sa.CheckConstraint(
            "archive_sha256 IS NULL OR char_length(archive_sha256) = 64",
            name="ck_backup_jobs_sha256",
        ),
    )
    op.create_index("ix_backup_jobs_created_at", "backup_jobs", ["created_at"])

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
            "GRANT SELECT, INSERT, UPDATE ON backup_jobs TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON backup_jobs TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON backup_jobs TO x_insight_migrate", "x_insight_migrate"),
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_backup_jobs_created_at"))
    for table in reversed(TABLES):
        op.drop_table(table)
