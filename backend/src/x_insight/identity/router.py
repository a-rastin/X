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


# --- S04 physician account administration (plan.md §§2.1, 4.3; FR-03–04) ---
#
# All routes under /api/v1 (plan.md §4.3), admin-only with session + CSRF on
# mutations. Role comes from the stored account row, never the login role.
# Responses use the safe physician shape (never hash/token/password).


class PhysicianCreateRequest(BaseModel):
    username: str = Field(min_length=0, max_length=150)
    password: str = Field(min_length=0, max_length=500)
    role: str | None = Field(default=None, max_length=20)


class PhysicianPatchRequest(BaseModel):
    username: str | None = Field(default=None, max_length=150)
    password: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


def _require_admin(request: Request, session: Session) -> dict[str, Any] | JSONResponse:
    user = _current_user(request, session)
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
    gate = _check_csrf(request, session)
    if isinstance(gate, JSONResponse):
        return gate
    assert isinstance(gate, dict)
    if gate.get("role") != "admin" or not gate.get("active", False):
        return error_response(
            403, "FORBIDDEN", "Administrator access required.", get_request_id(request)
        )
    return gate


def _physician_response(user: dict[str, Any]) -> JSONResponse:
    safe = service.safe_physician(user)
    payload = {"user": safe}
    response = JSONResponse(status_code=200, content=payload)
    response.headers["ETag"] = contracts.format_etag(int(safe["revision"]))
    return response


def _idempotency_key_or_none(request: Request) -> str | None:
    raw = request.headers.get(contracts.IDEMPOTENCY_KEY_HEADER)
    return contracts.parse_idempotency_key(raw)


def _idempotency_replay(
    stored: dict[str, Any],
) -> JSONResponse:
    body = dict(stored["response_body"])
    response = JSONResponse(status_code=int(stored["response_status"]), content=body)
    user = body.get("user")
    if isinstance(user, dict) and "revision" in user:
        try:
            response.headers["ETag"] = contracts.format_etag(int(user["revision"]))
        except ValueError:
            pass
    return response


def _idempotency_conflict(request_id: str) -> JSONResponse:
    return error_response(
        409,
        "IDEMPOTENCY_CONFLICT",
        "Idempotency-Key was already used with a different request body.",
        request_id,
    )


@router.get("/physicians")
def list_physicians(
    request: Request,
    session: Session = Depends(get_session),
    limit: int | None = None,
    offset: int | None = None,
) -> JSONResponse:
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    items, total = service.list_physicians(session, limit=limit, offset=offset)
    safe_items = [service.safe_physician(item) for item in items]
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    return JSONResponse(
        status_code=200,
        content={
            "items": safe_items,
            "total": total,
            "limit": resolved_limit,
            "offset": resolved_offset,
        },
    )


@router.post("/physicians", status_code=201)
def create_physician(
    payload: PhysicianCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    if payload.role is not None and payload.role.strip() != service.PHYSICIAN_ROLE:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Only physician accounts can be created here.",
            {"role": ["Must be 'physician'."]},
        )
    body = {"username": payload.username, "password": payload.password}
    if payload.role is not None:
        body["role"] = payload.role
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = service.idempotency_request_hash(body)
        stored = service.lookup_idempotency(
            session, operation="physicians.create", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    created = service.create_physician(
        session, username=payload.username, password=payload.password
    )
    audit_module.record_audit(
        session,
        operation="physicians.create.success",
        actor=str(admin["username"]),
        request_id=request_id,
        details={"username": str(created["username"])},
    )
    safe = service.safe_physician(created)
    response_body = {"user": safe}
    if key is not None:
        assert request_hash is not None
        service.store_idempotency(
            session,
            operation="physicians.create",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=201,
            response_body=response_body,
        )
    response = JSONResponse(status_code=201, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(safe["revision"]))
    return response


@router.get("/physicians/{user_id}")
def get_physician(
    user_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    target = service.get_user_by_id(session, user_id)
    if target is None:
        return error_response(404, "NOT_FOUND", "Physician not found.", get_request_id(request))
    return _physician_response(target)


@router.patch("/physicians/{user_id}")
def patch_physician(
    user_id: uuid.UUID,
    payload: PhysicianPatchRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    if payload.username is None and payload.password is None:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Nothing to update.",
            {"request": ["Provide username and/or password."]},
        )
    target = service.get_user_by_id(session, user_id)
    if target is None:
        return error_response(404, "NOT_FOUND", "Physician not found.", request_id)
    # Optimistic revision: a supplied If-Match that mismatches is 412;
    # absent/"*" means no precondition (contracts.parse_if_match).
    if_match_raw = request.headers.get(contracts.IF_MATCH_HEADER)
    expected = contracts.parse_if_match(if_match_raw)
    if expected is not None and expected != int(target["revision"]):
        return error_response(
            412,
            "STALE_REVISION",
            "The account changed. Reload and reconcile your edits.",
            request_id,
        )
    body: dict[str, Any] = {}
    if payload.username is not None:
        body["username"] = payload.username
    if payload.password is not None:
        body["password"] = payload.password
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = service.idempotency_request_hash(body, target_id=user_id)
        stored = service.lookup_idempotency(
            session, operation="physicians.patch", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    updated = service.patch_physician(
        session, user_id, username=payload.username, password=payload.password
    )
    audit_module.record_audit(
        session,
        operation="physicians.patch.success",
        actor=str(admin["username"]),
        request_id=request_id,
        details={"username": str(updated["username"])},
    )
    safe = service.safe_physician(updated)
    response_body = {"user": safe}
    if key is not None:
        assert request_hash is not None
        service.store_idempotency(
            session,
            operation="physicians.patch",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    response = JSONResponse(status_code=200, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(safe["revision"]))
    return response


class DeactivateRequest(BaseModel):
    draft_action: str
    draft_set_revision: int | None = None

    model_config = {"extra": "forbid"}


@router.post("/physicians/{user_id}/deactivate")
def deactivate_physician(
    user_id: uuid.UUID,
    payload: DeactivateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    body: dict[str, Any] = {"draft_action": payload.draft_action}
    if payload.draft_set_revision is not None:
        body["draft_set_revision"] = payload.draft_set_revision
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = service.idempotency_request_hash(body, target_id=user_id)
        stored = service.lookup_idempotency(
            session, operation="physicians.deactivate", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    updated, changed, current_revision, reviewed = service.deactivate_physician(
        session,
        user_id,
        draft_action=payload.draft_action,
        draft_set_revision=payload.draft_set_revision,
    )
    if changed:
        audit_module.record_audit(
            session,
            operation="physicians.deactivate.success",
            actor=str(admin["username"]),
            request_id=request_id,
            details={
                "username": str(updated["username"]),
                "draft_action": payload.draft_action,
                "draft_set_revision": int(current_revision),
                "reviewed_drafts": list(reviewed),
            },
        )
    safe = service.safe_physician(updated)
    response_body: dict[str, Any] = {
        "user": safe,
        "draft_action": payload.draft_action,
        "draft_set_revision": int(current_revision),
        "reviewed_drafts": list(reviewed),
    }
    if key is not None:
        assert request_hash is not None
        service.store_idempotency(
            session,
            operation="physicians.deactivate",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    response = JSONResponse(status_code=200, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(safe["revision"]))
    return response


@router.get("/physicians/{user_id}/open-drafts")
def get_open_drafts(
    user_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    """Admin-only draft-set preview for discard confirmation (S51 §2).

    Returns minimal identifiers (encounter/patient/revision, no clinical
    content) + stable revision. Does not grant draft viewing/editing and does
    not deactivate. Changing the set invalidates a previously reviewed
    revision (discard then 409).
    """
    admin = _require_admin(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    target = service.get_user_by_id(session, user_id)
    if target is None:
        return error_response(404, "NOT_FOUND", "Physician not found.", get_request_id(request))
    open_drafts = service.list_open_drafts_for_author(session, target["id"])
    revision = service.compute_draft_set_revision(open_drafts)
    reviewed = service.draft_set_identifiers(open_drafts)
    return JSONResponse(
        status_code=200,
        content={"draft_set_revision": int(revision), "reviewed_drafts": list(reviewed)},
    )


@router.post("/physicians/{user_id}/reactivate")
def reactivate_physician(
    user_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> JSONResponse:
    request_id = get_request_id(request)
    admin = _require_admin_mutation(request, session)
    if isinstance(admin, JSONResponse):
        return admin
    assert isinstance(admin, dict)
    key = _idempotency_key_or_none(request)
    request_hash: str | None = None
    if key is not None:
        request_hash = service.idempotency_request_hash({}, target_id=user_id)
        stored = service.lookup_idempotency(
            session, operation="physicians.reactivate", actor_id=admin["id"], key=key
        )
        if stored is not None:
            if stored["request_hash"] != request_hash:
                return _idempotency_conflict(request_id)
            return _idempotency_replay(stored)
    updated, changed = service.reactivate_physician(session, user_id)
    if changed:
        audit_module.record_audit(
            session,
            operation="physicians.reactivate.success",
            actor=str(admin["username"]),
            request_id=request_id,
            details={"username": str(updated["username"])},
        )
    safe = service.safe_physician(updated)
    response_body = {"user": safe}
    if key is not None:
        assert request_hash is not None
        service.store_idempotency(
            session,
            operation="physicians.reactivate",
            actor_id=admin["id"],
            key=key,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
        )
    response = JSONResponse(status_code=200, content=response_body)
    response.headers["ETag"] = contracts.format_etag(int(safe["revision"]))
    return response
