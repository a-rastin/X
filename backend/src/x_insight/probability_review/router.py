"""CPT adjustment commands (S48a, seam T1).

``POST /api/v1/question-runs/{id}/cpt-adjustments`` — author-only
(physician + CSRF). Body carries the target row identity
(``node_id``, full ``parent_states``, ``state``), the decimal-string
``target_percentage``, and ``expected_review_revision`` (optimistic
pointer, ``If-Match`` wins when present). The server redistributes from
the immediately preceding committed row (integer units, largest-remainder,
declared state-order ties), persists one immutable complete
``cpt_revisions`` row plus the ``question_review_states`` pointer and
atomic audit, and returns the saved revision/state. ``Idempotency-Key``
repeats return the original without duplicating revisions; same key with
a different body is ``409``; stale expected revisions are ``412``;
invalid targets/precision are ``422`` (never repaired). Failed originals
(no ``OriginalBaseline``) have no adjustable baseline (``422``).
Wrong-author/admin reads and adjustments are denied without content leak.
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.db import get_session
from x_insight.identity import service as identity_service
from x_insight.probability_review import service as review_service

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


class AdjustmentRequest(BaseModel):
    """One completed slider command (extra=forbid, server redistributes)."""

    model_config = {"extra": "forbid"}

    node_id: str = Field(min_length=1)
    parent_states: list[str] = Field(default_factory=list)
    state: str = Field(min_length=1)
    target_percentage: str = Field(min_length=1)
    expected_review_revision: int = Field(ge=1)


@router.post("/question-runs/{run_id}/cpt-adjustments", status_code=200)
def create_adjustment(
    run_id: uuid.UUID,
    payload: AdjustmentRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    from x_insight.reasoning import coordinator as coordinator_module
    from x_insight.reasoning import snapshots as snapshots_service
    from x_insight.reasoning import tables as reasoning_tables

    request_id = get_request_id(request)
    physician = _require_physician_mutation(request, session)
    if isinstance(physician, JSONResponse):
        return physician
    assert isinstance(physician, dict)
    # Optimistic revision: If-Match wins when present, else the body field.
    # Both present but disagreeing is a stale write (never merged).
    header_raw = request.headers.get(contracts.IF_MATCH_HEADER)
    header_expected: int | None = None
    if header_raw is not None and header_raw.strip() != "*":
        header_expected = contracts.parse_if_match(header_raw)
    expected = int(payload.expected_review_revision)
    if header_expected is not None and int(header_expected) != int(expected):
        return error_response(
            412,
            "STALE_REVISION",
            "The probability review changed. Reload and reconcile your edits.",
            request_id,
            {"expected_review_revision": ["Stale review revision."]},
        )
    if header_expected is not None:
        expected = int(header_expected)
    key = contracts.parse_idempotency_key(request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER))
    request_hash = review_service.adjustment_request_hash(
        run_id,
        expected,
        payload.node_id,
        list(payload.parent_states),
        payload.state,
        payload.target_percentage,
    )
    if key is not None:
        stored = identity_service.lookup_idempotency(
            session,
            operation=review_service.ADJUSTMENT_IDEMPOTENCY_OPERATION,
            actor_id=physician["id"],
            key=key,
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return error_response(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key was already used with a different request body.",
                    request_id,
                )
            replay = dict(stored["response_body"])
            return JSONResponse(status_code=int(stored["response_status"]), content=replay)
    run_row = (
        session.execute(
            select(reasoning_tables.question_runs).where(
                reasoning_tables.question_runs.c.id == run_id
            )
        )
        .mappings()
        .first()
    )
    if run_row is None:
        return error_response(404, "NOT_FOUND", "Question run not found.", request_id)
    run = dict(run_row)
    # Author-only (403 for strangers/admin without content; 404 stays 404).
    batch, _, _ = snapshots_service.get_generation_batch(session, run["batch_id"], physician)
    stored_baseline = coordinator_module.get_baseline(session, run_id)
    revision, new_state = review_service.apply_adjustment(
        session,
        author=physician,
        run=run,
        batch=batch,
        baseline=stored_baseline,
        node_id=payload.node_id,
        parent_states=list(payload.parent_states),
        state=payload.state,
        target_percentage=payload.target_percentage,
        expected_review_revision=int(expected),
        request_id=request_id,
    )
    safe_rev = review_service.safe_revision(revision)
    safe_state = review_service.safe_review_state(new_state)
    assert safe_state is not None
    response_body: dict[str, Any] = {
        "revision": safe_rev,
        "review_state": safe_state,
        "current_cpt_revision_id": safe_rev["id"],
        "review_revision": int(safe_state["review_revision"]),
        "cpt_hash": str(safe_rev["cpt_hash"]),
        "current_tables": list(safe_rev["cpt_artifact"]),
    }
    if key is not None:
        identity_service.store_idempotency(
            session,
            operation=review_service.ADJUSTMENT_IDEMPOTENCY_OPERATION,
            actor_id=physician["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    return JSONResponse(status_code=200, content=response_body)
