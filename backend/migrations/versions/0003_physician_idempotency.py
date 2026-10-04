"""0003: per-command idempotency store for physician account commands (S04).

Plan.md §4.1/§4.3: retried create/patch/deactivate/reactivate commands carry
``Idempotency-Key``; same key + same body replays the original result, same
key + changed body is ``409``. The store holds actor/operation/key, the
canonical request hash, and the original response (status + safe body) so
replays never re-execute the mutation or duplicate audit rows.

Grants mirror 0001/0002's idempotent DO $$ pattern: app SELECT+INSERT (never
UPDATE/DELETE — records are immutable), readonly SELECT, migrate ALL.
Downgrade drops the table. No draft/clinical tables are created here (S51
owns Cases integration).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("actor_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "operation", "actor_id", "idempotency_key", name="uq_idempotency_operation_actor_key"
        ),
    )
    op.create_index(
        "ix_idempotency_actor_operation", "idempotency_records", ["actor_id", "operation"]
    )

    op.execute(sa.text("REVOKE ALL ON idempotency_records FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                "BEGIN "
                "REVOKE ALL ON idempotency_records FROM "
                f"{role}; "
                "EXCEPTION "
                f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                "END; END $$;"
            )
        )
    for stmt, role in (
        (
            "GRANT SELECT, INSERT ON idempotency_records TO x_insight_app",
            "x_insight_app",
        ),
        (
            "GRANT SELECT ON idempotency_records TO x_insight_readonly",
            "x_insight_readonly",
        ),
        ("GRANT ALL ON idempotency_records TO x_insight_migrate", "x_insight_migrate"),
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
    op.drop_table("idempotency_records")
