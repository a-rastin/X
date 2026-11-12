# Follow-up implementation plan for an AI agent

Status: ready for implementation against the reconciled 2026-11-12 requirements;
no implementation or runtime verification is claimed. Baseline inspected:
`b153b7b32f624a0c907362a7fe0bac7a6a67b181`, schema `0031`, 33 backup tables.

The [follow-up specification](follow-up-specification.md) is the behavioral
authority. Read it in full, the [source archive](../changes/follow-up-2026-11-12/README.md),
the [analysis checkpoint](progress/follow-up-planning-2026-11-12.md), and current
source before editing. Product questions are reconciled; this plan specifies the
implementation choices. The specification's [research development policy](follow-up-specification.md#research-development-policy)
applies to every package: no legal confirmation, author evidence submission,
source attestation, medical/clinical approval or reviewer sign-off is a prerequisite
to implementing or changing the app or its medical knowledge. Older content-review
instructions do not add those prerequisites to this work.

## 1. Entry procedure and scope

1. Follow root `AGENTS.md`, Keep the Why and the progress handoffs. Inspect Git
   status and preserve unrelated work. This planning session did not implement
   application code or migrate a database.
2. Inspect the current migration head and source symbols. If implementation starts
   after another migration, use the next additive revision rather than creating a
   second `0032` head. Do not assume old checkpoint “next session” instructions
   describe current work.
3. Use disposable PostgreSQL databases and synthetic patients for tests. Do not
   migrate the user's existing application database merely to run acceptance.
4. Execute packages A–H below in dependency order. Update this plan's package
   status and the dated progress report with actual checks, failures and remaining
   work. No package is complete solely because its files exist.
5. Do not request approval, evidence dossiers, source confirmation or medical
   validation before implementing a package or changing a medical rule. Use the
   specified behavior and automated engineering checks. Missing bibliographic or
   approval metadata is not a development or runtime blocker. Reused content
   loaders must not impose human-review status gates on `follow-up-v1`; keep
   structural/hash checks and historical approval metadata without fabricating
   approvals or changing old workflow behavior.
6. Reuse existing model, DDI checker, autosave, final-plan revision storage,
   assessment engine, permissions and UI theme. Add no runtime dependency or
   generalized workflow framework for this change. Do not rewrite the legacy
   worker/CPT pipeline or New case clinical behavior.

Path shorthand: `B` = `backend/src/x_insight`; `BT` = `backend/tests`;
`W` = `web/src`. Proposed paths below are new files, not claims they exist.

## 2. Verified seams and change map

| Existing source | Reuse/change |
| --- | --- |
| `B/cases/encounters.py:create_open_draft`, `patch_draft`, `safe_draft_for_author`, `require_legacy_workflow` | Dispatch immutable `follow-up-v1`, resolve signed baseline under the patient lock, strict typed autosave, reject cross-workflow commands. The legacy guard currently rejects only New case; extend it to an explicit `legacy-v1` allowlist. |
| `B/cases/chart.py:read_followup_baseline_for_author` | Preserve legacy response; add the frozen historical summary for the new version, not editable history mislabeled as baseline. |
| `B/cases/new_case.py`, `new_case_rules.py` | Reuse finite decimal parsing, BMI, lab comparisons, override attribution patterns. Do not call the registration-only evaluator with a follow-up or copy its MRI/antidepressant/timing policies. |
| `B/models/oral_antipsychotic.py:rank_oral` | Retain trusted loader and full 22-outcome distribution; implement follow-up filtering over `distribution`, not only its already sliced five candidates. |
| `B/cases/new_case_candidates.py` | Reuse calculation/checker concepts while keeping New case's interaction-annotation behavior unchanged. A dedicated follow-up candidate helper is sufficient. |
| `B/ddi/checker.py:check`, `check_candidates` | Current-medication pairs, all-candidate comparisons, D/X letters and missing coverage. No string heuristics or new source importer. |
| `B/cases/new_case_plans.py`, `signing.py`, `tables.py`, `router.py` | Reuse immutable-proposal/freshness/signing patterns and secondary-plan commands; add a distinct proposal/signature variant with strict ownership. |
| `B/reasoning/snapshots.py`, reasoning routes, legacy medication/effects/history/diagnosis commands | Audit all public legacy entry points; refuse the new workflow after privacy checks. Do not rely only on UI hiding. |
| `B/operations/{backup,restore}.py`, `B/db.py` | New schema/table/FKs, supported archive versions, version/variant checks and inventory ordering. |
| `W/features/encounters/{EncounterPage.tsx,workflow.ts,useAutosave.ts}` | New version dispatch, shared draft save/conflict behavior, explicit unknown-version refusal. |
| `W/features/new-case/{NewCaseSections.tsx,NewCasePlanPage.tsx}` | Reuse focused input/rendering patterns; extract only genuinely shared leaf helpers when necessary. Avoid expanding registration component props into a universal wizard. |
| `W/features/patients/chart/`, `W/features/plans/SignedView.tsx`, `B/cases/reporting.py` | Frozen summary, new signed view/report, mixed chronology, existing addenda. Exact v4 checks must not misclassify v5 as legacy. |
| `deploy/Dockerfile.app`, `deploy/check.sh`, startup content resolution | Package and resolve follow-up content through `X_INSIGHT_CONTENT_DIR`. No second oral XML copy or provider requirement. |

## 3. Intended contracts

### 3.1 Version and data boundaries

Use `workflow_version = "follow-up-v1"` only with `kind = "follow_up"`.
Newly created follow-ups select it server-side after rollout; clients cannot
choose/patch identity. Existing legacy follow-ups retain their version and UI.
No New case or Known case registration changes identity.

Proposed backend modules:

- `B/cases/follow_up.py`: typed state, defaults, autosave, content pins, evaluation
  and validation. Reuse small shared helpers without creating a plugin framework.
- `B/cases/follow_up_baseline.py`: signed-variant projection/adapters and immutable
  summary. Keep this separate from editing and the decision evaluator.
- `B/cases/follow_up_rules.py`: pure branch decisions, priorities, adjuncts,
  non-drug recommendations and 16-factor mapping.
- `B/cases/follow_up_candidates.py`: full-distribution filtering and interactions.
- `B/cases/follow_up_plans.py`: proposal persistence, freshness and atomic signing.

Keep routes in the existing Cases router unless file size warrants one included
follow-up router. Domain services use caller transactions; do not commit inside
services. Preserve Core DML and existing `CommittedRoute` response semantics.

`draft_data` retains shared `panss.answers`, `cssrs.answers` and medication IDs.
Store new state under `draft_data.follow_up`, with strict unknown-key refusal:

| Section | Data/initial state |
| --- | --- |
| Identity | `schema_version`, frozen `visit_year`, exact content/model/assessment/Lexicomp pins; server-owned |
| Baseline | Source encounter/snapshot IDs, hash, source visit/sign dates, bounded frozen summary, inherited field provenance; server-owned |
| Harm | `self`, `others`: false |
| History | Psychiatric presence/conditions/Other text; fresh lab-report flag/values; inherited medical booleans plus current overrides; heart failure, tobacco, substances and four categories/Other text; pregnancy/lactation, BMI and mode, neurological booleans; optional `hospitalization_count`; conditional `current_antidepressant: bool\|null` |
| Effects | `sedation: none\|tolerable\|intervention`; six other booleans, false |
| Treatment | `main_catalog_drug_id: string\|null`, `formulation: oral\|lai\|null` |
| Considerations | `recovery: controlled\|partial\|none_or_worse\|null`; adherence/basic needs true, observation/LAI preference false; inherited seizure/QT risks |
| Branch answers | `switch_needed`, `dose_reduction_possible`, `treatment_resistant`, `persistent_harm_risk`, `dystonia_resolved`: boolean or null, initially null |

Implement exact Python/TypeScript field definitions together in A/C. Each
inherited value carries source field, encounter/hash/date; missing is null rather
than No. For medical suggestions store current `manual: bool|null` plus server
actor/time separately from inherited answer and source findings. Do not accept
client-forged provenance, pins, source summary, evaluated factors or rule results.

Unmatched historical medication IDs live in the frozen baseline projection with
separate current resolution states (pending, replaced with a valid selected ID,
or explicitly removed). They are never inserted as invalid current IDs. Require
all pending items resolved before leaving Medications; deleting all is supported.
Carry a prior structured main selection only if it still identifies a valid
current entry/formulation; never extract adoption from a free-text signed plan.

Keep raw state inside existing draft depth/node/byte bounds. Do not embed an
entire historical chart or 16 MB model in a draft. Store a bounded summary and
most recent recorded prior lab per analyte; full historical records remain in
signed snapshots. Test the worst supported medication/lab/summary shape against
current bounds before considering any targeted bound change.

### 3.2 APIs

All encounter operations are author-only, with ordinary 401/403/404 privacy,
active-account/archive checks. Write routes require CSRF and quoted encounter
`If-Match`; support the existing optional `Idempotency-Key` and replay semantics.
Errors use the repository contract and localized field/message keys.

| Method/path under `/api/v1` | Request/result |
| --- | --- |
| `GET /follow-up/config` | Physician read: versioned labels/definitions, catalog/formulations, assessment/rule/source hashes and supported LAI equivalences. No credentials or provider dependency. |
| `POST /patients/{id}/encounters` | Existing `{kind:"follow_up"}` creates server-versioned draft from latest signed source, after single-slot checks; no client inline baseline override for the new version. |
| `GET /encounters/{id}` | Existing draft read exposes immutable workflow identity and typed raw state. |
| `PATCH /encounters/{id}` | Existing autosave; incomplete allowed, malformed/forged/cross-version state rejected. Stale revision 412 preserves local edits. |
| `GET /encounters/{id}/follow-up` | Revision, frozen summary, effective flags/findings, BMI, step/branch errors, decision preview/trace, model-factor requirements and latest proposal/freshness. Reading never creates a proposal. |
| `POST /encounters/{id}/follow-up/validate-step` | Body `{destination:<step-id>}`; validate applicable prerequisites through the step before destination. Returns `can_navigate`, field errors and current evaluated state; no state change or inferred answers. |
| `POST /encounters/{id}/follow-up/proposals` | Empty body; require all applicable inputs, calculate/persist or reuse identical immutable inputs/content. |
| `GET /encounters/{id}/follow-up/proposals/latest` | Latest proposal or null and independently computed freshness. |
| `GET /encounters/{id}/follow-up/proposals/{proposal_id}` | Same-encounter owned proposal and freshness; historical content stays pinned. |
| `POST /encounters/{id}/follow-up/sign` | `{proposal_id, expected_plan_revision}` plus encounter revision; atomically validate/freeze/release slot/audit. |

Step IDs: `panss`, `harm`, `history`, `medications`, `effects`, `considerations`,
`proposal`. Generation/signing independently revalidate all required applicable
inputs even if the client bypasses navigation. Retain secondary-plan GET/PATCH
and its separate plan revision; do not add a second final-text storage channel.

Use errors such as `WORKFLOW_MISMATCH`, `STALE_REVISION`, `STALE_INPUTS`,
`FOLLOW_UP_INCOMPLETE` and a retryable content/model lookup failure, following
existing status conventions. A valid physician-review outcome is not a content
failure. Never turn infrastructure errors into an empty successful recommendation.

### 3.3 Proposal and signature storage

Add `follow_up_proposals`, mirroring the small immutable New case table:
ID, encounter/author IDs, source encounter revision, input/content hashes,
nullable model-version FK, required DDI-release FK, payload and creation time.
Enforce same-input/content uniqueness per encounter; index latest-by-encounter.
Apply appropriate app/read-only role grants and preserve immutability conventions.

Payload schema `follow-up-proposal-v1` includes baseline reference/hash, raw and
effective inputs, per-field provenance, assessed age/visit year, reached questions,
decision trace, original/intended/final action status, adjuncts, all independent
sections, model-run status/reason, factors/source map, full distribution when
used, exclusions/coverage/current and candidate pairs, settings revision/IDs,
model/content/release versions/hashes. Do not require or fabricate a model result
on continuation or physician-review branches.

Include a `content_snapshot` with the actual small lab/rule/factor/formulation/
equivalence definitions and source metadata used, with hashes. Model bytes stay
in the existing model registry. Preserve versioned package assets for supported
unsigned drafts; restore validates frozen proposal content without substituting
the latest active package. This snapshot belongs in proposal payload, not an
unbounded deeply nested historical chart inside `draft_data`.

Add nullable `signed_encounter_snapshots.follow_up_proposal_id`. Extend the
exactly-one-reference constraint to three variants:

1. Legacy: `batch_id` and legacy `proposal_id` nonnull; both deterministic IDs null.
2. New case: only `new_case_proposal_id` nonnull.
3. Follow-up: only `follow_up_proposal_id` nonnull.

New follow-up snapshot uses `signed-encounter-v5`, freezes workflow/encounter,
demographics, raw/effective inputs, original proposal, source references and final
physician text/revision/signer/time. Other signature schemas keep their bytes.
No UPDATE migration of old snapshots. Guard down-migration against destroying
stored follow-up proposals/signatures, as the New case migration already does.

At the inspected baseline the additive revision would be `0032`; expect 34 backup
tables after adding one. Verify the actual migration/inventory instead of changing
tests to those values mechanically if intervening work changes the baseline.

## 4. Package A — Pin content and encode the decision contract

Depends: none. Status: partial — pinned content and offline integrity checks added;
proposal-generation and integrated packaging acceptance remain open. See
[Package A checkpoint](progress/follow-up-package-a-2026-11-12.md).

Read: specification §§1, 3–7; `content/new-case/v1/{manifest,labs,antipsychotic-formulations,oral-model-manifest}.json`;
`B/models/oral_antipsychotic.py`; supplied original Mermaid.

Write: `content/follow-up/v1/manifest.json`, explicit rule/factor/source-map JSON
and LAI-equivalence content; proposed `BT/models/test_follow_up_sources.py`.
Reference existing oral bytes and lab/formulation content by hash/path;
do not duplicate the XML or change New case hashes.

1. Record the agreed decision/rule IDs and every source/overlay. Preserve the
   original graph file unchanged. Encode effect precedence and independent
   recommendation rules explicitly, rather than only in renderer text.
2. Define all 16 model sources exactly as specification §6. No effect defaults
   from registration and no invented anticholinergic model parent.
3. Build explicit supported oral-to-LAI mappings from the existing inventory;
   distinguish depot from short/intermediate injection. Validate source/catalog
   links mechanically against the existing inventory; no source-owner or medical
   confirmation is required. If an ingredient has no supported Lexicomp identity, retain visible
   coverage-unavailable rather than manufacturing one.
4. Include precise lab hash, model hash, assessment versions, Lexicomp source
   release and formulation inventory hashes. New follow-up content has its own
   version/hash. Source references are descriptive; absent bibliographic or review
   metadata does not prevent building, loading or using a structurally valid
   package. Do not add legal, author-evidence or clinical-approval fields as
   required activation conditions.
5. Check source-path resolution from a packaged working directory and modified
   content rejection. Test all graph terminals are represented and the unsupported
   current-LAI terminal is deliberate, with no generic LAI-network execution.
   Test that valid content with no human approval or source-attestation record
   loads and generates proposals, while malformed or hash-mismatched content
   still fails the normal integrity checks.

Exit: content is loadable offline; hashes validate; graph rules/overlays and 16
factor mappings are complete, with available reference notes retained and no
human approval/evidence/source-confirmation gate.

## 5. Package B — Add versioned persistence and mixed-record recovery

Depends: A for identities. Status: partial — additive schema and compatibility groundwork; follow-up archive validation and mixed-record recovery remain open. See [Package B checkpoint](progress/follow-up-package-b-2026-11-12.md).

Files: next `backend/migrations/versions/*`; `B/cases/tables.py`, `B/db.py`;
`B/operations/{backup,restore}.py`; recovery schema/inventory fixtures.

1. Add the table/FK/reference-variant constraint in §3.3. Extend workflow check
   constraints/triggers from the actual current DDL; allow `follow-up-v1` only
   for follow-up while retaining immutable identity. Do not replace old values.
2. Extend safe signed-reference serializers with nullable follow-up proposal ID;
   preserve old callers. Add model/DDI/proposal/signature FK dependency ordering.
3. Extend every archive schema-version gate, not only the top-level allowlist.
   Current restore has repeated version checks and a hardcoded new-case table
   condition; keep v4 support valid at the new head.
4. Validate follow-up workflow/raw shapes, source references, proposal ownership,
   canonical hashes, DDI/model identities, signature schema and exactly-one
   reference. Verify signed baseline references point to the same patient and
   a supported immutable snapshot. Older archives default absent new table/FK
   safely without assigning old records a new workflow.
5. Tests: fresh migration, upgrade from 0031 with legacy/v4 records, attempted
   identity mutation, malformed variant/FK rejection, old backup restore and
   new-table ordering. Keep the expected schema version/inventory truthful.

Exit: additive migration and structural recovery checks pass, old signatures
remain byte/hash-identical. Full mixed-data public recovery drill occurs in H.

## 6. Package C — Build signed baselines, typed state and autosave

Depends: A, B. Status: partial — backend state contract implemented and tested
(latest signed source across variants, v1–v5 adapters/frozen summary, typed
defaults/normalization/autosave, config/preview/validate-step); `follow-up-v1`
creation stays gated off until F/G. See
[Package C checkpoint](progress/follow-up-package-c-2026-11-12.md).

Files: proposed baseline/state modules; `B/cases/{encounters,chart,router}.py`;
`BT/http/test_follow_up_state.py`; relevant `BT/assessments/` pure-state tests.

1. Move new-version baseline resolution inside the existing patient-row lock.
   Check the single-draft slot and latest signed source consistently so sign/create
   races cannot select a stale New case while a more recent follow-up is signed.
2. Implement explicit adapters for legacy v1–v3 snapshots, New case v4, and new
   follow-up v5. Only map known semantically equivalent structured fields. Unknown
   fields, unavailable prior data and unmatched historical catalog IDs stay visible
   as unresolved; historical IDs are not silently reinterpreted in another release.
3. Freeze summary and inherited values/source dates separately from editable state.
   Resolve per-analyte prior labs and prior contraception records from signed
   structured data. Never copy a prior final free-text plan as current medications.
4. Initialize fresh PANSS, five explicit C-SSRS No values, effect defaults,
   required recovery null and branch-answer nulls. Carry only specified history,
   BMI, medication and risk fields. With no baseline, show “no prior signed visit”
   and require missing applicable inputs; do not block opening the draft itself.
5. Implement strict raw/autosave normalization, finite decimals, bounded strings,
   selected catalog IDs and conditional masks. Retain inherited and current manual
   attribution separately. Test suggestion priority, later lab edits, off/on
   report toggles, multiple triggers and reset-to-automatic.
6. Keep new state out of legacy autosave normalization and old effects/history
   validators. Reject forged version, provenance, baseline/pins and cross-workflow
   state. Make the legacy allowlist explicit and audit sibling callers.
7. Implement config/preview/validate-step API contracts and literal shared types.
   Resolve branch questions without creating a proposal on read. Missing optional
   PANSS never prevents completion; missing mandatory/conditional fields do.

Checks include: latest legacy follow-up after New case; v5-after-v5 carry-forward;
same-patient source ownership; stable tie ordering; two-physician create race;
stranger/admin draft denial; archived/inactive guards; 412; idempotent replay;
max supported draft shape; historical summary unchanged after current edits;
zero versus missing count/lab; no age-to-birth-year inference.

Exit: new follow-up creation/read/edit/resume is version-correct; old drafts still
round-trip without changed defaults, questionnaire gates or signed bytes.

## 7. Package D — Implement pure decisions and recommendation rules

Depends: A, C. Status: pending.

Files: proposed `B/cases/follow_up_rules.py`; `BT/assessments/test_follow_up_rules.py`.

1. Implement the original oral decision table, with controlled/partial/none-or-worse
   recovery distinctions and only reached physician branch questions.
2. Evaluate all effects for every recovery state. Deduplicate adjuncts, record
   acute-dystonia response/dose-reduction answers, and make effect-driven oral
   replacement override clozapine/LAI/continuation without dropping adjuncts.
3. Preserve no-main/current-LAI physician-review states. Define explicit terminals
   (`continue_same`, `reduce_dose`, `adjunct_only`, `oral_replacement`,
   `same_drug_lai`, `clozapine`, `physician_review`) and reason/trace IDs.
4. Map complete branch-required model factors with provenance; require missing
   hyperprolactinemia only when needed. Normal/no historical prolactin and a
   genuinely absent prolactin measurement are distinct cases.
5. Implement independent care/lab/MRI/antidepressant/counseling/timing sections
   exactly as specification §7. Keep all inpatient reasons; do not import age-only
   MRI or unchanged 2–4-week timing from New case.

Use parameterized decision-table tests, not tests that simply repeat helper code.
Minimum behavior matrix:

| Group | Required cases |
| --- | --- |
| Controlled oral | Same dose; lower dose; requested oral switch; LAI preference with/without equivalent |
| Partial response | Nonadherence with/without equivalent; adherent persistent risk Yes/No; resistance question inactive |
| No improvement | Resistance Yes; resistance No then both adherence branches and persistent risk outcomes |
| Effects | Each of seven effects; tolerable sedation; dystonia resolved/unresolved and reducible/not reducible; TD adjunct only |
| Priority | Parkinsonism + TD; unresolved dystonia + TD; effect switch + resistant clozapine; effect switch + nonadherent LAI; duplicate adjunct deduplication |
| Unsupported treatment | Empty medications; list without main; current LAI; main removed; unsupported equivalence |
| Other sections | Each inpatient trigger alone and together; C-SSRS 4/5 Yes with self-harm No; depression/PTSD with/without current antidepressant; MRI triggers and age-only negative; male/female/pregnant/breastfeeding; ages 55/56; all timing outcomes |

Exit: pure rules and factor completeness pass without a database, provider, worker
or fake model result; each terminal has an explainable trace.

## 8. Package E — Implement ranking and D/X policy

Depends: A, C, D. Status: pending.

Files: proposed `B/cases/follow_up_candidates.py`;
`BT/models/test_follow_up_candidates.py`; DDI public lookup regressions.

1. Call the trusted oral ranking with all 16 observed values; retain its complete
   distribution. Do not alter the generic XML bound or global empty-evidence
   legacy policy. No model call on a non-model branch.
2. Compare eligible full-distribution candidates against every current drug,
   including the outgoing main. Filter zero/unavailable/current-main/D/X before
   taking five; retain original probabilities/ties/ranks and all removal reasons.
3. Build current-list pairs separately from candidate pairs. Use one pinned release
   and deduplicate unordered pairs; do not check a catalog identity against itself.
4. Apply formulation availability and D/X checks to direct clozapine/LAI results.
   Explicit D/X returns physician review with pairs; unknown coverage returns
   its warning rather than forged A or an unrequested blanket exclusion.
5. Distinguish no eligible oral candidate, unsupported direct mapping, valid
   non-model outcome, and missing/corrupt model/lookup infrastructure. Only valid
   limited-scope outcomes can produce successful physician-review proposals.

Checks: top-five all excluded but sixth eligible; X/D against one/multiple drugs;
outgoing-main pair retained; current drug excluded as replacement; no renormalization;
formulation-specific unavailability; missing pair versus explicit A; no duplicate
self-pair; all candidates removed; direct exclusions; trusted-model integrity;
unchanged New case candidate behavior.

Exit: up to five eligible candidates and complete auditable exclusions, with the
source distribution unchanged and no provider dependency.

## 9. Package F — Persist proposals and sign atomically

Depends: B–E. Status: pending.

Files: proposed `B/cases/follow_up_plans.py`; Cases routes/serializers;
`BT/http/test_follow_up_plans.py`; legacy reasoning guard regressions.

1. Implement canonical fingerprint including raw/effective relevant inputs,
   baseline hash/summary references, demographics/frozen year, rules/factor map,
   exact model/DDI/assessment/content pins and physician availability revision/IDs.
   Keep presentation language, final text and read timestamps out of it.
2. Generate from acknowledged saved inputs, reuse identical immutable inputs,
   and never UPDATE an old proposal. Persist trace, adjuncts, factors/distribution
   or explicit not-run reason, independent sections and interaction evidence.
3. Compute current freshness on reads and again on sign. Verify pinned bytes;
   global content publication does not repin an old draft or rewrite historical
   proposal evidence. Settings/demographic/input edits invalidate as specified.
4. Use existing secondary-plan revision storage. Server never overwrites edited
   final text during regeneration. Require the latest intended owned proposal,
   current input hash and saved nonblank final text for explicit confirmation.
5. Sign in the existing consistent transaction/lock pattern; recheck author,
   account activity, patient archive state, encounter revision, plan revision,
   proposal source/ownership/completeness/freshness and supported outcome status.
   Write one v5 signature, release slot and audit atomically. Lost responses and
   identical concurrent confirmations replay one signature, not two.
6. Block legacy generate/retry/sign/adjust/accept and old clinical write routes
   for `follow-up-v1`; block new commands on legacy/New case. Do not weaken
   successful-generation requirements on legacy drafts.
7. Audit create/carry source, attributed overrides/reset, completed proposal,
   invalidation-relevant changes, final-plan writes and confirmation using existing
   event conventions, with no fabricated LLM/CPT events or sensitive diagnostics.

Checks: proposal deduplication; stale input/settings/demographics; foreign proposal;
cross-workflow endpoints; null/malformed final text; 412 from each revision;
no partial signature on failure; current-LAI/no-main/no-eligible supported review
signing; content failure cannot sign; sign/create/settings/deactivation/archive
races; original/final text retained independently; immutable v1–v4 hashes unchanged.

Exit: public HTTP generation/edit/sign works without provider settings or worker;
every accepted signature points to exactly one valid proposal variant.

## 10. Package G — Build the bilingual seven-step UI and signed views

Depends: C–F. Status: pending.

Proposed UI: `W/features/follow-up/{api.ts,FollowUpWizard.tsx,FollowUpSections.tsx,FollowUpPlanPage.tsx}`;
existing encounter dispatch/chart/signed views/messages; tests under `web/` and
`e2e/follow-up-v1.spec.ts` following current test discovery.

1. Dispatch on immutable workflow version; retain the old wizard for old follow-ups.
   Unknown versions fail closed. Use existing Astryx shell/components/tokens;
   consult project UI skills when implementing UI. Keep layout, theme and language
   behavior consistent with the implemented academic UI.
2. Render frozen summary before steps, collapsed historical details and dated
   prior findings. Show human labels/names instead of raw history/catalog IDs.
   Keep previous values visually distinct from current editable controls.
3. Build the exact step order and field defaults. Reuse PANSS and focused lab,
   BMI, catalog and disclosure primitives. Five recent C-SSRS items are nested
   under harm; simple effects replace old questionnaires only in this version.
4. Medication fields append a blank row and honor three-character search, clear
   unmatched inputs, main-drug selection and formulation. Clearing all drugs stays
   navigable with explicit no-main review. No interaction panel on step 4.
5. Show only reached branch questions and field errors, but include new conditional
   hyperprolactinemia/antidepressant questions when required. Gate direct hash,
   Continue and Generate against saved server validation; do not infer completed
   assessment from merely opening a step or disclosure.
6. Use existing autosave and dirty-navigation guards. Flush input saves and final
   text before dependent actions. A 412, lost response, generation error or language
   change must preserve typed values and edits. Retry stable idempotent commands
   using the original key where appropriate.
7. Proposal shows a primary drug action or explicit physician-review outcome,
   adjuncts, reasons, candidate probabilities/exclusions, source warnings and other
   sections. No probability sliders, provider setup gate or generic empty success.
   Keep immutable output separate from editable physician text; seed once and offer
   explicit replacement after regeneration.
8. Update chart API types, signed schema discriminators, v5 proposal view, addenda
   integration and escaped printable report. Preserve mixed historical display.
   Do not parse final text into presumed prescribed drug, adherence or lab data.

Browser acceptance: English/Persian × Chromium/Firefox; light/dark representative
pages; RTL with readable LTR drug/unit/number spans; keyboard disclosures and
required/errors; representative axe checks without blanket contrast suppression.
Cover original branches, all precedence combinations, latest baseline, incomplete
PANSS, missing history, lab overrides, medication deletion, C-SSRS risk, stale
proposal, lost-response/reload recovery, final-edit preservation, sign and chart.
Use behavioral locators; preserve old-suite contracts for existing versions.

Exit: real public-HTTP/browser journeys pass in both browsers; UI does not display
new workflow success on mocked-only evidence.

## 11. Package H — Package, recover and verify end to end

Depends: A–G. Status: pending.

Files: deployment/startup checks; `BT/operations/test_follow_up_packaging.py`;
mixed `BT/recovery/` tests; docs and final progress report.

1. Ship `content/follow-up/` and referenced existing assets in the app image;
   resolve them with `X_INSIGHT_CONTENT_DIR` away from repository cwd. Existing
   migration/startup already installs the oral model and Lexicomp; extend normal
   preparation only where needed. No manual provider/API-key prerequisite.
2. Run a fresh-schema install and a non-destructive clone upgrade. Verify identity,
   table/constraint/grants, content pins and health/readiness. Keep production
   deployment separate from this acceptance work.
3. Create synthetic mixed histories through supported APIs: legacy signed record,
   New case v4, new follow-up v5, and another new follow-up whose baseline is v5.
   Include a non-model and a model proposal, overridden labs, exclusions, edited
   final text and addendum. Existing historical hashes must remain unchanged.
4. Make a real full backup; stage and commit restore via public recovery HTTP
   into an isolated database. Verify model/Lexicomp/content identities, table/FK
   order, proposal hashes, immutable old/new signatures, chart/report output,
   author privacy and next follow-up creation. Restore older supported archives
   too. Record archive/expanded sizes and existing bounds without hiding failures.
5. Complete repository gates and focused regression below. Fix new failures;
   distinguish an independently verified pre-existing failure from an assumed
   baseline problem. Never count interrupted/skipped work as passing evidence.
6. Update requirements/API/user documentation from planned to implemented only
   after actual integration; record exact head/commands/results/limitations and
   open release gates in the checkpoint/index. Refresh affected memory hashes.
   Commit only when separately requested.

Suggested command sequence (repo root; use repository environment/Makefile):

```sh
make check
make test-web
make test-backend TEST='tests/models/test_follow_up_sources.py tests/assessments/test_follow_up_rules.py tests/http/test_follow_up_state.py tests/models/test_follow_up_candidates.py tests/http/test_follow_up_plans.py'
make test-backend TEST='tests/http/test_followup.py tests/http/test_established_followup.py tests/http/test_new_case.py tests/http/test_new_case_plans.py tests/http/test_exports.py'
make test-e2e TEST=e2e/follow-up-v1.spec.ts
make test-e2e TEST=e2e/new-case.spec.ts
make test-recovery
make prod-check
make test-backend
```

The new test paths are proposed package deliverables; add any split state/source
tests to the selector rather than silently omitting them. Full recovery/browser
tests require configured disposable databases and a running acceptance stack;
passing `make check` alone does not establish this workflow. No live provider
credential or real patient is needed. Use existing documented startup commands;
do not expose service secrets in logs or source-controlled reports.

Exit: agreed behavior passes public interfaces, mixed recovery is proven, old
workflows/signatures remain valid, and engineering limitations are explicit.
No legal, author-evidence, source-confirmation or medical-approval step is part
of acceptance.

## 12. Acceptance and handoff checklist

- [ ] No package, content activation or proposal requires legal confirmation,
  author evidence submission, source attestation or external medical approval.
- [ ] A: immutable source manifest, rules, factor map and LAI equivalences verified
  mechanically; content works without human approval metadata.
- [ ] B: additive schema, three signature variants, old archive compatibility.
- [ ] C: latest signed source across variants, frozen summary, typed autosave,
  exact defaults, missing-value/provenance/conditional validation.
- [ ] D: all original graph paths and confirmed overlays covered; effect replacement
  priority, adjunct accumulation and independent recommendations are correct.
- [ ] E: full-distribution D/X filtering/backfill; direct exclusions, coverage and
  outgoing-drug comparisons preserved; New case unchanged.
- [ ] F: immutable proposal, editable final text, freshness, replay-safe atomic v5
  signing and explicit workflow guards; supported review outcomes remain honest.
- [ ] G: seven-step bilingual UI, no diagnosis/CPT gate, both browsers, accessibility,
  error/reload recovery, signed chart/report and addenda.
- [ ] H: packaged fresh/upgrade startup, full gates and real mixed-data recovery.

The implementation agent's final handoff must state completed packages, remaining
work, exact tested revision and database/schema context, passing/failing/blocked
checks, historical compatibility evidence and whether changes are committed.
Do not label the work complete while a required package or acceptance case remains open.
