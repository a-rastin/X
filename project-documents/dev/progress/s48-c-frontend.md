# S48c-frontend — Complete CPT review, comparison, and acceptance UI (2026-10-10)

Frontend-only slice of tasks.md S48c §§1–3 + §4 frontend half (FR-50–57,
FR-58, NFR-03, NFR-06; seams T1/T9). Backend is reused as-is: the S48c-backend
step's `POST /question-runs/{id}/acceptance` plus the extended
`GET /question-runs/{id}/review` (acceptance/is_accepted/acceptances +
revision/result/freshness fields) were read-only inputs here; no `backend/`,
`content/`, or migration changes were made by this session. (The worktree
still carries the backend step's uncommitted 0015 files; they were preserved
untouched. The local dev database was moved `0014` → `0015` via
`alembic upgrade head` — a no-code operation — because GET review 500'd
without the `probability_acceptances` table during browser verification.)

## Scope and synthetic basis

- Per completed question, one CPT panel with every root/conditional row:
  original/current exact percentages (decimal strings, 6-decimal storage
  precision), row totals, signed point-differences, direct/redistributed
  badges; outputs read-only; single-state rows locked at 100% with the
  constraint explained. Native keyboard sliders (arrow keys, global
  focus-visible rule, accessible labels, exact numeric readout); search +
  per-node folding keep large tables fully reachable with X-of-Y counts and
  a clear action (rows never silently omitted). Readable in both themes via
  existing semantic tokens only.
- Original vs latest-successful outputs/recommendations side by side even
  when wording is unchanged; unchanged/recalculating/successfully_
  recalculated/failed plus a separate out-of-date (stale) indicator, each
  text + glyph (never color alone); earlier successes carry an
  earlier-revision label with revision ids.
- Completed slider edits autosave debounced (700ms, serialized in browser
  order) via POST cpt-adjustments with If-Match + Idempotency-Key; sliders
  stay enabled while recalculating (acceptance/sign blocked); refresh/resume
  restores from GET review (committed values are durable server-side, no
  extra localStorage); reset/retry affect one panel and retain history;
  obsolete browser responses ignored by monotonic fetch/post sequence
  fencing; recalculating polls every 2.5s, paused in hidden tabs.
- Acceptance UI (§4 frontend half): per-question Accept POSTs exact
  references from GET review (api `buildAcceptanceBody`, the
  `_accept_body(review)` mirror — unchanged originals hash the baseline
  tables via platform SHA-256 over canonical JSON; adjusted revisions reuse
  the live cpt_hash/result id); blocked with reason on
  recalculating/failed/stale/mismatch; accepted state + history shown;
  edits/reset invalidate visibly via re-GET. Sign UI consumes the exposed
  acceptance references; enforcement itself stays S49.

Depends DONE: S48 (panel shell/testids/storage), S48a/S48b (adjustment +
local-calc semantics), S48c-backend (acceptance endpoint + review fields).
NOT modified: backend/, content/, migrations, e2e/ (no new spec claimed).

Synthetic scope explicit: no worker runs in e2e; completed/failed/stale
journeys with real calculations belong to dev-test's
e2e/probability-review.spec.ts (not claimed here). This session proves
mount/build/lazy-fetch/honest-empty via the existing S48 suite plus a
temporary mount smoke (run, then deleted).

## Implementation (frontend only)

Extended (`web/src/features/reasoning/api.ts`):

- `QuestionReviewPayload` gains the S48a–c fields: `input_freshness`,
  `original/current_tables`, `current_cpt_revision_id`,
  `displayed_result_revision_id`, `current_result_matches`,
  `calculation_state`, `calculation/displayed_result(s)`,
  `local_job(s)`, `review_revision`, `revisions`, `cpt_hash`,
  `outputs_read_only`, `acceptance/is_accepted/acceptances`
  (`CptRevision`/`CalculationResult`/`ProbabilityAcceptance` mirror the
  `safe_*` serializers; no notes/names/IDs/phone path exists).
- `postCptAdjustment` / `postCptReset` / `postRetryCalculation` /
  `postAcceptance` (If-Match + fresh Idempotency-Key each) plus
  `canonicalJson`/`canonicalHash`, `buildAcceptanceBody`,
  `acceptBlockedReason` (stale → missing baseline → recalculating →
  failed, mirroring server order).

New (`web/src/features/reasoning/CptReviewPanel.tsx`, ~700 lines, one
component per run):

- Own lazy toggle (`cpt-toggle-<key>`) + own GET review, so S48
  transparency fetch counts are untouched; root `cpt-panel-<key>`,
  `cpt-accept/reset/retry-<key>` testids for dev-test.
- Sliders `input[type=range]` 0–100 step 0.01 with verbatim original/current
  readouts, integer-unit diffs/totals (no float drift), direct vs
  redistributed badges from the latest revision's `direct_edit`.
- Comparison grid (original baseline vs latest successful result with
  earlier-revision / verified-reuse labels + unchanged-wording note);
  per-panel reset/retry/accept with 412-reload and 409-reason surfacing;
  revision + acceptance history in `<details>`.

Modified (`web/src/` only):

- `features/reasoning/ProposalReviewPanel.tsx` (+import, +`onSessionExpired`
  pass-through, +`<CptReviewPanel>` mount per question block; header
  selector contract documents the `cpt-*` testids).
- `shared/theme.css` (+52 `.xi-cpt-*`: panel/node/slider/badge/compare —
  semantic tokens only, both deliberate themes, global focus rule covers
  sliders; compare grid collapses to one column under 720px).

No second data paths: every read/mutation goes through the same-origin
`/api/v1` typed client to the existing routes; nothing is recomputed from
the chart and no draft/chart/note field is rendered.

## What was checked

```sh
# web
npx tsc --noEmit
# clean
npm run build
# green, 56 modules (was 55)
npx playwright test e2e/proposal-review.spec.ts --project=chromium
# 11 passed (26.9s) — no S48 regression; cpt-panel roots mount in every block
npx playwright test e2e/proposal-review.spec.ts --project=firefox
# 11 passed (32.6s)
npx playwright test e2e/themes.spec.ts --project=chromium --project=firefox
# 14 passed (6.6s) — theme.css addition regresses nothing
# temporary mount smoke (deleted after the run, not a claimed suite):
npx playwright test e2e/cpt-mount.smoke.spec.ts --project=chromium
# 1 passed — toggle mounts with 0 review GETs, keyboard Enter expands,
# 1 lazy GET, honest "No adjustable baseline", 0 mutation POSTs
npx playwright test e2e/cpt-mount.smoke.spec.ts --project=firefox
# 1 passed
```

Totals: 22 proposal-review + 14 themes + 2 mount-smoke = 38 browser
checks green; tsc + build green. No backend suite was run (backend
untouched); local dev DB migrated 0014 → 0015 to match the already-landed
backend contract (GET review 500'd before that — environment fix, not a
code finding).

## What works

- Every question block carries a CPT review toggle; expansion lazy-GETs the
  persisted review only (0 fetches before, 1 after); no-baseline runs show
  the honest empty state with no sliders and no POSTs.
- With a baseline, all rows render grouped by node/parent-states with exact
  strings, totals, diffs, badges; single-state rows explain the 100% lock;
  search reports X-of-Y with clear; nodes fold without omission.
- Comparison shows original vs latest-successful posteriors/sections with
  earlier-revision and verified-reuse labels; recalculating keeps sliders
  live while acceptance is blocked; stale shows the separate out-of-date
  panel; failed shows retry/reset with acceptance blocked.
- Accept POSTs the exact `_accept_body` shape with If-Match + key; accepted
  state exposes id/actor/time plus baseline/revision/result/input
  references for S49; history lists revisions and acceptances; edits/reset
  lapse acceptance on re-GET.

## Remaining blockers

- Full S48c journeys (real completed adjustment → recalculation → accept,
  forged-acceptance 409s, edit-invalidates, reset-then-reaccept, keyboard
  slider commit on a completed table, large-table reachability with real
  baselines, dark-theme CPT read-through) belong to dev-test's
  e2e/probability-review.spec.ts — not run or claimed here.
- `canonicalHash` assumes `crypto.subtle` (localhost HTTPS-equivalent is
  fine); non-secure-context fallback is unexplained — dev-test should note
  if their harness serves over plain HTTP on a non-localhost host.
- Pre-existing `test_only_s02_tables_exist` drift (now 0015 tables) is still
  the backend step's known item; untouched here.
- S48d (freshness/regeneration) and S49 (signing consumes these acceptance
  references) untouched.

## Handoff / next session

Reuse `web/src/features/reasoning/api.ts` (`getQuestionReview`,
`postCptAdjustment/postCptReset/postRetryCalculation/postAcceptance`,
`buildAcceptanceBody`, `acceptBlockedReason`) and the `cpt-panel/toggle/
accept/reset/retry-<key>` testids in `CptReviewPanel.tsx` (header documents
the contract). Defaults unchanged (debounce 700ms, recalc poll 2.5s,
sliders 0–100 step 0.01, If-Match = `review_revision` + fresh key per
attempt). Dev-test (T1/T9) owns e2e/probability-review.spec.ts: seed via
`s48Package()` + `seedStoredProposal`, complete runs through the real
worker or controlled route mocks, then assert sliders/commit, comparison,
failed/retry/reset/resume, forged-acceptance, and edit-invalidates. Next
is **dev-test full S48c e2e**, then **S48d**, then **S49**.

## Follow-up — 2026-10-10 (S48c code review, docs only)

No code changes here. The S48c completion roll-up and the non-blocking
code-review follow-ups (Standards smells, Spec partials, scope notes, nits)
are recorded in [S48c-devtest](s48-c-devtest.md). Next is S48d, then S49;
content approvals remain as recorded in S39.b.
