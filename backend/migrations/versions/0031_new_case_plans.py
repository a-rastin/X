"""Complete new-case identity, proposal storage and additive v4 signatures.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("name_fa", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("name_en", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "unavailable_antipsychotics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_table(
        "new_case_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "encounter_id", UUID(as_uuid=True), sa.ForeignKey("encounters.id"), nullable=False
        ),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_encounter_revision", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "model_version_id", UUID(as_uuid=True), sa.ForeignKey("model_network_versions.id")
        ),
        sa.Column(
            "ddi_release_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ddi_dataset_releases.id"),
            nullable=False,
        ),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "encounter_id", "input_hash", "content_hash", name="uq_new_case_proposal_inputs"
        ),
    )
    op.create_index(
        "ix_new_case_proposals_encounter", "new_case_proposals", ["encounter_id", "created_at"]
    )
    op.add_column(
        "signed_encounter_snapshots",
        sa.Column(
            "new_case_proposal_id", UUID(as_uuid=True), sa.ForeignKey("new_case_proposals.id")
        ),
    )
    for column in ("batch_id", "proposal_id"):
        op.alter_column("signed_encounter_snapshots", column, nullable=True)
    op.create_check_constraint(
        "ck_signed_reference_variant",
        "signed_encounter_snapshots",
        "(batch_id IS NOT NULL AND proposal_id IS NOT NULL AND new_case_proposal_id IS NULL) OR "
        "(batch_id IS NULL AND proposal_id IS NULL AND new_case_proposal_id IS NOT NULL)",
    )
    op.execute("REVOKE ALL ON new_case_proposals FROM PUBLIC")
    for role, grant in (
        ("x_insight_app", "SELECT, INSERT"),
        ("x_insight_readonly", "SELECT"),
        ("x_insight_migrate", "ALL"),
    ):
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN "
            f"GRANT {grant} ON new_case_proposals TO {role}; END IF; END $$;"
        )


def downgrade():
    # Signed new-case data must be preserved rather than silently destroyed.
    op.drop_constraint("ck_signed_reference_variant", "signed_encounter_snapshots")
    for column in ("batch_id", "proposal_id"):
        op.alter_column("signed_encounter_snapshots", column, nullable=False)
    op.drop_column("signed_encounter_snapshots", "new_case_proposal_id")
    op.drop_table("new_case_proposals")
    for column in ("unavailable_antipsychotics", "name_en", "name_fa"):
        op.drop_column("users", column)
