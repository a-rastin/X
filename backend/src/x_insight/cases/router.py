"""Patient registry + author-owned draft HTTP routes (S06-S07 + S14, seam T1).

``POST /api/v1/patients`` (physician-only, CSRF) atomically creates a
patient plus its registration draft; ``GET /api/v1/patients/{id}`` and
``GET /api/v1/patients`` (directory search) are readable by any active
authenticated user (physician or administrator) and never carry draft
clinical content (transitive privacy).

S07 draft routes (plan.md §§2.3, 4.3; FR-16, FR-22):
- ``POST /api/v1/patients/{id}/encounters`` — physician-only single-slot
  creation (patient-row lock + partial index); occupied slot is a generic
  ``409 OPEN_DRAFT_EXISTS`` with no author/content.
- ``GET /api/v1/encounters/{id}`` — author-only draft read (403 strangers,
  404 missing/released). Carries the ETag revision.
- ``PATCH /api/v1/encounters/{id}`` — author-only autosave replacing
  ``draft_data``; ``If-Match`` with the current revision is required (absent/
  ``*``/malformed is 422, mismatch is ``412 STALE_REVISION`` and changes
  nothing). Per-command ``Idempotency-Key`` store like S04/S06.
- ``POST /api/v1/encounters/{id}/discard`` — author-only release requiring
  ``{"confirm": true}`` plus the current ``If-Match`` revision; releases the
  slot without deleting the patient and calls the job-cancellation hook
  (no-op until jobs exist).

S12 history/effects page state (plan.md §§2.2, 5; FR-14, FR-20-21; T2/T1):
- ``GET /encounters/{id}/history`` — author-only preview over
  ``draft_data["history"]`` (values, provenance, reconciliation, phone
  update, evaluation, definition version, revision) with ETag.
- ``POST .../history`` — author-only strict save (If-Match-fenced,
  server-stamped provenance, 422 on undeclared/excluded/invalid).
- ``GET /encounters/{id}/effects`` — author-only preview of the four
  effects with questionnaires, reviewed severities, urgent flag, and
  definition versions, with ETag.
- ``POST .../effects/{effect}/status`` — author-only status transition
  (present requires complete questionnaire + reviewed severity;
  absent/not_assessed require null severity; stale severity is 422).

History/effects answers travel via the existing S07 ``PATCH`` autosave
(``draft_data["history"]`` / ``draft_data["effects"]``); no new table.

S13 attributed page notes (plan.md §§2.3, 4.1-4.3; FR-16, FR-22; T1 only):
- ``POST /encounters/{id}/notes`` — author-only append of ``{page, text}``
  (If-Match-fenced, server-derived actor/time, revision-bumping, 201
  ``{note, revision, server_timestamp}`` + ETag). Per-command
  ``Idempotency-Key`` store (``notes.create``) like S04/S06/S07/S12.
- ``GET /encounters/{id}/notes`` — author-only list (``403`` strangers,
  ``404`` missing/discarded) with optional ``?page=`` filter and bounded
  ``limit``/``offset`` (25/100), ``{items, total, revision}`` + ETag.

Notes live in their own table, never in ``draft_data`` and never in the
analysis-visible history channel (serializer exclusion lives in
``B/cases/notes.py`` for future S40 snapshots; S40/S41/S59 carry the
mandatory end-to-end note-noninterference checks). Append-only: no
edit/delete endpoint exists; correction is a new note.

S14 shared chart + follow-up entry (plan.md §§2.2-2.3; FR-20-23; T1 only):
- ``GET /patients/{id}/chart`` — shared read for any active authenticated
  user (demographics + signed references + ``open_draft {exists}`` badge +
  honestly-unavailable proposal); never carries draft content/author/revision.
- ``POST /patients/{id}/encounters`` extends S07 with optional inline
  ``baseline`` + ``baseline_encounter_id`` for ``kind='follow_up'`` only:
  copied history gets ``copied_baseline`` provenance + ``pending``
  reconciliation while PANSS/C-SSRS start empty (prior scores display as
  historical only). Registration drafts still start empty.
- ``GET /encounters/{id}/followup-baseline`` — author-only baseline preview
  (``baseline`` + ``reconciliation`` + ``prior_scores_display`` with
  ``historical: True``) with ETag. S48d/S51 owns demographic-change staleness;
  this session documents the hook only.

All failures use the standard ``contracts.ErrorBody`` (never
secrets/tracebacks/clinical content). No delete/merge route exists in v1.
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import chart as chart_service
from x_insight.cases import effects as effects_service
from x_insight.cases import encounters as encounters_service
from x_insight.cases import history as history_service
from x_insight.cases import medications as medications_service
from x_insight.cases import notes as notes_service
from x_insight.cases import patients as patients_service
from x_insight.db import get_session
from x_insight.identity import service as identity_service


def get_request_id(request: Request) -> str:
    scope_id = request.scope.get("request_id")
    if isinstance(scope_id, str) and scope_id:
        return scope_id
    return request.headers.get("x-request-id") or contracts.new_request_id()


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    field_errors: dict[str, list[str]] | None = None,
    retryable: bool = False,
) -> _JSONResponse:
    body = contracts.ErrorBody(
        code=code,
        message=message,
        field_errors=field_errors or {},
        request_id=request_id,
        retryable=retryable,
    )
    return _JSONResponse(status_code=status_code, content=body.model_dump())


router = APIRouter()


class PatientCreateRequest(BaseModel):
    """Registration body: structural typing only; semantics live in service.

    ``age`` is strict (bools, floats, and numeric strings are rejected even
    when they look whole); the identifier stays text so leading zeros survive
    JSON. Unknown keys are forbidden (serialization allowlist spirit).
    """

    model_config = {"extra": "forbid"}

    identifier: str
    given_name: str
    family_name: str
    sex: Literal["M", "F"]
    age: StrictInt = Field(ge=18, le=99)
    clinical_status: Literal["first_time", "established"]
    phone: str | None = None


def _current_user(request: Request, session: Session) -> dict[str, Any] | None:
    return identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )


def _require_user(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = _current_user(request, session)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    return user


def _require_physician_mutation(
    request: Request, session: Session
) -> dict[str, Any] | JSONResponse:
    """Session + CSRF gate (same contract as identity mutations) + physician.

    Role comes from the stored account row, never request input: only active
    physicians may register patients; administrators get 403 here (they read
    and archive, they do not register — plan §2.1).
    """
    raw_token = request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    if not raw_token:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    row = identity_service.get_session_row(session, raw_token)
    if row is None or row.get("revoked_at") is not None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    presented = request.headers.get(identity_service.CSRF_HEADER_NAME, "")
    expected = row.get("csrf_token", "")
    if not presented or not expected or not hmac_module.compare_digest(presented, expected):
        return error_response(403, "FORBIDDEN", "CSRF validation failed.", get_request_id(request))
    user = identity_service.get_session_user(session, raw_token)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    if user.get("role") != "physician" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Physician access required.", get_request_id(request)
        )
    return user


def _idempotency_key_or_none(request: Request) -> str | None:
    raw = request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER)
    return contracts.parse_idempotency_key(raw)


def _idempotency_conflict(request_id: str) -> JSONResponse:
    return error_response(
        409,
        "IDEMPOTENCY_CONFLICT",
        "Idempotency-Key was already used with a different request body.",
        request_id,
    )


@router.post("/patients", status_code=201)
def create_patient(
    payload: PatientCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    fields = patients_service.validate_registration_payload(
        identifier=payload.identifier,
        given_name=payload.given_name,
        family_name=payload.family_name,
        sex=payload.sex,
        age=payload.age,
        clinical_status=payload.clinical_status,
        phone=payload.phone,
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = patients_service.idempotency_request_hash(fields)
        stored = identity_service.lookup_idempotency(
            session,
            operation=patients_service.PATIENTS_CREATE_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            replay = dict(stored["response_body"])
            return JSONResponse(status_code=int(stored["response_status"]), content=replay)
    patient, draft, server_timestamp = patients_service.create_patient_with_draft(
        session, author=physician, fields=fields, request_id=request_id
    )
    safe_patient = patients_service.safe_patient(patient)
    safe_draft = patients_service.safe_encounter(draft)
    response_body: dict[str, Any] = {
        "patient": safe_patient,
        # The registration draft is the encounter in draft lifecycle; both
        # keys carry the same object (plan calls it an encounter, S06 a draft).
        "draft": safe_draft,
        "encounter": safe_draft,
        "server_timestamp": server_timestamp,
        "revision": safe_patient["revision"],
    }
    if key is not None:
        assert request_hash is not None
        try:
            identity_service.store_idempotency(
                session,
                operation=patients_service.PATIENTS_CREATE_OPERATION,
                actor_id=physician["id"],
                key=key,
                request_hash=request_hash,
                response_status=201,
                response_body=response_body,
            )
        except IntegrityError:
            # Same-key race already stored this key: the patient insert above
            # succeeded, so the competing body differs (same body would have
            # collided on the identifier first) — report the key conflict and
            # roll back this creation rather than leaking a second record.
            session.rollback()
            return _idempotency_conflict(request_id)
    return JSONResponse(status_code=201, content=response_body)


@router.get("/patients/{patient_id}")
def get_patient(
    patient_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    patient = patients_service.get_patient(session, patient_id)
    if patient is None:
        return error_response(404, "NOT_FOUND", "Patient not found.", get_request_id(request))
    return JSONResponse(
        status_code=200, content={"patient": patients_service.safe_patient(patient)}
    )


@router.get("/patients")
def list_patients(
    request: Request,
    session: Session = Depends(get_session),
    query: str | None = None,
    clinical_status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    include_archived: bool = False,
) -> JSONResponse:
    """Shared directory: name/ID substring search, status filter, pagination.

    Archived patients are excluded unless ``include_archived=true`` (plan
    §2.3: archive is an additional explicit filter). Stable order, bounded
    pages via :func:`contracts.parse_pagination` (default 25, max 100).
    """
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    status = patients_service.validate_clinical_status_filter(clinical_status)
    items, total = patients_service.search_patients(
        session,
        query=query,
        clinical_status=status,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    return JSONResponse(
        status_code=200,
        content={
            "items": [patients_service.safe_patient(item) for item in items],
            "total": total,
            "limit": resolved_limit,
            "offset": resolved_offset,
        },
    )


@router.get("/patients/{patient_id}/chart")
def get_chart(
    patient_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Shared chart: demographics + signed chart + occupancy badge (S14).

    Any active authenticated user may read (physician or administrator, like
    the S06 directory/demographics reads — plan §2.1). Carries no draft
    content, no author oracle, no revision — just slot occupancy for
    directory badges.
    """
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    chart = chart_service.read_chart(session, patient_id, user)
    return JSONResponse(status_code=200, content=chart)


# --- S07 author-owned drafts (plan.md §§2.3, 4.3; FR-16, FR-22) ---
#
# Revision ownership for later assessment pages: every wizard page autosaves
# through PATCH here with the encounter revision from GET (ETag + ``revision``
# field) as ``If-Match`` and a fresh ``Idempotency-Key`` per save attempt
# (retries of the same attempt reuse the key). A ``412`` means another tab
# saved first — keep local edits, GET server truth, reconcile, retry with the
# new revision. Only a 2xx response is durable ("Saved"); any 4xx/5xx leaves
# server truth unchanged. ``draft_data`` stays one opaque versioned object so
# later pages extend its keys instead of adding persistence mechanisms.


class EncounterCreateRequest(BaseModel):
    """Single-slot creation body: kind plus optional follow-up baseline (S14).

    ``baseline`` is the inline snapshot (history_values, prior_scores,
    medications, provenance_note) stored as ``draft_data.followup_baseline``
    with copied history provenance; ``baseline_encounter_id`` is the opaque
    test-only baseline reference stamped into provenance/reconciliation.
    Semantic validation lives in :mod:`x_insight.cases.encounters` (422).
    """

    model_config = {"extra": "forbid"}

    kind: Literal["registration", "follow_up"] = "follow_up"
    baseline: dict[str, Any] | None = None
    baseline_encounter_id: str | None = None


class DraftPatchRequest(BaseModel):
    """Autosave body: full replacement of the private draft object.

    Structural typing only (top-level object); semantic bounds live in
    :func:`encounters_service.validate_draft_data`.
    """

    model_config = {"extra": "forbid"}

    draft_data: dict[str, Any]


class DraftDiscardRequest(BaseModel):
    """Discard body: explicit confirmation (revision travels in If-Match)."""

    model_config = {"extra": "forbid"}

    confirm: bool


def _draft_response(
    encounter: dict[str, Any],
    *,
    status_code: int,
    include_body: bool,
    server_timestamp: str | None = None,
) -> JSONResponse:
    """Draft response with the ETag revision (replay-safe for idempotency)."""
    if include_body:
        safe = encounters_service.safe_draft_for_author(encounter)
        content: dict[str, Any] = {
            "encounter": encounters_service.safe_encounter_reference(encounter),
            "draft_data": safe["draft_data"],
            "revision": safe["revision"],
        }
    else:
        reference = encounters_service.safe_encounter_reference(encounter)
        content = {"encounter": reference, "revision": reference["revision"]}
    if server_timestamp is not None:
        content["server_timestamp"] = server_timestamp
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


def _draft_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


@router.post("/patients/{patient_id}/encounters", status_code=201)
def create_encounter(
    patient_id: uuid.UUID,
    payload: EncounterCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = encounters_service.idempotency_request_hash(
            {
                "kind": payload.kind,
                "baseline": payload.baseline,
                "baseline_encounter_id": payload.baseline_encounter_id,
            },
            patient_id,
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=encounters_service.ENCOUNTERS_CREATE_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _draft_idempotency_replay(stored)
    encounter, server_timestamp = encounters_service.create_open_draft(
        session,
        author=physician,
        patient_id=patient_id,
        kind=payload.kind,
        request_id=request_id,
        baseline=payload.baseline,
        baseline_encounter_id=payload.baseline_encounter_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "draft_data": dict(encounter.get("draft_data") or {}),
        "revision": int(encounter["revision"]),
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        try:
            identity_service.store_idempotency(
                session,
                operation=encounters_service.ENCOUNTERS_CREATE_OPERATION,
                actor_id=physician["id"],
                key=key,
                request_hash=request_hash,
                response_status=201,
                response_body=response_body,
            )
        except IntegrityError:
            # Same-key race already stored this key: the slot insert above
            # succeeded, so the competing body differs (same body would have
            # hit the slot first) — roll back this creation and report the
            # key conflict rather than leaking a second record.
            session.rollback()
            return _idempotency_conflict(request_id)
    return _draft_response(
        encounter, status_code=201, include_body=True, server_timestamp=server_timestamp
    )


@router.get("/encounters/{encounter_id}")
def get_encounter(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    return _draft_response(encounter, status_code=200, include_body=True)


@router.get("/encounters/{encounter_id}/followup-baseline")
def get_followup_baseline(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Author-only follow-up baseline (S14): baseline + reconciliation + history.

    Reads need no CSRF, like other GET previews. Prior scores display with
    ``historical: True`` — never as current answers.
    """
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = chart_service.read_followup_baseline_for_author(session, encounter_id, user)
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "baseline": state["baseline"],
        "reconciliation": state["reconciliation"],
        "prior_scores_display": state["prior_scores_display"],
    }
    response = JSONResponse(status_code=200, content=content)
    response.headers["ETag"] = contracts.format_etag(int(encounter["revision"]))
    return response


@router.patch("/encounters/{encounter_id}")
def patch_encounter(
    encounter_id: uuid.UUID,
    payload: DraftPatchRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    # If-Match is required (absent/"*"/malformed is 422 here, never silent).
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = encounters_service.idempotency_request_hash(
            {"draft_data": payload.draft_data, "expected_revision": expected},
            encounter_id,
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=encounters_service.ENCOUNTERS_PATCH_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _draft_idempotency_replay(stored)
    encounter, server_timestamp = encounters_service.patch_draft(
        session,
        author=physician,
        encounter_id=encounter_id,
        draft_data=payload.draft_data,
        expected_revision=expected,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "draft_data": dict(encounter.get("draft_data") or {}),
        "revision": int(encounter["revision"]),
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=encounters_service.ENCOUNTERS_PATCH_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _draft_response(
        encounter, status_code=200, include_body=True, server_timestamp=server_timestamp
    )


@router.post("/encounters/{encounter_id}/discard")
def discard_encounter(
    encounter_id: uuid.UUID,
    payload: DraftDiscardRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = encounters_service.idempotency_request_hash(
            {"confirm": payload.confirm, "expected_revision": expected},
            encounter_id,
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=encounters_service.ENCOUNTERS_DISCARD_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _draft_idempotency_replay(stored)
    encounter, server_timestamp = encounters_service.discard_draft(
        session,
        author=physician,
        encounter_id=encounter_id,
        confirm=payload.confirm,
        expected_revision=expected,
        request_id=request_id,
    )
    # The released draft's former body is deliberately not echoed back.
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=encounters_service.ENCOUNTERS_DISCARD_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _draft_response(
        encounter, status_code=200, include_body=False, server_timestamp=server_timestamp
    )


# --- S12 history/effects page state (author-only, draft_data + revision) ---


class HistorySaveRequest(BaseModel):
    """Strict history save body: values plus minimal reconciliation/phone.

    Structural typing only (top-level keys); semantic validation
    (undeclared/excluded/invalid values, bad reconciliation, non-text phone)
    lives in :mod:`x_insight.cases.history` and returns 422.
    """

    model_config = {"extra": "forbid"}

    values: dict[str, Any]
    reconciliation: dict[str, Any] | None = None
    phone_update: str | None = None


class EffectStatusRequest(BaseModel):
    """Effect status transition body: status plus reviewed severity.

    ``severity`` is the BARS global int for akathisia, a reviewed label for
    the other present effects, and must be null for absent/not_assessed
    (stale severity is 422 — the caller must explicitly clear it).
    """

    model_config = {"extra": "forbid"}

    status: str
    severity: Any = None


def _history_response(
    encounter: dict[str, Any],
    state: dict[str, Any],
    *,
    status_code: int,
    server_timestamp: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_version": state["definition_version"],
        "values": state["values"],
        "provenance": state["provenance"],
        "reconciliation": state["reconciliation"],
        "phone_update": state["phone_update"],
        "evaluation": state["evaluation"],
        "analysis_visible": state["analysis_visible"],
        "analysis_visible_label": state["analysis_visible_label"],
    }
    if server_timestamp is not None:
        content["server_timestamp"] = server_timestamp
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


def _history_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


def _effects_response(
    encounter: dict[str, Any],
    state: dict[str, Any],
    *,
    status_code: int,
    server_timestamp: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_versions": state["definition_versions"],
        "effects": state["effects"],
    }
    if server_timestamp is not None:
        content["server_timestamp"] = server_timestamp
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


def _effects_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


@router.get("/encounters/{encounter_id}/history")
def get_history(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = history_service.read_history_for_author(session, encounter_id, user)
    return _history_response(encounter, state, status_code=200)


@router.post("/encounters/{encounter_id}/history")
def save_history(
    encounter_id: uuid.UUID,
    payload: HistorySaveRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = history_service.idempotency_request_hash(
            encounter_id,
            expected,
            {
                "values": payload.values,
                "reconciliation": payload.reconciliation,
                "phone_update": payload.phone_update,
            },
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=history_service.HISTORY_SAVE_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _history_idempotency_replay(stored)
    encounter, state, server_timestamp = history_service.save_history(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        values=payload.values,
        reconciliation=payload.reconciliation,
        phone_update=payload.phone_update,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_version": state["definition_version"],
        "values": state["values"],
        "provenance": state["provenance"],
        "reconciliation": state["reconciliation"],
        "phone_update": state["phone_update"],
        "evaluation": state["evaluation"],
        "analysis_visible": state["analysis_visible"],
        "analysis_visible_label": state["analysis_visible_label"],
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=history_service.HISTORY_SAVE_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _history_response(encounter, state, status_code=200, server_timestamp=server_timestamp)


@router.get("/encounters/{encounter_id}/effects")
def get_effects(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = effects_service.read_effects_for_author(session, encounter_id, user)
    return _effects_response(encounter, state, status_code=200)


@router.post("/encounters/{encounter_id}/effects/{effect_key}/status")
def set_effect_status(
    encounter_id: uuid.UUID,
    effect_key: str,
    payload: EffectStatusRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    operation = f"{effects_service.EFFECT_STATUS_OPERATION_PREFIX}.{effect_key}"
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = effects_service.idempotency_request_hash(
            encounter_id,
            effect_key,
            expected,
            {"status": payload.status, "severity": payload.severity},
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=operation,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _effects_idempotency_replay(stored)
    encounter, state, server_timestamp = effects_service.set_effect_status(
        session,
        author=physician,
        encounter_id=encounter_id,
        effect=effect_key,
        status=payload.status,
        severity=payload.severity,
        expected_revision=expected,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_versions": state["definition_versions"],
        "effects": state["effects"],
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=operation,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _effects_response(encounter, state, status_code=200, server_timestamp=server_timestamp)


# --- S13 attributed page notes (author-only, own table + revision) ---


class NoteCreateRequest(BaseModel):
    """Note create body: page + verbatim text only (extra=forbid).

    Actor/time are server-derived (author snapshot + UTC timestamp), never
    client-supplied: any ``author_id``/timestamp key here is ``422``.
    """

    model_config = {"extra": "forbid"}

    page: str
    text: str


def _note_response(
    note: dict[str, Any],
    revision: int,
    *,
    status_code: int,
    server_timestamp: str,
) -> JSONResponse:
    content: dict[str, Any] = {
        "note": notes_service.safe_note(note),
        "revision": revision,
        "server_timestamp": server_timestamp,
    }
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(revision)
    return response


def _note_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


@router.post("/encounters/{encounter_id}/notes", status_code=201)
def create_note(
    encounter_id: uuid.UUID,
    payload: NoteCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = notes_service.idempotency_request_hash(
            encounter_id, expected, {"page": payload.page, "text": payload.text}
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=notes_service.NOTES_CREATE_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _note_idempotency_replay(stored)
    note, encounter, server_timestamp = notes_service.create_note(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        page=payload.page,
        text=payload.text,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "note": notes_service.safe_note(note),
        "revision": int(encounter["revision"]),
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=notes_service.NOTES_CREATE_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
        )
    return _note_response(
        note, int(encounter["revision"]), status_code=201, server_timestamp=server_timestamp
    )


@router.get("/encounters/{encounter_id}/notes")
def list_notes(
    encounter_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    page: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JSONResponse:
    """Author-only note list (reads need no CSRF, like other GET previews)."""
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, items, total = notes_service.list_notes_for_author(
        session, encounter_id, user, page_filter=page, limit=limit, offset=offset
    )
    content: dict[str, Any] = {
        "items": [notes_service.safe_note(item) for item in items],
        "total": total,
        "revision": int(encounter["revision"]),
    }
    response = JSONResponse(status_code=200, content=content)
    response.headers["ETag"] = contracts.format_etag(int(encounter["revision"]))
    return response


# --- S20 medications + versioned DDI report (author-only, draft_data) ---


class MedicationItem(BaseModel):
    """Drug-only entry: catalog reference alone (extra=forbid)."""

    model_config = {"extra": "forbid"}

    catalog_drug_id: str = Field(min_length=1, max_length=200)


class MedicationsSaveRequest(BaseModel):
    """Strict medications save body: drug-only entries plus pin/reconcile.

    Structural typing only; semantic validation (unknown catalog identifiers,
    bad versions, bad reconciliation) lives in
    :mod:`x_insight.cases.medications` and returns 422/404/503.
    """

    model_config = {"extra": "forbid"}

    medications: list[MedicationItem]
    dataset_version: str | None = None
    reconciliation: dict[str, Any] | None = None


def _medications_response(
    encounter: dict[str, Any],
    state: dict[str, Any],
    *,
    status_code: int,
    server_timestamp: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "entries": state["entries"],
        "provenance": state["provenance"],
        "reconciliation": state["reconciliation"],
        "dataset_version": state["dataset_version"],
        "catalog_version": state["catalog_version"],
        "medication_fingerprint": state["medication_fingerprint"],
        "generated_at": state["generated_at"],
        "evaluation": state["evaluation"],
    }
    if server_timestamp is not None:
        content["server_timestamp"] = server_timestamp
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


def _medications_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


@router.get("/encounters/{encounter_id}/medications")
def get_medications(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = medications_service.read_medications_for_author(session, encounter_id, user)
    return _medications_response(encounter, state, status_code=200)


@router.post("/encounters/{encounter_id}/medications")
def save_medications(
    encounter_id: uuid.UUID,
    payload: MedicationsSaveRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    expected = encounters_service.require_if_match_revision(
        request.headers.get(contracts.IF_MATCH_HEADER)
    )
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = medications_service.idempotency_request_hash(
            encounter_id,
            expected,
            {
                "medications": [item.model_dump() for item in payload.medications],
                "dataset_version": payload.dataset_version,
                "reconciliation": payload.reconciliation,
            },
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=medications_service.MEDICATIONS_SAVE_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _medications_idempotency_replay(stored)
    encounter, state, server_timestamp = medications_service.save_medications(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        medications=[item.model_dump() for item in payload.medications],
        dataset_version=payload.dataset_version,
        reconciliation=payload.reconciliation,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "entries": state["entries"],
        "provenance": state["provenance"],
        "reconciliation": state["reconciliation"],
        "dataset_version": state["dataset_version"],
        "catalog_version": state["catalog_version"],
        "medication_fingerprint": state["medication_fingerprint"],
        "generated_at": state["generated_at"],
        "evaluation": state["evaluation"],
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=medications_service.MEDICATIONS_SAVE_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _medications_response(
        encounter, state, status_code=200, server_timestamp=server_timestamp
    )


@router.get("/encounters/{encounter_id}/ddi-report")
def get_ddi_report(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Author-only versioned DDI report with stale fencing (reads need no CSRF)."""
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, payload = medications_service.read_ddi_report_for_author(session, encounter_id, user)
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "report_status": payload["report_status"],
        "reason": payload["reason"],
        "report": payload["report"],
        "medication_fingerprint": payload["medication_fingerprint"],
        "stored_fingerprint": payload["stored_fingerprint"],
        "dataset_version": payload["dataset_version"],
        "catalog_version": payload["catalog_version"],
        "generated_at": payload["generated_at"],
        "reconciliation": payload["reconciliation"],
    }
    response = JSONResponse(status_code=200, content=content)
    response.headers["ETag"] = contracts.format_etag(int(encounter["revision"]))
    return response
