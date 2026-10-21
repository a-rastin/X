"""0022: preserve DDInter original source bytes and structured CSV occurrences."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ddi_source_documents", sa.Column("source_format", sa.Text(), nullable=True))
    op.add_column(
        "ddi_source_documents", sa.Column("source_bytes", sa.LargeBinary(), nullable=True)
    )
    op.add_column("ddi_source_documents", sa.Column("source_metadata", sa.JSON(), nullable=True))
    op.add_column(
        "ddi_interaction_evidence", sa.Column("source_record_metadata", sa.JSON(), nullable=True)
    )
    op.create_index(
        "uq_ddi_concepts_release_catalog",
        "ddi_concepts",
        ["release_id", "catalog_drug_id"],
        unique=True,
        postgresql_where=sa.text("catalog_drug_id IS NOT NULL"),
    )
    op.create_index(
        "ix_ddi_concepts_release_name",
        "ddi_concepts",
        ["release_id", "canonical_name", "catalog_drug_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ddi_concepts_release_name", table_name="ddi_concepts")
    op.drop_index("uq_ddi_concepts_release_catalog", table_name="ddi_concepts")
    op.drop_column("ddi_interaction_evidence", "source_record_metadata")
    for name in ("source_metadata", "source_bytes", "source_format"):
        op.drop_column("ddi_source_documents", name)
