"""0021: capability status on provider settings (S42 slice 4).

A save is not a passed check. New rows default to ``untested``.
The test route appends another revision (``verified`` or ``failed``).
No new grant: the app role already has INSERT only.

Downgrade drops the check and the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_settings",
        sa.Column(
            "capability_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'untested'"),
        ),
    )
    op.create_check_constraint(
        "ck_provider_settings_capability_status",
        "provider_settings",
        "capability_status IN ('untested', 'verified', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_provider_settings_capability_status",
        "provider_settings",
        type_="check",
    )
    op.drop_column("provider_settings", "capability_status")
