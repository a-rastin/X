# X-INSIGHT UI design review

Open [the interactive wireframe](wireframe.html). It is a self-contained HTML file;
no server, sign-in, network, or app installation is needed to view it. Use the
screen menu, language button, and light/dark button above the app frame.

**Status: direction confirmed and implemented in the application.** This standalone
prototype remains available as the approved reference. All records in it are
fictional; it is a visual prototype, not a clinical application.

## Review scope

Seven representative views establish a reusable design vocabulary:

| View | What to assess |
| --- | --- |
| Patients | Search/filter hierarchy, compact record rows, clinical versus archive status, row actions |
| PANSS | Encounter context, current New case step sequence, subscale navigation, explicit ratings, incomplete summary |
| Medications | Selection/evidence split, coverage disclosure, source identity |
| Proposal | Read-only original alongside editable physician plan, disclosure hierarchy, signing entry |
| Signed chart | Encounter history, immutable document presentation, separate original/addenda |
| Administration | Separate administrator navigation, connection fields, disabled pre-save provider test |
| Sign in | Product purpose, readable form, visible research status |

The outer review toolbar is a prototype control, not proposed product navigation.
Navigation is deliberately abbreviated: some actions jump to representative
screens, only a subset of questionnaire content is present, and provider fields
are display-only. A jump does not propose skipping real workflow steps or gates.
Unsupported sidebar entries are disabled only in the prototype. No actual saving,
assessment evaluation, interaction check, provider request, or signing occurs.
The narrow view uses the review menu to reach screens; a production rollout must
preserve accessible app navigation at narrow widths.

## Design direction

A quiet clinical workspace with document-oriented typography, consistent encounter
context, and explicit evidence states. Retain the existing teal identity rather
than invent a new clinical color language. Avoid decorative statistics and
repeated dashboard cards: the work is assessment and review of individual records.

| Role | Light | Dark |
| --- | --- | --- |
| Primary action | `#06786D` | `#2DD4BF` |
| Primary text | `#172D3B` | `#E5EDF4` |
| Supporting text | `#536776` | `#AFBFCD` |
| Page | `#F4F7F9` | `#101C27` |
| Surface | `#FFFFFF` | `#172634` |
| Border | `#D3DEE5` | `#405260` |

Georgia headings give the English view an academic document character. Arial body
text and Tahoma Persian fallbacks prioritize legibility and require no downloads.
Persian headings use sans-serif text. The type scale starts at 14, with 28px page
headings, 19px section headings, and 12px metadata. Colors and dimensions live in
an isolated Astryx theme; no application theme tokens have been changed.

The layout follows Astryx's `shell-side-nav`, `table`, and `form-wizard` template
frames, using AppShell, SideNav, Layout, Section, Stack, Table, and form components.
The assessment summary moves below the worksheet at narrower widths; the proposal
columns stack. Tables retain horizontal scrolling inside their region when needed.

## Verified codebase map

This investigation focused on UI architecture and behavior boundaries. It was not
a full backend audit or live deployment validation.

| Area | Current source and implications |
| --- | --- |
| App entry and shell | `web/src/main.tsx`, `web/src/app/App.tsx`: React 19/Vite, shared header, role-sensitive navigation, research acknowledgement gate |
| Routing | `web/src/app/router.ts`, `navigationGuard.ts`: custom hash routes; save-before-navigation must survive a visual refactor |
| Presentation | `web/src/shared/theme.css` plus feature CSS: shared semantic themes and separate assessment, history, medication, New case layouts |
| Language | `web/src/shared/i18n.tsx`, `messages.ts`: English default, session-local language choice, document language/direction, translated content, isolated LTR identifiers |
| Directory/dashboard | `features/patients/DirectoryPage.tsx`, `app/pages.tsx`: shared patient pool; server search/filter/pagination; physicians register/start follow-up, admins read only |
| New case | `features/new-case/NewCaseWizard.tsx`: after patient registration, Diagnosis → PANSS → Harm assessment → History → Medications → Considerations → Proposal |
| New case review | `NewCasePlanPage.tsx`: fixed-model output, immutable original, separate autosaved final plan, confirmation and freshness checks |
| Existing encounters | `features/encounters/EncounterPage.tsx`, `workflow.ts`: legacy workflow remains separate; follow-up includes adverse effects |
| Assessments | `features/assessments/`: saved answers and server evaluation; unanswered PANSS items are not zero; incomplete total is absent |
| Medications | `features/medications/`, `features/new-case/NewCaseSections.tsx`: existing DDInter path versus New case pinned Lexicomp path; source/coverage semantics must remain distinct |
| Chart and signing | `features/patients/chart/`, `features/plans/`: immutable signed snapshots, separate proposals/plans, attributed addenda |
| Administration | `features/admin/`: provider settings, versions/networks, audit, exports, backups, restores, status; retain separate authorization and operation semantics |
| Backend | `backend/src/x_insight/app.py` and module routers: FastAPI `/api/v1`, PostgreSQL, session/CSRF/revision/idempotency contracts; outside redesign scope |
| Reasoning | `backend/src/x_insight/reasoning/`, `models/`, `cases/new_case*`: legacy queued LLM/CPT path versus deterministic New case fixed-model path; outside redesign scope |

Source paths above are relative to the repository root; consult the current
[codebase overview](../../../codebase-memory/overview.md) and
[New case state](../../../docs/new-case-state.md) for deeper architecture.
The current source supersedes older progress reports describing an English-only
interface or the earlier registration sequence.

## UI opportunities and rollout boundary

The shell currently places many administrator destinations in a wrapping top
navigation. A grouped side rail gives those destinations a stable location.
Feature screens use a mixture of presentation conventions; shared page headers,
field spacing, tables, action rows, and disclosure patterns can make them coherent.
Long clinical forms benefit from clear assessment groups and a separate readout;
proposal review benefits from stronger original-versus-final-plan separation.
These are source-backed design observations, not measured user-research findings.

After design approval, implementation should begin with shared visual tokens and
shell presentation, then adapt one representative feature at a time. Preserve:

- Routes, server payloads, autosave timing, conflict handling, and unsaved-change guards.
- Clinical questions, options, defaults, validation, scoring, and conditional visibility.
- Role restrictions, research acknowledgement, immutable records, signing and freshness checks.
- All loading/error/empty/coverage states, urgent alerts, and readable status text.
- English/Persian translation sources, language-switch behavior, theme persistence,
  keyboard operation, print output, and existing accessible names.

The prototype relocates language/theme controls into its review toolbar only.
Their production behavior and placement need explicit review against the current
bilingual UI contract before implementation. Persian clinical wording in this
prototype is illustrative and has not received clinical translation review.

## Rebuild and verification

From this directory:

```sh
npm ci
npm run typecheck
npm run build
npm run verify
```

Verification reuses the repository's installed Playwright/axe dependencies and
system Chromium (`CHROMIUM_PATH` overrides `/usr/bin/chromium`). It opens the local
file directly. See [verification/results.json](verification/results.json) and the
PNG screen captures in that directory for the recorded run.

The build uses esbuild because Vite's native Rolldown binding crashed with SIGILL
on this machine during prototype setup. The coredump located the crash inside
`rolldown-binding.linux-x64-gnu.node`; no CPU/OS root cause is claimed. This affects
only the prototype build choice. Application tooling was not edited.

The self-contained export replaces bundled viewport-height rules with a fixed
900px reference so the inline review frame can size itself from content. The
AppShell itself uses content-driven height. This export accommodation is not a
proposal for the app's production layout.

## Implementation

The approved direction was applied to the application; see the latest UI
implementation handoff in the progress index for verification and limits.
