"""Record the last acknowledged research-warning version for each account.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    # NULL means unacknowledged: existing and new accounts both start pending.
    op.add_column(
        "users",
        sa.Column("research_warning_acknowledged_version", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("users", "research_warning_acknowledged_version")
