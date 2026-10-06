"""DDI runtime routes (S19, seams T1/T4; plan.md §§4.3, 6.3, FR-14/15).

``GET /api/v1/drugs?query=...`` — authenticated catalog search for forms;
any active session (physician or administrator) may read, no CSRF.
``POST /api/v1/ddi/check`` — deterministic drug-only check; any active
session may call, CSRF required (mutation). All failures use the standard
``contracts.ErrorBody`` (never secrets/tracebacks).

Input is drug-only ``{medications: [{catalog_drug_id}], dataset_version}``:
free-text ``unknown-label``, dose/unit/route/frequency/status fields are
rejected ``422`` even if forged (``extra=forbid``). Unresolved ingestion
concepts stay in ingestion review and are never accepted as patient input —
unknown catalog IDs are ``422``, not free-text medications.
"""

from __future__ import annotations

import hmac as hmac_module
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight import db as db_module
from x_insight.db import get_session
from x_insight.identity import service as identity_service

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


def _require_mutation(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    """Any active session + CSRF (DDI checks are readable by admin or physician)."""
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
    return user


class MedicationItem(BaseModel):
    """Drug-only entry: catalog reference alone (extra=forbid rejects dose etc.)."""

    model_config = {"extra": "forbid"}

    catalog_drug_id: str = Field(min_length=1, max_length=200)


class CheckRequest(BaseModel):
    """Drug-only check body: no free-text, no regimen fields."""

    model_config = {"extra": "forbid"}

    medications: list[MedicationItem]
    dataset_version: str = Field(min_length=1, max_length=100)


@router.get("/drugs")
def search_drugs(
    request: Request,
    session: Session = Depends(get_session),
    query: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    dataset_version: str | None = None,
) -> JSONResponse:
    user = _require_user(request, session)
    if isinstance(user, JSONResponse):
        return user
    request_id = get_request_id(request)
    engine = db_module.get_engine()
    from x_insight.ddi import checker as checker_module

    version = dataset_version or checker_module.latest_release_version(engine)
    if version is None:
        return error_response(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.", request_id)
    try:
        release = checker_module.get_release_row(engine, version)
    except checker_module.CheckError as exc:
        return error_response(exc.status_code, exc.code, exc.message, request_id, exc.field_errors)
    except Exception:
        return error_response(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.", request_id)
    try:
        rows = checker_module.list_concepts(engine, release["id"])
    except checker_module.CheckError as exc:
        return error_response(exc.status_code, exc.code, exc.message, request_id, exc.field_errors)
    except Exception:
        return error_response(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.", request_id)
    items = [
        {
            "catalog_drug_id": row["catalog_drug_id"],
            "concept_id": row["concept_id"],
            "canonical_name": row["canonical_name"],
            "concept_type": row["concept_type"],
        }
        for row in rows
    ]
    if query is not None and query.strip() != "":
        needle = " ".join(query.lower().split())
        items = [
            row
            for row in items
            if needle in str(row["canonical_name"]).lower()
            or needle in str(row["catalog_drug_id"]).lower()
        ]
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    total = len(items)
    page = items[resolved_offset : resolved_offset + resolved_limit]
    return JSONResponse(
        status_code=200,
        content={
            "dataset_version": version,
            "catalog_version": release.get("terminology_version") or "",
            "items": page,
            "total": total,
            "limit": resolved_limit,
            "offset": resolved_offset,
        },
    )


@router.post("/ddi/check")
def check_ddi(
    payload: CheckRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    user = _require_mutation(request, session)
    if isinstance(user, JSONResponse):
        return user
    engine = db_module.get_engine()
    from x_insight.ddi import checker as checker_module

    catalog_ids = [item.catalog_drug_id for item in payload.medications]
    try:
        report = checker_module.check(engine, catalog_ids, payload.dataset_version)
    except checker_module.CheckError as exc:
        return error_response(exc.status_code, exc.code, exc.message, request_id, exc.field_errors)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Internal server error.", request_id)
    return JSONResponse(status_code=200, content=report)
