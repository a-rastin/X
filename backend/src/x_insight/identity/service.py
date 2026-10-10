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
from typing import Any

from sqlalchemy import func, insert, select, update
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


# --- S04 physician account administration (plan.md §§2.1, 4.3; FR-03–04) ---
#
# Admin-only commands over the stored account row (role comes from the row,
# never the login-selected role). All persistence uses the caller's
# transaction; audit is recorded by the router in the same transaction.
# Passwords are verbatim (no trimming); usernames are normalized
# (strip + lowercase) for lookup/uniqueness with a stable UUID actor ID
# across renames. Safe shapes never include hashes/tokens (router enforces).

PHYSICIAN_ROLE = "physician"


def safe_physician(user: dict) -> dict:
    """Safe account shape for admin physician routes (never secrets)."""
    return {
        "id": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "active": bool(user.get("active", False)),
        "theme": user.get("theme", "light"),
        "revision": int(user.get("revision", 1)),
    }


def validate_new_username(username: str) -> str:
    """Normalize + validate a physician username (non-empty, bounded)."""
    normalized = normalize_username(username)
    if not normalized:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Username must not be empty.",
            {"username": ["Must not be empty."]},
        )
    if len(username.strip()) > 150:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Username is too long.",
            {"username": ["Must be at most 150 characters."]},
        )
    return normalized


def validate_new_password(password: str) -> str:
    """Validate a physician password verbatim (non-empty, bounded)."""
    if password == "":
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Password must not be empty.",
            {"password": ["Must not be empty."]},
        )
    if len(password) > 500:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Password is too long.",
            {"password": ["Must be at most 500 characters."]},
        )
    return password


def create_physician(
    session: Session, *, username: str, password: str, now: datetime | None = None
) -> dict:
    """Create one active physician account (admin-only caller checks role).

    Raises 409 CONFLICT on duplicate username (normalized). Only the
    physician role is provisioned here; admin creation is denied.
    """
    normalized = validate_new_username(username)
    validate_new_password(password)
    if get_user_by_username(session, normalized) is not None:
        raise contracts.ContractError(
            409,
            "CONFLICT",
            "Username already exists.",
            {"username": ["Already exists."]},
        )
    moment = now or contracts.utcnow()
    new_id = uuid.uuid4()
    session.execute(
        insert(tables.users).values(
            id=new_id,
            username=normalized,
            role=PHYSICIAN_ROLE,
            active=True,
            password_hash=passwords.hash_password(password),
            credential_revision=1,
            theme="light",
            created_at=moment,
            updated_at=moment,
            revision=1,
        )
    )
    created = get_user_by_id(session, new_id)
    assert created is not None
    return created


def list_physicians(
    session: Session, *, limit: int | None, offset: int | None
) -> tuple[list[dict], int]:
    """Admin-only list with bounded pagination (default 25, max 100).

    Stable ordering by username then id. Returns (items, total).
    """
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    total = int(session.execute(select(func.count()).select_from(tables.users)).scalar_one())
    rows = (
        session.execute(
            select(tables.users)
            .order_by(tables.users.c.username, tables.users.c.id)
            .limit(resolved_limit)
            .offset(resolved_offset)
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows], total


def patch_physician(
    session: Session,
    target_id: uuid.UUID,
    *,
    username: str | None = None,
    password: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Edit physician credentials (admin-only caller checks role).

    - At least one of username/password is required (router enforces).
    - Admin username is immutable: renaming an admin row is 422.
    - Rename keeps the stable UUID; duplicates are 409.
    - Password reset is verbatim, bumps credential_revision and revokes
      all sessions immediately (same path as own-password revocation).
    - Every edit bumps users.revision (If-Match/ETag contract; the router
      enforces stale 412 when a precondition is supplied).
    """
    target = get_user_by_id(session, target_id)
    if target is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Physician not found.")
    values: dict = {}
    moment = now or contracts.utcnow()
    if username is not None:
        normalized = validate_new_username(username)
        if target.get("role") == "admin" and normalized != target.get("username"):
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Admin username cannot be changed.",
                {"username": ["Admin username is immutable."]},
            )
        if normalized != target.get("username"):
            existing = get_user_by_username(session, normalized)
            if existing is not None and existing["id"] != target["id"]:
                raise contracts.ContractError(
                    409,
                    "CONFLICT",
                    "Username already exists.",
                    {"username": ["Already exists."]},
                )
            values["username"] = normalized
    password_changed = False
    if password is not None:
        validate_new_password(password)
        values["password_hash"] = passwords.hash_password(password)
        values["credential_revision"] = int(target["credential_revision"]) + 1
        password_changed = True
    values["revision"] = int(target["revision"]) + 1
    values["updated_at"] = moment
    session.execute(update(tables.users).where(tables.users.c.id == target["id"]).values(**values))
    if password_changed:
        revoke_all_user_sessions(session, target["id"], now=moment)
    updated = get_user_by_id(session, target["id"])
    assert updated is not None
    return updated


# --- S04 Slice 2/3 + S51 deactivation-to-Cases integration (plan §§2.1, 4.3) ---
#
# S51 replaces the empty-set contract with the author's real open-draft set:
# retain keeps drafts occupying the slot (author inactive, others 409) and
# cancels/fences queued jobs; discard requires the exact reviewed revision
# (409 on change), discards all open drafts (releasing slots, cancelling jobs,
# auditing), then deactivates. Reactivation never resurrects discarded drafts
# nor old sessions. Minimal identifiers for confirmation never grant draft
# viewing.

EMPTY_DRAFT_SET_REVISION = 0

DRAFT_ACTIONS = ("retain", "discard")


def validate_draft_action(action: str | None) -> str:
    if action not in DRAFT_ACTIONS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "draft_action must be 'retain' or 'discard'.",
            {"draft_action": ["Must be 'retain' or 'discard'."]},
        )
    return action


def list_open_drafts_for_author(session: Session, author_id: Any) -> list[dict]:
    """Author's open drafts (lifecycle draft) ordered stably, no clinical body."""
    from x_insight.cases import tables as cases_tables

    try:
        import uuid as _uuid

        want = _uuid.UUID(str(author_id))
    except Exception:
        return []
    rows = (
        session.execute(
            select(cases_tables.encounters)
            .where(
                cases_tables.encounters.c.author_id == want,
                cases_tables.encounters.c.lifecycle == "draft",
            )
            .order_by(cases_tables.encounters.c.created_at, cases_tables.encounters.c.id)
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def compute_draft_set_revision(open_drafts: list[dict]) -> int:
    """Stable int revision for the open-draft set (0 when empty).

    Covers count + IDs + revisions hash (sorted); any create/discard/sign or
    content-revision bump changes it, so a changing set invalidates the
    discard confirmation. Empty stays 0 for S04 backward compat.
    """
    if not open_drafts:
        return EMPTY_DRAFT_SET_REVISION
    ordered = sorted(
        [{"id": str(d.get("id")), "revision": int(d.get("revision", 1))} for d in open_drafts],
        key=lambda e: str(e["id"]),
    )
    digest = contracts.canonical_hash({"drafts": ordered})
    try:
        value = int(digest[:8], 16)
    except Exception:
        value = 1
    return value if value != 0 else 1


def draft_set_identifiers(open_drafts: list[dict]) -> list[dict]:
    """Minimal identifiers for discard confirmation (no clinical content)."""
    ordered = sorted(open_drafts, key=lambda d: str(d.get("id")))
    return [
        {
            "encounter_id": str(d.get("id")),
            "patient_id": str(d.get("patient_id")),
            "revision": int(d.get("revision", 1)),
        }
        for d in ordered
    ]


def validate_draft_set_revision(provided: int | None, current: int | None = None) -> int:
    """Draft-set confirmation: provided must equal current (409 on change).

    Absent means the caller reviewed the empty set (0). S51 discard calls
    this with the live current; retain skips it (non-destructive, returns
    current for discovery). Changing the set (create/discard/sign/revision)
    invalidates the confirmation.
    """
    want = EMPTY_DRAFT_SET_REVISION if current is None else int(current)
    got = EMPTY_DRAFT_SET_REVISION if provided is None else int(provided)
    if int(got) != int(want):
        raise contracts.ContractError(
            409,
            "DRAFT_SET_CHANGED",
            "The draft set changed. Review and reconfirm.",
            {"draft_set_revision": ["Reviewed set is stale."]},
        )
    return got


def deactivate_physician(
    session: Session,
    target_id: uuid.UUID,
    *,
    draft_action: str,
    draft_set_revision: int | None = None,
    now: datetime | None = None,
) -> tuple[dict, bool, int, list[dict]]:
    """Deactivate (active=false + revoke all sessions immediately).

    S51 integration: locks the user row FOR UPDATE (serializes sign vs
    deactivation), computes the live open-draft set, enforces the discard
    confirmation (retain skips validation and returns current for discovery;
    discard requires exact match, 409 on change), cancels/fences queued jobs,
    discards (lifecycle draft->discarded, slot release, audit) on confirmed
    discard, then deactivates. Returns (user, changed, revision, identifiers).
    Already-inactive is an idempotent no-op (changed=False, no bump).
    Admin accounts cannot be deactivated. Credential revision bumps so old
    sessions never resurrect across reactivation.
    """
    validate_draft_action(draft_action)
    # Lock the user row first (serializes concurrent sign/deactivation).
    locked = (
        session.execute(
            select(tables.users).where(tables.users.c.id == target_id).with_for_update()
        )
        .mappings()
        .first()
    )
    target = dict(locked) if locked is not None else get_user_by_id(session, target_id)
    if target is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Physician not found.")
    if target.get("role") == "admin":
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Admin account cannot be deactivated.",
            {"role": ["Admin account must stay active."]},
        )
    open_drafts = list_open_drafts_for_author(session, target["id"])
    current_revision = compute_draft_set_revision(open_drafts)
    reviewed = draft_set_identifiers(open_drafts)
    if draft_action == "discard":
        validate_draft_set_revision(draft_set_revision, current_revision)
    if not target.get("active", False):
        return target, False, int(current_revision), reviewed
    moment = now or contracts.utcnow()
    # Cancel/fence queued jobs for the author's open drafts (both classes);
    # retained drafts keep occupying the slot, discarded release it below.
    try:
        from x_insight.reasoning import queue as _queue

        for draft in open_drafts:
            try:
                _queue.cancel_active_jobs_for_encounter(session, draft["id"], now=moment)
            except Exception:
                pass
            # Local jobs share the same tables with a different class; fence
            # them explicitly (claim/commit already fences author inactive).
            try:
                session.execute(
                    update(_queue.reasoning_tables.reasoning_jobs)
                    .where(
                        _queue.reasoning_tables.reasoning_jobs.c.batch_id.in_(
                            select(_queue.reasoning_tables.generation_batches.c.id).where(
                                _queue.reasoning_tables.generation_batches.c.encounter_id
                                == draft["id"]
                            )
                        ),
                        _queue.reasoning_tables.reasoning_jobs.c.status.in_(["queued", "leased"]),
                    )
                    .values(lease_token=None, status="cancelled", updated_at=moment)
                )
            except Exception:
                pass
    except Exception:
        pass
    if draft_action == "discard" and open_drafts:
        from x_insight.cases import tables as cases_tables
        from x_insight.operations import audit as _audit

        for draft in open_drafts:
            session.execute(
                update(cases_tables.encounters)
                .where(cases_tables.encounters.c.id == draft["id"])
                .values(
                    lifecycle="discarded",
                    revision=int(draft.get("revision", 1)) + 1,
                    updated_at=moment,
                )
            )
            _audit.record_audit(
                session,
                operation="encounters.discard.success",
                actor="admin",
                request_id=str(target_id),
                details={
                    "encounter_id": str(draft.get("id")),
                    "patient_id": str(draft.get("patient_id")),
                    "reason": "deactivation_discard",
                },
            )
        session.flush()
    session.execute(
        update(tables.users)
        .where(tables.users.c.id == target["id"])
        .values(
            active=False,
            revision=int(target["revision"]) + 1,
            credential_revision=int(target["credential_revision"]) + 1,
            updated_at=moment,
        )
    )
    revoke_all_user_sessions(session, target["id"], now=moment)
    updated = get_user_by_id(session, target["id"])
    assert updated is not None
    return updated, True, int(current_revision), reviewed


def reactivate_physician(
    session: Session, target_id: uuid.UUID, now: datetime | None = None
) -> tuple[dict, bool]:
    """Reactivate (active=true). Idempotent no-op when already active.

    Never resurrects discarded drafts (none exist yet) and never revives old
    sessions: deactivation already revoked + bumped credential_revision, so
    the author must log in again. Retained drafts (none yet) become
    resumable by the author after this call (S51 fills the Cases side).
    """
    target = get_user_by_id(session, target_id)
    if target is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Physician not found.")
    if target.get("active", False):
        return target, False
    moment = now or contracts.utcnow()
    session.execute(
        update(tables.users)
        .where(tables.users.c.id == target["id"])
        .values(
            active=True,
            revision=int(target["revision"]) + 1,
            updated_at=moment,
        )
    )
    updated = get_user_by_id(session, target["id"])
    assert updated is not None
    return updated, True


# --- S04 Slice 4: per-command idempotency (plan.md §4.3) ---
#
# Same key + same body replays the original result without re-executing the
# mutation (and without duplicating audit rows); same key + changed body is
# 409. Keys are scoped per (operation, actor) so two admins never collide.
# Request hashes cover the target + body; responses store only safe bodies.


def idempotency_request_hash(body: dict, target_id: uuid.UUID | None = None) -> str:
    envelope: dict = {"body": body}
    if target_id is not None:
        envelope["target_id"] = str(target_id)
    return contracts.canonical_hash(envelope)


def lookup_idempotency(
    session: Session, *, operation: str, actor_id: uuid.UUID, key: str
) -> dict | None:
    row = (
        session.execute(
            select(tables.idempotency_records).where(
                tables.idempotency_records.c.operation == operation,
                tables.idempotency_records.c.actor_id == actor_id,
                tables.idempotency_records.c.idempotency_key == key,
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def store_idempotency(
    session: Session,
    *,
    operation: str,
    actor_id: uuid.UUID,
    key: str,
    request_hash: str,
    response_status: int,
    response_body: dict,
    now: datetime | None = None,
) -> None:
    moment = now or contracts.utcnow()
    session.execute(
        insert(tables.idempotency_records).values(
            id=uuid.uuid4(),
            operation=operation,
            actor_id=actor_id,
            idempotency_key=key,
            request_hash=request_hash,
            response_status=response_status,
            response_body=response_body,
            created_at=moment,
        )
    )
    session.flush()
