"""Identity HTTP routes (S03, seam T1, plan.md §4.3).

``POST /api/v1/auth/login`` / ``POST /api/v1/auth/logout`` /
``GET /api/v1/me`` / ``POST /api/v1/me/password`` /
``PATCH /api/v1/me/preferences``. All failures use the standard
``contracts.ErrorBody`` (never secrets/tracebacks). Sessions are opaque
HttpOnly/SameSite cookies (Secure under HTTPS); mutations additionally
require the per-session CSRF token.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.db import get_session
from x_insight.identity import passwords, service, tables
from x_insight.operations import audit as audit_module


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

_GENERIC_LOGIN_MESSAGE = "Invalid username, password, or role."


class LoginRequest(BaseModel):
    # Empty strings reach generic 401 (no enumeration); missing fields stay 422.
    username: str = Field(min_length=0, max_length=150)
    password: str = Field(min_length=0, max_length=500)
    role: str = Field(min_length=0, max_length=20)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=0, max_length=500)
    new_password: str = Field(min_length=0, max_length=500)


class PreferencesRequest(BaseModel):
    theme: str

    model_config = {"extra": "forbid"}


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "theme": user["theme"],
    }


def _warning_for(role: str) -> str | None:
    if role == "physician":
        return service.RESEARCH_WARNING
    return None


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _raw_session_token(request: Request) -> str | None:
    return request.cookies.get(service.SESSION_COOKIE_NAME)


def _set_session_cookie(response: Response, request: Request, raw_token: str) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(
        service.SESSION_COOKIE_NAME,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(service.SESSION_COOKIE_NAME, path="/")


def _current_user(request: Request, session: Session) -> dict[str, Any] | None:
    return service.get_session_user(session, _raw_session_token(request))


def _require_user(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = _current_user(request, session)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    return user


def _check_csrf(request: Request, session: Session) -> JSONResponse | dict[str, Any]:
    """CSRF gate for mutating authenticated routes (plan.md §4.3/§11)."""
    raw_token = _raw_session_token(request)
    if not raw_token:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    row = service.get_session_row(session, raw_token)
    if row is None or row.get("revoked_at") is not None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    presented = request.headers.get(service.CSRF_HEADER_NAME, "")
    # Constant-time compare on non-empty values only; empty never matches.
    import hmac as hmac_module

    expected = row.get("csrf_token", "")
    if not presented or not expected or not hmac_module.compare_digest(presented, expected):
        return error_response(403, "FORBIDDEN", "CSRF validation failed.", get_request_id(request))
    user = service.get_session_user(session, raw_token)
    if user is None:
        return error_response(
            401, "UNAUTHENTICATED", "Authentication required.", get_request_id(request)
        )
    return user


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    client_ip = _client_ip(request)
    # Throttling precedes credential checks (429, retryable).
    if service.is_login_throttled(client_ip, payload.username):
        audit_module.record_audit(
            session,
            operation="auth.login.throttled",
            actor=service.normalize_username(payload.username),
            request_id=request_id,
            details={"username": service.normalize_username(payload.username)},
        )
        return error_response(
            429,
            "RATE_LIMITED",
            "Too many login attempts. Retry shortly.",
            request_id,
            retryable=True,
        )
    # Empty password fails without trimming (generic 401, no user enumeration).
    username_norm = service.normalize_username(payload.username)
    password_raw = payload.password  # verbatim, never stripped
    role_selected = payload.role.strip()
    if password_raw == "" or username_norm == "":
        service.record_login_failure(client_ip, payload.username)
        audit_module.record_audit(
            session,
            operation="auth.login.failure",
            actor=username_norm or None,
            request_id=request_id,
            details={"username": username_norm},
        )
        return error_response(401, "UNAUTHENTICATED", _GENERIC_LOGIN_MESSAGE, request_id)
    user = service.get_user_by_username(session, payload.username)
    ok = (
        user is not None
        and user.get("active", False)
        and passwords.verify_password(password_raw, str(user.get("password_hash", "")))
        and user.get("role") == role_selected
    )
    if not ok:
        service.record_login_failure(client_ip, payload.username)
        audit_module.record_audit(
            session,
            operation="auth.login.failure",
            actor=username_norm,
            request_id=request_id,
            details={"username": username_norm},
        )
        return error_response(401, "UNAUTHENTICATED", _GENERIC_LOGIN_MESSAGE, request_id)
    assert user is not None
    service.clear_login_failures(client_ip, payload.username)
    raw_token, csrf_token = service.create_session(session, user)
    audit_module.record_audit(
        session,
        operation="auth.login.success",
        actor=str(user["username"]),
        request_id=request_id,
        details={"username": str(user["username"]), "role": str(user["role"])},
    )
    body: dict[str, Any] = {
        "user": _public_user(user),
        "csrf_token": csrf_token,
        "research_warning": _warning_for(str(user["role"])),
    }
    json_response = JSONResponse(status_code=200, content=body)
    _set_session_cookie(json_response, request, raw_token)
    # Correlation header is added by middleware on send; include request_id
    # in payload-adjacent audit only (no secret in body).
    return json_response


@router.post("/auth/logout")
def logout(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    request_id = get_request_id(request)
    gate = _check_csrf(request, session)
    if isinstance(gate, JSONResponse):
        return gate
    assert isinstance(gate, dict)
    user = gate
    raw_token = _raw_session_token(request)
    assert raw_token is not None
    service.revoke_session(session, raw_token)
    audit_module.record_audit(
        session,
        operation="auth.logout",
        actor=str(user["username"]),
        request_id=request_id,
        details={"username": str(user["username"])},
    )
    json_response = JSONResponse(status_code=200, content={"status": "ok"})
    _clear_session_cookie(json_response)
    return json_response


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)) -> JSONResponse:
    request_id = get_request_id(request)
    user = _current_user(request, session)
    if user is None:
        return error_response(401, "UNAUTHENTICATED", "Authentication required.", request_id)
    return JSONResponse(
        status_code=200,
        content={
            "user": _public_user(user),
            "research_warning": _warning_for(str(user["role"])),
        },
    )


@router.post("/me/password")
def change_password(
    payload: PasswordChangeRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    gate = _check_csrf(request, session)
    if isinstance(gate, JSONResponse):
        return gate
    assert isinstance(gate, dict)
    user = gate
    # Empty new password fails explicitly (422); no trimming, no complexity.
    if payload.new_password == "":
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "New password must not be empty.",
            {"new_password": ["Must not be empty."]},
        )
    fresh = service.get_user_by_id(session, uuid.UUID(str(user["id"])))
    if fresh is None or not fresh.get("active", False):
        return error_response(401, "UNAUTHENTICATED", _GENERIC_LOGIN_MESSAGE, request_id)
    if not passwords.verify_password(payload.current_password, str(fresh.get("password_hash", ""))):
        audit_module.record_audit(
            session,
            operation="auth.password_change.failure",
            actor=str(fresh["username"]),
            request_id=request_id,
            details={"username": str(fresh["username"])},
        )
        return error_response(401, "UNAUTHENTICATED", _GENERIC_LOGIN_MESSAGE, request_id)
    now = contracts.utcnow()
    new_revision = int(fresh["credential_revision"]) + 1
    new_row_revision = int(fresh["revision"]) + 1
    session.execute(
        update(tables.users)
        .where(tables.users.c.id == fresh["id"])
        .values(
            password_hash=passwords.hash_password(payload.new_password),
            credential_revision=new_revision,
            revision=new_row_revision,
            updated_at=now,
        )
    )
    # Revoke every prior session (including the one used here), then issue a
    # fresh session so the changer stays authenticated with new credentials.
    service.revoke_all_user_sessions(session, fresh["id"], now=now)
    updated = service.get_user_by_id(session, fresh["id"])
    assert updated is not None
    raw_token, csrf_token = service.create_session(session, updated, now=now)
    audit_module.record_audit(
        session,
        operation="auth.password_change.success",
        actor=str(updated["username"]),
        request_id=request_id,
        details={"username": str(updated["username"])},
    )
    json_response = JSONResponse(
        status_code=200,
        content={
            "user": _public_user(updated),
            "csrf_token": csrf_token,
        },
    )
    _set_session_cookie(json_response, request, raw_token)
    return json_response


@router.patch("/me/preferences")
def update_preferences(
    payload: PreferencesRequest, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    gate = _check_csrf(request, session)
    if isinstance(gate, JSONResponse):
        return gate
    assert isinstance(gate, dict)
    user = gate
    if payload.theme not in ("light", "dark"):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid theme.",
            {"theme": ["Must be 'light' or 'dark'."]},
        )
    now = contracts.utcnow()
    fresh = service.get_user_by_id(session, uuid.UUID(str(user["id"])))
    if fresh is None:
        return error_response(401, "UNAUTHENTICATED", "Authentication required.", request_id)
    session.execute(
        update(tables.users)
        .where(tables.users.c.id == fresh["id"])
        .values(theme=payload.theme, updated_at=now, revision=int(fresh["revision"]) + 1)
    )
    updated = service.get_user_by_id(session, fresh["id"])
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="auth.preferences.success",
        actor=str(updated["username"]),
        request_id=request_id,
        details={"username": str(updated["username"]), "theme": payload.theme},
    )
    return JSONResponse(status_code=200, content={"user": _public_user(updated)})
