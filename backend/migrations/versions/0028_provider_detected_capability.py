"""Persist capability detection on the tested provider settings revision.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "provider_settings", sa.Column("detected_capability_mode", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_provider_settings_detected_capability_mode",
        "provider_settings",
        "detected_capability_mode IS NULL OR detected_capability_mode IN ('schema', 'json')",
    )


def downgrade():
    op.drop_constraint(
        "ck_provider_settings_detected_capability_mode", "provider_settings", type_="check"
    )
    op.drop_column("provider_settings", "detected_capability_mode")
