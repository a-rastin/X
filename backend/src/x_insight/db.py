"""Database roles, connection lifecycle, and readiness (S02, plan.md §§4.1, 11).

Roles (least privilege; created by migration ``0001``):
- ``x_insight_app`` — ordinary runtime role: connect + read/write the
  operational tables it owns flows for, but never UPDATE/DELETE audit rows.
- ``x_insight_migrate`` — schema migration role: DDL + migration entry point.
- ``x_insight_readonly`` — inspection role: SELECT only.

The disposable local stack runs every login as the database owner, so these
grants harden deployments that connect with the named logins; the migration
still declares them so ``audit permissions are prepared`` (tasks.md S02 exit)
without a generic repository layer.

All future modules share one transaction context: :func:`session_scope`
(commit-or-rollback) and the ``get_session`` FastAPI dependency. The audit
helper takes such a session and never commits by itself (transaction-scoped).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

APP_ROLE = "x_insight_app"
MIGRATION_ROLE = "x_insight_migrate"
READONLY_ROLE = "x_insight_readonly"

# Alembic head this build is compatible with (migration ``0009``).
EXPECTED_SCHEMA_VERSION = "0009"

DEFAULT_DATABASE_URL = "postgresql+psycopg://x_insight@localhost:5432/x_insight"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://x_insight@localhost:5433/x_insight_test"


def normalize_url(url: str) -> str:
    """Accept ``postgresql://``/``postgres://`` and pin the psycopg driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def get_database_url() -> str:
    """Runtime database URL (``DATABASE_URL`` env, else disposable-local)."""
    return normalize_url(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))


def get_test_database_url() -> str:
    """Isolated test database URL (``TEST_DATABASE_URL`` env, else default)."""
    return normalize_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


def build_engine(url: str) -> Engine:
    """Create an engine with fail-fast connects and dead-connection checks."""
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 3},
    )


_engine: Engine | None = None


def get_engine() -> Engine:
    """Process-wide runtime engine, created lazily (import never connects)."""
    global _engine
    if _engine is None:
        _engine = build_engine(get_database_url())
    return _engine


def reset_engine() -> None:
    """Dispose and forget the process engine (tests and app shutdown).

    No-op when the engine was never created, so import/shutdown is safe
    without a prior :func:`get_engine` call (lazy creation is preserved).
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """One shared transaction context: commit on success, rollback on error."""
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding the shared transaction-scoped session."""
    with session_scope() as session:
        yield session


def get_applied_versions(connection: Connection) -> list[str]:
    """Alembic versions recorded in ``alembic_version`` ([] when absent)."""
    try:
        rows = connection.execute(text("SELECT version_num FROM alembic_version"))
    except Exception:
        return []
    return sorted(row[0] for row in rows)


@dataclass(frozen=True)
class Readiness:
    ok: bool
    code: str
    message: str
    retryable: bool


def check_readiness(engine: Engine) -> Readiness:
    """Database/schema readiness without leaking secrets or tracebacks.

    Unreachable database → ``DB_UNAVAILABLE`` (retryable); reachable but
    missing/foreign schema version → ``SCHEMA_INCOMPATIBLE``. Only the safe
    code/message leave this function; the raw error is never rendered.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            versions = get_applied_versions(connection)
    except Exception:
        return Readiness(
            ok=False,
            code="DB_UNAVAILABLE",
            message="Database is unavailable. Retry shortly.",
            retryable=True,
        )
    if versions != [EXPECTED_SCHEMA_VERSION]:
        return Readiness(
            ok=False,
            code="SCHEMA_INCOMPATIBLE",
            message="Database schema is incompatible. Contact the administrator.",
            retryable=False,
        )
    return Readiness(ok=True, code="READY", message="Ready.", retryable=False)
