"""Cases storage: patients + encounters (S06, plan.md §4.1).

Patients: UUID, globally unique ten-digit text identifier (TEXT, never
numeric — leading zeros round-trip, including among archived rows), names,
sex, age, clinical status, optional phone, archive flag, revision, UTC
timestamps. Encounters: UUID, patient FK, kind (``registration`` in S06),
author FK (``users.id``), lifecycle (``draft``/``signed``/``discarded``),
revision, UTC timestamps. The partial unique index
``one_open_draft_per_patient`` enforces at most one open draft per patient
(plan §2.3); migration ``0004`` owns the DDL, this module is the read model
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
