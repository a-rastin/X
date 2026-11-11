# UI/UX analysis and wireframe — 2026-11-11

## Scope and result

User requested a professional, academic UI/UX direction using Astryx, with a
high-fidelity wireframe for confirmation before project-wide implementation.
Functionality and backend changes are expressly excluded.

Inspected the current app shell/routing, shared styles/i18n, directory/dashboard,
New case wizard/review, PANSS behavior, existing feature surfaces, architecture
memory, and relevant progress handoffs. Current source confirms the separate
New case and legacy/follow-up workflows and existing bilingual UI.

Created an isolated Astryx prototype in
[`project-documents/design/ui-wireframe`](../../design/ui-wireframe/README.md).
Its [self-contained HTML](../../design/ui-wireframe/wireframe.html) includes
seven representative views with local interactions, English/Persian RTL, and
light/dark themes. The README contains the source-backed UI map, design rationale,
scope limits, reproduction commands, and implementation boundaries.

No production app/backend files or root/web manifests were changed. No API
requests, real patient data, model execution, signing, commits, or publication.
Routine design choices remain in the design report; no new Keep the Why entry
was warranted for an unapproved visual proposal.

## Verification

See the prototype's [`verification/results.json`](../../design/ui-wireframe/verification/results.json).
TypeScript and the static build passed. Chromium checks exercised local search,
empty/error/retry, and rating selection; checked seven English/light views at
1440, 728, and 390 CSS pixels and five Persian/dark views at the same widths.
Automated accessibility checks cover patient directory, PANSS, proposal, and
sign-in in English/light and Persian/dark. No page errors or external requests
were observed in the final run. Screenshots are stored beside the JSON.

Vite's native Rolldown binding raised SIGILL during initial prototype build.
System coredump inspection identified the crashing module; the cause was not
established. The prototype uses esbuild instead; application tooling is intact.
The inline export removes bundled viewport-height units to avoid resize loops.

## Handoff

**Await user design confirmation. Do not treat prototype creation as permission
for a production rollout.** Some controls are preview shortcuts; unrepresented
screens, real saves, scoring, interaction checks, provider calls, and signing
are intentionally absent. The current clinical workflows and all existing gates
must remain intact during any later presentation-only work.

The narrow preview uses its screen menu; production must retain accessible app
navigation. The review-toolbar language/theme controls do not prescribe moving
the actual app's language switch. Persian clinical wording requires the existing
translation sources and clinical review during any rollout.

No clinical validation, full app regression suite, live backend acceptance,
Firefox coverage, or completion of all production screen designs is claimed.
