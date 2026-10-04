"""Released-assessment content routes (S08, seam T1; plan.md §4.3).

``GET /api/v1/content/assessments/{type}`` returns the released definition
plus its version. Any active authenticated session (physician or
administrator) may read; missing/revoked sessions get ``401``, unknown
types ``404``. Reads need no CSRF token (same contract as the patient
directory). All failures use the standard ``contracts.ErrorBody`` and the
correlation header is attached by middleware.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.released import RELEASED_TYPES, get_released_definition
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
