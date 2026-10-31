"""Persist an optional provider HTTP timeout on each settings revision.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    # NULL preserves environment/default resolution for existing installations.
    op.add_column("provider_settings", sa.Column("timeout_seconds", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_provider_settings_timeout", "provider_settings", "timeout_seconds BETWEEN 10 AND 900"
    )


def downgrade():
    op.drop_constraint("ck_provider_settings_timeout", "provider_settings", type_="check")
    op.drop_column("provider_settings", "timeout_seconds")
