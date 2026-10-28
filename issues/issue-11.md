# Issue 11 — Discard draft should be a button, not a card

Date: 2026-10-28
Status: implemented; browser verification pending

## Requirement and scope

On every persisted new-patient registration step, show a single **Discard draft** button at the bottom instead of a titled explanatory box. Keep the explicit confirmation dialog after clicking the button. “Only a button” describes the page presentation; it does not authorize immediate destructive execution.

Repository root is already `x/`, so this document is `x/issues/issue-11.md`.

## Root cause

The shared `DraftEditor` in [EncounterPage.tsx](../web/src/features/encounters/EncounterPage.tsx#L1013) unconditionally renders a `<section className="xi-card" aria-labelledby="draft-danger-heading">` below the step-specific content. It contains a “Discard draft” heading, a paragraph describing patient-slot release and the revision, and the actual button. Because this markup sits outside the current-step branches, it appears on every persisted registration step: Register patient, Diagnosis, PANSS, C-SSRS, History, Medications, and Proposal review.

[theme.css:376](../web/src/shared/theme.css#L376) gives `.xi-card` a background, border, rounded corners, shadow, padding, and bottom margin. These styles make the wrapper a visible box. The button itself is already a native `type="button"` using `xi-btn xi-btn-secondary`. The problem is the extra card markup, not a missing button or a backend defect. Changing shared card styles would affect unrelated screens without removing the redundant heading and text.

The same editor serves follow-up drafts, which therefore also show this card. The unsaved `#/patients/new` page uses [RegistrationForm.tsx](../web/src/features/patients/RegistrationForm.tsx), which has no discard control: no server draft exists until valid patient creation. Do not add a discard API action to that pre-creation form.

## Historical explanation and evidence limits

Commit `97b41f1` (`feat(S07): persist, resume, and discard author-owned drafts`) introduced the titled card with this explanatory copy and button. The [S07 report](../project-documents/dev/progress/s07.md) documents explicit confirmation, current-revision protection, and preserving the patient while releasing the draft slot. It does not establish why a permanent card was preferred over a standalone button; that original visual rationale is unknown.

Evidence: the requested presentation is confirmed by the user; the rendering mechanism and historical markup are corroborated by static source and Git inspection. The original analysis did not include a running-browser reproduction. This is a focused analysis of the registration/discard path, not an audit of the entire application.

## Fix plan

1. Change only the discard presentation in `DraftEditor`. For `encounter.kind === "registration"`, render the existing button without the `xi-card` section, heading, or explanatory paragraph. Keep it at its current bottom-of-page position before Back to directory. Use existing layout spacing if needed, without a bordered or filled container.
2. Preserve follow-up presentation with a small local conditional, since the supplied requirement concerns adding new patients. Share the existing button markup/handler locally if useful; do not create a new shared component or redesign the footer. A global follow-up presentation change needs separate scope.
3. Preserve the button label, secondary styling, native button semantics, and handler resetting confirmation/error state before opening the existing dialog. Remove any registration-only dangling `aria-labelledby` reference with the heading.
4. Leave the confirmation dialog and discard implementation intact. The dialog already explains patient preservation, the revision and irreversibility. Keep checkbox confirmation, Cancel, busy/error handling, focus behavior, autosave flush, revision fencing, session-expiry handling and discarded-state feedback.
5. Extend the existing browser coverage in `e2e/autosave.spec.ts` to check the compact presentation across all seven persisted registration steps, then exercise the existing confirmation flow. Use valid saved fixtures for guarded transitions, or supported step links; do not weaken clinical navigation guards to make the test pass.
6. Run the frontend build and root TypeScript check, then the focused autosave browser suite in Chromium and Firefox with the app/database available. Record actual results and any environment blockers in this issue and S07 handoff.

## Implementation allowlist — everything that needs to change

| File | Allowed change |
| --- | --- |
| `web/src/features/encounters/EncounterPage.tsx` | Registration discard wrapper/heading/paragraph only, plus minimal local conditional/layout needed to keep one standalone button and preserve follow-up behavior. |
| `e2e/autosave.spec.ts` | Focused regression assertions for button-only registration presentation and continued confirmation behavior. |
| `issues/issue-11.md` | Implementation status and verification evidence after the fix. |
| `project-documents/dev/progress/s07.md` | Append a concise implementation/verification handoff. |
| `project-documents/dev/progress/README.md` | Keep a short link to the issue/handoff current. |

No CSS change is expected: existing button and layout classes suffice. Do not change shared `.xi-card` styling, assessment components, wizard steps/routes/guards, registration creation or success navigation, working notes, Back to directory behavior, `useAutosave`, encounter API contracts, backend discard/auth/audit logic, database schema, signing, dependencies, or unrelated formatting. No new component library, dialog implementation, migration, or backend test is needed for this presentation change.

## Acceptance and regression checks

- Each of the seven persisted registration steps shows exactly one visible “Discard draft” button. No “Discard draft” page heading, enclosing discard card, or persistent slot/revision explanation remains. Other cards remain styled normally.
- Check both themes and desktop/narrow layouts: the button has readable contrast, normal spacing, keyboard focus and no horizontal overflow.
- Clicking opens the existing named confirmation dialog without discarding. Confirm remains disabled until its checkbox is checked; Cancel keeps the draft and permits reopening.
- Confirmed discard still flushes pending edits, uses the current revision, frees the slot and keeps the patient. Retain existing stale-revision/error behavior and regression coverage; avoid expanding this into a backend rewrite.
- Follow-up discard presentation and behavior remain unchanged. The pre-creation registration page still has no server-draft discard action.

## Handoff

The registration editor now renders the existing discard button without the card, heading, or explanatory text; follow-up retains the card and the confirmation flow is unchanged. Focused browser assertions cover the seven registration steps and cancel/reopen behavior.

The frontend build and root TypeScript check passed. The focused autosave browser suite could not connect to `localhost:5173`: four tests failed during setup and 14 did not run after the suite was interrupted. Chromium and Firefox browser verification, including themes and narrow layouts, remains pending with the application and database available.
