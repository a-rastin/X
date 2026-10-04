"""Patient registry HTTP routes (S06, seam T1, plan.md §§2.2-2.3, 4.3).

``POST /api/v1/patients`` (physician-only, CSRF) atomically creates a
patient plus its registration draft; ``GET /api/v1/patients/{id}`` and
``GET /api/v1/patients`` (directory search) are readable by any active
authenticated user (physician or administrator). All failures use the
standard ``contracts.ErrorBody`` (never secrets/tracebacks). No
delete/merge route exists in v1.
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
