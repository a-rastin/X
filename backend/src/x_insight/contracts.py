"""Shared persistence and HTTP contracts (S02, plan.md §4.2–§4.3).

Actual public uses (not speculative utilities):
- ``canonical_json``/``canonical_hash`` anchor audit payload hashes in
  :mod:`x_insight.operations.audit` (never hash a presentation string).
- ``utcnow``/``serialize_utc``/``parse_utc`` stamp the readiness response and
  audit rows; all times are UTC with explicit display timezone.
- ``parse_if_match``/``format_etag`` and ``parse_idempotency_key`` are the
  revision/idempotency conventions every S03+ mutation uses
  (``If-Match``/ETag for mutable revisions, ``Idempotency-Key`` for retried
  commands: same key + same body replays the original result, same key +
  changed body is ``409``).
- ``parse_pagination`` fixes list-query bounds (default 25, maximum 100).
- ``ErrorBody``/``ContractError`` are the standard error body behind every
  HTTP failure below (no secret values, no tracebacks).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

MAX_BODY_BYTES = 1_048_576  # 1 MiB JSON/request cap at the HTTP edge.
REQUEST_ID_HEADER = "X-Request-ID"
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IF_MATCH_HEADER = "If-Match"
PAGINATION_DEFAULT_LIMIT = 25
PAGINATION_MAX_LIMIT = 100

_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class ErrorBody(BaseModel):
    """Standard error body (plan.md §4.3): safe, correlated, no traceback."""

    code: str
    message: str
    field_errors: dict[str, list[str]] = {}
    request_id: str
    retryable: bool = False


class ContractError(Exception):
    """A contract violation that already knows its safe HTTP rendering."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: dict[str, list[str]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.retryable = retryable

    def to_body(self, request_id: str) -> ErrorBody:
        return ErrorBody(
            code=self.code,
            message=self.message,
            field_errors=self.field_errors,
            request_id=request_id,
            retryable=self.retryable,
        )


def new_request_id() -> str:
    return str(uuid.uuid4())


def canonical_json(value: Any) -> bytes:
    """Canonical UTF-8 JSON per plan.md §4.2.

    Sorted object keys, compact separators, raw UTF-8 (no ``\\u`` escaping),
    semantically ordered arrays preserved, explicit nulls kept, missing keys
    untouched (never confused with null), schema version supplied by the
    caller, finite numbers only. Raises ``TypeError`` for non-JSON values.
    """
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise TypeError(f"not canonical-JSON serializable: {exc}") from exc
    return text.encode("utf-8")


def canonical_hash(value: Any) -> str:
    """SHA-256 hex over :func:`canonical_json` (immutable-artifact anchor)."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime (never naive)."""
    return datetime.now(UTC)


def serialize_utc(moment: datetime) -> str:
    """Serialize a datetime to ISO-8601 UTC with ``Z`` suffix.

    Naive datetimes are rejected: UTC must be explicit at the call site.
    """
    if moment.tzinfo is None:
        raise ValueError("naive datetime cannot be serialized as UTC")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp back to an aware UTC datetime."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return moment.astimezone(UTC)


IfMatch = Literal["*"] | int | None


def parse_if_match(value: str | None) -> int | None:
    """Parse an ``If-Match`` precondition into an expected revision.

    ``None`` (absent) or ``"*"`` means no specific revision is demanded;
    ``'"<n>"'`` demands revision ``n``. Anything else is a ``422``
    ``INVALID_IF_MATCH`` contract error (malformed preconditions are client
    content errors, not stale-revision ``412`` conflicts).
    """
    if value is None or value.strip() == "*":
        return None
    match = re.fullmatch(r'\s*"(\d+)"\s*', value)
    if match is None:
        raise ContractError(
            422,
            "INVALID_IF_MATCH",
            "If-Match must be '*' or an ETag revision like \"3\".",
            {"if_match": ["Must be '*' or a quoted positive integer revision."]},
        )
    revision = int(match.group(1))
    if revision < 1:
        raise ContractError(
            422,
            "INVALID_IF_MATCH",
            "If-Match must be '*' or an ETag revision like \"3\".",
            {"if_match": ["Must be '*' or a quoted positive integer revision."]},
        )
    return revision


def format_etag(revision: int) -> str:
    """Format a mutable-resource revision as an ETag value."""
    if revision < 1:
        raise ValueError("revision must be >= 1")
    return f'"{revision}"'


def parse_idempotency_key(value: str | None) -> str | None:
    """Validate an ``Idempotency-Key`` header value (S03+ command convention).

    Keys are opaque client-chosen strings: 1–64 ``[A-Za-z0-9_-]`` characters
    (UUIDs recommended). Absent keys pass through as ``None`` here; each
    future command route decides whether to require one. Malformed keys are a
    ``422`` ``INVALID_IDEMPOTENCY_KEY`` error mentioning the field.
    """
    if value is None:
        return None
    if _IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        raise ContractError(
            422,
            "INVALID_IDEMPOTENCY_KEY",
            "Idempotency-Key must be 1-64 [A-Za-z0-9_-] characters.",
            {"idempotency_key": ["Use 1-64 characters from A-Z a-z 0-9 _ -."]},
        )
    return value


def parse_pagination(limit: int | None, offset: int | None) -> tuple[int, int]:
    """Bound list-query pagination (default 25, maximum 100, stable order)."""
    resolved_limit = PAGINATION_DEFAULT_LIMIT if limit is None else limit
    resolved_offset = 0 if offset is None else offset
    errors: dict[str, list[str]] = {}
    if not 1 <= resolved_limit <= PAGINATION_MAX_LIMIT:
        errors["limit"] = [f"Must be 1-{PAGINATION_MAX_LIMIT}."]
    if resolved_offset < 0:
        errors["offset"] = ["Must be >= 0."]
    if errors:
        raise ContractError(422, "INVALID_PAGINATION", "Invalid pagination.", errors)
    return resolved_limit, resolved_offset
