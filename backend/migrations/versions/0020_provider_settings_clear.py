"""0020: allow a cleared provider-settings revision (S42 slice 2).

``clear`` stores a new append-only row with NULL nonce and ciphertext.
0019 left those columns NOT NULL. No new table, role, or grant.

Downgrade restores NOT NULL. It fails if a cleared row is present.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "provider_settings",
        "key_nonce",
        existing_type=sa.LargeBinary(),
        nullable=True,
    )
    op.alter_column(
        "provider_settings",
        "key_ciphertext",
        existing_type=sa.LargeBinary(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "provider_settings",
        "key_nonce",
        existing_type=sa.LargeBinary(),
        nullable=False,
    )
    op.alter_column(
        "provider_settings",
        "key_ciphertext",
        existing_type=sa.LargeBinary(),
        nullable=False,
    )
