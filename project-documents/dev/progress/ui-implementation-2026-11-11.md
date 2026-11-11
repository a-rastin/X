# Academic UI implementation — 2026-11-11

## Scope and outcome

The user confirmed the [Astryx wireframe](../../design/ui-wireframe/README.md)
and requested application-wide implementation without functionality or backend
changes. The existing app now uses the approved teal/slate palette, academic
headings, responsive side navigation, compact table treatments and shared forms.
Astryx supplies the theme and navigation components. The language control remains
physically top-left, including Persian RTL; all role-specific destinations remain
available and wrap on narrow screens. Existing route and unsaved-change guards
remain in place.

The patient directory places registration beside its heading. New case shows
patient identity and a numbered step track, uses the existing patient fetch, gives
medication selection a dedicated surface, and presents the immutable original
proposal beside the editable final plan where space permits. PANSS places its
subscale outline above the worksheet and retains the existing separate review
rail. The legacy wizard uses the same visual step treatment without changing
its links or step numbering. Shared theme/form/table styling carries the visual
direction into sign-in, charts, administration, legacy workflows and other
screens; feature-specific clinical controls and backend endpoints are unchanged.

The original prototype and its prior progress report are retained. The local
codebase-memory summaries for the app shell, shared theme and New case wizard
were refreshed. The palette change triggered the existing revisit condition in
`context/ui-contrast.md`, so that entry is marked `needs-review`; its documented
primary action values were preserved and browser contrast checks passed. The
optional Keep the Why linter was not run because it is not installed.

## Verification

- `make check`: passed (ruff formatting/lint, mypy, web production build and root
  TypeScript); Vite reports its large-chunk advisory.
- `npm run test-web`: 22 passed.
- Chromium, a freshly migrated disposable PostgreSQL database and bundled content:
  four New case journeys passed (English/Persian × oral/LAI), including registration,
  assessments, proposal, save/reload and signing. The English oral journey passed
  again after the final medication styling. One legacy PANSS incomplete-form check
  passed after updating its previously stale draft-save-status assertion.
- Chromium: the focused three-test mocked navigation/keyboard/axe suite and the
  admin 200% navigation check passed; the keyboard/axe case passed again after the
  final shell adjustment. English and Persian, light and dark, admin and physician,
  and narrow navigation were exercised.
- Theme/language regression run: 12 passed initially. A Chromium target crash in
  the unsent-registration language case passed on isolated rerun. Two older theme
  contrast probes omitted the now-required physician name; after updating their
  fixtures both passed. The old 200% navigation probe was updated to include the
  existing Provider link and passed. The expected dark canvas value was updated to
  the approved palette.

No backend source, clinical algorithms, database schema, API contract, storage,
assessment options, signing gates, provider behavior or server permissions were
changed. No Firefox run or full repository browser suite was performed. The test
database and server processes were isolated from the shared database and were removed after verification.

## Handoff

The generated Astryx theme files under `web/src/shared/astryx/built/` are compiled
from `academic.ts`; rebuild with `cd web && astryx theme build
src/shared/astryx/academic.ts --out src/shared/astryx/built/academic.css` after
changing that source. Existing app `--*` semantic tokens still serve feature CSS;
keep them aligned with the Astryx theme. The existing release and clinical
validation status is unchanged. Confirm the palette revisit entry and run broader
browser/deployed acceptance if those are required for a release.

Commit preparation: `make check` and `cd web && npm run test-web` passed again;
Vite continued to report its large-chunk advisory. The generated wireframe run log
was left outside the commit.
