# Bayesian reasoning charts — 2026-11-18

Step 4 (V1–V3) adds two chart components to the Reasoning Explorer feature.
The probability comparison uses a fixed 0–100% scale, a dotted patient tick,
and a solid scenario bar for each modeled treatment. Eligible ranks and
exclusion badges are separate from the raw probabilities. The compact view
includes both candidate lists, the selected treatment, and eligibility
changes. Settled rows are ordered by treatment ID. During interaction,
existing rows retain that order and newly relevant rows append.

The selected-treatment tornado uses the pinned model reference as its
common origin. Both sides share a symmetric scale in percentage points,
with outlined patient marks and filled scenario marks. The reference,
posteriors, and reconstruction are shown above the factor rows. Focus and
selection expose the effective factor value and the source paths supplied
by the backend graph. Zero contributions can be expanded to show all
16 parents. Both charts provide English and Persian labels, LTR axes,
keyboard selection, and a table of values.

## Verification

- All four simulation HTTP tests passed on disposable PostgreSQL 16.
  A temporary component preview rendered the responses captured from those
  tests, including New case baseline, QTc change and LAI transition, and
  follow-up non-model and oral-replacement decisions.
- Chromium and Firefox checks passed for all 22 treatment coordinates,
  fixed patient ticks, zero vs. missing posteriors, signed factor geometry,
  stable row order during interaction, keyboard selection, and factor
  provenance. A separate zero-contribution fixture exercised the factor
  disclosure. Both tables and axe checks passed at 360 px in Persian RTL
  with the dark theme and reduced motion.
- `npm --prefix web run test-web` passed all eight test files.
  `npm --prefix web run build` passed with the existing Vite chunk-size warning.

## Handoff

Step 5 (S1–S3) mounts these components in the shared proposal page and
owns the selected treatment, selected factor, and interaction flag.
Use one coherent `SimulationResponse` for both charts. Key the workspace
by the displayed proposal and encounter. The component preview exercised
the chart rendering directly. The proposal-page flow, live controls,
request cancellation and reset remain later steps.
