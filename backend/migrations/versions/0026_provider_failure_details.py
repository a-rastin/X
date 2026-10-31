"""Keep private-safe provider failure details on the attempt ledger.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in (
        ("failure_class", sa.Text()),
        ("http_status", sa.Integer()),
        ("exception_type", sa.Text()),
        ("error_subcode", sa.Text()),
        ("elapsed_seconds", sa.Float()),
    ):
        op.add_column("reasoning_job_attempts", sa.Column(name, kind, nullable=True))
    op.create_check_constraint(
        "ck_attempt_failure_class",
        "reasoning_job_attempts",
        "failure_class IN ('timeout', 'disconnect', 'http_status', 'parse', 'other')",
    )
    op.create_check_constraint(
        "ck_attempt_elapsed",
        "reasoning_job_attempts",
        "elapsed_seconds >= 0 AND elapsed_seconds < 'Infinity'::float8",
    )
    op.create_check_constraint(
        "ck_attempt_http_status",
        "reasoning_job_attempts",
        "http_status BETWEEN 100 AND 599",
    )


def downgrade():
    for name in ("ck_attempt_http_status", "ck_attempt_elapsed", "ck_attempt_failure_class"):
        op.drop_constraint(name, "reasoning_job_attempts", type_="check")
    for name in (
        "elapsed_seconds",
        "error_subcode",
        "exception_type",
        "http_status",
        "failure_class",
    ):
        op.drop_column("reasoning_job_attempts", name)
