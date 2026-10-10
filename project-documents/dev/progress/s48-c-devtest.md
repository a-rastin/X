# S48c-devtest — Complete CPT review, comparison, and acceptance tests (2026-10-10)

TESTS ONLY slice of tasks.md S48c §§1-4 (FR-50-57/58, NFR-03/06; seams T1/T9).
Backend acceptance + frontend CptReviewPanel reused read-only; no
`backend/src`, `web/src`, `content/`, or migration changes. Tests use planned
public seams only (T1 HTTP via real PostgreSQL + real routes, T9 browser via
real auth/draft + controlled batch fixtures à la S48 seedStoredProposal/
s48Package; no worker harness — completed/failed/stale use route mocks as the
documented alternative; no DB-row asserts, no internal mocks, no private-helper
mirror tests).

## Scope and synthetic basis

- §1 (T9): every completed question exposes all CPTs incl. roots + grouped
  tables, original/current exact percentages, row totals/differences,
  direct/redistributed highlights; outputs read-only; single-state 100% lock
  explained; keyboard sliders + readable labels in both themes; search X-of-Y +
  folding keep large tables reachable; honest empty (no baseline) has no
  sliders and no mutation POSTs.
- §2 (T9): original + latest-successful outputs/recommendations side-by-side
  even when wording unchanged; unchanged/recalculating/successfully_
  recalculated/failed + separate out-of-date; earlier revisions labeled with
  revision ids.
- §3 (T9): slider autosave responsive during calculation; stale 412 shows
  reload without overwrite; refresh/resume restores committed values/state;
  reset/retry affect one panel + retain history; obsolete fencing via
  If-Match/sequence (412 path proven; backend fences older responses).
- §4 (T1+T9): author-only exact current baseline/revision/hash/result/input
  incl. unchanged originals (backend); e2e proves accept-enabled vs blocked,
  forged 409 via browser fetch, retry isolates one panel, edit invalidates but
  retains history; sign UI consumes explicit references (S49 owns signing).
- Synthetic: two-node A→B (80/20, 90/10, 30/70) + single-state S 100%;
  adjusted A 60/40 gives P(B=yes)=0.34; mocks carry 64-char hashes, fixed
  UUIDs, `fp-mock-1` fingerprints; sentinel note-leakage asserts in payloads +
  DOM. Real pending batch (T1, no worker) proves mount-restore + honest empty
  + stranger 403.

Depends DONE: S48, S48a, S48b, S48c-backend (acceptance endpoint + 0015),
S48c-frontend (CptReviewPanel + api.ts + testids). NOT modified: backend/
frontend implementation, S48d freshness/regeneration, S49 signing,
`test_only_s02_tables_exist` drift.

## Implementation (tests only)

Extended (`backend/tests/http/test_probability_acceptance.py`, +2 → 13 T1):

- `test_invalid_acceptance_bodies_rejected_422_without_write` — malformed
  baseline/result/revision UUIDs, empty/blank hashes, extra `note` field
  (extra=forbid), missing `result_id` → 422, no acceptance row.
- `test_if_match_header_mismatch_is_412_and_matching_header_accepts` —
  correct body + wrong `If-Match` → 412; wrong body + correct header → 412;
  matching header + body → 200.

New (`e2e/probability-review.spec.ts`, 8 T9 journeys):

1. Real pending run: honest empty CPT, 0 sliders, 0 mutation POSTs, stranger
   batch GET 403 without leak.
2. Completed CPT reachability: roots/totals/diffs/badges, read-only outputs,
   single-state, search X-of-Y + clear, fold, sentinel noninterference.
3. Keyboard + themes: toggle via Enter, slider label/focus/outline, ArrowRight
   → Unsaved → POST (node/state/target/revision) → Saved + recalculating with
   sliders live; dark theme readable, restored to light.
4. Comparison: unchanged (original-is-current) vs successfully_recalculated
   with wording-unchanged note + revision/result ids, no false reuse label.
5. States: recalculating (sliders live, accept blocked, earlier revision) +
   failed + stale out-of-date ◍ with retry + accept blocked.
6. Save/resume: first edit 412 → reload offered, originals intact; second edit
   Saved + recalculating; reload restores 60/40 + recalculating.
7. Reset isolation: q1 reset → 80 + 2 revisions + verified reuse of baseline;
   q2 stays 60/40 + 1 revision, no reuse leak.
8. Accept/forged/retry/invalidate: forged browser fetch 409, exact accept →
   Accepted + ids + 1 acceptance; retry q2 → recalculating with revision-3
   body, q1 stays Accepted; q1 slider edit → recalculating + Not accepted
   with history retained + S49 references visible.

## What was checked

```sh
# backend (TEST_DATABASE_URL from .env)
cd backend && set -a; . ../.env; set +a
uv run pytest tests/http/test_probability_acceptance.py -q
# 13 passed (~43s)
uv run pytest tests/http/test_probability_acceptance.py tests/probability_review tests/http/test_probability_review.py -q
# 34 passed (13 new + 11 T5 + 10 T1)
uv run pytest tests/worker/test_snapshots.py tests/worker/test_queue.py -q
# 23 passed
uv run pytest tests/worker/test_single_question.py tests/worker/test_workflows.py tests/worker/test_recovery.py -q
# 28 passed
uv run pytest tests/worker/test_local_calculation.py -q
# 13 passed
uv run pytest tests/models -q
# 208 passed
uv run pytest tests/mcp/test_scoped_transport.py tests/provider/test_estimation.py -q
# 24 passed
uv run pytest tests/http --deselect tests/http/test_contracts.py::test_only_s02_tables_exist -q
# 270 passed, 1 deselected
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy src
# 117 files formatted, all checks passed, 60 source files no issues

# web
cd web && npx tsc --noEmit  # clean
npm run build  # green, 56 modules
npx playwright test e2e/probability-review.spec.ts --project=chromium  # 8 passed
npx playwright test e2e/probability-review.spec.ts --project=firefox   # 8 passed
npx playwright test e2e/proposal-review.spec.ts --project=chromium     # 11 passed
npx playwright test e2e/proposal-review.spec.ts --project=firefox      # 11 passed
```

Full `tests/http -q` still shows only the pre-existing
`test_only_s02_tables_exist` failure (expects S02–S24 tables, head 0015 adds
reasoning/cpt/calculation/acceptance tables); left untouched.

## What works

- T1 denial matrix holds: wrong author/admin 403 without leak, 404 stays 404,
  pending/recalculating/failed/no-baseline 409, stale 409, each forged
  reference 409 naming the field, stale pointer + If-Match mismatch 412,
  malformed bodies 422, idempotent replay identical + changed-body 409 +
  keyless dedupe, edit/reset invalidate with history, note-only preserves.
- T9 full reachability holds: honest empty, exact CPTs/totals/diffs/badges,
  read-only outputs, single-state, search/fold, keyboard + labels in both
  themes, side-by-side comparison with unchanged-wording note, four states +
  separate stale + earlier labels, responsive autosave + 412 reload + resume,
  one-panel reset/retry with history, exact accept + forged 409 + retry
  isolation + edit invalidation with S49 references.

## Remaining blockers

- `test_only_s02_tables_exist` needs its expected table list updated for
  0009–0015 in a separate maintenance session; untouched here.
- Large-table virtualization/pagination beyond search/fold not exercised;
  mocks use 4-row tables on the same grouping/search path (ceiling noted).
- `canonicalHash` assumes `crypto.subtle` (localhost HTTPS-equivalent fine);
  non-secure-context fallback still unexplained (frontend handoff item).
- S48d (affected-only regeneration/freshness) and S49 (signing consumes these
  acceptance references) untouched.

## Handoff / next session

Reuse `backend/tests/http/test_probability_acceptance.py` (`s48a_package()`,
`_new_physician_batch`/`_succeed_batch`/`_accept_body`, 80/20 + 90/10 + 30/70
literals, T=100_000_000, review_revision 1 +1 per adjustment/reset) and
`e2e/probability-review.spec.ts` (mockOriginal/AdjustedTables, mockRevision/
CalcResult/Review/Batch builders, cpt-*/proposal-* testids, sentinel pattern).
Defaults unchanged (debounce 700ms, recalc poll 2.5s, sliders 0–100 step 0.01,
If-Match + fresh Idempotency-Key per attempt). Next is **S48d**
(regenerate only affected questions; reuse freshness/stale + regeneration
harness here), then **S49** (signing enforcement consumes the acceptance ids
exposed in test 8).

## Follow-up — 2026-10-10 (S48c code review, docs only)

S48c build is complete across its three slices; this follow-up records the
code-review outcome and the session roll-up. No `backend/`, `web/`,
`content/`, migration, or test changes were made here.

Roll-up (slice handoffs above are the record; preserved, not rewritten):
[S48c-backend](s48-c-backend.md) landed the exact current-result acceptance
endpoint + migration 0015 (11 T1, combined 32, http 268 passed /
1 deselected); [S48c-frontend](s48-c-frontend.md) landed the CPT
review/comparison/acceptance UI (tsc/build green, proposal-review 11+11,
themes 14); this report added +2 T1 (→ 13, malformed 422 bodies and
If-Match 412/200) and the 8-journey e2e/probability-review.spec.ts ×
chromium/firefox. Final S48c tallies: worker 23+28+13, models 208,
mcp+provider 24, http 270 passed / 1 deselected (pre-existing
`test_only_s02_tables_exist` drift), ruff/mypy/tsc/build green,
proposal-review 11+11 no regression.

What works (consolidated): T1 denial matrix (author-only 403/404; 409
pending/recalculating/failed/no-baseline/stale/forged-reference; 412 stale
pointer + If-Match mismatch; 422 malformed; idempotent replay/dedupe;
edit/reset invalidation with history; note-only preserves) + T9
reachability (exact CPTs/totals/diffs/badges, read-only outputs,
single-state lock, search/fold, keyboard + both themes), comparison
(original vs latest-successful, unchanged-wording note, earlier labels),
failed/retry/reset/resume, forged-acceptance 409, and S49 acceptance
references (id, baseline/revision/CPT hash, result kind+id, input +
projection hashes, actor/time).

Code review — non-blocking, recorded as follow-ups (code untouched).
Standards: 0 hard violations, judgement smells only — AcceptanceRefs Data
Clumps; 5x REFERENCE_MISMATCH Repeated Switches; `_live_acceptance_point`
Feature Envy; api.ts post* Duplicated Code + canonicalHash mirror of
`_accept_body` (shape pinned by the forged-acceptance journeys; dedupe only
if it diverges); CptReviewPanel.tsx 1033-line Divergent Change (split only
when a panel concern next changes, not preemptively). Spec, 3 partials —
obsolete-response seq-vs-id compare (412 path proven in journey 6; any
id-based fencing extension belongs to S48d); earlier-results
single-displayed-only (comparison shows latest-successful vs original;
confirm in S49 whether sign UI needs more); mocked resume/
unchanged-original + no regeneration test (resume uses route mocks without
a worker; regeneration-triggered invalidation belongs to S48d's freshness
suite). Scope notes, out of S48c — projection_hash/result_kind are already
exposed per acceptance for S49 signing to consume; client-side
canonicalHash stays a preview (server recomputes the live point).
Looks-wrong nits, intended behavior kept — direct/redistributed badges
reflect the latest revision's `direct_edit` (rows edited only earlier show
redistributed after 2 edits); sliders display 2-decimal steps while storage
holds 6-decimal precision (committed GET-review values authoritative).

Remaining blockers (carried forward): `test_only_s02_tables_exist` drift
(separate maintenance session); large-table virtualization ceiling beyond
search/fold (mocks use 4-row tables on the proven grouping/search path);
`crypto.subtle` non-secure-context fallback unexplained; S48d/S49 untouched.
Next is **S48d** (affected-only regeneration; reuse the freshness/stale +
regeneration harness), then **S49** (signing consumes the acceptance ids).
Content approvals remain as recorded in S39.b; bundle release and activation
retain their independent status.
