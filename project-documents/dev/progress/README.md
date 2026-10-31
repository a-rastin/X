# X-INSIGHT progress tracker

[Provider branch consolidation (2026-10-31)](s43.md#follow-up-2026-10-31--consolidate-provider-fix-branches) preserves both fix branch tips on `dev`; only `main` and `dev` remain locally.

[B02 provider failure details (2026-10-31)](s43.md#follow-up-2026-10-31--b02-provider-failure-details)
records passing acceptance and baseline comparisons; repository gates remain red, and the user requested a commit.

Start here for recorded progress, then read the relevant session report and its
handoff, deferred items, and blockers. Reports describe their dated scope;
older next-session and status statements are historical, not current instructions.
The [B26 parsing follow-up (2026-10-31)](s43.md#follow-up-2026-10-31--b26-response-parsing) records focused passing tests and incomplete commit gates.
The [B01 provider-timeout follow-up (2026-10-30)](s43.md#follow-up-2026-10-30--b01-configurable-provider-timeout) records configurable deadlines and lease renewal; acceptance passed; repository gates remain red.
The [branch consolidation (2026-10-31)](s43.md#follow-up-2026-10-31--consolidate-development-branches) moves B01 into `dev` and removes the two merged local branches; only `main` and `dev` remain locally.
The [User Guide (2026-10-30)](s62.md#follow-up-2026-10-30--application-user-guide) documents current physician and administrator workflows; documentation links and whitespace checks passed.
The [registration title and draft context cleanup (2026-10-30)](s07.md#follow-up-2026-10-30--registration-page-title-and-draft-context) updates the first registration step; the frontend build passed.
The [Medications and proposal header cleanup (2026-10-30)](s20.md#follow-up-2026-10-30--medications-and-proposal-header-cleanup) simplifies the registration title and draft cards; the frontend build passed.
The [Medications workspace redesign (2026-10-30)](s20.md#follow-up-2026-10-30--medications-workspace-redesign) adds selection/review columns, severity filters and immediate stale-report hiding; build, TypeScript and focused Chromium/Firefox checks passed.
The [History reconciliation UI removal (2026-10-29)](s12.md#follow-up-2026-10-29--remove-history-reconciliation-controls) removes its controls while preserving saved data; the frontend build passed.
The [History presentation follow-up (2026-10-28)](s12.md#follow-up-2026-10-28--history-page-presentation) simplifies the History page; the frontend build and Chromium browser suite passed, with Firefox partially run.
The [C-SSRS presentation follow-up (2026-10-28)](s11.md#follow-up-2026-10-28--c-ssrs-page-presentation) simplifies the assessment page; build and TypeScript checks passed.
The [PANSS questionnaire redesign (2026-10-30)](s10.md#follow-up-2026-10-30--panss-desktop-questionnaire-redesign) adds subscale navigation, a rating worksheet, and a saved-results rail; build, TypeScript, and Chromium/Firefox checks passed.
The [Diagnosis presentation follow-up (2026-10-28)](s09.md#follow-up-2026-10-28--diagnosis-page-presentation) simplifies the registration Diagnosis page; diagnosis and registration browser suites passed in Chromium and Firefox.
The [direct Diagnosis registration implementation (2026-10-28)](s07.md#follow-up-2026-10-28--direct-diagnosis-registration-implementation)
addresses [issue-06.md](../../../issues/issue-06.md); browser verification remains pending.
The [Diagnosis Unknown-default implementation (2026-10-27)](s09.md#follow-up-2026-10-27--diagnosis-unknown-default-implementation)
addresses [issue-05.md](../../../issues/issue-05.md); runtime verification remains pending.
The [Step 7 Unknown/empty-input implementation (2026-10-27)](s48.md#follow-up-2026-10-27--proposal-generation-with-unknownempty-inputs-implemented)
implements [issue-04.md](../../../issues/issue-04.md): estimation proceeds with missing/null context and disclosed uncertainty; warnings no longer block generation. Diagnosis history is preserved in the same report.
The [Diagnosis Continue confirmation implementation (2026-10-27)](s09.md#follow-up-2026-10-27--diagnosis-continue-confirmation-implemented)
links [issue-03.md](../../../issues/issue-03.md): guarded registration Continue
for nonconfirming results implemented, pending live verification.
The [skip/bypass diagnosis (2026-10-26)](s07.md#follow-up-2026-10-26--redundant-assessment-skipbypass-diagnosis)
links [issue-02.md](../../../issues/issue-02.md): Continue already permits incomplete steps.
The [skip/bypass removal implementation (2026-10-26)](s07.md#follow-up-2026-10-26--redundant-skipbypass-removal-implemented)
removes the active Diagnosis bypass and PANSS/C-SSRS Skip controls (Resume-only recovery retained,
acknowledgment-only proposal copy); `tsc` passes and independent review is PASS,
with live-browser/backend proof still blocked by environment (uncommitted working tree on `dev`).
The preceding provider destination work is [onboarding and destination safety (2026-10-26)](s42.md#follow-up-2026-10-26--provider-onboarding-and-destination-safety),
with diagnosis, implementation details, and evidence in [issue-01.md](../../../issues/issue-01.md). The real-TLS browser run passed 10/10 across Chromium and Firefox; the release gate remains open.
The preceding [S62 handoff](s62.md#follow-up-2026-10-26--provider-security-implementation-handoff) links that work without changing the rehearsal's release status.
The latest unrelated implementation follow-up is [automatic schema/DDInter startup (2026-10-23)](s18.md#follow-up-2026-10-23--prepare-schema-and-ddinter-during-normal-startup):
normal local/production launch prepares the database before dependent services;
11 startup regressions and real Chromium/Firefox registration checks passed.
The preceding [bundled approved DDInter setup (2026-10-23)](s18.md#follow-up-2026-10-23--bundle-approved-ddinter-for-fresh-installations):
normal local/production migration provisions fresh databases from committed sources;
real Chromium/Firefox registration and backend regression checks passed.
The preceding [C-SSRS recent ideation form cut (2026-10-23)](s11.md#follow-up-2026-10-23--c-ssrs-recent-ideation-form-cut):
Step 4 and follow-up ask five recent-ideation items, each Yes, No, or Unknown, default Unknown.
The preceding [LAI, aggression and C-SSRS input cleanup (2026-10-22)](s12.md#follow-up-2026-10-22--lai-aggression-and-c-ssrs-input-cleanup):
revised History controls, Step 4 C-SSRS generation context and input contract v3.
Implementation, tests and independent review are complete; see the commit handoff.
The preceding [proposal-readiness fix (2026-10-22)](s48.md#follow-up-2026-10-22--check-proposal-readiness-before-creating-a-run):
History lists unresolved required context and Step 7 checks saved readiness before creating a run.
The preceding [complete supplied DDInter candidate and
verification (2026-10-21)](s18.md#follow-up-2026-10-21--complete-supplied-ddinter-candidate)
includes [medication integration and release association](s20.md#follow-up-2026-10-21--ddinter-workflow-and-release-association).
The [complete review bundle archive](s18.md#follow-up-2026-10-21--archive-complete-ddinter-review-bundle)
retains all source/generated files, with evidence in Git LFS.
The [owner-confirmed local application publication](s18.md#follow-up-2026-10-21--owner-confirmation-and-local-application-publication)
published `ddi-c45e2051cf16` into `x_insight` on port 5432 after backup and migration
to `0022`; the separate reviewed manifest records approval. The earlier
[page-note follow-up (2026-10-20)](s13.md#follow-up-2026-10-20--remove-page-notes-keep-the-working-note)
keeps the working note.
The [2026-10-20 UI refinement](s61.md#follow-up-2026-10-20--shared-ui-refinement)
updates shared navigation, typography, cards, forms, tables, and sign-in presentation.
The latest recorded checkpoint is [S42](s42.md) (2026-10-17): provider
settings are implemented (encryption, masked admin API, capability check,
admin page). Its [2026-10-18 UI follow-up](s42.md#follow-up-2026-10-18--disable-testing-before-the-first-save)
disables capability testing until a settings revision has been saved.
Its [CSRF diagnosis](s42.md#follow-up-2026-10-18--csrf-failure-diagnosis)
records the tab-local token/shared-cookie mismatch and links the
[resolved issue](../issue-csrf-session-desynchronization.md). The
[CSRF follow-up](s42.md#follow-up-2026-10-18--synchronize-csrf-with-the-shared-session)
synchronizes the token during startup and across tabs without weakening the
backend gate. The [fake-IP follow-up](s42.md#follow-up-2026-10-18--fake-ip-capability-check-and-failure-color)
stops treating `198.18.0.0/15` as a blocked special-use address and colors a
failed capability diagnostic as an error. The
[capability-check issue](../issue-provider-capability-check.md) is fixed: Test
provider uses a synthetic chat/tool exchange, not the private echo. Schema
head and the fresh-backup expectation are now `0026` (B02 follow-up above), with
`provider_settings` in a 32-table inventory. The [S62](s62.md) release gate
otherwise stays open: not a release, not clinical validation, not high
availability. tasks.md has no session after S62 and no S63.
[The 2026-10-18 documentation assessment](s62.md#follow-up-2026-10-18--documentation-assessment-and-next-work)
recommends provider runtime integration first, then content and release evidence.
The [saved-settings generation follow-up](s62.md#follow-up-2026-10-18--saved-provider-settings-drive-generation)
connects a pinned settings revision to generation. A later live capability
check against `stepfun-ai/step-5-preview` succeeded after empty `content` was
allowed to fall back to `reasoning_content`.
The [README rewrite](s62.md#follow-up-2026-10-18--root-readme-rewrite) and
[installation guide](s62.md#follow-up-2026-10-18--installation-and-running-guide)
provide current project orientation and local/server setup instructions.
The [encounter-draft follow-up](s07.md#follow-up-2026-10-18--one-page-per-wizard-step)
shows each wizard step on its own page. The
[registration entry follow-up](s07.md#follow-up-2026-10-18--registration-opens-on-the-wizard)
opens Register new patient on that wizard; the post-create notes page is gone.
The S13 follow-up above supersedes its two-note-box placement with the working
note only. A later
[follow-up](s07.md#follow-up-2026-10-18--adverse-effects-leave-the-registration-wizard)
removes Adverse effects from that registration wizard and keeps them on follow-up.

The [diagnosis worksheet redesign](s09.md#follow-up-2026-10-20--diagnosis-worksheet-redesign)
replaces criterion cards with aligned response rows, A–F navigation, and a desktop review sidebar.
The [PANSS](s10.md#follow-up-2026-10-20--panss-worksheet-redesign),
[C-SSRS](s11.md#follow-up-2026-10-20--c-ssrs-worksheet-redesign), and
[History](s12.md#follow-up-2026-10-20--history-worksheet-redesign) follow-ups apply
the same worksheet language to response controls, navigation, and result review.

The [dashboard disclaimer removal (2026-10-30)](s05.md#follow-up-2026-10-30--remove-dashboard-disclaimer-box) removes the dashboard notice while retaining the login dialog; the frontend build passed.

The [clinical disclaimer presentation (2026-10-29)](s05.md#follow-up-2026-10-29--professional-clinical-disclaimer-presentation) refines the expanded physician warning; build, TypeScript, and focused Chromium/Firefox UI checks passed (synthetic auth).

[Registration C-SSRS Yes/No (issue 12)](../../../issues/issue-12.md) is implemented; Chromium passed and Firefox remains pending ([S11 handoff](s11.md#follow-up-2026-10-28--registration-c-ssrs-yesno-implementation-issue-12)).

## Reading and updating progress

[Discard button presentation (issue 11)](../../../issues/issue-11.md) is implemented; browser verification remains pending ([S07 handoff](s07.md#follow-up-2026-10-28--discard-button-presentation-implementation)).

The [Step 7 integration fix (2026-10-22)](s48.md#follow-up-2026-10-22--connect-step-7-to-reviewed-workflow-generation)
connects owner-confirmed pinned bundles, typed history inputs and browser-independent
run recovery; the earlier diagnosis is retained in the same report.
The [History readiness diagnosis](s48.md#follow-up-2026-10-22--proposal-blocked-by-missing-history-context)
explains the subsequent clarification/no-baseline messages and recovery.

The [Step 6 DDI diagnosis (2026-10-20)](s20.md#follow-up-2026-10-20--step-6-dataset-unavailable-diagnosis)
traces the earlier shared unavailable error; the local publication and fresh-install
bootstrap follow-ups above resolve missing releases within their respective scope.

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
| S12 | [Structured history and adverse effects; conditional History correction (2026-10-22)](s12.md) |
| S13 | [Page notes (2026-10-05); removed, working note retained (2026-10-20)](s13.md) |
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
| S60 | [Completed cross-user, failure, and security acceptance; S61 handoff (2026-10-16)](s60.md) |
| S60 frontend dev-test | [Strict browser rerun 8/8 after backend fix; proof limits recorded (2026-10-16)](s60-frontend-devtest.md) |
| S60 backend | [Cross-user, failure, and security acceptance incl. commit-timing fix + review blockers (2026-10-16)](s60-backend.md) |
| S61 | [Desktop usability defects proven, with verification gaps (2026-10-16)](s61.md) |
| S62 | [Release rehearsal recorded; gate still open (2026-10-16)](s62.md) |
| S42 | [Store and test provider settings securely (2026-10-17); provider security follow-up (2026-10-26)](s42.md) |
