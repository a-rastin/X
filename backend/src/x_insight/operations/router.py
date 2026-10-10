"""Operations HTTP routes (S52, seam T1, plan.md §§4.3, 10.1).

``GET /api/v1/audit-events`` — admin-only inspection over the atomically
committed events (S51 transactions own the commit; this route only reads).
Filters by actor/operation/time/target, stable (``occurred_at``, ``id``)
ordering, bounded pagination (default 25, max 100 via
``contracts.parse_pagination``). No update/delete interface exists by
design (append-only; normal app role lacks UPDATE/DELETE grants). This is
not tamper-proof storage: the database owner or a restore can replace
history.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.db import get_session
from x_insight.identity import service as identity_service
from x_insight.operations import audit as audit_module

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


def _require_admin(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    if user.get("role") != "admin" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Administrator access required.", get_request_id(request)
        )
    return user


def _parse_uuid_param(value: str | None, field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            f"Invalid {field}.",
            {field: ["Must be a UUID."]},
        ) from exc


def _parse_time_param(value: str | None, field: str) -> Any | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return contracts.parse_utc(str(value).strip())
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            f"Invalid {field}.",
            {field: ["Must be an ISO-8601 UTC timestamp."]},
        ) from exc


@router.get("/audit-events")
def list_audit_events(
    request: Request,
    session: Session = Depends(get_session),
    limit: int | None = None,
    offset: int | None = None,
    actor: str | None = None,
    operation: str | None = None,
    since: str | None = None,
    until: str | None = None,
    patient_id: str | None = None,
    encounter_id: str | None = None,
    question_run_id: str | None = None,
    batch_id: str | None = None,
    question_key: str | None = None,
) -> JSONResponse:
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    request_id = get_request_id(request)
    try:
        resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
        since_dt = _parse_time_param(since, "since")
        until_dt = _parse_time_param(until, "until")
        patient_uuid = _parse_uuid_param(patient_id, "patient_id")
        encounter_uuid = _parse_uuid_param(encounter_id, "encounter_id")
        run_uuid = _parse_uuid_param(question_run_id, "question_run_id")
        batch_uuid = _parse_uuid_param(batch_id, "batch_id")
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    rows = audit_module.list_audit_events(
        session,
        limit=resolved_limit,
        offset=resolved_offset,
        actor=actor,
        operation=operation,
        since=since_dt,
        until=until_dt,
        patient_id=patient_uuid,
        encounter_id=encounter_uuid,
        question_run_id=run_uuid,
        batch_id=batch_uuid,
        question_key=question_key,
    )
    total = audit_module.count_audit_events(
        session,
        actor=actor,
        operation=operation,
        since=since_dt,
        until=until_dt,
        patient_id=patient_uuid,
        encounter_id=encounter_uuid,
        question_run_id=run_uuid,
        batch_id=batch_uuid,
        question_key=question_key,
    )
    return JSONResponse(
        status_code=200,
        content={
            "items": [audit_module.safe_event(row) for row in rows],
            "total": int(total),
            "limit": int(resolved_limit),
            "offset": int(resolved_offset),
        },
    )
