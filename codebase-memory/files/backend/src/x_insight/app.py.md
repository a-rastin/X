# backend/src/x_insight/app.py

Source SHA-256: 3a39265cd2ed91bcf13d717522a5aa8bfc061f8ddeeaf1002deafb688abbbffc
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
FastAPI application factory and HTTP edge: correlation IDs, size limits, idempotency-key
format, maintenance fencing, and the single standard error body.

## Important Symbols
- `create_app()` — builds the app and mounts every router at `/api/v1` (identity, cases,
  assessments, ddi, models, reasoning, provider_config, probability_review, operations).
- `RequestContextMiddleware` — validates `X-Request-ID` against an RFC 7230 token
  (1–128 chars) and regenerates it otherwise; echoes it on every response.
- `SizeLimitMiddleware` — 1 MiB default cap, buffered even when `Content-Length` lies;
  `path_max_bytes` raises the cap only for `POST /api/v1/restores/validate`.
- `MaintenanceMiddleware` — 503 `MAINTENANCE` for all writes except login, `/restores/*`,
  health and ready, while the restore filesystem lock is held.
- `GET /api/v1/health` (no DB) and `GET /api/v1/ready` (DB + schema check, safe 503).

## Behavior and Constraints
- Middleware is added so correlation wraps every edge rejection: Starlette applies the
  last-added middleware outermost, so `RequestMetricsMiddleware` counts 413/422 too.
- No secrets or tracebacks ever leave this layer; unknown failures render
  `500 INTERNAL_ERROR` with only the request id.
- Lifespan seeds the singleton admin best-effort and disposes the engine on shutdown.

## Related Files
- `backend/src/x_insight/contracts.py` — `ErrorBody`, `ContractError`, header constants.
- `backend/src/x_insight/db.py` — readiness check and engine lifecycle.
- `backend/src/x_insight/operations/restore.py` — owns `is_maintenance_active()`.
