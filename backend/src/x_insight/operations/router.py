"""Operations HTTP routes (S52/S54, seams T1/T10, plan.md §§4.3, 10.1–10.2).

``GET /api/v1/audit-events`` — admin-only inspection over the atomically
committed events (S51 transactions own the commit; this route only reads).
Filters by actor/operation/time/target, stable (``occurred_at``, ``id``)
ordering, bounded pagination (default 25, max 100 via
``contracts.parse_pagination``). No update/delete interface exists by
design (append-only; normal app role lacks UPDATE/DELETE grants). This is
not tamper-proof storage: the database owner or a restore can replace
history.

S53 CSV exports (plan.md §10.1; FR-40) live here per plan §3.2
(Operations owns export): ``GET /api/v1/exports/patients.csv`` and
``GET /api/v1/exports/physicians.csv`` — admin-only (physicians 403, like
audit), ``private, no-store``, audited atomically at the command path.
Builders live in the cases owning module
(:mod:`x_insight.cases.reporting`); this router only gates, audits, and
serves bytes (no business logic here).

S54 backups (plan.md §10.2; FR-41–42, seam T10 over T1): ``POST
/api/v1/backups`` (admin-only mutation with CSRF + optional
``Idempotency-Key``; synchronous bounded build, 201 with the terminal job),
``GET /api/v1/backups/{id}`` (admin-only progress/manifest read,
``private, no-store``) and ``GET /api/v1/backups/{id}/download``
(admin-only bounded zip download, ``private, no-store``). No restore
routes exist here — staging/commit belong to S55/S56. Builders live in
:mod:`x_insight.operations.backup`; this router only gates and serves.
"""

from __future__ import annotations

import hmac as hmac_module
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from fastapi.responses import JSONResponse as _JSONResponse
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import reporting as reporting_module
from x_insight.db import get_engine, get_session
from x_insight.identity import service as identity_service
from x_insight.operations import audit as audit_module
from x_insight.operations import backup as backup_module

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


def _require_admin_mutation(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    """Session + CSRF + admin gate for backup creation (plan.md §§4.3, 11).

    Mirrors the cases archive/unarchive mutation gate: missing/revoked
    sessions are 401, CSRF mismatch is 403, active physicians are 403.
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
    if user.get("role") != "admin" or not user.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Administrator access required.", get_request_id(request)
        )
    return user


def _idempotency_key_or_none(request: Request) -> str | None:
    raw = request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER)
    return contracts.parse_idempotency_key(raw)


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


# --- S53 CSV exports (admin-only, audited, private/no-store) ---


def _csv_response(filename: str, body: bytes) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/exports/patients.csv", response_model=None)
def export_patients_csv(request: Request, session: Session = Depends(get_session)) -> Response:
    """Admin-only patient list CSV (physicians 403, like audit).

    Stable English headers, UTF-8, QUOTE_MINIMAL, formula-neutralized text,
    identifier as exact 10-digit text bytes (import the column as Text —
    CSV carries no types, no ``="..."`` wrappers). Audited atomically with
    actor + exported count (no clinical bodies beyond required refs).
    """
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    patients = reporting_module.list_all_patients(session)
    body = reporting_module.build_patients_csv(
        [
            {
                **row,
                "created_at": contracts.serialize_utc(row["created_at"]),
                "updated_at": contracts.serialize_utc(row["updated_at"]),
            }
            for row in patients
        ]
    )
    audit_module.record_audit(
        session,
        operation=audit_module.EXPORT_PATIENTS_SUCCESS_OPERATION,
        actor=str(admin.get("username")),
        request_id=get_request_id(request),
        details={
            "actor_id": str(admin.get("id")),
            "actor_display": str(admin.get("username", "")),
            "exported_count": len(patients),
        },
    )
    return _csv_response("patients.csv", body)


@router.get("/exports/physicians.csv", response_model=None)
def export_physicians_csv(request: Request, session: Session = Depends(get_session)) -> Response:
    """Admin-only physician list CSV (physicians 403; safe fields only).

    Never carries password hashes, tokens, or credential revisions — only
    the admin table's safe columns. Audited atomically like patients.csv.
    """
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    physicians = reporting_module.list_all_physicians(session)
    body = reporting_module.build_physicians_csv(physicians)
    audit_module.record_audit(
        session,
        operation=audit_module.EXPORT_PHYSICIANS_SUCCESS_OPERATION,
        actor=str(admin.get("username")),
        request_id=get_request_id(request),
        details={
            "actor_id": str(admin.get("id")),
            "actor_display": str(admin.get("username", "")),
            "exported_count": len(physicians),
        },
    )
    return _csv_response("physicians.csv", body)


# --- S54 consistent full backups (admin-only, audited, private/no-store) ---


def _parse_backup_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Backup id must be a UUID.",
            {"backup_id": ["Must be a UUID."]},
        ) from exc


@router.post("/backups", status_code=201, response_model=None)
def create_backup(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    """Create one consistent full backup (admin-only mutation).

    Synchronous bounded build with an async-compatible polling shape: 201
    carries the terminal job (``succeeded`` or ``failed``). Same
    ``Idempotency-Key`` replays the original job without a new export; a
    fresh key mints a fresh archive so retries never overwrite a good prior
    backup. Audited atomically with safe details (hashes/counts only).
    """
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    request_id = get_request_id(request)
    try:
        key = _idempotency_key_or_none(request)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    try:
        job, _replayed, _failed = backup_module.create_backup(
            session,
            get_engine(),
            actor=admin,
            request_id=request_id,
            idempotency_key=key,
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    return JSONResponse(
        status_code=201,
        content=backup_module.safe_job(job),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/backups/{backup_id}", response_model=None)
def get_backup(
    backup_id: str, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Backup progress/manifest read (admin-only, no CSRF, like audit reads).

    Returns the job with its manifest (hashes/inventory, no clinical
    bodies, no secrets) so coherence checks work without downloading.
    """
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    request_id = get_request_id(request)
    try:
        target = _parse_backup_id(backup_id)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    job = backup_module.get_backup(session, target)
    if job is None:
        return error_response(404, "NOT_FOUND", "Backup not found.", request_id)
    return JSONResponse(
        status_code=200,
        content=backup_module.safe_job(job),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/backups/{backup_id}/download", response_model=None)
def download_backup(
    backup_id: str, request: Request, session: Session = Depends(get_session)
) -> Response:
    """Bounded zip download (admin-only, ``private, no-store``).

    Only succeeded jobs with a staged final archive download; failed jobs
    have no complete archive (409), purged files are 404. The staged bytes
    are re-hashed before serving so a corrupted stage never downloads.
    """
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    request_id = get_request_id(request)
    try:
        target = _parse_backup_id(backup_id)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    job = backup_module.get_backup(session, target)
    if job is None:
        return error_response(404, "NOT_FOUND", "Backup not found.", request_id)
    try:
        blob = backup_module.read_archive_bytes(job)
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id, exc.field_errors, exc.retryable
        )
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="x-insight-backup-{target}.zip"',
            "Cache-Control": "private, no-store",
        },
    )
