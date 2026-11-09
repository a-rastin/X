"""Add frozen new-case workflow identity and observed birth year.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("patients", sa.Column("birth_year", sa.Integer(), nullable=True))
    op.drop_constraint("ck_patients_age", "patients", type_="check")
    op.create_check_constraint("ck_patients_age", "patients", "age BETWEEN 18 AND 100")
    op.add_column(
        "encounters",
        sa.Column("workflow_version", sa.Text(), nullable=False, server_default="legacy-v1"),
    )
    op.create_check_constraint(
        "ck_encounters_workflow",
        "encounters",
        "workflow_version IN ('legacy-v1', 'new-case-v1') "
        "AND (workflow_version != 'new-case-v1' OR kind = 'registration')",
    )
    op.execute("""
        CREATE FUNCTION prevent_workflow_identity_change() RETURNS trigger AS $$
        BEGIN
            IF NEW.workflow_version IS DISTINCT FROM OLD.workflow_version THEN
                RAISE EXCEPTION 'Encounter workflow identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER encounters_workflow_immutable
        BEFORE UPDATE OF workflow_version ON encounters
        FOR EACH ROW EXECUTE FUNCTION prevent_workflow_identity_change();
    """)


def downgrade():
    op.execute("DROP TRIGGER encounters_workflow_immutable ON encounters")
    op.execute("DROP FUNCTION prevent_workflow_identity_change()")
    op.drop_constraint("ck_encounters_workflow", "encounters", type_="check")
    op.drop_column("encounters", "workflow_version")
    op.drop_column("patients", "birth_year")
    op.drop_constraint("ck_patients_age", "patients", type_="check")
    op.create_check_constraint("ck_patients_age", "patients", "age BETWEEN 18 AND 99")
