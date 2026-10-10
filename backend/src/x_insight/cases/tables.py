"""Cases storage: patients + encounters (S06-S07, plan.md §4.1).

Patients: UUID, globally unique ten-digit text identifier (TEXT, never
numeric — leading zeros round-trip, including among archived rows), names,
sex, age, clinical status, optional phone, archive flag, revision, UTC
timestamps. Encounters: UUID, patient FK, kind (``registration``/``follow_up``),
author FK (``users.id``), lifecycle (``draft``/``signed``/``discarded``),
revision, UTC timestamps, plus S07's ``draft_data`` JSONB clinical body (always
a JSON object, ``'{}'`` by default — the author's private autosave content).
The partial unique index ``one_open_draft_per_patient`` enforces at most one
open draft per patient (plan §2.3); migration ``0004`` owns the base DDL,
migration ``0005`` owns ``draft_data``, this module is the read model
for queries (no migrations here).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

metadata = MetaData()

patients = Table(
    "patients",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("identifier", Text, nullable=False, unique=True),
    Column("given_name", Text, nullable=False),
    Column("family_name", Text, nullable=False),
    Column("sex", Text, nullable=False),
    Column("age", Integer, nullable=False),
    Column("clinical_status", Text, nullable=False),
    Column("phone", Text, nullable=True),
    Column("archived", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    CheckConstraint("identifier ~ '^[0-9]{10}$'", name="ck_patients_identifier"),
    CheckConstraint("sex IN ('M', 'F')", name="ck_patients_sex"),
    CheckConstraint("age BETWEEN 18 AND 99", name="ck_patients_age"),
    CheckConstraint(
        "clinical_status IN ('first_time', 'established')",
        name="ck_patients_clinical_status",
    ),
)

encounters = Table(
    "encounters",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "patient_id",
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", Text, nullable=False, default="registration"),
    Column(
        "author_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("lifecycle", Text, nullable=False, default="draft"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    # S07 private autosave body: always a JSON object ('{}' for fresh drafts).
    # Lifecycle stays the persistence state; no calculation/readiness column
    # is added here (plan §2.3 keeps them distinct).
    Column("draft_data", JSONB, nullable=False, default=dict),
    CheckConstraint("kind IN ('registration', 'follow_up')", name="ck_encounters_kind"),
    CheckConstraint(
        "lifecycle IN ('draft', 'signed', 'discarded')", name="ck_encounters_lifecycle"
    ),
)

Index("ix_encounters_patient_id", encounters.c.patient_id)
Index(
    "one_open_draft_per_patient",
    encounters.c.patient_id,
    unique=True,
    postgresql_where=(encounters.c.lifecycle == "draft"),
)

# S13 attributed page notes (plan.md §§2.3, 4.1; FR-16, FR-22): one row per
# note, separate from ``draft_data`` and from the analysis-visible history
# channel. Append-only (no UPDATE/DELETE grant); migration ``0006`` owns
# the DDL, this module is the read model for queries (no migrations here).
notes = Table(
    "notes",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "encounter_id",
        PG_UUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("page", Text, nullable=False),
    Column(
        "author_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Username snapshot at write time (stable attribution even if renamed).
    Column("author_display", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("text", Text, nullable=False),
    CheckConstraint("char_length(page) BETWEEN 1 AND 64", name="ck_notes_page_nonempty"),
    CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="ck_notes_text_bounds"),
)

Index("ix_notes_encounter_id", notes.c.encounter_id)
Index("ix_notes_encounter_page", notes.c.encounter_id, notes.c.page)

# S49 secondary plans + signed snapshots + addenda (plan.md §§4.1, 9.2;
# FR-15, FR-22, FR-57, NFR-04): separate revisioned physician plan text
# (own If-Match fence, never in the analysis fingerprint), one immutable
# signed snapshot per encounter (full frozen record + hash), append-only
# attributed addenda. Migration ``0016`` owns the DDL, this module is the
# read model for queries (no migrations here).
secondary_plans = Table(
    "secondary_plans",
    metadata,
    Column(
        "encounter_id",
        PG_UUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("text", Text, nullable=False, default=""),
    Column("revision", Integer, nullable=False, default=1),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision >= 1", name="ck_secondary_plans_rev_gte_1"),
)

signed_encounter_snapshots = Table(
    "signed_encounter_snapshots",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "encounter_id",
        PG_UUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    Column(
        "patient_id",
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "batch_id",
        PG_UUID(as_uuid=True),
        ForeignKey("generation_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "proposal_id",
        PG_UUID(as_uuid=True),
        ForeignKey("proposal_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("secondary_plan_revision", Integer, nullable=False),
    Column("secondary_plan_text", Text, nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("snapshot_hash", Text, nullable=False),
    Column(
        "signer_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("signer_username", Text, nullable=False),
    Column("signed_at", DateTime(timezone=True), nullable=False),
    Column("encounter_revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("secondary_plan_revision >= 1", name="ck_signed_plan_rev_gte_1"),
    CheckConstraint("encounter_revision >= 1", name="ck_signed_enc_rev_gte_1"),
    CheckConstraint("char_length(snapshot_hash) = 64", name="ck_signed_snapshot_sha256"),
)

encounter_addenda = Table(
    "encounter_addenda",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "encounter_id",
        PG_UUID(as_uuid=True),
        ForeignKey("encounters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "author_id",
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Username snapshot at write time (stable attribution even if renamed).
    Column("author_display", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("text", Text, nullable=False),
    CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="ck_addenda_text_bounds"),
)

Index("ix_encounter_addenda_encounter_id", encounter_addenda.c.encounter_id)
Index("ix_signed_snapshots_patient_id", signed_encounter_snapshots.c.patient_id)
Index("ix_signed_snapshots_batch_id", signed_encounter_snapshots.c.batch_id)
