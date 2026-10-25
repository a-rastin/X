"""0023: structured final-plan evidence and append-only confirmations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "secondary_plans",
        sa.Column("clozapine_included", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.create_check_constraint(
        "ck_secondary_plan_clozapine",
        "secondary_plans",
        "clozapine_included IN ('yes', 'no', 'unknown')",
    )
    op.add_column("encounter_addenda", sa.Column("clozapine_included", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_addendum_clozapine",
        "encounter_addenda",
        "clozapine_included IS NULL OR clozapine_included IN ('yes', 'no', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_addendum_clozapine", "encounter_addenda", type_="check")
    op.drop_column("encounter_addenda", "clozapine_included")
    op.drop_constraint("ck_secondary_plan_clozapine", "secondary_plans", type_="check")
    op.drop_column("secondary_plans", "clozapine_included")
