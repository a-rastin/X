"""Author-owned draft commands: save/read/discard + single-slot creation (S07).

Plan.md §§2.3, 4.1-4.3 (FR-16, FR-22, NFR-04):

- One patient has at most one open draft across authors and encounter kinds.
  Creation takes a patient-row lock (``SELECT ... FOR UPDATE``) so concurrent
  creates serialize; the partial unique index ``one_open_draft_per_patient``
  remains the backstop. A second create — sequential, raced, or by the same
  author — is a generic ``409 OPEN_DRAFT_EXISTS`` naming no author and no
  clinical content.
- Only the draft's author can read its clinical body (``draft_data``) or
  derived artifacts through ordinary routes, and only the author can save or
  discard it. Other physicians and administrators get ``403`` without content;
  patient directory/demographics routes never carry ``draft_data`` (transitive
  privacy — enforced by the serializers here, verified in
  ``BT/http/test_drafts.py``).
- Lifecycle (``draft``/``signed``/``discarded``) is the persistence state and
  is distinct from calculation/readiness state (no readiness column exists in
  S07). ``GET``/``PATCH`` serve drafts only; discarded rows read as ``404``.
  Discard moves lifecycle to ``discarded`` (releasing the slot atomically),
  never deletes the patient, and requires explicit confirmation plus the
  current revision. Physical draft-content retention remains proposed (plan
  §1.3) — this module makes no retention promise; the row is simply kept.
- Optimistic concurrency: ``PATCH`` and discard require ``If-Match`` with the
  current revision (``contracts.parse_if_match``/``format_etag``). A mismatch
  is ``412 STALE_REVISION``; the failed write changes nothing, so the client
  keeps its unsaved edits and reconciles against server truth from ``GET``.
- Job cancellation: :func:`cancel_draft_jobs` is the explicit hook discard
  calls. No job tables exist yet (S07), so it is a no-op by design — later
  sessions fill it without changing this contract. Do not fabricate job
  tables here.

All persistence uses the caller's :func:`x_insight.db.session_scope`
transaction; audit is recorded in the same transaction and this module never
commits. No delete/merge, archive, assessment, DDI, note, or signing
machinery is added here.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import tables
from x_insight.operations import audit as audit_module

ENCOUNTERS_CREATE_OPERATION = "encounters.create"
ENCOUNTERS_PATCH_OPERATION = "encounters.patch"
ENCOUNTERS_DISCARD_OPERATION = "encounters.discard"

KINDS = ("registration", "follow_up")
DRAFT_LIFECYCLE = "draft"
DISCARDED_LIFECYCLE = "discarded"

# Autosave body bounds: generous for wizard pages, small enough to keep the
# 1 MiB edge cap irrelevant. Depth/node limits stop pathological nesting.
MAX_DRAFT_TOP_KEYS = 100
MAX_DRAFT_KEY_CHARS = 128
MAX_DRAFT_DEPTH = 6
MAX_DRAFT_NODES = 1000
MAX_DRAFT_SERIALIZED_BYTES = 200_000


def validate_kind(value: Any) -> str:
    """Encounter kind for single-slot creation (422 otherwise)."""
    if value not in KINDS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid encounter kind.",
            {"kind": ["Must be 'registration' or 'follow_up'."]},
        )
    return str(value)


def _check_draft_nodes(value: Any, depth: int, counter: list[int]) -> None:
    """Recurse the draft body enforcing depth/node/JSON-type bounds."""
    counter[0] += 1
    if counter[0] > MAX_DRAFT_NODES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content is too large.",
            {"draft_data": [f"Must have at most {MAX_DRAFT_NODES} values."]},
        )
    if depth > MAX_DRAFT_DEPTH:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content is nested too deeply.",
            {"draft_data": [f"Must nest at most {MAX_DRAFT_DEPTH} levels deep."]},
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_DRAFT_KEY_CHARS:
                raise contracts.ContractError(
                    422,
                    "VALIDATION_FAILED",
                    "Draft content keys are invalid.",
                    {
                        "draft_data": [
                            "Keys must be non-empty text "
                            f"of at most {MAX_DRAFT_KEY_CHARS} characters."
                        ]
                    },
                )
            _check_draft_nodes(item, depth + 1, counter)
    elif isinstance(value, list):
        for item in value:
            _check_draft_nodes(item, depth + 1, counter)
    elif not isinstance(value, (str, int, float, bool)) and value is not None:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content values are invalid.",
            {
                "draft_data": [
                    "Values must be JSON text, numbers, booleans, null, lists, or objects."
                ]
            },
        )


def validate_draft_data(value: Any) -> dict[str, Any]:
    """Validate the opaque autosave body shared by all wizard pages (S07+).

    The body is always a JSON object (top-level dict) so later assessment
    pages reuse this same autosave path instead of inventing separate
    persistence mechanisms. Structural typing (dict at the top) is enforced
    by the HTTP schema; this is the semantic contract: bounded keys, depth,
    node count, serialized size, and canonical-JSON serializability (which
    also rejects non-finite floats). Returns the value unchanged.
    """
    if not isinstance(value, dict):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content must be a JSON object.",
            {"draft_data": ["Must be a JSON object."]},
        )
    if len(value) > MAX_DRAFT_TOP_KEYS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content is too large.",
            {"draft_data": [f"Must have at most {MAX_DRAFT_TOP_KEYS} top-level fields."]},
        )
    _check_draft_nodes(value, 0, [0])
    try:
        serialized = contracts.canonical_json(value)
    except TypeError as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content is not serializable.",
            {"draft_data": ["Must be finite JSON values only."]},
        ) from exc
    if len(serialized) > MAX_DRAFT_SERIALIZED_BYTES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Draft content is too large.",
            {"draft_data": [f"Must serialize to at most {MAX_DRAFT_SERIALIZED_BYTES} bytes."]},
        )
    return value


def validate_discard_confirm(value: Any) -> bool:
    """Discard requires explicit confirmation (422 otherwise)."""
    if value is not True:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Discard requires confirmation.",
            {"confirm": ["Must be true to discard this draft."]},
        )
    return True


def require_if_match_revision(raw: str | None) -> int:
    """Draft writes require ``If-Match`` with the current revision.

    Absent/``"*"`` preconditions are ``422`` (the client must name the base
    revision it edited — otherwise stale-tab loss is silent); malformed
    values are ``422`` via :func:`contracts.parse_if_match`; a well-formed
    but outdated revision becomes ``412`` at the call site.
    """
    if raw is None or raw.strip() == "*":
        raise contracts.ContractError(
            422,
            "INVALID_IF_MATCH",
            'If-Match with the current revision is required (e.g. "3").',
            {"if_match": ["Send If-Match with the draft revision from GET."]},
        )
    parsed = contracts.parse_if_match(raw)
    assert parsed is not None  # parse raises 422 for malformed input
    return parsed


def safe_encounter_reference(row: dict[str, Any]) -> dict[str, Any]:
    """Public encounter shape for slot/creation responses (never clinical)."""
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


def safe_draft_for_author(row: dict[str, Any]) -> dict[str, Any]:
    """Author-only draft shape: reference fields plus the private body."""
    reference = safe_encounter_reference(row)
    body = row.get("draft_data")
    reference["draft_data"] = dict(body) if isinstance(body, dict) else {}
    return reference


def get_encounter(session: Session, encounter_id: uuid.UUID) -> dict[str, Any] | None:
    """Read one encounter by internal UUID (None when missing)."""
    row = (
        session.execute(select(tables.encounters).where(tables.encounters.c.id == encounter_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_open_draft_for_patient(session: Session, patient_id: uuid.UUID) -> dict[str, Any] | None:
    """The patient's occupying open draft, if any (None when the slot is free)."""
    row = (
        session.execute(
            select(tables.encounters).where(
                tables.encounters.c.patient_id == patient_id,
                tables.encounters.c.lifecycle == DRAFT_LIFECYCLE,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def require_draft_lifecycle(encounter: dict[str, Any]) -> dict[str, Any]:
    """Ordinary draft routes serve drafts only (404 for released rows).

    Discarded (and future signed) rows are not readable/writable here, so both
    the author and strangers see the same generic ``404`` — no content, no
    lifecycle oracle beyond what the slot ``409`` already implies.
    """
    if encounter.get("lifecycle") != DRAFT_LIFECYCLE:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    return encounter


def require_author(encounter: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Author-only gate for draft content and mutation (403 without content)."""
    if str(encounter.get("author_id")) != str(user.get("id")):
        raise contracts.ContractError(
            403, "FORBIDDEN", "Only the draft author can access this draft."
        )
    return encounter


def read_draft_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> dict[str, Any]:
    """Author-only draft read: 404 when missing/released, 403 for strangers."""
    encounter = get_encounter(session, encounter_id)
    if encounter is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    require_draft_lifecycle(encounter)
    require_author(encounter, user)
    return encounter


def create_open_draft(
    session: Session,
    *,
    author: dict[str, Any],
    patient_id: uuid.UUID,
    kind: str,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    """Create the patient's single open draft (409 when the slot is taken).

    Takes the patient-row lock first (plan §2.3) so simultaneous creates —
    including two requests by the same author — serialize; the loser sees the
    occupying draft and gets the generic ``409 OPEN_DRAFT_EXISTS``. The
    partial unique index is the backstop: an ``IntegrityError`` here also
    becomes the same generic ``409`` (transaction rolled back first so the
    caller's scope-commit stays a clean no-op). Fresh drafts start with an
    empty object body. Returns (encounter, server timestamp).
    """
    validated_kind = validate_kind(kind)
    patient_row = (
        session.execute(
            select(tables.patients).where(tables.patients.c.id == patient_id).with_for_update()
        )
        .mappings()
        .first()
    )
    if patient_row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    occupying = get_open_draft_for_patient(session, patient_id)
    if occupying is not None:
        raise contracts.ContractError(
            409,
            "OPEN_DRAFT_EXISTS",
            "An open draft already exists for this patient.",
            {"patient_id": ["An open draft already exists for this patient."]},
        )
    moment = contracts.utcnow()
    encounter_id = uuid.uuid4()
    try:
        session.execute(
            insert(tables.encounters).values(
                id=encounter_id,
                patient_id=patient_id,
                kind=validated_kind,
                author_id=author["id"],
                lifecycle=DRAFT_LIFECYCLE,
                draft_data={},
                revision=1,
                created_at=moment,
                updated_at=moment,
            )
        )
        session.flush()
    except IntegrityError as exc:
        # Lost a race the lock did not serialize (or a stale read): the
        # transaction is aborted, so roll back first, then report the same
        # generic slot conflict — never the index internals, never content.
        session.rollback()
        raise contracts.ContractError(
            409,
            "OPEN_DRAFT_EXISTS",
            "An open draft already exists for this patient.",
            {"patient_id": ["An open draft already exists for this patient."]},
        ) from exc
    encounter = get_encounter(session, encounter_id)
    assert encounter is not None
    audit_module.record_audit(
        session,
        operation="encounters.create.success",
        actor=str(author["username"]),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "patient_id": str(patient_id),
            "kind": validated_kind,
        },
    )
    return encounter, contracts.serialize_utc(moment)


def patch_draft(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    draft_data: Any,
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    """Author-only autosave: replace the body, bump the revision (412 stale).

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404 missing/released, 403 stranger), then the revision fence (a mismatch
    is ``412 STALE_REVISION`` and changes nothing), then body validation
    (``422`` changes nothing). Only a fully valid write bumps
    ``revision = expected + 1`` and records audit — a failed save never looks
    saved because server truth (``GET`` + revision) only moves on success.
    Returns (encounter, server timestamp).
    """
    row = (
        session.execute(
            select(tables.encounters)
            .where(tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    require_draft_lifecycle(encounter)
    require_author(encounter, author)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    validated = validate_draft_data(draft_data)
    moment = contracts.utcnow()
    session.execute(
        update(tables.encounters)
        .where(tables.encounters.c.id == encounter_id)
        .values(draft_data=validated, revision=expected_revision + 1, updated_at=moment)
    )
    updated = get_encounter(session, encounter_id)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="encounters.patch.success",
        actor=str(author["username"]),
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "revision": expected_revision + 1},
    )
    return updated, contracts.serialize_utc(moment)


def cancel_draft_jobs(encounter_id: uuid.UUID) -> None:
    """Job-cancellation hook discard calls (explicit no-op until jobs exist).

    Later sessions (reasoning queue) fill this in to cancel pending generation
    jobs for the discarded draft. There are deliberately no job tables in S07
    — do not fabricate any here; the discard path calls this hook so the
    integration point is already wired and tested (see ``test_drafts.py``).
    """
    _ = encounter_id
    return None


def discard_draft(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    confirm: Any,
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    """Author-only discard: confirm + current revision releases the slot.

    Privacy first (404 missing/already-released, 403 stranger), then explicit
    ``confirm is True`` (``422``), then the revision fence (``412`` on
    mismatch). Success moves lifecycle to ``discarded`` with ``revision + 1``,
    invokes :func:`cancel_draft_jobs`, and records audit. The slot release is
    longer occupies the slot). The patient row is untouched (never deleted).
    Returns (encounter, server timestamp).
    """
    row = (
        session.execute(
            select(tables.encounters)
            .where(tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    require_draft_lifecycle(encounter)
    require_author(encounter, author)
    validate_discard_confirm(confirm)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    moment = contracts.utcnow()
    session.execute(
        update(tables.encounters)
        .where(tables.encounters.c.id == encounter_id)
        .values(lifecycle=DISCARDED_LIFECYCLE, revision=expected_revision + 1, updated_at=moment)
    )
    # No jobs exist in S07 — the hook is the integration point, not a table.
    # The slot release is atomic with this lifecycle move via the partial
    # unique index (a discarded row no longer occupies the slot).
    cancel_draft_jobs(encounter_id)
    updated = get_encounter(session, encounter_id)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="encounters.discard.success",
        actor=str(author["username"]),
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "revision": expected_revision + 1},
    )
    return updated, contracts.serialize_utc(moment)


def idempotency_request_hash(body: dict[str, Any], target_id: uuid.UUID) -> str:
    """Canonical hash of a draft command body + target (replay key)."""
    return contracts.canonical_hash({"body": body, "target_id": str(target_id)})
