"""0019: append-only encrypted provider settings (S42 slice 1).

One row per save. The API key is nonce + AES-GCM ciphertext; the
deployment key is operator-held and is not a column. No FK (created_by
is a username snapshot). App role SELECT+INSERT only (no UPDATE, no
DELETE); readonly SELECT; migrate ALL. No new database role.

Downgrade drops the table. Roles stay.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TABLES = ("provider_settings",)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "provider_settings",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_provider_settings_revision",
        "provider_settings",
        ["revision"],
        unique=True,
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
            "GRANT SELECT, INSERT ON provider_settings TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON provider_settings TO x_insight_readonly",
            "x_insight_readonly",
        ),
        (
            "GRANT ALL ON provider_settings TO x_insight_migrate",
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
    op.execute(sa.text("DROP INDEX IF EXISTS ix_provider_settings_revision"))
    for table in reversed(TABLES):
        op.drop_table(table)
