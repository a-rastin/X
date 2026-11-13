# backend/src/x_insight/cases/signing.py

Source SHA-256: 0078f90669182340549be2b2ea46f3728cb043fb02c32377e2d0d22d375f37df

## Purpose
Final-plan persistence, atomic signing with an immutable snapshot, and append-only addenda.
`SNAPSHOT_SCHEMA_VERSION = "signed-encounter-v3"` (docs still say v2 — source wins).

## Important Symbols
- `save_secondary_plan()` — own `If-Match` fence on `secondary_plans.revision`, **not** the
  encounter revision; plan text never enters the analysis fingerprint.
- `sign_encounter()` — one transaction: lock encounter + patient + author, then check
  already-signed (409), stale encounter/plan/**review** revisions (412), empty plan text
  (409 `PLAN_NOT_READY`), a complete proposal, and acceptances covering **exactly** the ready
  runs. Freshness is recomputed inside the locked transaction via per-question hashes —
  never from client flags.
- Frozen payload + `snapshot_hash = canonical_hash(snapshot)`; `UNIQUE(encounter_id)` yields
  one immutable snapshot per encounter (concurrent sign → 409 `ALREADY_SIGNED`).
- `create_addendum()` — **any active physician** may append; the encounter row is never
  bumped, so the snapshot stays byte-identical.
- `established_clozapine_eligible()` — follow-up only; only a prior *signed* plan or addendum
  saying `clozapine_included == "yes"` qualifies. Missing evidence stays `unknown`.

## Behavior and Constraints
- Signing checks four revision namespaces: encounter, secondary plan, review pointer, patient
  (`STALE_INPUTS` 409 on relevant-demographics drift — currently age/sex/clinical_status only).
- Failed initial generation has no manual-plan signing bypass.
- `MAX_SECONDARY_PLAN_CHARS = 10_000`, `MAX_ADDENDUM_CHARS = 2000`.

## Related Files
- `backend/src/x_insight/probability_review/service.py` — the acceptance state signing reads.
- `backend/src/x_insight/cases/chart.py` — read-only shared chart from signed snapshots.

## Versioned signing boundary
`sign_encounter` remains the legacy v3 acceptance path. New case v4 and follow-up
v5 confirmation live in `new_case_plans` and `follow_up_plans`. Both reuse
secondary-plan storage and safe snapshot serialization; follow-up references are
nullable alongside legacy and New case references. Follow-up final-text saves
and any-physician addenda acquire the patient lock before the encounter lock.
Addenda preserve the original snapshot bytes/hash.
