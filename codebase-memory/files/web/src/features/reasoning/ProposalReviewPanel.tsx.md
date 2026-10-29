# web/src/features/reasoning/ProposalReviewPanel.tsx

Source SHA-256: 4a4f2702e864d96e28c4be82c139cb618fca35d0ee61f7ffbcecc36b06fd0067
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Step 7 UI: starts or reuses a generation batch, polls per-question run states, renders
transparency and freshness, and exposes retry/refresh. The largest frontend file (~1729 lines).

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
- All request/response shapes live in `features/reasoning/api.ts` (~753 lines); this component
  owns presentation and polling only.

## Related Files
- `web/src/features/reasoning/api.ts`, `CptReviewPanel.tsx`.
- `backend/src/x_insight/reasoning/router.py` — the batch/readiness/review endpoints.
- `e2e/proposal-review.spec.ts`, `e2e/question-freshness.spec.ts`.
