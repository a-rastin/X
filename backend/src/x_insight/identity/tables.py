"""Identity storage: users + opaque sessions (S03, plan.md §4.1).

Users: UUID, normalized unique username, immutable admin identity (enforced
in service/routes — no username-mutation route exists), role admin/physician,
active, password hash, credential revision, theme; plus revision/updated_at
for profile concurrency. Sessions: UUID, user FK, hashed opaque token
(unique), CSRF token, credential-revision snapshot, created/revoked times.
No timeout column: sessions never expire (NFR-02); revocation is explicit
(logout, credential change) via ``revoked_at`` + revision mismatch.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("username", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("password_hash", Text, nullable=False),
    Column("credential_revision", Integer, nullable=False, default=1),
    Column("theme", Text, nullable=False, default="light"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    CheckConstraint("role IN ('admin', 'physician')", name="ck_users_role"),
    CheckConstraint("theme IN ('light', 'dark')", name="ck_users_theme"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column(
        "user_id", PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("token_hash", Text, nullable=False),
    Column("csrf_token", Text, nullable=False),
    Column("credential_revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

Index("ix_users_username", users.c.username, unique=True)
Index("one_admin_only", users.c.role, unique=True, postgresql_where=(users.c.role == "admin"))
Index("ix_sessions_token_hash", sessions.c.token_hash, unique=True)
Index("ix_sessions_user_id", sessions.c.user_id)
