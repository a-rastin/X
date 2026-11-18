# Linked reasoning explorer — 2026-11-18

Step 6 (S4–S6) renders the backend's active pathway beside the treatment
comparison and factor attribution in both proposal workflows. The graph
keeps stable node positions across scenarios. Line patterns identify
dependency types, and arrowheads identify patient-only, scenario-only and
shared edges. Node details expose the returned values, provenance and
connections. The compact graph preserves changed paths and nonzero
contributions. Selecting or focusing a tornado factor highlights its
path and the associated controls.

The comparison callout uses the response's structured diff and eligible
candidate order. It reports combined inputs, ties, probability and
eligibility changes, decision branches, independent care sections,
assessment-only changes and partial results.

Reset restores the captured patient response and initial treatment,
including its graph and attribution. The hook cancels queued and in-flight
work and advances the request epoch so earlier responses cannot restore
overrides. Reset leaves the physician's draft intact.

## Verification

- The Node 22 production build, repository TypeScript check, and all eight
  web test files passed. The build emitted the existing chunk-size warning.
- Chromium and Firefox exercised both proposal hosts using responses
  captured from PostgreSQL-backed tests. Both checked factor focus and
  control linkage, stable graph positions, PANSS-only changes, a combined
  scenario with ties, partial and direct follow-up branches, and reset with
  an outstanding response. Reset made no requests, restored bar endpoints
  to their patient ticks, and preserved the final-plan draft. Both checked
  a 360 px Persian RTL layout with dark theme and reduced motion.
- All 12 tests in the focused attribution, active pathway and simulation
  HTTP suites passed against disposable PostgreSQL 16.
- The repository-wide `make check` stopped on existing Ruff formatting
  findings in `backend/src/x_insight/cases/encounters.py` and
  `backend/tests/test_registration_birth_year.py`.

## Handoff

Step 7 (T4–T6) covers the committed browser suite, broader regression and
performance runs, the final anti-drift review, and API and user-guide
documentation. Step 6 used a temporary browser harness with intercepted
transport. The wide application acceptance run remains open.
