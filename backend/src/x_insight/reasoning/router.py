"""Generation start/read HTTP routes (S40, seam T1; plan.md §§4.3, 8.1).

``POST /api/v1/encounters/{id}/generation-batches`` — author-only freeze
(physician + CSRF, ``If-Match`` with the current encounter revision,
optional ``Idempotency-Key``). Carries the synthetic question package
inline for this engineering proof (S39 bundles + S24 registry supply
pinned packages later). Returns ``202`` with the immutable batch and its
single projected question run. Failures (401/403/404/409/412/422) create
no partial rows.

``GET /api/v1/generation-batches/{id}`` — author-only read of the frozen
projection plus derived freshness (``stale`` when later relevant edits
moved the analysis fingerprint; note-only edits stay fresh). Old
snapshots remain readable history. No general patient snapshot endpoint
exists and no direct-record provider path is added here.
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.db import get_session
from x_insight.identity import service as identity_service
from x_insight.reasoning import snapshots as snapshots_service

router = APIRouter()


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


def _require_user(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    return user


def _require_physician_mutation(
    request: Request, session: Session
) -> dict[str, Any] | JSONResponse:
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


class GenerationStartRequest(BaseModel):
    """Synthetic start body: the question package to freeze (extra=forbid)."""

    model_config = {"extra": "forbid"}

    package: dict[str, Any] = Field(min_length=1)


def _batch_response(
    batch: dict[str, Any],
    runs: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    *,
    status_code: int,
) -> JSONResponse:
    content: dict[str, Any] = {
        "batch": snapshots_service.safe_batch(batch),
        "question_runs": [snapshots_service.safe_run(run) for run in runs],
    }
    if freshness is not None:
        content["freshness"] = freshness
    return JSONResponse(status_code=status_code, content=content)


@router.post("/encounters/{encounter_id}/generation-batches", status_code=202)
def start_generation(
    encounter_id: uuid.UUID,
    payload: GenerationStartRequest,
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
        request_hash = snapshots_service.idempotency_request_hash(
            encounter_id, expected, payload.package
        )
        stored = identity_service.lookup_idempotency(
            session,
            operation=snapshots_service.GENERATION_START_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            replay = dict(stored["response_body"])
            return JSONResponse(status_code=int(stored["response_status"]), content=replay)
    batch, runs = snapshots_service.start_generation_batch(
        session,
        author=physician,
        encounter_id=encounter_id,
        expected_revision=expected,
        package=payload.package,
        request_id=request_id,
    )
    response_body: dict[str, Any] = {
        "batch": snapshots_service.safe_batch(batch),
        "question_runs": [snapshots_service.safe_run(run) for run in runs],
    }
    if key is not None:
        assert request_hash is not None
        identity_service.store_idempotency(
            session,
            operation=snapshots_service.GENERATION_START_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=202,
            response_body=response_body,
        )
    return JSONResponse(status_code=202, content=response_body)


@router.get("/generation-batches/{batch_id}")
def read_generation(
    batch_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    assert isinstance(user, dict)
    batch, runs, freshness = snapshots_service.get_generation_batch(session, batch_id, user)
    return _batch_response(batch, runs, freshness, status_code=200)
