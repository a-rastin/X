"""FastAPI application: S01 health check plus S02 request contracts + S03 identity.

S01 behavior is preserved exactly: ``GET /api/v1/health`` still returns
``{"status": "ok", "service": "x-insight"}`` with no database access.

S02 additions (plan.md §§4.3, 11):
- ``GET /api/v1/ready`` — liveness is cheap (health); readiness checks the
  database/schema and fails safe with ``503`` (never secrets/tracebacks).
- Request correlation: ``X-Request-ID`` is echoed when valid, generated
  otherwise, and attached to every response and every error body.
- Standard error body (``contracts.ErrorBody``) for contract errors,
  validation failures, routing errors, and unexpected failures.
- Size limits (``contracts.MAX_BODY_BYTES``) and ``Idempotency-Key`` format
  enforcement at the edge; revision conventions live in ``contracts`` for
  S03+ command routes (no test-only production endpoint is added here).

S03 additions (plan.md §§2.1, 4.3, 11): identity router
(``/api/v1/auth/*``, ``/api/v1/me*``) with singleton admin seeding,
opaque hashed sessions, CSRF, throttling, and revision revocation.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from x_insight import contracts, db
from x_insight.contracts import (
    IDEMPOTENCY_KEY_HEADER,
    MAX_BODY_BYTES,
    REQUEST_ID_HEADER,
)

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Allowlisted X-Request-ID charset (RFC 7230 token, 1-128 chars). Anything else
# (including CR/LF, controls, spaces, or overlong values) is rejected and
# replaced with a fresh UUID instead of being echoed.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9!#$%&'*+\-.^_`|~]{1,128}\Z")

_CODE_FOR_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    412: "STALE_REVISION",
    413: "REQUEST_TOO_LARGE",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    503: "UNAVAILABLE",
}


def get_request_id(request: Request) -> str:
    """Correlation ID for this request (scope, context, or fresh)."""
    scope_id = request.scope.get("request_id")
    if isinstance(scope_id, str) and scope_id:
        return scope_id
    context_id = request_id_var.get()
    if context_id:
        return context_id
    return contracts.new_request_id()


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


class RequestContextMiddleware:
    """Attach/generate the correlation ID and echo it on every response."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
                incoming = value.decode("latin-1").strip()
                break
        if _REQUEST_ID_RE.fullmatch(incoming or ""):
            request_id = incoming
        else:
            request_id = contracts.new_request_id()
        scope["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append(
                    (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class SizeLimitMiddleware:
    """Reject oversized request bodies with a safe ``413`` error body.

    The default cap is ``contracts.MAX_BODY_BYTES`` (1 MiB). ``path_max_bytes``
    raises the cap ONLY for the listed ``(method, path)`` pairs — currently
    just ``POST /api/v1/restores/validate`` (raw-zip staging up to
    ``MAX_ARCHIVE_BYTES``). Every other route, including
    ``GET /api/v1/backups/{id}/download``, keeps the 1 MiB default; the
    router-level ``MAX_ARCHIVE_BYTES`` check stays authoritative for truly
    oversized archives.
    """

    def __init__(
        self,
        app: Callable,
        max_bytes: int = MAX_BODY_BYTES,
        path_max_bytes: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_max_bytes = dict(path_max_bytes or {})

    def _limit_for(self, scope: dict) -> int:
        method = scope.get("method", "")
        path = scope.get("path", "")
        normalized = path.rstrip("/") or "/"
        for (override_method, override_path), override_limit in self.path_max_bytes.items():
            if method == override_method and normalized == (override_path.rstrip("/") or "/"):
                return override_limit
        return self.max_bytes

    def _too_large(self, request_id: str, limit: int) -> JSONResponse:
        return error_response(
            413,
            "REQUEST_TOO_LARGE",
            f"Request body exceeds {limit} bytes.",
            request_id,
        )

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request_id = scope.get("request_id") or contracts.new_request_id()
        limit = self._limit_for(scope)
        length: int | None = None
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == "content-length":
                try:
                    length = int(value.decode("latin-1"))
                except ValueError:
                    length = None
                break
        if length is not None and length > limit:
            response = self._too_large(str(request_id), limit)
            await response(scope, receive, send)
            return
        # Enforce actual bytes read even when Content-Length is present and
        # within limit, so a lying small Content-Length + large body cannot
        # bypass the cap. Both known-length and chunked bodies are buffered
        # with the same cap before routing.
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                response = self._too_large(str(request_id), limit)
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)
        return


class MaintenanceMiddleware:
    """Quiesce HTTP writes behind the S56 filesystem maintenance lock.

    When ``restore.is_maintenance_active()`` (filesystem file, independent of
    either database), mutating methods (POST/PUT/PATCH/DELETE) fail safe 503
    ``MAINTENANCE`` except the allowlist that must stay usable behind the
    lock: ``POST /api/v1/auth/login`` (health-check login before reopening),
    every ``/api/v1/restores/*`` route (validate/commit progress/reopen), and
    ``/api/v1/health`` + ``/api/v1/ready`` (liveness/readiness probes).
    Reads (GET) are never blocked so the operator can verify login/read
    during maintenance. No secrets or tracebacks leave this edge.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", "") or "")
        normalized = path.rstrip("/") or "/"
        if normalized == "/api/v1/auth/login" or normalized.startswith("/api/v1/restores"):
            await self.app(scope, receive, send)
            return
        if normalized in ("/api/v1/health", "/api/v1/ready"):
            await self.app(scope, receive, send)
            return
        try:
            from x_insight.operations import restore as restore_module

            active = restore_module.is_maintenance_active()
        except Exception:
            active = False
        if not active:
            await self.app(scope, receive, send)
            return
        request_id = str(scope.get("request_id") or contracts.new_request_id())
        response = error_response(
            503,
            "MAINTENANCE",
            "System is under maintenance. Retry shortly.",
            request_id,
            retryable=True,
        )
        await response(scope, receive, send)


class IdempotencyKeyMiddleware:
    """Enforce the ``Idempotency-Key`` format on mutating requests (S02 edge).

    Presence/requirement stays per-command (S03+); the shared format rule is
    enforced here so every future command inherits the same ``422`` contract.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") == "http" and scope.get("method") in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            raw: str | None = None
            for key, value in scope.get("headers", []):
                if key.decode("latin-1").lower() == IDEMPOTENCY_KEY_HEADER.lower():
                    raw = value.decode("latin-1")
                    break
            if raw is not None:
                try:
                    contracts.parse_idempotency_key(raw)
                except contracts.ContractError as exc:
                    request_id = str(scope.get("request_id") or contracts.new_request_id())
                    response = error_response(
                        exc.status_code,
                        exc.code,
                        exc.message,
                        request_id,
                        exc.field_errors,
                        exc.retryable,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Seed the singleton admin once, then dispose the engine on shutdown."""
    try:
        from x_insight.identity import service as identity_service

        identity_service.ensure_default_admin()
    except Exception:
        # Seeding is best-effort at startup (DB may be unavailable);
        # readiness stays the safe signal and login seeds lazily via tests.
        logger.exception("identity seeding failed at startup")
    yield
    db.reset_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="X-INSIGHT", lifespan=lifespan)

    # Starlette applies later-added middleware outermost, so correlation wraps
    # every edge rejection (size/idempotency) as well as routed responses.
    # ponytail: raw-zip staging needs MAX_ARCHIVE_BYTES at the edge; every
    # other route keeps the 1 MiB default (download is a GET with no body).
    from x_insight.operations import backup as backup_module
    from x_insight.operations import status as status_module

    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(IdempotencyKeyMiddleware)
    app.add_middleware(
        SizeLimitMiddleware,
        path_max_bytes={("POST", "/api/v1/restores/validate"): backup_module.MAX_ARCHIVE_BYTES},
    )
    app.add_middleware(RequestContextMiddleware)
    # S58 §3: outermost request gauge (counts/errors/latency per save/search/
    # poll group). Added last so edge rejections (413/422) count too; fixed
    # keys keep memory bounded.
    app.add_middleware(status_module.RequestMetricsMiddleware)

    @app.exception_handler(contracts.ContractError)
    async def contract_error_handler(
        request: Request, exc: contracts.ContractError
    ) -> JSONResponse:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            get_request_id(request),
            exc.field_errors,
            exc.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            parts = [str(p) for p in error.get("loc", ()) if p != "body"]
            field = ".".join(parts) if parts else "request"
            field_errors.setdefault(field, []).append(str(error.get("msg", "invalid")))
        return error_response(
            422,
            "VALIDATION_FAILED",
            "Request validation failed.",
            get_request_id(request),
            field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_FOR_STATUS.get(exc.status_code, "ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else code
        return error_response(
            exc.status_code,
            code,
            detail,
            get_request_id(request),
            retryable=exc.status_code in (429, 503),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id(request)
        logger.exception("unhandled error request_id=%s", request_id)
        return error_response(500, "INTERNAL_ERROR", "Internal server error.", request_id)

    from x_insight.identity.router import router as identity_router

    app.include_router(identity_router, prefix="/api/v1")

    from x_insight.cases.router import router as cases_router

    app.include_router(cases_router, prefix="/api/v1")

    from x_insight.assessments.router import router as assessments_router

    app.include_router(assessments_router, prefix="/api/v1")

    from x_insight.ddi.router import router as ddi_router

    app.include_router(ddi_router, prefix="/api/v1")

    from x_insight.models.router import router as models_router

    app.include_router(models_router, prefix="/api/v1")

    from x_insight.reasoning.router import router as reasoning_router

    app.include_router(reasoning_router, prefix="/api/v1")

    from x_insight.probability_review.router import router as review_router

    app.include_router(review_router, prefix="/api/v1")

    from x_insight.operations.router import router as operations_router

    app.include_router(operations_router, prefix="/api/v1")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "x-insight"}

    @app.get("/api/v1/ready", response_model=None)
    def ready(request: Request):
        """Readiness: 200 when the database/schema are usable, else safe 503.

        S56: while the filesystem maintenance lock is held the system is not
        ready for writes — 503 ``MAINTENANCE`` (retryable, no config
        disclosure) so the disposable drill can observe maintenance via T1.
        """
        try:
            from x_insight.operations import restore as restore_module

            if restore_module.is_maintenance_active():
                return JSONResponse(
                    status_code=503,
                    content=contracts.ErrorBody(
                        code="MAINTENANCE",
                        message="System is under maintenance. Retry shortly.",
                        field_errors={},
                        request_id=get_request_id(request),
                        retryable=True,
                    ).model_dump(),
                )
        except Exception:
            pass
        result = db.check_readiness(db.get_engine())
        if result.ok:
            return {
                "status": "ready",
                "service": "x-insight",
                # UTC serialization at its actual public use (contracts).
                "checked_at": contracts.serialize_utc(contracts.utcnow()),
                "schema_version": db.EXPECTED_SCHEMA_VERSION,
            }
        return JSONResponse(
            status_code=503,
            content=contracts.ErrorBody(
                code=result.code,
                message=result.message,
                field_errors={},
                request_id=get_request_id(request),
                retryable=result.retryable,
            ).model_dump(),
        )

    return app


app = create_app()
