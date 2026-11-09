# New case Package E checkpoint — 2026-11-09

## Status: backend integration implemented

This follow-up replaces the initial helper-only checkpoint. Typed raw state,
deterministic evaluation and the application routes are now connected. Required
A/B content and identity prerequisites are supplied here; this is not a claim
that all Package B identity/settings work or the full new-case product is done.

### Content and identity

- Added `labs.json`, `blood-pressure-lowering-drugs.json`,
  `antipsychotic-formulations.json` and the hash-verified workflow manifest under
  `content/new-case/v1/`. Lab units, sex-specific bounds, precise operators,
  critical/outpatient distinctions, source metadata, exact Lexicomp mappings
  and oral/injectable formulation identities are versioned.
- Source retrieval succeeded for NIDDK, MedlinePlus, NHLBI and Iowa pages on
  November 9. ABIM returned a challenge; Mayo and OhioHealth denied retrieval.
  These limitations are recorded in content metadata. Requested thresholds
  are not represented as independently clinically validated or approved.
- Migration `0030` adds nullable birth year, extends age bounds to 100 and adds
  immutable encounter workflow identity. Existing rows remain `legacy-v1`.
  Birth-year first-time requests initialize the new workflow atomically with
  the patient, frozen UTC year and validated content pins. Age-only clients
  remain legacy. No existing signed data is rewritten by this migration.
- Model source SHA remains
  `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`;
  Lexicomp CSV SHA remains
  `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`.

### Application wiring

- `cases/new_case.py` owns strict raw inputs, canonical decimal measurements,
  autosave normalization, server-controlled metadata, persistent attributed
  manual overrides, field errors, step gates and deterministic projection.
  It uses the existing medication and assessment owners, not alternate schemas.
- Preview exposes effective inputs, all triggering findings, laboratory warnings,
  BMI mode/category, the 16 ordered factors and deterministic plan sections.
  Disabled/hidden inputs do not trigger findings. Movement factors are explicit
  workflow defaults. Contraception recommendations remain separate outputs.
- Ordinary nonpositive diagnosis progression is blocked. An explicit audited,
  revision-fenced physician assertion clears negative/partial/unknown active
  answers, preserves positive answers, survives unrelated saves and is invalidated
  by answer edits. Legacy acknowledgment/bypass cannot substitute for it.
- Authenticated content, author-only preview, CSRF/revision-fenced navigation
  validation and idempotent assertion routes are mounted in the application.
  Existing diagnosis reads use the same gate. Generic autosave retains its
  payload bounds, concurrency and idempotency contracts.
- Legacy medication strict-save, generation and signing routes refuse new-case
  drafts. Candidate/interactions report `not_generated`; LAI candidates report
  `model_unavailable`. No provider or legacy worker is invoked by preview.
- Recovery recognizes schema `0030`, validates staged workflow/pins/attribution
  and admits only the exact trusted model hash through the bounded oral loader.
  Arbitrary uploaded execution-profile metadata cannot bypass generic admission.
  Older archives retain legacy defaults; no backup-table expansion was needed.

API details and installation prerequisites: [New-case state](../../../docs/new-case-state.md).

## Verification

- Pure rules/state/demographics/recovery checks: 80 passed, 14 subtests passed.
  Covers exact suggestion boundaries, sex-specific/critical decisions, hidden
  data, BMI modes, overrides, all plan conditions and trusted restore admission.
- New-case HTTP plus shared HTTP contracts: 47 passed (8 new-case, 39 shared).
  Tests use real PostgreSQL,
  bundled Lexicomp and the fixed oral model, not fake content IDs. They cover
  age 100, pin/catalog/metadata forgery, canonical saves, stale revisions,
  assertion replay/invalidation/positive preservation, partial PANSS, author/CSRF,
  archive guards and the database workflow-immutability trigger.
- A real backup containing the new draft, override, Lexicomp and oral XML passed
  `validate_archive_bytes`; raw draft bytes/identity round-tripped within the
  unchanged archive/expanded-size bounds. Restore-over-live was not exercised.
- Fresh migration `0030` ran on an isolated disposable PostgreSQL 16 database.
  A dedicated previous-head signed-snapshot preservation drill was not run.
- Broader legacy regression: 130 passed, then stopped at three signing failures
  with `NETWORK_INVALID`. The previously reported missing generic XML schema
  path is independently reproducible (`schema_unavailable`); it is unchanged
  here. The supplied schema remains under `BNs-new`/`old-BNs`, not the generic
  validator's expected path. No unrelated inference or schema-path fix was made.
- Changed-file Ruff lint/format and `git diff --check` passed; `mypy src` passed
  for all 83 source files. No browser/deployment/clinical acceptance is claimed.
- Final focused rerun: 127 tests and 14 subtests passed. The disposable
  `x-new-case-e-test` container was removed after validation; no live database
  or deployment was changed.

## Remaining boundaries and handoff

- Complete the rest of Package B: physician name identity/settings, unavailable
  drugs and their authorization checks, and birth-year editing semantics.
- Package F owns actual candidate/interactions generation, proposal persistence,
  fingerprints, final-text editing and atomic new-case signing/read/report shapes.
  Do not remove the legacy route guards to simulate that missing workflow.
- Package G owns the birth-year UI, workflow branching, rendered bilingual
  messages, navigation guards and browser acceptance. The current age-only UI
  intentionally does not opt into these new defaults.
- Resolve the pre-existing generic XML schema-path issue in its own change;
  rerun broad legacy signing/inference and recovery acceptance afterward.
