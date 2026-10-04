"""0001: append-only audit events + least-privilege roles (S02).

Only the storage S02 needs: the audit table future command modules share.
Domain tables land with their own features (S03+), each with its migration.
Roles are deployment-level: downgrade drops the table but keeps the roles.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = ("x_insight_app", "x_insight_migrate", "x_insight_readonly")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
    )
    # Role creation is idempotent-safe for non-superuser runs (e.g. `make
    # migrate` as the table-owner role): skip gracefully with NOTICE when a
    # role already exists or cannot be created, instead of failing the whole
    # migration with InsufficientPrivilege. Fresh-superuser runs still create
    # every missing role.
    for role in ROLES:
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                "BEGIN "
                f"CREATE ROLE {role} WITH NOLOGIN; "
                "EXCEPTION "
                "WHEN duplicate_object THEN NULL; "
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping role "
                f"{role}: insufficient privilege'; "
                "END; "
                "END IF; END $$;"
            )
        )
    # Default-deny first, then the minimal grants: the app role appends and
    # reads audit rows but can never UPDATE/DELETE them; the read-only role
    # only reads; migration owns DDL. Each role-scoped REVOKE/GRANT applies
    # only when permitted (role exists and caller may grant); otherwise it
    # raises NOTICE instead of failing owner-run migrations.
    op.execute(sa.text("REVOKE ALL ON audit_events FROM PUBLIC"))
    for role in ROLES:
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                "BEGIN "
                f"REVOKE ALL ON audit_events FROM {role}; "
                "EXCEPTION "
                f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                "END; END $$;"
            )
        )
    for stmt, role in (
        ("GRANT SELECT, INSERT ON audit_events TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT ON audit_events TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT ALL ON audit_events TO x_insight_migrate", "x_insight_migrate"),
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
    op.drop_table("audit_events")
