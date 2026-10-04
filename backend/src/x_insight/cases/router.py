"""Patient registry + author-owned draft HTTP routes (S06-S07, seam T1).

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
from x_insight.cases import encounters as encounters_service
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
    """Single-slot creation body: kind only (defaults to follow-up)."""

    model_config = {"extra": "forbid"}

    kind: Literal["registration", "follow_up"] = "follow_up"


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
            {"kind": payload.kind}, patient_id
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
