"""Add follow-up proposal storage and immutable workflow identity.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_encounters_workflow", "encounters", type_="check")
    op.create_check_constraint(
        "ck_encounters_workflow",
        "encounters",
        "workflow_version IN ('legacy-v1', 'new-case-v1', 'follow-up-v1') "
        "AND (workflow_version != 'new-case-v1' OR kind = 'registration') "
        "AND (workflow_version != 'follow-up-v1' OR kind = 'follow_up')",
    )
    op.create_table(
        "follow_up_proposals",
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
            "encounter_id", "input_hash", "content_hash", name="uq_follow_up_proposal_inputs"
        ),
    )
    op.create_index(
        "ix_follow_up_proposals_encounter", "follow_up_proposals", ["encounter_id", "created_at"]
    )
    op.add_column(
        "signed_encounter_snapshots",
        sa.Column(
            "follow_up_proposal_id", UUID(as_uuid=True), sa.ForeignKey("follow_up_proposals.id")
        ),
    )
    op.drop_constraint("ck_signed_reference_variant", "signed_encounter_snapshots", type_="check")
    op.create_check_constraint(
        "ck_signed_reference_variant",
        "signed_encounter_snapshots",
        "(batch_id IS NOT NULL AND proposal_id IS NOT NULL AND new_case_proposal_id IS NULL "
        "AND follow_up_proposal_id IS NULL) OR "
        "(batch_id IS NULL AND proposal_id IS NULL AND new_case_proposal_id IS NOT NULL "
        "AND follow_up_proposal_id IS NULL) OR "
        "(batch_id IS NULL AND proposal_id IS NULL AND new_case_proposal_id IS NULL "
        "AND follow_up_proposal_id IS NOT NULL)",
    )
    op.execute("REVOKE ALL ON follow_up_proposals FROM PUBLIC")
    for role, grant in (
        ("x_insight_app", "SELECT, INSERT"),
        ("x_insight_readonly", "SELECT"),
        ("x_insight_migrate", "ALL"),
    ):
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN "
            f"GRANT {grant} ON follow_up_proposals TO {role}; END IF; END $$;"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM follow_up_proposals) OR EXISTS
           (SELECT 1 FROM encounters WHERE workflow_version = 'follow-up-v1') THEN
            RAISE EXCEPTION 'Cannot discard follow-up data';
        END IF;
    END $$""")
    op.drop_constraint("ck_signed_reference_variant", "signed_encounter_snapshots", type_="check")
    op.drop_column("signed_encounter_snapshots", "follow_up_proposal_id")
    op.create_check_constraint(
        "ck_signed_reference_variant",
        "signed_encounter_snapshots",
        "(batch_id IS NOT NULL AND proposal_id IS NOT NULL AND new_case_proposal_id IS NULL) OR "
        "(batch_id IS NULL AND proposal_id IS NULL AND new_case_proposal_id IS NOT NULL)",
    )
    op.drop_table("follow_up_proposals")
    op.drop_constraint("ck_encounters_workflow", "encounters", type_="check")
    op.create_check_constraint(
        "ck_encounters_workflow",
        "encounters",
        "workflow_version IN ('legacy-v1', 'new-case-v1') "
        "AND (workflow_version != 'new-case-v1' OR kind = 'registration')",
    )
