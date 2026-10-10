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

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import tables
from x_insight.operations import audit as audit_module

PATIENTS_CREATE_OPERATION = "patients.create"
PATIENTS_ARCHIVE_OPERATION = "patients.archive"
PATIENTS_UNARCHIVE_OPERATION = "patients.unarchive"
PATIENTS_UPDATE_OPERATION = "patients.update"
REGISTRATION_KIND = "registration"
DRAFT_LIFECYCLE = "draft"

# S51 §2 provisional analysis-visible demographics (plan §4.2): age/sex/
# clinical_status are relevant inputs (included in freshness); names/
# identifier/phone are identifying and excluded (never model inputs).
# Provisional only - NOT owner-confirmed, no clinical thresholds invented.
RELEVANT_DEMOGRAPHIC_FIELDS = ("age", "sex", "clinical_status")

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


def require_patient_not_archived(patient: dict[str, Any]) -> dict[str, Any]:
    """Provisional archive policy: archived patients are read-only (409).

    Archived blocks create/edit/sign and CPT/generation mutations without
    leaking draft content. Reads (author GET draft, shared chart) stay
    allowed; unarchive resumes writes. This policy is provisional per
    plan §1.3 - NOT owner-confirmed, do not mark confirmed.
    """
    if bool(patient.get("archived", False)):
        raise contracts.ContractError(
            409,
            "PATIENT_ARCHIVED",
            "Patient is archived and read-only.",
            {"patient_id": ["Patient is archived."]},
        )
    return patient


def set_patient_archived(
    session: Session,
    patient_id: Any,
    *,
    archived: bool,
    expected_revision: int,
    actor: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], str, bool]:
    """Admin-only archive/unarchive with patient-revision fence (412 stale).

    Row-locked read-modify-write in the caller's transaction: 404 missing,
    412 on revision mismatch (changes nothing), idempotent no-op when already
    in the desired state (no bump, no audit). Success flips the flag with
    revision+1 and audits. Returns (patient, server_timestamp, changed).
    """
    import uuid as _uuid

    try:
        want = _uuid.UUID(str(patient_id))
    except Exception as exc:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.") from exc
    row = (
        session.execute(
            select(tables.patients).where(tables.patients.c.id == want).with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    patient = dict(row)
    if int(patient["revision"]) != int(expected_revision):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The patient changed. Reload and reconcile your edits.",
        )
    if bool(patient.get("archived", False)) == bool(archived):
        return patient, contracts.serialize_utc(patient["updated_at"]), False
    moment = contracts.utcnow()
    session.execute(
        update(tables.patients)
        .where(tables.patients.c.id == want)
        .values(archived=bool(archived), revision=int(expected_revision) + 1, updated_at=moment)
    )
    session.flush()
    updated = get_patient(session, want)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation=("patients.archive.success" if archived else "patients.unarchive.success"),
        actor=str(actor.get("username")),
        request_id=request_id,
        details={"patient_id": str(want), "archived": bool(archived)},
    )
    return updated, contracts.serialize_utc(moment), True


def archive_request_hash(patient_id: Any, expected_revision: int, archived: bool) -> str:
    """Canonical hash for archive/unarchive idempotency (target + rev + flag)."""
    return contracts.canonical_hash(
        {
            "target_id": str(patient_id),
            "expected_revision": int(expected_revision),
            "archived": bool(archived),
        }
    )


def require_encounter_mutable(
    session: Session, encounter: dict[str, Any], author: dict[str, Any] | None = None
) -> dict[str, Any]:
    """S51 §1+§4 provisional read-only fence for draft mutations (409/403).

    Locks the patient row FOR UPDATE (serializes archive/demographics vs
    draft writes), raises 409 PATIENT_ARCHIVED when archived (no content
    leak), and 403 when the author is inactive (closes deactivation races).
    Reads stay allowed; unarchive resumes writes. Provisional per plan §1.3.
    """
    from sqlalchemy import select as _select

    patient_row = (
        session.execute(
            _select(tables.patients)
            .where(tables.patients.c.id == encounter["patient_id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if patient_row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    if bool(dict(patient_row).get("archived", False)):
        raise contracts.ContractError(
            409,
            "PATIENT_ARCHIVED",
            "Patient is archived and read-only.",
            {"patient_id": ["Patient is archived."]},
        )
    if author is not None:
        from x_insight.identity import service as _identity

        author_row = _identity.get_user_by_id(session, author["id"])
        if author_row is None or not bool(author_row.get("active", False)):
            raise contracts.ContractError(403, "FORBIDDEN", "Author is inactive.")
    return encounter


def relevant_demographics(patient: dict[str, Any]) -> dict[str, Any]:
    """Provisional analysis-visible demographics (S51 §2, plan §4.2).

    Only age/sex/clinical_status enter freshness; names/identifier/phone are
    identifying and excluded (never model inputs). Provisional - NOT
    owner-confirmed, no clinical thresholds invented.
    """
    return {
        "age": int(patient.get("age", 0)),
        "sex": str(patient.get("sex", "")),
        "clinical_status": str(patient.get("clinical_status", "")),
    }


def relevant_demographics_hash(patient: dict[str, Any]) -> str:
    """Stable hash of relevant demographics for staleness comparison."""
    return contracts.canonical_hash(relevant_demographics(patient))


def validate_patch_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Validate shared demographics PATCH (422, identifier/archived immutable).

    Allows given_name/family_name/sex/age/clinical_status/phone; identifier
    and archived travel via their own routes (422 here, never silently
    ignored). Empty body is 422. Returns normalized storage fields.
    """
    if not isinstance(body, dict) or not body:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "Nothing to update.", {"request": ["Provide fields."]}
        )
    allowed = {"given_name", "family_name", "sex", "age", "clinical_status", "phone"}
    unknown = set(body) - allowed
    if unknown:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Unknown or immutable field.",
            {name: ["Unknown or immutable field."] for name in sorted(unknown)},
        )
    normalized: dict[str, Any] = {}
    errors: dict[str, list[str]] = {}
    if "given_name" in body:
        try:
            normalized["given_name"] = normalize_person_name(body["given_name"], "given_name")
        except contracts.ContractError as exc:
            errors.update(exc.field_errors)
    if "family_name" in body:
        try:
            normalized["family_name"] = normalize_person_name(body["family_name"], "family_name")
        except contracts.ContractError as exc:
            errors.update(exc.field_errors)
    if "sex" in body:
        if body["sex"] not in SEXES:
            errors["sex"] = ["Must be 'M' or 'F'."]
        else:
            normalized["sex"] = body["sex"]
    if "age" in body:
        age = body["age"]
        if isinstance(age, bool) or not isinstance(age, int) or not 18 <= age <= 99:
            errors["age"] = ["Must be an integer 18-99."]
        else:
            normalized["age"] = age
    if "clinical_status" in body:
        if body["clinical_status"] not in CLINICAL_STATUSES:
            errors["clinical_status"] = ["Must be 'first_time' or 'established'."]
        else:
            normalized["clinical_status"] = body["clinical_status"]
    if "phone" in body:
        phone = body["phone"]
        if phone is not None and not isinstance(phone, str):
            errors["phone"] = ["Must be text."]
        else:
            normalized["phone"] = phone
    if errors:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "Patient demographics are invalid.", errors
        )
    return normalized


def update_patient_demographics(
    session: Session,
    patient_id: Any,
    *,
    fields: dict[str, Any],
    expected_revision: int,
    actor: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], str]:
    """Physician-only shared demographics edit (If-Match fence, revision bump).

    Row-locked read-modify-write: 404 missing, 412 stale (changes nothing),
    422 invalid (changes nothing). Bumps revision+1 and audits; never carries
    draft content (no draft grant - stranger cannot read drafts via this
    route). Relevant edits (age/sex/status) invalidate affected results via
    S48d per-question freshness (stored relevant hash vs current); phone/name
    do not. Returns (patient, server_timestamp).
    """
    import uuid as _uuid

    try:
        want = _uuid.UUID(str(patient_id))
    except Exception as exc:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.") from exc
    row = (
        session.execute(
            select(tables.patients).where(tables.patients.c.id == want).with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    patient = dict(row)
    if int(patient["revision"]) != int(expected_revision):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The patient changed. Reload and reconcile your edits.",
        )
    normalized = validate_patch_fields(fields)
    moment = contracts.utcnow()
    session.execute(
        update(tables.patients)
        .where(tables.patients.c.id == want)
        .values(**normalized, revision=int(expected_revision) + 1, updated_at=moment)
    )
    session.flush()
    updated = get_patient(session, want)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="patients.update.success",
        actor=str(actor.get("username")),
        request_id=request_id,
        details={"patient_id": str(want), "fields": sorted(normalized)},
    )
    return updated, contracts.serialize_utc(moment)


def update_request_hash(patient_id: Any, expected_revision: int, body: dict[str, Any]) -> str:
    """Canonical hash for demographics PATCH idempotency (target + rev + body)."""
    return contracts.canonical_hash(
        {"target_id": str(patient_id), "expected_revision": int(expected_revision), "body": body}
    )
