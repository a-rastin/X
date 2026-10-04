"""Identity commands: seeding, authentication, sessions (S03).

All persistence goes through the caller's :func:`x_insight.db.session_scope`
transaction; audit is recorded with :func:`x_insight.operations.audit.record_audit`
in the same transaction (no credentials in payloads). Passwords are never
trimmed. Privileges always come from the stored account row, never the
login-selected role.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.identity import passwords, tables

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"

SESSION_COOKIE_NAME = "x_insight_session"
CSRF_HEADER_NAME = "X-CSRF-Token"

RESEARCH_WARNING = (
    "This application is a research prototype and must not be used as the sole "
    "basis for treating patients."
)

# Login throttling (prototype-basic, plan.md §11): at most 5 failures per
# 60s window per (client IP, normalized username); the 6th+ attempt is 429.
# In-memory by design for a single-host prototype (<10 physicians); tests
# reset via clear_login_throttle() and advance time via contracts.utcnow.
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60

_login_failures: dict[tuple[str, str], list[datetime]] = {}


def normalize_username(username: str) -> str:
    """Normalize for lookup/uniqueness: strip + lowercase (passwords never)."""
    return username.strip().lower()


def hash_session_token(raw_token: str) -> str:
    """SHA-256 hex of the opaque cookie value (only the hash is stored)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def clear_login_throttle() -> None:
    """Reset in-memory throttling (test isolation only)."""
    _login_failures.clear()


def _throttle_key(client_ip: str, username: str) -> tuple[str, str]:
    return (client_ip, normalize_username(username))


def is_login_throttled(client_ip: str, username: str, now: datetime | None = None) -> bool:
    moment = now or contracts.utcnow()
    failures = _login_failures.get(_throttle_key(client_ip, username), [])
    cutoff = moment.timestamp() - LOGIN_WINDOW_SECONDS
    recent = [t for t in failures if t.timestamp() > cutoff]
    return len(recent) >= MAX_LOGIN_ATTEMPTS


def record_login_failure(client_ip: str, username: str, now: datetime | None = None) -> None:
    moment = now or contracts.utcnow()
    key = _throttle_key(client_ip, username)
    failures = _login_failures.get(key, [])
    cutoff = moment.timestamp() - LOGIN_WINDOW_SECONDS
    failures = [t for t in failures if t.timestamp() > cutoff]
    failures.append(moment)
    _login_failures[key] = failures


def clear_login_failures(client_ip: str, username: str) -> None:
    _login_failures.pop(_throttle_key(client_ip, username), None)


def get_user_by_username(session: Session, username: str) -> dict | None:
    row = (
        session.execute(
            select(tables.users).where(tables.users.c.username == normalize_username(username))
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_user_by_id(session: Session, user_id: uuid.UUID) -> dict | None:
    row = (
        session.execute(select(tables.users).where(tables.users.c.id == user_id)).mappings().first()
    )
    return dict(row) if row is not None else None


def ensure_default_admin(engine=None) -> None:
    """Seed exactly one admin/admin once; second init preserves password.

    Inserts ``admin`` only when no ``role='admin'`` row exists (the partial
    unique index ``one_admin_only`` also blocks a second admin at the DB).
    Existing admins are untouched, so a changed password is never recreated
    at the next startup. Uses :func:`x_insight.db.session_scope`.
    """
    from x_insight import db as db_module

    with db_module.session_scope(engine) as session:
        existing = session.execute(
            select(tables.users.c.id).where(tables.users.c.role == "admin")
        ).first()
        if existing is not None:
            return
        now = contracts.utcnow()
        session.execute(
            insert(tables.users).values(
                id=uuid.uuid4(),
                username=DEFAULT_ADMIN_USERNAME,
                role="admin",
                active=True,
                password_hash=passwords.hash_password(DEFAULT_ADMIN_PASSWORD),
                credential_revision=1,
                theme="light",
                created_at=now,
                updated_at=now,
                revision=1,
            )
        )


def create_session(session: Session, user: dict, now: datetime | None = None) -> tuple[str, str]:
    """Create an opaque session; returns (raw_token, csrf_token) for cookies."""
    moment = now or contracts.utcnow()
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session.execute(
        insert(tables.sessions).values(
            id=uuid.uuid4(),
            user_id=user["id"],
            token_hash=hash_session_token(raw_token),
            csrf_token=csrf_token,
            credential_revision=user["credential_revision"],
            created_at=moment,
            revoked_at=None,
        )
    )
    return raw_token, csrf_token


def get_session_user(session: Session, raw_token: str | None) -> dict | None:
    """Validate cookie session: hash lookup, not revoked, active, revision.

    No timeout check: advancing any clock never expires a session (plan.md
    §2.1/NFR-02). Returns the account row (privileges source) or None.
    """
    if not raw_token:
        return None
    token_hash = hash_session_token(raw_token)
    sess = (
        session.execute(
            select(tables.sessions).where(
                tables.sessions.c.token_hash == token_hash,
                tables.sessions.c.revoked_at.is_(None),
            )
        )
        .mappings()
        .first()
    )
    if sess is None:
        return None
    user_row = (
        session.execute(select(tables.users).where(tables.users.c.id == sess["user_id"]))
        .mappings()
        .first()
    )
    if user_row is None:
        return None
    user = dict(user_row)
    if not user.get("active", False):
        return None
    if sess["credential_revision"] != user.get("credential_revision"):
        return None
    return user


def get_session_row(session: Session, raw_token: str) -> dict | None:
    row = (
        session.execute(
            select(tables.sessions).where(
                tables.sessions.c.token_hash == hash_session_token(raw_token)
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def revoke_session(session: Session, raw_token: str, now: datetime | None = None) -> None:
    moment = now or contracts.utcnow()
    session.execute(
        update(tables.sessions)
        .where(
            tables.sessions.c.token_hash == hash_session_token(raw_token),
            tables.sessions.c.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )


def revoke_all_user_sessions(
    session: Session, user_id: uuid.UUID, now: datetime | None = None
) -> None:
    moment = now or contracts.utcnow()
    session.execute(
        update(tables.sessions)
        .where(
            tables.sessions.c.user_id == user_id,
            tables.sessions.c.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )
