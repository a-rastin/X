"""Minimal FastAPI application for S01 bootstrap.

Exposes a single public health check at ``GET /api/v1/health``.
No database, no clinical content, no authentication yet (S02+).
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="X-INSIGHT")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "x-insight"}

    return app


app = create_app()
