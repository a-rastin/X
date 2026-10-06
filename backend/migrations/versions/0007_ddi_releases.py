"""0007: immutable DDI dataset releases (S18).

Plan.md §§6.1-6.2 (FR-14, NFR-05): offline ingestion builds a candidate
dataset; S18 publishes an atomic immutable release only after its declared
review gate passes. Original monographs under project-documents/ are never
modified; corrections land as overlay records referencing source spans.

Tables (all immutable, no UPDATE/DELETE for the app role):
- ``ddi_dataset_releases`` — one row per published release, anchored by
  ``content_hash`` (canonical hash of dataset + manifest core). Same hash
  re-publishes the same version (UNIQUE, no duplicate rows); a changed
  source yields a different hash and a new row. Failed/invalid publication
  rolls back its transaction, so the prior released row stays readable.
- ``ddi_source_documents`` — per-release source inventory (path + checksum).
- ``ddi_concepts`` — per-release concept snapshot (stable ID, canonical
  name, type, catalog ID). Alias review provenance lives in the release
  ``manifest`` JSONB; runtime checks resolve catalog IDs, not free text.
- ``ddi_interaction_evidence`` — one row per ingested assertion. Duplicate
  directions from independent sources stay separate rows; ``source_severity``
  keeps the monograph category verbatim while ``raw_text`` preserves the
  full management prose (never inferred, never overwritten). ``correction``
  holds an optional owner-approved overlay; the original columns stay
  unchanged.

Grants mirror 0001-0006's idempotent DO $$ pattern: app SELECT only
(immutable release reads for S19), readonly SELECT, migrate ALL.
Downgrade drops the new tables; roles kept.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = (
    "ddi_dataset_releases",
    "ddi_source_documents",
    "ddi_concepts",
    "ddi_interaction_evidence",
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.create_table(
        "ddi_dataset_releases",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("terminology_version", sa.Text(), nullable=False),
        sa.Column("terminology_checksum", sa.Text(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("content_hash", name="uq_ddi_releases_content_hash"),
        sa.UniqueConstraint("version", name="uq_ddi_releases_version"),
        sa.CheckConstraint(
            "status IN ('released_complete', 'released_limited')",
            name="ck_ddi_releases_status",
        ),
    )
    op.create_table(
        "ddi_source_documents",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("release_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["release_id"], ["ddi_dataset_releases.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "release_id", "source_path", name="uq_ddi_documents_release_path"
        ),
    )
    op.create_table(
        "ddi_concepts",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("release_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("concept_type", sa.Text(), nullable=False),
        sa.Column("catalog_drug_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_id"], ["ddi_dataset_releases.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "release_id", "concept_id", name="uq_ddi_concepts_release_concept"
        ),
    )
    op.create_table(
        "ddi_interaction_evidence",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("release_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("source_severity", sa.Text(), nullable=False),
        sa.Column("interacting_name", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("subject_concept_id", sa.Text(), nullable=True),
        sa.Column("interacting_concept_id", sa.Text(), nullable=True),
        sa.Column("pair_key", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("correction", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_id"], ["ddi_dataset_releases.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_ddi_evidence_release_pair",
        "ddi_interaction_evidence",
        ["release_id", "pair_key"],
    )
    op.create_index(
        "ix_ddi_evidence_release_source",
        "ddi_interaction_evidence",
        ["release_id", "source_path"],
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
        ("SELECT", "x_insight_app"),
        ("SELECT", "x_insight_readonly"),
        ("ALL", "x_insight_migrate"),
    ):
        for table in TABLES:
            full = f"GRANT {stmt} ON {table} TO {role}"
            op.execute(
                sa.text(
                    "DO $$ BEGIN "
                    f"IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                    "BEGIN "
                    f"{full}; "
                    "EXCEPTION "
                    f"WHEN insufficient_privilege THEN RAISE NOTICE 'skipping {full}: insufficient privilege'; "  # noqa: E501
                    "END; "
                    "ELSE "
                    f"RAISE NOTICE 'skipping {full}: role {role} missing'; "
                    "END IF; END $$;"
                )
            )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ddi_evidence_release_source"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ddi_evidence_release_pair"))
    for table in reversed(TABLES):
        op.drop_table(table)
