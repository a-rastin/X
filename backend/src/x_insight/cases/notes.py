"""Attributed page notes, stored separate from draft content (S13).

Plan.md §§2.3, 4.1-4.3 (FR-16, FR-22): a page note carries page, author ID +
display snapshot, server timestamp, and text. Notes live per-encounter
per-page in their own ``notes`` table (migration ``0006``) — never inside
``encounters.draft_data`` (the S07 opaque autosave object) and never in the
analysis-visible history channel (S12). Notes can change the encounter
revision without changing the analysis fingerprint.

Write contract (``POST /encounters/{id}/notes``):
- Author-only (``403`` strangers/admin without content, ``404``
  missing/discarded), ``If-Match`` with the current encounter revision is
  required (``422`` absent/``*``/malformed, ``412 STALE_REVISION`` stale),
  per-command ``Idempotency-Key`` store (``notes.create`` keyed
  ``(operation, actor, key)``): same key + same body replays the single
  created note, same key + changed body is ``409 IDEMPOTENCY_CONFLICT``.
- Actor/time are server-derived: ``author_id`` + ``author_display``
  (username snapshot) + ``created_at`` UTC come from the session and the
  server clock, never the request body. The request carries ``{page, text}``
  only — anything else (``author_id``, timestamps, …) is ``422`` via the
  HTTP schema's ``extra=forbid``.
- Append-only: there is no edit/delete endpoint and no UPDATE/DELETE grant;
  correction is a new note. Creating a note bumps the encounter revision so
  S07 autosave stays coherent (a stale tab reconciles through ``GET`` first).
- Bounds: ``page`` must be in :data:`ALLOWED_PAGES` (demographics plus the
  wizard steps); ``text`` is preserved verbatim (literal markup kept, no
  stripping) and must be non-empty with at most :data:`MAX_NOTE_CHARS`
  characters. Violations are ``422`` with ``field_errors``.

Read contract (``GET /encounters/{id}/notes``): author-only list with an
optional ``?page=`` filter and bounded ``limit``/``offset``
(:func:`contracts.parse_pagination`, default 25, max 100), stable
``(created_at, id)`` order, ``{items, total, revision}`` plus ETag.

Separation proof (no speculative snapshot machinery here):
- :data:`ANALYSIS_EXCLUDED_CHANNELS` + :func:`exclude_notes_from_analysis`
  are the explicit serializer exclusion future S40 snapshots must use: notes
  are excluded from the analysis fingerprint/projection while signed
  snapshots preserve the full displayed record including notes (plan §4.2).
  Only this exclusion function/allowlist is defined here — no
  GenerationBatch/QuestionRun/projection tables or endpoints.
- Mandatory end-to-end note-noninterference checks are recorded for
  **S40, S41, and S59**: each must prove that page notes never leak into
  analysis-visible history, prompts, CPTs, DDI, or proposal selection (a page
  note is never relabeled algorithm-visible history), and that a note-only
  change does not invalidate probability acceptance. Those sessions implement
  the checks; this module only defines the exclusion they assert against.

All persistence uses the caller's :func:`x_insight.db.session_scope`
transaction; audit is recorded in the same transaction and this module never
commits.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import tables as cases_tables
from x_insight.operations import audit as audit_module

NOTES_CREATE_OPERATION = "notes.create"

#: Pages that may carry attributed notes: demographics (supported after the
#: initial draft creation) plus every wizard step string.
ALLOWED_PAGES = (
    "demographics",
    "diagnosis",
    "panss",
    "cssrs",
    "history",
    "medications",
    "effects",
    "proposal",
    "secondary_plan",
)

#: Note text bounds: non-empty, verbatim (no stripping), at most this many chars.
MAX_NOTE_CHARS = 2000

#: Channels excluded from the analysis fingerprint/projection (plan §4.2).
#: Future S40 snapshots must apply :func:`exclude_notes_from_analysis` so a
#: page note is never relabeled algorithm-visible history; S40/S41/S59 carry
#: the mandatory end-to-end note-noninterference checks asserting this.
ANALYSIS_EXCLUDED_CHANNELS = ("notes",)


def validate_page(value: Any) -> str:
    """Page allowlist for notes (422 with field_errors otherwise)."""
    if value not in ALLOWED_PAGES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid note page.",
            {"page": [f"Must be one of {list(ALLOWED_PAGES)}."]},
        )
    return str(value)


def validate_text(value: Any) -> str:
    """Note text bounds: non-empty, at most MAX_NOTE_CHARS (422 otherwise).

    The text is returned verbatim — literal markup is preserved, nothing is
    stripped or normalized — so what the author typed is what resume shows.
    """
    if not isinstance(value, str) or value == "":
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Note text must be non-empty.",
            {"text": ["Must be non-empty text."]},
        )
    if len(value) > MAX_NOTE_CHARS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Note text is too long.",
            {"text": [f"Must be at most {MAX_NOTE_CHARS} characters."]},
        )
    return value


def safe_note(row: dict[str, Any]) -> dict[str, Any]:
    """Public note shape: opaque UUID id + server-derived attribution."""
    return {
        "id": str(row["id"]),
        "encounter_id": str(row["encounter_id"]),
        "page": row["page"],
        "author_id": str(row["author_id"]),
        "author_display": row["author_display"],
        "created_at": contracts.serialize_utc(row["created_at"]),
        "text": row["text"],
    }


def exclude_notes_from_analysis(record: dict[str, Any]) -> dict[str, Any]:
    """Explicit serializer exclusion for future S40 snapshots (plan §4.2).

    Returns the analysis-visible projection of an encounter record with the
    :data:`ANALYSIS_EXCLUDED_CHANNELS` (notes) removed. Notes live in their
    own table so well-behaved serializers never see them; this function is
    the allowlist future snapshot code must route through so a page note can
    never be relabeled algorithm-visible history. S40/S41/S59 assert this
    end-to-end; only the exclusion itself is defined here (no snapshot
    machinery, no GenerationBatch/QuestionRun/projection tables).
    """
    return {key: value for key, value in record.items() if key not in ANALYSIS_EXCLUDED_CHANNELS}


def _get_note(session: Session, note_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(select(cases_tables.notes).where(cases_tables.notes.c.id == note_id))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def create_note(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    page: Any,
    text: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Author-only append: validate, insert one note, bump revision (412 stale).

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404 missing/discarded, 403 stranger), then the revision fence (``412``
    changes nothing), then page/text validation (``422`` changes nothing).
    Success inserts one row with server-derived author snapshot + UTC time,
    bumps ``revision = expected + 1`` (S07 autosave coherence), and audits.
    Returns (note, encounter, server timestamp).
    """
    row = (
        session.execute(
            select(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    encounters_service.require_draft_lifecycle(encounter)
    encounters_service.require_author(encounter, author)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    validated_page = validate_page(page)
    validated_text = validate_text(text)
    moment = contracts.utcnow()
    note_id = uuid.uuid4()
    session.execute(
        insert(cases_tables.notes).values(
            id=note_id,
            encounter_id=encounter_id,
            page=validated_page,
            author_id=author["id"],
            author_display=str(author["username"]),
            created_at=moment,
            text=validated_text,
        )
    )
    session.execute(
        update(cases_tables.encounters)
        .where(cases_tables.encounters.c.id == encounter_id)
        .values(revision=expected_revision + 1, updated_at=moment)
    )
    session.flush()
    note = _get_note(session, note_id)
    assert note is not None
    updated = encounters_service.get_encounter(session, encounter_id)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="notes.create.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "note_id": str(note_id),
            "page": validated_page,
            "revision": expected_revision + 1,
        },
    )
    return note, updated, contracts.serialize_utc(moment)


def list_notes_for_author(
    session: Session,
    encounter_id: uuid.UUID,
    user: dict[str, Any],
    *,
    page_filter: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Author-only note list: 404 missing/discarded, 403 strangers.

    Optional ``page_filter`` must itself be allowlisted (``422`` otherwise);
    pagination is bounded via :func:`contracts.parse_pagination` (25/100).
    Stable ``(created_at, id)`` order. Returns (encounter, items, total).
    """
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    if page_filter is not None:
        validate_page(page_filter)
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    query = select(cases_tables.notes).where(cases_tables.notes.c.encounter_id == encounter_id)
    if page_filter is not None:
        query = query.where(cases_tables.notes.c.page == page_filter)
    total = int(
        session.execute(
            select(func.count()).select_from(query.subquery()),
        ).scalar_one()
    )
    rows = (
        session.execute(
            query.order_by(cases_tables.notes.c.created_at, cases_tables.notes.c.id)
            .limit(resolved_limit)
            .offset(resolved_offset)
        )
        .mappings()
        .all()
    )
    return encounter, [dict(row) for row in rows], total


def idempotency_request_hash(
    target_id: uuid.UUID, expected_revision: int, body: dict[str, Any]
) -> str:
    """Canonical hash of a note command (target + revision + body)."""
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision, "body": body}
    )
