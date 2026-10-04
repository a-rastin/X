"""0002: identity users + sessions (S03).

Users/sessions per plan.md §4.1: UUID, normalized unique username, immutable
admin identity (no username-mutation route), role, active, hash, credential
revision, theme; hashed opaque session token, CSRF, created/revoked, no
timeout. Partial unique index ``one_admin_only`` blocks a second admin.
Seeds the default admin/admin once (ON CONFLICT DO NOTHING) so a fresh DB
permits admin/admin; later password changes are never overwritten.

Roles/grants mirror 0001's idempotent DO $$ pattern: app SELECT/INSERT/UPDATE
(no DELETE — revocation is UPDATE; no audit-style append-only), readonly
SELECT, migrate ALL. Downgrade drops sessions then users.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Default hash (hash of "admin"); kept as one literal for readability.
DEFAULT_ADMIN_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$tjHQEyjmzxcTSSiMHZCVAA==$"
    "UASsgt9clH2ILDdwDx3OwXdEfbg0pY1iLMWdRIkPa1E="
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "users",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "credential_revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("theme", sa.Text(), nullable=False, server_default=sa.text("'light'")),
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
        sa.CheckConstraint("role IN ('admin', 'physician')", name="ck_users_role"),
        sa.CheckConstraint("theme IN ('light', 'dark')", name="ck_users_theme"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.execute(
        sa.text("CREATE UNIQUE INDEX IF NOT EXISTS one_admin_only ON users (role) WHERE role = 'admin'")
    )
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # Seed default admin/admin once; never overwrite a changed password.
    op.execute(
        sa.text(
            "INSERT INTO users (id, username, role, active, password_hash, "
            "credential_revision, theme, created_at, updated_at, revision) "
            "SELECT gen_random_uuid(), 'admin', 'admin', true, "
            f"'{DEFAULT_ADMIN_PASSWORD_HASH}', 1, 'light', now(), now(), 1 "
            "WHERE NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin') "
            "ON CONFLICT DO NOTHING"
        )
    )

    op.execute(sa.text("REVOKE ALL ON users FROM PUBLIC"))
    op.execute(sa.text("REVOKE ALL ON sessions FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        for table in ("users", "sessions"):
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
        ("GRANT SELECT, INSERT, UPDATE ON users TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT, INSERT, UPDATE ON sessions TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON users TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON sessions TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON users TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON sessions TO x_insight_migrate", "x_insight_migrate"),
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
    op.drop_table("sessions")
    op.drop_table("users")
