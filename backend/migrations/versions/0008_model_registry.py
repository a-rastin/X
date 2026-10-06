"""0008: model version administration registry (S24).

Plan.md §§4.1, 7.3 (FR-03, FR-37): admin imports/edits XML as immutable
versions; activation atomically moves a per-workflow pointer with revision
and audit. Source BNs/ medical sources are never modified here — only
synthetic XML fixtures flow through these tables in tests.

Tables (new, S24-owned):
- ``model_networks`` — one row per admin-named network (unique name).
- ``model_network_versions`` — immutable versions: exact source bytes,
  SHA-256, status + review record; versions retained (no UPDATE/DELETE
  for the app role). Prior bytes/hash/reports survive edits because edits
  insert a new row.
- ``model_workflow_pointers`` — mutable activation pointer per workflow
  (``registration``/``followup``): active bundle JSON (synthetic selections
  until S39 defines real bundles), revision for If-Match/ETag, updated_at.
  No draft BN is active by default (no seed rows).

Grants mirror 0001-0007's idempotent DO $$ pattern: networks/versions
app SELECT+INSERT only (immutable), pointers app SELECT+INSERT+UPDATE
(mutable pointer, no DELETE), readonly SELECT, migrate ALL.
Downgrade drops the new tables; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLES = (
    "model_networks",
    "model_network_versions",
    "model_workflow_pointers",
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "model_networks",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_model_networks_name"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100",
                           name="ck_model_networks_name_bounds"),
    )
    op.create_table(
        "model_network_versions",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("network_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("review", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["network_id"], ["model_networks.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("network_id", "version_number",
                            name="uq_model_versions_network_number"),
        sa.CheckConstraint("version_number >= 1",
                           name="ck_model_versions_number_gte_1"),
        sa.CheckConstraint("status IN ('draft', 'awaiting_review', 'approved')",
                           name="ck_model_versions_status"),
        sa.CheckConstraint("char_length(source_hash) = 64",
                           name="ck_model_versions_hash_sha256"),
    )
    op.create_index("ix_model_versions_network_id",
                    "model_network_versions", ["network_id"])
    op.create_table(
        "model_workflow_pointers",
        sa.Column("workflow", sa.Text(), primary_key=True),
        sa.Column("active_bundle", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.CheckConstraint("workflow IN ('registration', 'followup')",
                           name="ck_model_pointers_workflow"),
        sa.CheckConstraint("revision >= 1",
                           name="ck_model_pointers_revision_gte_1"),
    )

    for table in TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM PUBLIC"))
    for role in ("x_insight_app", "x_insight_migrate", "x_insight_readonly"):
        for table in TABLES:
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    "BEGIN "
                    f"REVOKE ALL ON {table} FROM {role}; "
                    "EXCEPTION "
                    f"WHEN undefined_object THEN RAISE NOTICE 'skipping REVOKE for missing role {role}'; "  # noqa: E501
                    f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping REVOKE for role {role}: insufficient privilege'; "  # noqa: E501
                    "END; END $$;"
                )
            )
    for stmt, role in (
        ("GRANT SELECT, INSERT ON model_networks TO x_insight_app", "x_insight_app"),
        ("GRANT SELECT, INSERT ON model_network_versions TO x_insight_app",
         "x_insight_app"),
        ("GRANT SELECT, INSERT, UPDATE ON model_workflow_pointers TO x_insight_app",
         "x_insight_app"),
        ("GRANT SELECT ON model_networks TO x_insight_readonly", "x_insight_readonly"),
        ("GRANT SELECT ON model_network_versions TO x_insight_readonly",
         "x_insight_readonly"),
        ("GRANT SELECT ON model_workflow_pointers TO x_insight_readonly",
         "x_insight_readonly"),
        ("GRANT ALL ON model_networks TO x_insight_migrate", "x_insight_migrate"),
        ("GRANT ALL ON model_network_versions TO x_insight_migrate",
         "x_insight_migrate"),
        ("GRANT ALL ON model_workflow_pointers TO x_insight_migrate",
         "x_insight_migrate"),
    ):
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                "BEGIN "
                f"{stmt}; "
                "EXCEPTION "
                f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping {stmt}: insufficient privilege'; "  # noqa: E501
                "END; "
                "ELSE "
                f"RAISE NOTICE 'skipping {stmt}: role {role} missing'; "
                "END IF; END $$;"
            )
        )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_model_versions_network_id"))
    for table in reversed(TABLES):
        op.drop_table(table)
