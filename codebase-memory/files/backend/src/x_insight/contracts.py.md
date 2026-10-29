# backend/src/x_insight/contracts.py

Source SHA-256: 26ec5ee6f499ae19afdb0ad664d7548998e754e86128d4aecfeccb32e65dc773
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Shared HTTP and persistence conventions every module uses: canonical hashing, UTC
serialization, revision (ETag/`If-Match`) and idempotency parsing, pagination bounds, and
the safe error body.

## Important Symbols
- `canonical_json` / `canonical_hash` — sorted keys, compact, raw UTF-8, `allow_nan=False`;
  the SHA-256 anchor for audit hashes, snapshot hashes, DDI content hashes and idempotency
  request hashes. Never hash a presentation string.
- `ErrorBody` / `ContractError` — a contract violation already knows its HTTP rendering
  (`status_code`, `code`, `message`, `field_errors`, `retryable`).
- `parse_if_match` — `*` or `"<n>"`; anything else is **422 `INVALID_IF_MATCH`**, not 412.
- `parse_idempotency_key` — 1–64 chars of `[A-Za-z0-9_-]`.
- `MAX_BODY_BYTES` (1 MiB), pagination default 25 / max 100.

## Behavior and Constraints
- All times are UTC (`utcnow`/`serialize_utc`/`parse_utc`); `serialize_utc` rejects naive
  datetimes, so a naive value fails loudly instead of shifting silently.
- Idempotency semantics live with each command route: same key + same body replays the
  original result, same key + changed body is 409.

## Related Files
- `backend/src/x_insight/operations/audit.py` — the main `canonical_hash` consumer.
- `backend/src/x_insight/app.py` — enforces these rules at the edge.
