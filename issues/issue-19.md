# Issue 19 — Failed-run transparency and bounded loading

Date: 2026-11-01 (analysis); updated 2026-11-02 (implementation and verification)  
Status: **Implemented and verified in Chromium and Firefox against a production preview build on this host. Deployed acceptance — the tester's incident cause, request evidence and running version — remains unverified.**

## Problem and scope

A tester reports that generation failed, the Status page said “No active alerts,” and transparency stayed at “Loading persisted transparency…”. The actionable acceptance in this issue is the transparency panel: a failed run with no stored transparency must show **“No transparency data because this run failed” within 2 seconds**, and a blocked review endpoint must produce an error with a working retry instead of an indefinite loader. The “How it should be” sentence repeating the loading text conflicts with the explicit steps and acceptance; this report follows the latter.

The incident's particular generation failure, request outcome and deployed version are unknown. The following source analysis identifies defects capable of producing the symptom; it does **not** prove which occurred in that incident.

## Root cause and pre-fix behavior

The four items below describe the code as analysed on 2026-11-01, before the fix. Items 1–3 are addressed by [the implementation](#implementation-2026-11-02); item 4 is a constraint the implementation respects.

1. Opening a question's transparency calls `getQuestionReview(runId)`, issuing `GET /api/v1/question-runs/{run_id}/review` ([panel](../web/src/features/reasoning/ProposalReviewPanel.tsx#L848), [API](../web/src/features/reasoning/api.ts#L459)). The panel sets a per-run loading entry and waits for the request. The fetch **and response body read** have no deadline or abort signal ([api.ts](../web/src/features/reasoning/api.ts#L373)); a connection or body that never settles therefore leaves the loading state indefinitely. Closing and reopening also reuses that loading cache entry ([panel](../web/src/features/reasoning/ProposalReviewPanel.tsx#L850)). A hanging request is a code-supported explanation, not an observed incident trace.
2. A failed original run normally has no persisted successful baseline. The author-scoped review endpoint returns HTTP 200 with `baseline: null`, `transparency: null`, and `adjustable: false`, not a recomputed result ([router](../backend/src/x_insight/reasoning/router.py#L397); [worker test](../backend/tests/worker/test_single_question.py#L427)). On a timely response the UI already exits loading, but shows a generic future-completion explanation instead of explicitly saying the run failed ([panel](../web/src/features/reasoning/ProposalReviewPanel.tsx#L876)). Thus failure itself does not automatically cause a permanent spinner.
3. Settled request failures already show an error and retry control, but the retry callback uses the expand/collapse toggle. Since the panel is already open, the first click closes it rather than issuing another request ([callback](../web/src/features/reasoning/ProposalReviewPanel.tsx#L1106), [toggle](../web/src/features/reasoning/ProposalReviewPanel.tsx#L848)). A cached null result can also remain unavailable if the same run later gets a successful baseline, because non-error entries are reused.
4. A question's generation failure is derived from its matching batch job and can include attempt count and safe error code; its generation retry is distinct from transparency retry ([panel](../web/src/features/reasoning/ProposalReviewPanel.tsx#L179), [retry](../web/src/features/reasoning/ProposalReviewPanel.tsx#L1269)). Do not infer per-question failure from the review endpoint's `job`: that compatibility field refers to the batch's first job, which may be for another question ([router](../backend/src/x_insight/reasoning/router.py#L454)).

### Why Status can report no alerts

Status is a separate operational snapshot, not a list of all failed generation jobs. Its existing conditions are old queued work, stale heartbeats, repeated provider authentication failures, and high disk use ([status.py](../backend/src/x_insight/operations/status.py#L285)); other failures may produce zero alerts, rendered as “No active alerts” ([StatusPage](../web/src/features/admin/status/StatusPage.tsx#L233)). This explains the possible mismatch, not the particular incident. The stated acceptance does not define a new alert threshold, scope or clearing policy. Do not invent one in this fix; treat broader Status alerting as a separate product decision. See [issue 18](issue-18.md) for existing failure-audit visibility and its verification limits.

## Implementation (2026-11-02)

Two frontend files changed; nothing backend, no new dependency. The existing lazy persisted-review GET is kept — opening transparency still performs exactly one request, and nothing is recomputed locally.

1. **`web/src/features/reasoning/api.ts`** — the shared reasoning request options gained an optional `signal?: AbortSignal`, forwarded into `fetch`, and `getQuestionReview(runId, options?: { signal?: AbortSignal })` accepts it ([api.ts](../web/src/features/reasoning/api.ts#L380), [getQuestionReview](../web/src/features/reasoning/api.ts#L464)). Every other caller (CPT review, freshness, signing) is unchanged and passes no signal.
2. **`web/src/features/reasoning/ProposalReviewPanel.tsx`** — a new `loadReview(runId)` performs one bounded persisted-review GET per call ([panel](../web/src/features/reasoning/ProposalReviewPanel.tsx#L899)):
   - a native `AbortController` plus a `REVIEW_TIMEOUT_MS = 1500` deadline ([constant](../web/src/features/reasoning/ProposalReviewPanel.tsx#L155)); the signal covers the body read as well as the connection, so neither can hang; the timer is cleared in `finally` on every outcome;
   - a per-run controller map and a monotonic sequence guard, so an unmount or a newer retry aborts the older request and a late or aborted response can never overwrite newer state ([guard](../web/src/features/reasoning/ProposalReviewPanel.tsx#L911));
   - the expand/collapse toggle (`toggleTransparency`) now only decides whether to load; the error panel's **Retry transparency** is bound to the loader directly ([binding](../web/src/features/reasoning/ProposalReviewPanel.tsx#L1191)), so one click refetches in place instead of re-toggling and closing the panel;
   - a timeout renders `Transparency did not load in time — check the connection and retry.`; other HTTP/network/body failures keep `Transparency failed to load. Check the connection and retry.`; HTTP 401 still routes to session expiry.
3. **Cache policy** ([toggle](../web/src/features/reasoning/ProposalReviewPanel.tsx#L966)) — completed successful results stay cached; a cached null is refetched once the run has a baseline, so a later success is shown; `loading` and `error` entries are never reused on reopen.
4. **Failed-run empty state** ([render](../web/src/features/reasoning/ProposalReviewPanel.tsx#L1397)) — a successful null review renders exactly `No transparency data because this run failed` only when the joined per-question `display === "failed"` (from `classify()`/`joinRuns()`). It is never derived from `review.job`, which is the batch's *first* job and may belong to another question. Pending, skipped and clarification questions keep the neutral `Result details will be available after this question completes successfully.`; a request failure is never presented as an empty result — it stays a `role="alert"` error with a retry.
5. **Tests** (below) drive all of this with mocked `page.route` responses; no worker runs, no backend change.

### Deliberate trade-off: the 1500 ms deadline

A short deadline bounds the failure mode the acceptance cares about — an indefinite loader — but it also rejects requests that are slow yet healthy: a review that would have answered at 1600 ms is reported as an error. That is accepted knowingly: the alternative is the original unbounded wait, and recovery is one click on **Retry transparency**, which issues a fresh bounded request without collapsing the panel. The value was chosen to leave rendering margin under the 2-second acceptance, **not** from a measurement of real endpoint latency; it should be revisited only with measured evidence from a real deployment (for example the p95 of `GET /question-runs/{id}/review`), not tuned on mocked fixtures. During verification one Firefox repeat showed exactly this behaviour: the mocked response took longer than 1500 ms to be delivered, the panel showed the bounded-timeout error with a working retry, and nothing was relaxed.

## Tests added or updated

`e2e/proposal-review.spec.ts` (shared `issue19Batch` / `issue19FailedJob` / `issue19Review` fixtures, real auth and draft via the API helpers, review GET mocked per case):

- `Issue 19 unresolved review GET times out and one retry click recovers in place`
- `Issue 19 failed review request retries in one click and recovers when answered`
- `Issue 19 null review stays neutral for unfinished questions and clears when the run gains a baseline`
- `Issue 19 a late answer from the superseded request does not overwrite the retried result`

`e2e/failure-recovery.spec.ts`:

- `Issue 19 failed run states no transparency data within 2s of opening the panel` — the 2-second bound, measured in-page.

Updated, not new: `S48 §3 transparency shows five fields, distinct CPT/posterior, exact saved inputs` had its neutral-text assertion changed to the failure-specific text for the failed question. Mocked browser checks prove UI and request behaviour only — never a live worker, real endpoint or deployment.

## Verification performed

| Check | Command / harness | Result |
| --- | --- | --- |
| Web build | `cd web && npm run build` | pass |
| Types | repo root `npx tsc --noEmit` | pass (exit 0) |
| Chromium, Issue 19 set | `npx playwright test --project=chromium -g "Issue 19\|transparency shows five fields" --workers=1` against `vite preview` (production build on 5173, backend :8000, docker DB) | **6 passed, 37.4 s** |
| Firefox, same set | same command, `--project=firefox` | **6 passed, 1.6 m** in a group run |

The 2-second bound is asserted **inside the page**: a capture-phase click listener records `openedAt` and an in-page watcher (mutation batch plus painted frame, whichever sees the text first) records `appearedAt`, both from `performance.now()`. Observed in-page latency: **Chromium 20–71 ms over 8 runs; Firefox 30–929 ms over 12 runs.** An earlier 2931 ms figure came from the previous Node-side `Date.now()` measurement against the unminified Vite **dev** server, which charged harness round-trip latency to the UI.

Host and harness constraints actually encountered:

- The host has ~955 MB RAM with ~70–250 MB free during runs and an active OOM killer. A later Firefox group run had three tests fail **only** by exceeding Playwright's 30 s default test timeout (teardown or early steps, no assertion failure); each passed when run individually or with a larger budget. Nothing in the application, the 1500 ms bound or the mocks was relaxed.
- Firefox launches only with `HOME=/tmp/opencode/fxhome PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright` — otherwise it refuses to start because `$HOME` is owned by another user.
- The stack needed `DB_PORT=5442 TEST_DB_PORT=5443 make dev`, because host PostgreSQL already holds 5432.

## Not verified

- The tester's specific incident cause, request evidence and deployed version are still unknown; no production database, deployment or live provider was inspected.
- Only the focused specs above were run, not the full e2e suite.
- The 1500 ms value has not been tuned against measured real-endpoint latency (see the trade-off above).
- Deployed acceptance still needs a separate check of the running version with authorized non-sensitive test data.

## Change boundary (as actually touched)

| Area | What was actually changed |
| --- | --- |
| `web/src/features/reasoning/ProposalReviewPanel.tsx` | Transparency loading/retry (bounded request, sequence guard, abort on unmount), failed-versus-neutral null rendering, cache invalidation. Question generation retry, sign-off gating, persisted data and existing accessible status/error roles are preserved. |
| `web/src/features/reasoning/api.ts` | Optional request abort signal and optional signal argument for `getQuestionReview`; other callers unchanged. |
| `e2e/proposal-review.spec.ts` | Issue 19 fixtures and the four regressions listed above. |
| `e2e/failure-recovery.spec.ts` | The Issue 19 two-second timing test, measured in-page. |
| Documentation | This report, the S48 progress handoff and its index line. |

Everything is committed on `dev` as `f69cf28` (`fix(reasoning): bound transparency request and state failed runs`). **Do not change (unchanged from the analysis):** backend review contracts or database schema, worker/generation retry budgets, provider behavior, audit or Status rules, clinical logic, permissions, unrelated UI, or add dependencies. Do not use `review.job` as the failed question's job, expose raw provider data, or conflate a timeout with a successful empty response. The backend `null`-result worker test remains the contract evidence for the endpoint; the endpoint was not modified. If an acceptance test reveals a separate required change, identify the evidence before widening the boundary.
