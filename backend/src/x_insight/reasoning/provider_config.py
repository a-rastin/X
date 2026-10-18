"""Admin provider settings (S42).

Append-only ``provider_settings`` revisions. The API key is AES-GCM
ciphertext (12-byte nonce, no AAD, tag appended), copied as stored bytes
on ``unchanged``, or NULL on ``clear``. The deployment key stays in
``X_INSIGHT_PROVIDER_DATA_KEY`` and never enters the database, audit
details, logs, or HTTP responses. Decryption happens only to build the
Authorization header for the capability check, and plaintext is not
logged, audited, or returned.
"""

from __future__ import annotations

import base64
import hmac as hmac_module
import ipaddress
import os
import socket
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.db import CommittedRoute, get_session
from x_insight.identity import service as identity_service
from x_insight.operations.audit import record_audit

PROVIDER_DATA_KEY_ENV = "X_INSIGHT_PROVIDER_DATA_KEY"
ALLOWED_HOSTS_ENV = "X_INSIGHT_PROVIDER_ALLOWED_HOSTS"
SAVE_OPERATION = "api_settings.save"
TEST_OPERATION = "api_settings.test"
KEY_STATUS_CONFIGURED = "configured"
KEY_STATUS_ABSENT = "absent"
CAPABILITY_UNTESTED = "untested"
CAPABILITY_VERIFIED = "verified"
CAPABILITY_FAILED = "failed"
_NONCE_BYTES = 12
_ACTIONS = frozenset({"unchanged", "replace", "clear"})

metadata = MetaData()

provider_settings = Table(
    "provider_settings",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("base_url", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("key_nonce", LargeBinary, nullable=True),
    Column("key_ciphertext", LargeBinary, nullable=True),
    Column("capability_status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Text, nullable=False),
)

router = APIRouter(route_class=CommittedRoute)


class ProviderKeyError(Exception):
    """Deployment key missing or not 32 bytes. Message never includes key material."""


class ProviderSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    secret_action: Literal["unchanged", "replace", "clear"] | None = None


class CapabilityTestRequest(BaseModel):
    """Empty body. The saved row is the only input."""

    model_config = ConfigDict(extra="forbid")


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
) -> JSONResponse:
    body = contracts.ErrorBody(
        code=code,
        message=message,
        field_errors=field_errors or {},
        request_id=request_id,
        retryable=retryable,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _current_user(request: Request, session: Session) -> dict[str, Any] | None:
    return identity_service.get_session_user(
        session, request.cookies.get(identity_service.SESSION_COOKIE_NAME)
    )


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


def _deployment_key() -> bytes:
    raw = os.environ.get(PROVIDER_DATA_KEY_ENV, "")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        raise ProviderKeyError("Provider data key is not configured.") from None
    if len(key) != 32:
        raise ProviderKeyError("Provider data key is not configured.")
    return key


def _seal(api_key: str) -> tuple[bytes, bytes]:
    key = _deployment_key()
    nonce = os.urandom(_NONCE_BYTES)
    try:
        ciphertext = AESGCM(key).encrypt(nonce, api_key.encode("utf-8"), None)
    except Exception:
        raise ProviderKeyError("Provider settings cannot be saved.") from None
    return nonce, ciphertext


def _masked(
    revision: int,
    base_url: str,
    model: str,
    *,
    has_key: bool,
    capability: str = CAPABILITY_UNTESTED,
) -> dict[str, Any]:
    return {
        "revision": revision,
        "api_key": KEY_STATUS_CONFIGURED if has_key else KEY_STATUS_ABSENT,
        "base_url": base_url,
        "model": model,
        "capability": capability,
    }


def _as_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    return bytes(value)


def _latest_stored(session: Session) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(
                provider_settings.c.revision,
                provider_settings.c.base_url,
                provider_settings.c.model,
                provider_settings.c.key_nonce,
                provider_settings.c.key_ciphertext,
            )
            .order_by(provider_settings.c.revision.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def read_latest(session: Session) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(
                provider_settings.c.revision,
                provider_settings.c.base_url,
                provider_settings.c.model,
                provider_settings.c.capability_status,
                provider_settings.c.key_ciphertext.is_not(None).label("has_key"),
            )
            .order_by(provider_settings.c.revision.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return _masked(
        int(row["revision"]),
        str(row["base_url"]),
        str(row["model"]),
        has_key=bool(row["has_key"]),
        capability=str(row["capability_status"]),
    )


def read_revision(session: Session, revision: int) -> dict[str, Any] | None:
    """One historical row. Does not decrypt."""
    row = (
        session.execute(
            select(
                provider_settings.c.revision,
                provider_settings.c.base_url,
                provider_settings.c.model,
                provider_settings.c.capability_status,
                provider_settings.c.key_ciphertext.is_not(None).label("has_key"),
            ).where(provider_settings.c.revision == revision)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return _masked(
        int(row["revision"]),
        str(row["base_url"]),
        str(row["model"]),
        has_key=bool(row["has_key"]),
        capability=str(row["capability_status"]),
    )


def _allowed_hosts() -> set[str]:
    raw = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def _require_allowed_destination(base_url: str) -> None:
    """Fail closed. Exact host match only; no DNS and no loopback exception."""
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
    except ValueError:
        parts = None
        host = None
    allowed = _allowed_hosts()
    if (
        parts is None
        or parts.scheme not in ("http", "https")
        or parts.username is not None
        or parts.password is not None
        or not host
        or host.casefold() not in allowed
    ):
        raise contracts.ContractError(
            422,
            "PROVIDER_DESTINATION_DENIED",
            "Provider base URL is not an allowed destination.",
            {
                "base_url": [
                    "Use http or https with no userinfo, and a host on the operator allowlist."
                ]
            },
        )


# RFC 2544 block used by fake-IP proxies. Not RFC1918 or link-local metadata.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _special_address(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text.split("%", 1)[0])
    except ValueError:
        return True
    if ip.version == 4 and ip in _FAKE_IP_NETWORK:
        return False
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolved_destination_allowed(base_url: str) -> bool:
    """DNS check for the capability POST. Special-use IPs must be allowlisted exactly."""
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        if not host:
            return False
        infos = socket.getaddrinfo(host, parts.port or 0, type=socket.SOCK_STREAM)
    except Exception:
        return False
    if not infos:
        return False
    allowed = _allowed_hosts()
    for info in infos:
        ip_text = str(info[4][0]).split("%", 1)[0]
        if _special_address(ip_text) and ip_text.casefold() not in allowed:
            return False
    return True


def _key_material(
    action: str,
    api_key: str | None,
    latest: dict[str, Any] | None,
) -> tuple[bytes | None, bytes | None]:
    """Resolve nonce and ciphertext. Never seals the masked placeholder."""
    if action == "unchanged":
        if latest is None:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "No saved provider settings to keep.",
                {"secret_action": ["Nothing to keep yet."]},
            )
        return _as_bytes(latest["key_nonce"]), _as_bytes(latest["key_ciphertext"])
    if action == "clear":
        return None, None
    if not api_key or api_key == KEY_STATUS_CONFIGURED:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "A new API key is required.",
            {"api_key": ["Provide a new API key. The masked placeholder cannot be stored."]},
        )
    return _seal(api_key)


def save_settings(
    session: Session,
    *,
    api_key: str | None,
    secret_action: str | None,
    base_url: str,
    model: str,
    username: str,
    request_id: str,
    if_match: int | None,
) -> dict[str, Any]:
    """Insert the next revision. Plaintext is sealed only on replace."""
    _require_allowed_destination(base_url)
    latest = _latest_stored(session)
    current = int(latest["revision"]) if latest is not None else None
    if if_match is not None and if_match != current:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "Provider settings changed. Reload and retry.",
        )
    action = secret_action if secret_action is not None else "replace"
    if action not in _ACTIONS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "secret_action must be unchanged, replace, or clear.",
            {"secret_action": ["Must be unchanged, replace, or clear."]},
        )
    nonce, ciphertext = _key_material(action, api_key, latest)
    has_key = nonce is not None and ciphertext is not None
    # ponytail: revision = latest+1; the unique index rejects a concurrent save.
    revision = (current or 0) + 1
    key_status = KEY_STATUS_CONFIGURED if has_key else KEY_STATUS_ABSENT
    session.execute(
        insert(provider_settings).values(
            id=uuid.uuid4(),
            revision=revision,
            base_url=base_url,
            model=model,
            key_nonce=nonce,
            key_ciphertext=ciphertext,
            capability_status=CAPABILITY_UNTESTED,
            created_at=contracts.utcnow(),
            created_by=username,
        )
    )
    record_audit(
        session,
        operation=SAVE_OPERATION,
        actor=username,
        request_id=request_id,
        details={
            "revision": revision,
            "model": model,
            "base_url": base_url,
            "key_status": key_status,
        },
    )
    body = _masked(revision, base_url, model, has_key=has_key)
    # Initial save and unchanged keep the prior credential, so jobs stay.
    if secret_action in ("replace", "clear"):
        from x_insight.reasoning.queue import cancel_active_generation_jobs

        body["affected_runs"] = cancel_active_generation_jobs(session)
    return body


def latest_settings_revision(session: Session) -> int | None:
    """Newest stored revision, or None when the administrator has saved nothing."""
    row = _latest_stored(session)
    if row is None:
        return None
    return int(row["revision"])


def open_runtime(session: Session, revision: int) -> tuple[str, str, str] | str:
    """Pinned endpoint, model, and API key, or a non-retryable error code.

    The key is returned only to the caller that builds the Authorization
    header. This function does not log it.
    """
    row = (
        session.execute(
            select(
                provider_settings.c.base_url,
                provider_settings.c.model,
                provider_settings.c.key_nonce,
                provider_settings.c.key_ciphertext,
            ).where(provider_settings.c.revision == revision)
        )
        .mappings()
        .first()
    )
    if row is None:
        return "PROVIDER_NOT_CONFIGURED"
    base_url = str(row["base_url"])
    try:
        _require_allowed_destination(base_url)
    except contracts.ContractError:
        return "PROVIDER_DESTINATION_DENIED"
    if not _resolved_destination_allowed(base_url):
        return "PROVIDER_DESTINATION_DENIED"
    nonce = _as_bytes(row["key_nonce"])
    ciphertext = _as_bytes(row["key_ciphertext"])
    if nonce is None or ciphertext is None:
        return "PROVIDER_AUTH"
    try:
        api_key = _open_key(nonce, ciphertext)
    except ProviderKeyError:
        return "PROVIDER_AUTH"
    if not api_key:
        return "PROVIDER_AUTH"
    return base_url, str(row["model"]), api_key


def _open_key(nonce: bytes, ciphertext: bytes) -> str:
    key = _deployment_key()
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
    except Exception:
        raise ProviderKeyError("Provider API key could not be read.") from None


def _capability_outcome(base_url: str, model: str, nonce: Any, ciphertext: Any) -> tuple[str, str]:
    """Check the saved endpoint. Fixed diagnostics only; never the upstream body or key."""
    if not _resolved_destination_allowed(base_url):
        return CAPABILITY_FAILED, "Resolved address is not an allowlisted IP."
    if nonce is None or ciphertext is None:
        return CAPABILITY_FAILED, "No provider API key is stored."
    try:
        api_key = _open_key(bytes(nonce), bytes(ciphertext))
    except ProviderKeyError:
        return CAPABILITY_FAILED, "Provider API key could not be read."
    from x_insight.reasoning.provider import probe_capability

    ok, diagnostic = probe_capability(base_url, model, api_key)
    return (CAPABILITY_VERIFIED if ok else CAPABILITY_FAILED), diagnostic


def record_capability_test(
    session: Session,
    *,
    username: str,
    request_id: str,
) -> dict[str, Any]:
    """Append one revision for this check. A save stays ``untested``."""
    latest = _latest_stored(session)
    if latest is None:
        raise contracts.ContractError(
            404,
            "NOT_FOUND",
            "Provider settings are not configured.",
        )
    base_url = str(latest["base_url"])
    _require_allowed_destination(base_url)
    capability, diagnostic = _capability_outcome(
        base_url,
        str(latest["model"]),
        latest["key_nonce"],
        latest["key_ciphertext"],
    )
    revision = int(latest["revision"]) + 1
    session.execute(
        insert(provider_settings).values(
            id=uuid.uuid4(),
            revision=revision,
            base_url=base_url,
            model=str(latest["model"]),
            key_nonce=_as_bytes(latest["key_nonce"]),
            key_ciphertext=_as_bytes(latest["key_ciphertext"]),
            capability_status=capability,
            created_at=contracts.utcnow(),
            created_by=username,
        )
    )
    record_audit(
        session,
        operation=TEST_OPERATION,
        actor=username,
        request_id=request_id,
        details={"capability": capability, "revision": revision},
    )
    return {
        "capability": capability,
        "diagnostic": diagnostic,
        "revision": revision,
        "base_url": base_url,
        "model": str(latest["model"]),
    }


@router.put("/api-settings")
def put_api_settings(
    payload: ProviderSettingsWrite,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    user = _require_admin_mutation(request, session)
    if isinstance(user, JSONResponse):
        return user
    request_id = get_request_id(request)
    try:
        if_match = contracts.parse_if_match(request.headers.get("if-match"))
        saved = save_settings(
            session,
            api_key=payload.api_key,
            secret_action=payload.secret_action,
            base_url=payload.base_url,
            model=payload.model,
            username=str(user.get("username") or ""),
            request_id=request_id,
            if_match=if_match,
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            request_id,
            exc.field_errors,
            exc.retryable,
        )
    except ProviderKeyError as exc:
        return error_response(503, "UNAVAILABLE", exc.args[0], request_id)
    return JSONResponse(status_code=200, content=saved)


@router.post("/api-settings/test")
def post_api_settings_test(
    _payload: CapabilityTestRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    user = _require_admin_mutation(request, session)
    if isinstance(user, JSONResponse):
        return user
    request_id = get_request_id(request)
    try:
        result = record_capability_test(
            session,
            username=str(user.get("username") or ""),
            request_id=request_id,
        )
    except contracts.ContractError as exc:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            request_id,
            exc.field_errors,
            exc.retryable,
        )
    except ProviderKeyError as exc:
        return error_response(503, "UNAVAILABLE", exc.args[0], request_id)
    return JSONResponse(status_code=200, content=result)


@router.get("/api-settings")
def get_api_settings(
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    latest = read_latest(session)
    if latest is None:
        return error_response(
            404, "NOT_FOUND", "Provider settings are not configured.", get_request_id(request)
        )
    return JSONResponse(status_code=200, content=latest)


@router.get("/api-settings/revisions/{revision}")
def get_api_settings_revision(
    revision: int,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    user = _require_admin(request, session)
    if isinstance(user, JSONResponse):
        return user
    row = read_revision(session, revision)
    if row is None:
        return error_response(
            404,
            "NOT_FOUND",
            "Provider settings revision was not found.",
            get_request_id(request),
        )
    return JSONResponse(status_code=200, content=row)
