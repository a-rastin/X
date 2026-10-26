"""Persist endpoint-specific local and fake-IP proxy approvals.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "provider_settings",
        sa.Column(
            "local_endpoint_approved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "provider_settings",
        sa.Column(
            "trusted_proxy_approved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade():
    op.drop_column("provider_settings", "trusted_proxy_approved")
    op.drop_column("provider_settings", "local_endpoint_approved")
