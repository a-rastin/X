"""Released-assessment content routes (S08, seam T1; plan.md §4.3).

``GET /api/v1/content/assessments/{type}`` returns the released definition
plus its version. Any active authenticated session (physician or
administrator) may read; missing/revoked sessions get ``401``, unknown
types ``404``. Reads need no CSRF token (same contract as the patient
directory). All failures use the standard ``contracts.ErrorBody`` and the
correlation header is attached by middleware.

S09 diagnosis page state (plan.md §§2.2, 5; FR-11, FR-16; seams T2/T1):

- ``GET /encounters/{id}/diagnosis`` — author-only live preview through the
  same T2 ``evaluate()`` (no drift): answers, evaluation, acknowledgment and
  bypass records with validity, plus the server ``can_proceed`` gate.
- ``POST .../diagnosis/acknowledgment`` — author-only, ``If-Match``-fenced
  warning acknowledgment for a completed below-threshold result. The body is
  empty (no reason field exists); the server stamps actor/time/status tied
  to the assessed revision + answers hash. Later relevant edits invalidate
  it (derived on every read).
- ``POST .../diagnosis/bypass`` — author-only bypass without any reason
  field. The server stamps actor/time/status, clears answers, and the record
  survives resume via the same ``draft_data`` body.

Diagnosis answers themselves travel through the existing S07
``PATCH /encounters/{id}`` autosave (``draft_data["diagnosis"]["answers"]``);
no new persistence mechanism is added.

S10 PANSS page state (plan.md §§2.2, 5; FR-12, FR-20; seams T2/T1):

- ``GET /encounters/{id}/panss`` — author-only live preview through the
  same T2 ``evaluate()`` (no drift): answers, evaluation, definition
  version, and revision. There is no acknowledgment, no bypass, no owner
  review, no treatment gate from score bands, no default ``1`` values, and
  no hidden zero.

PANSS answers themselves travel through the existing S07
``PATCH /encounters/{id}`` autosave (``draft_data["panss"]["answers"]``);
no new table, no new persistence mechanism, no migration.
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments import diagnosis as diagnosis_service
from x_insight.assessments import panss as panss_service
from x_insight.assessments.released import RELEASED_TYPES, get_released_definition
from x_insight.cases import encounters as encounters_service
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


@router.get("/content/assessments/{assessment_type}")
def get_assessment(
    assessment_type: str, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    user: dict[str, Any] | None = identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )
    if user is None:
        return error_response(401, "UNAUTHENTICATED", "Authentication required.", request_id)
    if assessment_type not in RELEASED_TYPES:
        return error_response(404, "NOT_FOUND", "Assessment not found.", request_id)
    definition = get_released_definition(assessment_type)
    return JSONResponse(
        status_code=200,
        content={
            "type": assessment_type,
            "version": definition["version"],
            "definition": definition,
        },
    )


# --- S09 diagnosis page state (author-only, draft_data + revision contract) ---


class DiagnosisEmptyRequest(BaseModel):
    """Empty command body: no reason field exists for ack/bypass.

    ``extra=forbid`` proves it: ``{}`` succeeds while any ``reason`` (or
    other) field is ``422``.
    """

    model_config = {"extra": "forbid"}


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
    """Session + CSRF + physician gate (same contract as draft mutations)."""
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


def _diagnosis_response(
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
        "answers": state["answers"],
        "evaluation": state["evaluation"],
        "acknowledgment": state["acknowledgment"],
        "acknowledgment_valid": state["acknowledgment_valid"],
        "bypass": state["bypass"],
        "bypass_valid": state["bypass_valid"],
        "can_proceed": state["can_proceed"],
        "requires_acknowledgment": state["requires_acknowledgment"],
        "proceed_via": state["proceed_via"],
    }
    if server_timestamp is not None:
        content["server_timestamp"] = server_timestamp
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


def _diagnosis_idempotency_replay(stored: dict[str, Any]) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    revision = body.get("revision")
    if isinstance(revision, int) and revision >= 1:
        response.headers["ETag"] = contracts.format_etag(revision)
    return response


@router.get("/encounters/{encounter_id}/diagnosis")
def get_diagnosis(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = diagnosis_service.read_diagnosis_for_author(session, encounter_id, user)
    return _diagnosis_response(encounter, state, status_code=200)


@router.post("/encounters/{encounter_id}/diagnosis/acknowledgment")
def acknowledge_diagnosis(
    encounter_id: uuid.UUID,
    payload: DiagnosisEmptyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    del payload  # empty body by contract: no reason field exists
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
        request_hash = diagnosis_service.idempotency_request_hash(encounter_id, expected)
        stored = identity_service.lookup_idempotency(
            session,
            operation=diagnosis_service.DIAGNOSIS_ACK_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _diagnosis_idempotency_replay(stored)
    encounter, state, server_timestamp = diagnosis_service.acknowledge_below_threshold(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_version": state["definition_version"],
        "answers": state["answers"],
        "evaluation": state["evaluation"],
        "acknowledgment": state["acknowledgment"],
        "acknowledgment_valid": state["acknowledgment_valid"],
        "bypass": state["bypass"],
        "bypass_valid": state["bypass_valid"],
        "can_proceed": state["can_proceed"],
        "requires_acknowledgment": state["requires_acknowledgment"],
        "proceed_via": state["proceed_via"],
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=diagnosis_service.DIAGNOSIS_ACK_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _diagnosis_response(encounter, state, status_code=200, server_timestamp=server_timestamp)


def _panss_response(
    encounter: dict[str, Any],
    state: dict[str, Any],
    *,
    status_code: int,
) -> JSONResponse:
    content: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_version": state["definition_version"],
        "answers": state["answers"],
        "evaluation": state["evaluation"],
    }
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["ETag"] = contracts.format_etag(int(content["revision"]))
    return response


@router.get("/encounters/{encounter_id}/panss")
def get_panss(
    encounter_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    encounter, state = panss_service.read_panss_for_author(session, encounter_id, user)
    return _panss_response(encounter, state, status_code=200)


@router.post("/encounters/{encounter_id}/diagnosis/bypass")
def bypass_diagnosis(
    encounter_id: uuid.UUID,
    payload: DiagnosisEmptyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    del payload  # empty body by contract: bypass needs no reason
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
        request_hash = diagnosis_service.idempotency_request_hash(encounter_id, expected)
        stored = identity_service.lookup_idempotency(
            session,
            operation=diagnosis_service.DIAGNOSIS_BYPASS_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _diagnosis_idempotency_replay(stored)
    encounter, state, server_timestamp = diagnosis_service.bypass_diagnosis(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "revision": int(encounter["revision"]),
        "definition_version": state["definition_version"],
        "answers": state["answers"],
        "evaluation": state["evaluation"],
        "acknowledgment": state["acknowledgment"],
        "acknowledgment_valid": state["acknowledgment_valid"],
        "bypass": state["bypass"],
        "bypass_valid": state["bypass_valid"],
        "can_proceed": state["can_proceed"],
        "requires_acknowledgment": state["requires_acknowledgment"],
        "proceed_via": state["proceed_via"],
        "server_timestamp": server_timestamp,
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=diagnosis_service.DIAGNOSIS_BYPASS_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return _diagnosis_response(encounter, state, status_code=200, server_timestamp=server_timestamp)
