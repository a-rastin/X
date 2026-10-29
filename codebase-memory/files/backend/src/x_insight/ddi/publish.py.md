# backend/src/x_insight/ddi/publish.py

Source SHA-256: 66739f9776531a01c5ed2603e3f14481d875d6a1490e6ec154dd4c73689a8f20
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Publishes an immutable, content-addressed DDI release from owner-reviewed staging. Nothing
reaches runtime lookup without passing these gates.

## Important Symbols
- Gate helpers raising `PublishRejected`: reviewer must be `owner` with decision
  `approved_complete` / `approved_limited` — **`awaiting_review` is never imported**; coverage
  scope must match the decision; parser/terminology versions and checksums must match;
  high-risk assertions need matching reviewed evidence; corrections must reference a real source
  span.
- `compute_content_hash()` — canonical hash over `{schema: "ddi-release/1", versions, concepts,
  documents (sorted), manifest core}`.
- `publish_release()` — `version = f"ddi-{content_hash[:12]}"`, an advisory transaction lock,
  idempotent reuse when that hash already exists, one transaction. Status is derived from the
  decision.
- A `ddi-ddinter-stream/1` candidate is delegated to `ddinter.publish_ddinter`.

## Behavior and Constraints
- Releases are content-addressed and immutable: identical content reuses the existing release
  rather than creating a second one.
- `bootstrap_release` (in `bootstrap.py`) rebuilds staging from committed CSVs, re-derives the
  hash locally and refuses when the rebuilt candidate differs, so no generated artifact has to
  be committed and Git LFS is unnecessary.

## Related Files
- `backend/src/x_insight/ddi/bootstrap.py`, `ddinter.py`, `checker.py`.
- `docs/ddinter-operations.md` — the owner build/review/publish/rollback runbook.
