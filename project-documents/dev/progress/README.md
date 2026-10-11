# X-INSIGHT progress tracker

Start here for recorded progress, then read the relevant session report and its
handoff, deferred items, and blockers. Reports describe their dated scope;
older next-session and status statements are historical, not current instructions.
The latest recorded checkpoint is [S59](s59.md): S59 complete —
verify complete registration + follow-up with CPT review (synthetic
`s59-test-v1` packages; 5-question registration 4-ready+1-skip with
S08 diagnosis/PANSS + synthetic meds/DDI + sequential generation +
adjust/compare + acceptance + sign + snapshots + report; follow-up
new encounter preserving priors; affected-only regen + fencing;
new `BT/http/test_complete_workflows.py` 3 T1/T8 +
`e2e/clinical-workflows.spec.ts` 9/9 × chromium+firefox;
`BT/http` 332 + 1 deselected, `BT/models` 212, mcp+provider 24,
worker 78 + 1 suite-order flake exonerated, probability_review 11,
`web/src` unchanged with tsc clean + build green 71 modules; code
review 0 hard + judgement nits + synthetic-only partials recorded
as explicit misses M1–M6, nothing S59-blocking; new test files
uncommitted, this report is the committed record). Next is S60.
Prior slice [S58](s58.md) below.
Predecessor [S58](s58.md): S58 complete — synthetic capacity
measurement + safe operational status (10k load + admission +
admin Status page/endpoint; misses M1–M6; artifacts uncommitted,
report is the committed record). Next was S59. Prior slices remain
[S57](s57.md) (one-host deployment packaging + upgrade drill),
[S56](s56.md) (commit restore with maintenance/fencing/rollback;
history replaceable by DB-owner/restore — NOT tamper-proof;
regressions hold except `test_only_s02_tables_exist` drift,
identity pager gap, and `ddi_*` suite-order workaround),
[S55](s55.md)
(validate/stage restores), [S54](s54.md)
(consistent full backups), [S53](s53.md)
(exports/reports), [S52](s52.md)
(append-only audit/administration views), [S51](s51.md)
(archive/deactivation/races), [S50](s50.md)
(final-plan/sign/addenda UI), [S49](s49.md) (atomic signing
backend), [S48d](s48-d.md) (per-question freshness + frontend +
dev-test + code review), [S48c](s48-c-devtest.md) (build + code
review), [S48c-frontend](s48-c-frontend.md) (UI) and
[S48c-backend](s48-c-backend.md) (endpoint, migration 0015).
Content approvals remain as recorded in S39.b; bundle release
and activation retain their independent status.

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
| S31 | [Build and verify the aggression_clozapine review draft (2026-10-09)](s31.md) |
| S32 | [Build and verify the established_case_clozapine review draft (2026-10-09)](s32.md) |
| S33 | [Build and verify the tardive_dyskinesia review draft (2026-10-09)](s33.md) |
| S34 | [Build and verify the akathisia review draft (2026-10-09)](s34.md) |
| S35 | [Build and verify the parkinsonism review draft (2026-10-09)](s35.md) |
| S36 | [Build and verify the acute_dystonia review draft (2026-10-09)](s36.md) |
| S37 | [Build and verify the no_improvement_clozapine review draft (2026-10-09)](s37.md) |
| S38 | [Build and verify the continue_or_adjust review draft (2026-10-09)](s38.md) |
| S39 | [Review and release both complete workflow bundles (2026-10-09)](s39.md) |
| S39.a | [Immutable F6 reference-table correction (2026-10-09)](s39-a.md) |
| S39.b | [Owner confirmation of all 11 question packages (2026-10-09)](s39-b.md) |
| S40 | [Freeze analysis snapshots and project one question's inputs (2026-10-09)](s40.md) |
| S44 | [Durable leased jobs and global admission (2026-10-09)](s44.md) |
| S41 | [Real private MCP transport and context grants (2026-10-09)](s41.md) |
| S43 | [Bounded provider CPT estimation and tool bridging (2026-10-09)](s43.md) |
| S45 | [Run one full synthetic clinical question end to end (2026-10-09)](s45.md) |
| S46 | [Execute ordered workflows and assemble a complete proposal (2026-10-09)](s46.md) |
| S47 | [Bound failures and recover at exact failed stage (2026-10-09)](s47.md) |
| S48 | [Build automatic proposal review and transparency UI (2026-10-09)](s48.md) |
| S48a | [Persist complete CPT adjustments and deterministic redistribution (2026-10-10)](s48-a.md) |
| S48b | [Recalculate locally with revision integrity, retry, and reset (2026-10-10)](s48-b.md) |
| S48c-backend | [Exact current-result acceptance endpoint (2026-10-10)](s48-c-backend.md) |
| S48c-frontend | [Complete CPT review, comparison, and acceptance UI, frontend only (2026-10-10)](s48-c-frontend.md) |
| S48c-devtest | [Complete CPT review, comparison, and acceptance tests, TESTS ONLY (2026-10-10)](s48-c-devtest.md) |
| S48d | [Regenerate only questions affected by patient-data changes (2026-10-10)](s48-d.md) |
| S49 | [Implement atomic plan signing and immutable snapshots (2026-10-10)](s49.md) |
| S50 | [Build final-plan editing, comparison, sign, and addenda UI (2026-10-10)](s50.md) |
| S51 | [Close archive, deactivation, and multi-user race cases (2026-10-10)](s51.md) |
| S52 | [Complete append-only audit and administration views (2026-10-10)](s52.md) |
| S53 | [Export lists and printable longitudinal patient reports (2026-10-10)](s53.md) |
| S54 | [Produce consistent full backups (2026-10-10)](s54.md) |
| S55 | [Validate and stage restores without touching live state (2026-10-10)](s55.md) |
| S56 | [Commit restore with maintenance, fencing, and rollback (2026-10-11)](s56.md) |
| S57 | [Package one-host Linux deployment and upgrades (2026-10-11)](s57.md) |
| S58 | [Measure capacity and expose useful operational status (2026-10-11)](s58.md) |
| S59 | [Verify complete registration and follow-up with CPT review (2026-10-11)](s59.md) |
