"""Persist the response format selected for each provider settings revision.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    # Existing revisions used schema unless the operator supplied an override.
    op.add_column(
        "provider_settings",
        sa.Column("capability_mode", sa.Text(), nullable=False, server_default="schema"),
    )
    op.create_check_constraint(
        "ck_provider_settings_capability_mode",
        "provider_settings",
        "capability_mode IN ('schema', 'json')",
    )


def downgrade():
    op.drop_constraint("ck_provider_settings_capability_mode", "provider_settings", type_="check")
    op.drop_column("provider_settings", "capability_mode")
