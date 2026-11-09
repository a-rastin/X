# New case Package G partial checkpoint — 2026-11-09

## Status: incomplete

The new-case wizard, registration birth-year control, forms, navigation guards,
settings and proposal editor are not implemented. The Package G exit criteria
and browser acceptance have not been met. Package B physician availability
settings and Package F proposal persistence, editing and signing remain absent;
see the Package E and F checkpoints before continuing.

## Implemented

The encounter UI now reads the server-owned `workflow_version` and opens the
existing wizard only for `legacy-v1`. New-case, missing and unknown workflow
identities show a bilingual unavailable message without mounting the legacy
editor or changing the draft. This prevents a new-case draft from presenting
legacy assessment, medication and proposal controls while its dedicated UI is
unfinished. The current age-based registration form remains unchanged and does
not create new-case drafts.

## Verification

- `npm --prefix web run build`: passed; Vite reported its existing large-chunk warning.
- `npm --prefix web run test-web`: 20 passed, including the workflow identity boundary and bilingual catalog checks.
- No browser, end-to-end, backend, clinical or deployment acceptance was run.

## Handoff

Implement the plan's Package G against the authoritative state/preview/navigation
contracts in `docs/new-case-state.md`, replacing the temporary unavailable view
with a separately guarded new-case wizard. Finish B/F and their revision and
signing contracts before presenting a complete proposal or confirmation. Test
both paths, reloads, direct navigation, accessibility and English/Persian in
Chromium and Firefox as specified by the plan; do not remove server guards or
route new-case drafts through legacy controls to simulate completion.
