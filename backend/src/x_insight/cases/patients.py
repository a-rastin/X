"""Patient registry commands: register, read, search (S06).

Plan.md §§2.2-2.3, 4.1-4.3 (FR-10, FR-23):

- Registration atomically creates one ``patients`` row plus one ``encounters``
  row (``kind='registration'``, ``lifecycle='draft'``) in the caller's
  transaction, together with the ``patients.create.success`` audit event. The
  global unique constraint on ``patients.identifier`` settles duplicate races
  (including archived rows): the loser gets a generic ``409 CONFLICT``, never
  a merge. There is deliberately no patient-row lock here — the patient does
  not exist yet, so the unique constraint is the race guard.
- Validation mirrors plan §2.2 exactly: names are Unicode letters after NFC
  normalization (nonempty, no whitespace/digits/punctuation, casing
  preserved); sex ``M|F``; integer age 18-99; identifier exactly ten ASCII
  digits stored as TEXT (never numeric — ``0012345678`` round-trips
  unchanged); clinical status ``first_time|established``; phone is optional
  free text with no country validation.
- Directory search matches name substrings (case-insensitive, given/family/
  full name) and identifier substrings, filters clinical status, pages with
  the shared bounded contract (default 25, max 100), orders stably by
  creation time then id, and excludes archived patients unless explicitly
  included. Results are shared across physicians (no author scoping).

All persistence uses the caller's :func:`x_insight.db.session_scope`
transaction; audit is recorded in the same transaction and this module never
commits. No delete/merge exists in v1.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any

from sqlalchemy import func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import tables
from x_insight.operations import audit as audit_module

PATIENTS_CREATE_OPERATION = "patients.create"
REGISTRATION_KIND = "registration"
DRAFT_LIFECYCLE = "draft"

SEXES = ("M", "F")
CLINICAL_STATUSES = ("first_time", "established")

# ASCII digits only: [0-9] never matches non-ASCII decimal digits (unlike \d),
# so fullwidth/Arabic-Indic digits fail validation as required.
_IDENTIFIER_RE = re.compile(r"[0-9]{10}\Z")


def validate_identifier(value: Any) -> str:
    """Exactly ten ASCII digits stored as text (leading zeros preserved)."""
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Patient ID must be exactly 10 ASCII digits.",
            {"identifier": ["Must be exactly 10 ASCII digits (0-9)."]},
        )
    return value


def normalize_person_name(value: Any, field: str) -> str:
    """NFC-normalize a name, then require nonempty Unicode letters only.

    ``str.isalpha`` accepts Unicode letters (after NFC composition, e.g.
    ``e`` + combining acute becomes ``é``) while rejecting whitespace,
    digits, and punctuation. Casing is preserved as sent.
    """
    if not isinstance(value, str):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Name must be text.",
            {field: ["Must be text."]},
        )
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or not all(char.isalpha() for char in normalized):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Name must contain Unicode letters only.",
            {
                field: [
                    "Must be non-empty Unicode letters only (no spaces, digits, or punctuation)."
                ]
            },  # noqa: E501
        )
    return normalized


def validate_registration_payload(
    *,
    identifier: Any,
    given_name: Any,
    family_name: Any,
    sex: Any,
    age: Any,
    clinical_status: Any,
    phone: Any = None,
) -> dict[str, Any]:
    """Validate + normalize one registration body (422 with field_errors).

    Structural typing (str/int/Literal, no extra keys) is enforced by the
    HTTP schema; this is the semantic contract shared by every entry point.
    Returns the normalized storage fields (unknown keys never pass through).
    """
    errors: dict[str, list[str]] = {}
    normalized: dict[str, Any] = {}
    try:
        normalized["identifier"] = validate_identifier(identifier)
    except contracts.ContractError as exc:
        errors.update(exc.field_errors)
    try:
        normalized["given_name"] = normalize_person_name(given_name, "given_name")
    except contracts.ContractError as exc:
        errors.update(exc.field_errors)
    try:
        normalized["family_name"] = normalize_person_name(family_name, "family_name")
    except contracts.ContractError as exc:
        errors.update(exc.field_errors)
    if sex not in SEXES:
        errors["sex"] = ["Must be 'M' or 'F'."]
    else:
        normalized["sex"] = sex
    # Bool is an int subclass but never a valid age; floats/strings are
    # rejected even when they look whole (30.0 is a decimal age, "30" is text).
    if isinstance(age, bool) or not isinstance(age, int) or not 18 <= age <= 99:
        errors["age"] = ["Must be an integer 18-99."]
    else:
        normalized["age"] = age
    if clinical_status not in CLINICAL_STATUSES:
        errors["clinical_status"] = ["Must be 'first_time' or 'established'."]
    else:
        normalized["clinical_status"] = clinical_status
    # Optional free text, never a number; no country/format validation.
    if phone is not None and not isinstance(phone, str):
        errors["phone"] = ["Must be text."]
    else:
        normalized["phone"] = phone
    if errors:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "Patient demographics are invalid.", errors
        )
    return normalized


def validate_clinical_status_filter(value: str | None) -> str | None:
    """Validate the directory ``clinical_status`` filter (None = no filter)."""
    if value is None:
        return None
    if value not in CLINICAL_STATUSES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid clinical status filter.",
            {"clinical_status": ["Must be 'first_time' or 'established'."]},
        )
    return value


def safe_patient(row: dict[str, Any]) -> dict[str, Any]:
    """Public patient shape (demographics + revision; never secrets)."""
    return {
        "id": str(row["id"]),
        "identifier": row["identifier"],
        "given_name": row["given_name"],
        "family_name": row["family_name"],
        "sex": row["sex"],
        "age": int(row["age"]),
        "clinical_status": row["clinical_status"],
        "phone": row["phone"],
        "archived": bool(row["archived"]),
        "revision": int(row["revision"]),
        "created_at": contracts.serialize_utc(row["created_at"]),
        "updated_at": contracts.serialize_utc(row["updated_at"]),
    }


def safe_encounter(row: dict[str, Any]) -> dict[str, Any]:
    """Public registration-draft shape (references only, no clinical body)."""
    return {
        "id": str(row["id"]),
        "patient_id": str(row["patient_id"]),
        "kind": row["kind"],
        "author_id": str(row["author_id"]),
        "lifecycle": row["lifecycle"],
        "revision": int(row["revision"]),
        "created_at": contracts.serialize_utc(row["created_at"]),
        "updated_at": contracts.serialize_utc(row["updated_at"]),
    }


def get_patient(session: Session, patient_id: uuid.UUID) -> dict[str, Any] | None:
    """Read one patient by internal UUID (None when missing)."""
    row = (
        session.execute(select(tables.patients).where(tables.patients.c.id == patient_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_encounter(session: Session, encounter_id: uuid.UUID) -> dict[str, Any] | None:
    """Read one encounter by internal UUID (None when missing)."""
    row = (
        session.execute(select(tables.encounters).where(tables.encounters.c.id == encounter_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def create_patient_with_draft(
    session: Session,
    *,
    author: dict[str, Any],
    fields: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Atomically create a patient plus its registration draft (409 on race).

    One transaction (the caller's): patient row, draft encounter row, and the
    audit event commit together. A duplicate identifier — sequential, raced,
    or archived — violates the global unique constraint and becomes a generic
    ``409 CONFLICT`` naming the identifier (never a merge, never a 500).
    Returns (patient, draft, server timestamp).
    """
    moment = contracts.utcnow()
    patient_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    try:
        session.execute(
            insert(tables.patients).values(
                id=patient_id,
                identifier=fields["identifier"],
                given_name=fields["given_name"],
                family_name=fields["family_name"],
                sex=fields["sex"],
                age=fields["age"],
                clinical_status=fields["clinical_status"],
                phone=fields["phone"],
                archived=False,
                revision=1,
                created_at=moment,
                updated_at=moment,
            )
        )
        session.execute(
            insert(tables.encounters).values(
                id=encounter_id,
                patient_id=patient_id,
                kind=REGISTRATION_KIND,
                author_id=author["id"],
                lifecycle=DRAFT_LIFECYCLE,
                revision=1,
                created_at=moment,
                updated_at=moment,
            )
        )
        # Flush inside the command so the unique/partial constraints fire
        # here (mapped to 409 below) instead of at scope-commit time (500).
        session.flush()
    except IntegrityError as exc:
        # The transaction is aborted: roll back so the caller's scope-commit
        # is a clean no-op, then report the generic duplicate conflict.
        session.rollback()
        raise contracts.ContractError(
            409,
            "CONFLICT",
            "Patient ID already exists.",
            {"identifier": ["Already exists."]},
        ) from exc
    patient = get_patient(session, patient_id)
    draft = get_encounter(session, encounter_id)
    assert patient is not None and draft is not None
    audit_module.record_audit(
        session,
        operation="patients.create.success",
        actor=str(author["username"]),
        request_id=request_id,
        details={"patient_id": str(patient_id), "identifier": fields["identifier"]},
    )
    return patient, draft, contracts.serialize_utc(moment)


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so the directory query matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_patients(
    session: Session,
    *,
    query: str | None,
    clinical_status: str | None,
    limit: int | None,
    offset: int | None,
    include_archived: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Shared directory search (no author scoping; archived opt-in only).

    Matches identifier substrings plus given/family/full-name substrings
    case-insensitively, filters clinical status, and pages with the shared
    bounded contract. Stable order: creation time, then id. Returns
    (items, total) where total counts the filter before pagination.
    """
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    filters: list[Any] = []
    if not include_archived:
        filters.append(tables.patients.c.archived.is_(False))
    if clinical_status is not None:
        filters.append(tables.patients.c.clinical_status == clinical_status)
    if query:
        pattern = f"%{_escape_like(query)}%"
        full_name = tables.patients.c.given_name + " " + tables.patients.c.family_name
        filters.append(
            or_(
                tables.patients.c.identifier.like(pattern, escape="\\"),
                tables.patients.c.given_name.ilike(pattern, escape="\\"),
                tables.patients.c.family_name.ilike(pattern, escape="\\"),
                full_name.ilike(pattern, escape="\\"),
            )
        )
    total = int(
        session.execute(
            select(func.count()).select_from(tables.patients).where(*filters)
        ).scalar_one()
    )
    rows = (
        session.execute(
            select(tables.patients)
            .where(*filters)
            .order_by(tables.patients.c.created_at, tables.patients.c.id)
            .limit(resolved_limit)
            .offset(resolved_offset)
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows], total


def idempotency_request_hash(fields: dict[str, Any]) -> str:
    """Canonical hash of the normalized registration body (replay key)."""
    return contracts.canonical_hash({"body": fields})
