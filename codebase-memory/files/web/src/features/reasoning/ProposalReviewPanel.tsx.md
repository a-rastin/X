# web/src/features/reasoning/ProposalReviewPanel.tsx

Source SHA-256: efcad2f33336f358d5d4242353be290518cefa2c61025c0bf89504205b9dccf0
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

Hash recomputed 2026-11-02 after the Issue 19 change (1818 lines; `index.json`
agrees).

## Purpose
Step 7 UI: starts or reuses a generation batch, polls per-question run states, renders
transparency and freshness, and exposes retry/refresh. The largest frontend file (~1818 lines).

## Important Symbols
- Batch caching via the `xi.proposal.<encounterId>` local key holding
  `{batchId, packages, sourceRevision}` — documented as an optimization only; server discovery
  still works without it.
- Polling intervals: roughly every 2 s while visible, 15 s while hidden.
- Feeds `CptReviewPanel.tsx` and `ProposalReadinessSummary.tsx`.

## Behavior and Constraints
- Unresolved readiness is shown with links back to the History controls rather than blocking the
  page silently.
- The chart's honest-unavailable path renders `generation_not_implemented` verbatim instead of a
  placeholder proposal.
- Lazy transparency calls the per-run review endpoint independently of batch polling
  (`loadReview`, issue 19): one request bounded by a native `AbortController` at
  `REVIEW_TIMEOUT_MS` (1500 ms, including the body read), a per-run sequence guard so a late
  response cannot overwrite a newer retry, and aborts on unmount. The expand/collapse toggle
  (`toggleTransparency`) only decides whether to call it — the error's retry button calls the
  loader directly, and a `loading` cache entry is never reused. A successful null review renders
  `No transparency data because this run failed` only when the joined per-question display is
  `failed` (never `review.job`, which is the batch's first job); other cases keep the neutral
  "available after this question completes successfully" text. See `issues/issue-19.md`.
- All request/response shapes live in `features/reasoning/api.ts` (~762 lines); this component
  owns presentation and polling only. Issue 19 gave that file's shared request helper and
  `getQuestionReview` an optional `AbortSignal`, used only by transparency — other callers pass
  nothing and keep their previous behaviour.

## Related Files
- `web/src/features/reasoning/api.ts`, `CptReviewPanel.tsx`.
- `backend/src/x_insight/reasoning/router.py` — the batch/readiness/review endpoints.
- `e2e/proposal-review.spec.ts`, `e2e/question-freshness.spec.ts`.
