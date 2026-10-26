"""Real settings API fixture; only external provider HTTP is deterministic.

Use a dedicated empty PostgreSQL database via DATABASE_URL. The runner refuses
nonempty provider settings, migrates it, provisions its managed key, and serves
normal auth/settings routes. No test-only application route is introduced.
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from x_insight import db
from x_insight.reasoning.provider_key import initialize_key
from provider_tls_fixture import public_provider_https


def main():
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL must name a dedicated empty browser-test database")
    if os.environ.get("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", "").strip():
        raise SystemExit("Public onboarding fixture requires an empty/unset host allowlist")
    backend = Path(__file__).resolve().parents[1] / "backend"
    os.chdir(backend)
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db.get_database_url())
    command.upgrade(cfg, "head")
    with db.get_engine().connect() as connection:
        if connection.execute(text("SELECT count(*) FROM provider_settings")).scalar_one():
            raise SystemExit("Fresh onboarding fixture refuses nonempty provider settings")
    initialize_key(db.get_engine())
    from x_insight.app import app
    with public_provider_https() as requests:
        uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PROVIDER_E2E_API_PORT", "18083")), access_log=False)
    print(f"External TLS fixture verified {len(requests)} requests: {requests}", flush=True)


if __name__ == "__main__":
    main()
