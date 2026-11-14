# Issue 37 — Persian and English interface

**Status:** Analysis and implementation plan only; no application code changed.  
**Date:** 2026-11-05  
**Source inspected:** working tree at `624c4da`. Existing unrelated working-tree changes are outside this issue.

## Finding

The app was designed and implemented as English-only. This is a missing product capability, rather than a broken language selector. The smallest complete fix is a shared frontend language context, English/Persian message catalogs, one reusable language button, and targeted right-to-left styling. Translate presentation at rendering boundaries; keep server contracts and clinical computation unchanged.

The requirement in this issue supersedes the older English-only scope: English is the default, Persian is available, and the language-switching button is physically at the top left in both languages.

## Root cause and evidence

| Evidence | Consequence |
| --- | --- |
| `project-documents/dev/user-requirements.md:7,127` explicitly says English only; `plan.md:27`, `system-architecture.md:7`, `system-design.md:9,422`, and `ui-context.md:13` repeat that scope. | Localization was outside the original implementation requirements. This establishes the documented scope; it does not establish any further historical reason for choosing English. |
| `web/package.json` has React and React DOM as its only runtime dependencies. `web/src/main.tsx:12` mounts `AuthProvider` and `App`, with no language provider. | No shared language state or translation facility exists. A dependency is not necessary to add these for two fixed languages. |
| `web/index.html:2` fixes `<html lang="en">`; no application language/direction switching was found in `web/src`. | The document remains English and left-to-right. Changing this attribute alone cannot translate text. |
| `web/src/app/App.tsx` owns the header for anonymous and authenticated users. Its navigation, loading text, account role and sign-out text are English; its existing toggle is `ThemeToggle`, inside the authenticated controls. | There is no language control, and putting one beside the authenticated theme control alone would omit login. |
| English literals occur throughout `web/src/features`, including labels, status maps, errors, confirmations, placeholders and accessibility names. `features/reasoning/questionLabels.ts` is an English-only presentation map used by History, proposal review, CPT review and signing. | Translating only navigation would leave most workflows English. Shared label helpers and all their callers must participate. |
| Diagnosis and PANSS render `item.prompt`; C-SSRS merges server prompts with local fallback prompts. History renders `field.definition`; Effects also renders definition prompts. Assessment response types include definition IDs/versions. | Some visible English comes from server-owned definitions, so extracting JSX literals alone is insufficient. |
| `features/identity/api.ts` defines `ApiError.code`, `.message` and `.fieldErrors`; feature API clients preserve English response messages. `LoginPage.tsx` and many other screens show these directly. `conflictHint()` also returns English strings. | Translation must include ordinary server errors, field validation and conflict guidance, without changing error codes or HTTP behavior. |
| `backend/src/x_insight/identity/service.py:30` defines the complete English disclaimer and `research-warning-v1`. `ResearchWarning.tsx` displays the server wording; login/session and changed-warning responses carry the warning/version. | The disclaimer needs a complete, version-bound Persian presentation, while acknowledgement remains tied to the server version. |
| `shared/theme.css` and assessment/medication styles use physical `left`/`right`, borders, spacing and text alignment. `admin/audit/AuditPage.tsx` uses browser-default date formatting. | Persian needs explicit RTL support and predictable display formatting. A global `text-align:right` rule is insufficient. |

Patient names already use Unicode letter validation in `features/patients/validation.ts`. The English-only interface is not evidence that Persian patient data requires a database encoding change. This issue does not change the existing name-character policy or ASCII identifier/numeric input contracts.

## Behavior to implement

- Every full application load starts in English (`lang="en"`, `dir="ltr"`), including on a Persian-language browser. One button switches to Persian (`lang="fa"`, `dir="rtl"`) and back immediately.
- Use a native button showing the target language: `فارسی` while English is active and `English` while Persian is active. Give it an accessible action name, visible focus and `type="button"`.
- Keep it physically at the top left of the application header, before and after login, for both roles and both themes. Do not let RTL grid placement move it to the right. Keep the skip link first in keyboard order and prevent overlap with it.
- The research-warning dialog is modal and makes the header inert. Reuse the same button inside the dialog's top-left area while it is open, so language can also change before acknowledgement. Preserve focus containment and the acknowledgement gate.
- Switching re-renders the existing tree. It must preserve the route, login input, clinical form state, pending saves, open panels, running proposal state and signed data. It must not trigger logout, navigation, a reload or a clinical mutation.
- **Minimal persistence policy:** keep language in React memory for the current page lifetime, across login/logout and route changes. Refresh resets to English. Remembering language across reloads/accounts/devices was not requested; do not add storage or a server preference in this implementation.
- Translate application-owned interface text on all existing screens, including printed report headings, dialogs, accessible labels, validation, empty/loading/error/success states and clinical prompt presentations. Preserve the INSIGHT name and standard clinical abbreviations where appropriate.
- Patient-entered notes, plan text, names, signed narrative, imported evidence quotations, filenames, URLs, raw technical diagnostics and externally supplied model prose retain their source text. Translate their surrounding controls and explanatory labels; do not promise automatic translation of arbitrary content.

## Minimal implementation plan

### 1. Add one small shared language layer

Add `web/src/shared/i18n.tsx` for a context/provider, `useLanguage`, and the reusable button. Add `web/src/shared/messages.ts` for the two typed catalogs and small pure lookup/interpolation helpers. Keep non-React helpers importable by the existing Node tests. Use stable semantic message keys, complete sentence templates and named substitutions; avoid sentence fragments, HTML strings and DOM text replacement. Type the Persian catalog against English keys.

Wrap the existing tree once in `main.tsx`, above authentication, and update `document.documentElement.lang/dir` from the language value. Retain the English static HTML default. Do not key the provider, `App`, routes or forms by language: that would remount components and lose draft state.

Use the context only in React components. Pass language explicitly to pure helpers such as `questionLabel`, `conflictHint` and validation-message formatters; do not call hooks from helpers or replace React state with a mutable module-global locale. Preserve existing English helper defaults where that avoids unrelated caller/test changes.

### 2. Translate all presentation boundaries

Replace app-owned English strings and label maps with catalog lookups. Preserve IDs, test IDs, option values, radio names, routes and API payload values. Update `questionLabels.ts` once and propagate language through every existing caller, including helper functions used to assemble status sentences.

For assessment prompts, map by definition ID/version and item ID; for workflow History/Effects use the versioned field/source identity supplied by the response. Check the expected source wording as well when needed to avoid attaching an old translation to changed clinical content. Translate prompts, answer captions, explanatory periods, findings, readiness reasons and bands as presentation only. Do not translate whole API objects or change the released definitions.

Inventory the active definitions/workflow fields actually displayed by the current UI and provide reviewed Persian equivalents. Cover local C-SSRS fallback prompts too. Unsupported future content must retain its original text with a translated “translation unavailable” indication, rather than silently substituting an unrelated prompt. English fallback is defensive behavior, not acceptance for missing translations of currently supported content.

Translate errors at render time using the retained error code/context and field names. Some codes cover multiple messages: inventory those cases and use a scoped known-source-message mapping where the current response lacks a finer discriminator. Preserve the error object and original details. Unknown external/server messages get a translated generic explanation plus their original diagnostic, without inventing a clinical interpretation or hiding actionable information. Do not refactor the duplicated HTTP clients.

Avoid storing translated strings in state: an error already visible when language switches must update too. Retain canonical message keys/parameters or the existing source message/error object and resolve the presentation during rendering. Apply the same rule to save status, confirmation and asynchronous completion messages.

For the disclaimer, include a reviewed, complete Persian translation associated with both `research-warning-v1` and its exact English source. The server remains authoritative for source wording, version and acknowledgement. Use the translation only when both match; otherwise show the complete new server text with the translation-unavailable indication. Never change or acknowledge a version on language switch. Test the existing changed-warning/409 path. Update the frontend comment about server wording to explain this presentation-only mapping. This avoids a new locale API, database field or alternate server acknowledgement path.

### 3. Apply only necessary RTL changes

Use the document direction and logical CSS properties (`margin-inline-start`, `padding-inline-start`, `border-inline-start`, `text-align:start/end`) where existing physical rules express reading order. Adjust only the header placement, affected rules and mixed-direction content. Keep the language button's physical-left placement explicit.

Use `dir="auto"`/`bdi` for user text where appropriate. Isolate IDs, phone numbers, URLs, timestamps, drug/model codes and numeric CPT controls as LTR where required. Preserve probability values, slider behavior, node order and graph coordinates; do not reverse clinical data arrays or graphs to achieve RTL. Check that Persian glyphs render with the existing system-font fallback before adding any font asset or dependency.

For browser-formatted UI dates, pass an explicit selected-language locale. Keep the existing Gregorian calendar, canonical stored timestamps, ASCII numeric entry and identifier rules; Persian language does not imply a Jalali-calendar or Persian-digit input feature. Do not reformat every technical timestamp merely because it is displayed.

### 4. Verify and reconcile current documentation

Run the focused checks below, then change only current English-only scope statements and record actual implementation evidence. Do not rewrite old progress reports or infer clinical validation from translated UI. Persian clinical wording and the complete disclaimer require fluent clinical-language review before accepting the Persian release; this analysis did not provide or validate those translations.

## Complete implementation change boundary

The following is the planned allowlist. Existing files receive presentation-only changes unless explicitly described. This is a capability spanning many screens; minimizing logic and preserving behavior matters more than hiding untranslated screens to reduce the file count.

| Files | Allowed changes |
| --- | --- |
| **New:** `web/src/shared/i18n.tsx`, `web/src/shared/messages.ts` | Shared language state/button and typed bilingual messages, version/source-bound clinical presentation lookups, small formatting helpers. Split neither by feature nor into a plugin system unless actual size makes that necessary. |
| `web/src/main.tsx`, `web/src/app/App.tsx`, `web/src/app/pages.tsx` | Mount provider; insert header button; translate shell, dashboards, route failures and account presentation. |
| `web/src/features/identity/{LoginPage,PasswordForm,PhysiciansPanel,ThemeToggle,ResearchWarning}.tsx` | Translate identity UI; reuse language control inside the modal; retain auth, theme and disclaimer behavior. |
| `web/src/features/identity/api.ts` | Only make the existing presentation helper `conflictHint` language-aware. Leave request parsing, error classes, CSRF and endpoint functions intact. |
| `web/src/features/patients/{DirectoryPage,RegistrationForm}.tsx`, `web/src/features/patients/validation.ts`, `web/src/features/patients/chart/{ChartPage,FollowupBaselinePanel}.tsx` | Translate labels, statuses, prompts and validation output; isolate mixed-direction patient data. Keep validation predicates and serialization unchanged. |
| `web/src/features/encounters/{EncounterPage,DemographicsDraftPanels}.tsx` | Translate wizard/step text, save-state presentation, confirmations and shared form text. |
| `web/src/features/assessments/diagnosis/DiagnosisSection.tsx`, `web/src/features/assessments/panss/PanssSection.tsx`, `web/src/features/assessments/cssrs/CssrsSection.tsx` | Translate worksheet UI and version-bound prompt presentations, labels and findings; preserve server evaluation and existing permitted answers. |
| `web/src/features/history/{HistorySection,EffectsSection}.tsx` | Translate local and server-supplied field presentations and associated guidance. |
| `web/src/features/medications/{MedicationsSection,DdiReportPanel,DdiEvidenceList}.tsx` | Translate medication/DDI controls, coverage/status/severity captions and surrounding evidence guidance; preserve drug identity and quoted evidence. |
| `web/src/features/reasoning/questionLabels.ts`, `web/src/features/reasoning/{ProposalReadinessSummary,ProposalReviewPanel,CptReviewPanel}.tsx` | Language-aware shared question labels, readiness/error messages, transparency and CPT presentation; pass language through pure sentence builders. |
| `web/src/features/plans/{SignPanel,SecondaryPlanPanel,SignedView}.tsx` | Translate controls, signing errors and frozen-record labels without modifying saved clinical narrative or snapshots. |
| `web/src/features/admin/networks/{NetworksPage,GraphView}.tsx`, `web/src/features/admin/provider-settings/ProviderSettingsPage.tsx` | Translate admin controls, graph UI labels and provider guidance/status. Preserve imported node/state identifiers and graph structure. |
| `web/src/features/admin/audit/AuditPage.tsx`, `web/src/features/admin/backups/BackupsPage.tsx`, `web/src/features/admin/restores/RestoresPage.tsx`, `web/src/features/admin/status/StatusPage.tsx`, `web/src/features/admin/exports/{ExportsPage,ReportPage}.tsx` | Translate admin/report interface and localized audit-date presentation. Preserve audit payloads, backup/restore behavior and CSV data. |
| `web/src/shared/theme.css`, `web/src/features/assessments/assessment-worksheet.css`, `web/src/features/assessments/diagnosis/diagnosis.css`, `web/src/features/assessments/panss/panss.css`, `web/src/features/medications/medications.css` | Targeted logical-property replacements, header/button layout and bidi fixes only. No palette or broad design changes. Other CSS files need changes only if verification proves an RTL defect. |
| **New:** `web/tests/i18n.test.mjs`, `e2e/language.spec.ts`; existing `web/tests/question-labels.test.mjs`, `web/tests/patient-validation.test.mjs` | Focused catalog/fallback checks and bilingual behavior tests; update helper assertions only if their interface changes. |
| `e2e/themes.spec.ts`, `e2e/usability.spec.ts` | Only adjust keyboard/header assertions directly affected by the newly inserted accessible control. Preserve the existing English-default tests elsewhere. |
| `project-documents/dev/{user-requirements,plan,system-architecture,system-design,ui-context,tasks}.md` | Replace current English-only statements/traceability with this requirement; add the language behavior and verification reference. Do not rewrite unrelated or historical requirements. |
| `issues/issue-37.md`, `project-documents/dev/progress/s61.md`, `project-documents/dev/progress/README.md` | Record implementation status and checks, with a short index link. |

Conditional boundary: `auth.tsx` and `encounters/useAutosave.ts` may receive the smallest presentation-state adjustment only if retained English messages cannot be localized at their render callers. Do not change session handling, autosave timing, effect dependencies, navigation guards or request behavior. Existing error strings can generally stay canonical and be translated by the consumer. If another file is needed, identify the specific untranslated surface or measured RTL defect before extending this allowlist.

### Explicitly outside this implementation

- No backend, database, migrations, user-language preference, new endpoints or locale headers.
- No new runtime packages, lockfile changes, routing library, translation service, DOM observer or automatic browser-language selection.
- No modifications to released `content/` definitions, BNs, DDI data, workflow packages, prompt files, content hashes or release manifests. Read those to inventory messages, not to translate persisted content in place.
- No changes to scoring, clinical defaults, readiness rules, CPT arithmetic, execution, retries, approvals, signing, research-warning acknowledgement/version, permissions or security.
- No translation of stored patient text, signed records, external prose or diagnostics; no CSV header/schema changes. Existing exports deliberately use stable English headers.
- No Persian-digit acceptance, name-policy expansion, Jalali calendar, new theme, redesign, mobile navigation or unrelated test repairs.

## Verification and acceptance

1. **Small unit checks:** catalog keys and placeholders match between languages; active question labels and displayed clinical definition fields have mappings; source/version mismatch uses the explicit safe fallback; validation outcomes remain identical. Keep the existing unknown-question readable fallback test and add its Persian presentation case. Verify missing required current translations fail the coverage check.
2. **Default and switching:** in Chromium and Firefox, including a Persian browser locale, a new page starts English/LTR. Button changes visible and accessible text to Persian/RTL and back on login, both dashboards and representative clinical/admin screens. Verify physical top-left bounds in both directions, both themes and the existing 200% desktop zoom case.
3. **Modal and async states:** change language inside the unacknowledged disclaimer, confirm complete text and focus behavior, exercise changed-version/409 and unsupported-version fallback, then acknowledge the exact server version. Display an error first, switch language, and verify its presentation updates; repeat while a save or request is pending.
4. **Clinical preservation:** enter an unsaved registration/follow-up answer and plan text, change language and verify no remount/data loss or language-triggered mutation. Compare request payloads, revisions, assessment results, probabilities and signing eligibility with the English path. Exercise History, Effects, medication/DDI review, proposal/CPT review and chart/report display using synthetic data.
5. **Coverage and layout:** inspect every route listed in the allowlist for untranslated app-owned text; review all currently displayed clinical prompt translations. Check mixed Persian/English names, IDs with leading zeros, phone numbers, drug names, graph labels, CPT sliders/tables and printable reports for bidi errors, clipping and horizontal overflow. Keep print chrome hidden, including the new button.
6. **Commands:** `make test-web`, `npm --prefix web run build`, and `make test-e2e TEST=e2e/language.spec.ts`. Run the directly affected theme/usability and clinical workflow browser suites on the existing isolated test stack. Record exact passes/failures and unavailable prerequisites; do not repair unrelated historical suite failures under this issue.

Acceptance requires full translation coverage for the current supported interface and definitions, reviewed Persian clinical/disclaimer wording, an English default, an operable top-left switch, correct RTL presentation and unchanged clinical/API behavior. An untranslated current screen or prompt is incomplete implementation, even if switching the header works.

## Analysis limits and handoff

This report is based on source, current requirements, repository memory verified against relevant source, and the S61 shared-UI handoff. No browser walkthrough, build, test suite or translation review ran during this analysis; runtime acceptance remains open. The historical product reason beyond the documented English-only requirement is unknown.

Implement only after this analysis task: the current request authorizes this report and progress bookkeeping, not the proposed code changes. Keep the existing unrelated working-tree edits intact. Record implementation evidence in S61 and link it from the progress index; do not treat this plan as completed work or a clinical release.
