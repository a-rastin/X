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
    """Reject oversized request bodies with a safe ``413`` error body."""

    def __init__(self, app: Callable, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self, request_id: str) -> JSONResponse:
        return error_response(
            413,
            "REQUEST_TOO_LARGE",
            f"Request body exceeds {self.max_bytes} bytes.",
            request_id,
        )

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request_id = scope.get("request_id") or contracts.new_request_id()
        length: int | None = None
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == "content-length":
                try:
                    length = int(value.decode("latin-1"))
                except ValueError:
                    length = None
                break
        if length is not None and length > self.max_bytes:
            response = self._too_large(str(request_id))
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
            if total > self.max_bytes:
                response = self._too_large(str(request_id))
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
    app.add_middleware(IdempotencyKeyMiddleware)
    app.add_middleware(SizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)

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

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "x-insight"}

    @app.get("/api/v1/ready", response_model=None)
    def ready(request: Request):
        """Readiness: 200 when the database/schema are usable, else safe 503."""
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
