# X-INSIGHT progress tracker

Start here for recorded progress, then read the relevant session report and its
handoff, deferred items, and blockers. Reports describe their dated scope;
older next-session and status statements are historical, not current instructions.
The latest recorded checkpoint is [S30](s30.md): lai_indication_choice draft green
(T5: 5 focused / 138 model tests; `make check` green). Content remains `awaiting_review`;
see the report for release blockers and the S31 handoff.

## Reading and updating progress

- Use [../tasks.md](../tasks.md) for planned sessions and
  [../plan.md](../plan.md) for implementation policy.
- Each session or bounded checkpoint has its own file: `s00.md`, `s16-a.md`,
  `s16-b.md`, and so on. Read prerequisite reports only as needed.
- Record a new session in `sNN.md` (or `sNN-a.md` / `sNN-b.md` for split work)
  and add its link below in session order. Update the latest checkpoint above.
- Preserve existing history. Append dated follow-ups to the relevant report;
  keep this index short rather than adding implementation logs here.
- Record cross-session content Q/A in [content-decisions.md](content-decisions.md).
  Track approval status in [the content review ledger](../../../content/review-ledger.md)
  and rationale in [the context index](../../../context/index.md).
- Bare `plan.md`, `tasks.md`, and other development-document names in reports
  refer to the parent `project-documents/dev/` directory. Source/code paths
  refer to the repository root; commands retain their stated working directory.
- The former monolithic tracker has been replaced by this directory.

## Session reports

| Session | Report |
| --- | --- |
| S00 | [Establish execution and content review ledger (2026-10-04)](s00.md) |
| S01 | [Bootstrap a reproducible development loop (2026-10-04)](s01.md) |
| S02 | [Establish real persistence and request contracts (2026-10-04)](s02.md) |
| S03 | [Implement login, sessions, and own credentials (2026-10-04)](s03.md) |
| S04 | [Implement physician account administration (2026-10-04)](s04.md) |
| S05 | [Build role navigation and both themes (2026-10-04)](s05.md) |
| Content Q/A decisions | [2026-10-04](content-decisions.md) |
| S06 | [Register and find a patient (2026-10-04)](s06.md) |
| S07 | [Persist, resume, and discard author-owned drafts (2026-10-04)](s07.md) |
| S08 | [Define and release experimental assessments and implement the evaluation interface (2026-10-04)](s08.md) |
| S09 | [Implement diagnosis, threshold warning, and bypass (2026-10-04)](s09.md) |
| S10 | [Implement PANSS without implicit minimum answers (2026-10-04)](s10.md) |
| S11 | [Implement C-SSRS form and distinct results (2026-10-04)](s11.md) |
| S12 | [Draft and implement structured history and adverse effects (2026-10-05)](s12.md) |
| S13 | [Add attributed page notes and prove separation (2026-10-05)](s13.md) |
| S14 | [Build shared chart and follow-up draft entry (2026-10-05)](s14.md) |
| S15 | [Parse one source through the ingestion interface (2026-10-05)](s15.md) |
| S16.a | [First bounded cut across real source formats (2026-10-06)](s16-a.md) |
| S16.b | [Bullet splitting → uncounted/cite/hybrid policies → per-file residual triage (2026-10-06)](s16-b.md) |
| S17 | [Controlled medication concepts and aliases](s17.md) |
| S18 | [Build, review, and publish an immutable DDI release (2026-10-06)](s18.md) |
| S19 | [Implement deterministic coverage-aware DDI checking (2026-10-06)](s19.md) |
| S20 | [Integrate medications and DDI into encounter history (2026-10-06)](s20.md) |
| S21 | [Safely import and inspect XMLBIF drafts (2026-10-06)](s21.md) |
| S22 | [Enforce model semantics and admission limits (2026-10-06)](s22.md) |
| S23.a | [Validate every CPT and build effective artifact (S23 checkpoint) (2026-10-06)](s23-a.md) |
| S23.b | [Bounded child-process exact inference (S23 inference checkpoint) (2026-10-06)](s23-b.md) |
| S24 | [Model version administration + read-only graph (2026-10-06)](s24.md) |
| S25 | [Define the reusable question-package contract and review harness (2026-10-06)](s25.md) |
| S27 | [Build and verify the pharmacotherapy review draft (2026-10-09)](s27.md) |
| S29 | [Build and verify the high_suicide_clozapine review draft (2026-10-09)](s29.md) |
| S30 | [Build and verify the lai_indication_choice review draft (2026-10-09)](s30.md) |
