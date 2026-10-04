# UI Context

Aligned 2026-10-03 to [user-requirements.md](user-requirements.md), [system-design.md](system-design.md), and [system-architecture.md](system-architecture.md). Visual tokens below are existing design references; they do not establish implemented components or completed accessibility checks. The required workflows and permissions take precedence over older archive/reference behavior.

## Product Identity and Branding

The product name is **X-INSIGHT**.

Use `X-INSIGHT` for the principal product wordmark. Product-logo treatments should use the uppercase form.

## Current Design Character

X-INSIGHT is an English-only research prototype and desktop-first decision-support workspace for psychiatrists using current Chrome/Firefox. Its dominant visual language is:

- bright and predominantly light;
- white or near-white clinical surfaces;
- dark neutral text;
- restrained teal for primary actions and selection;
- explicit semantic colors for urgent, warning, normal, follow-up, and informational states;
- compact, information-dense clinical workspaces;
- wider spacing on authentication and initial-entry states;
- clear clinician review, provenance, and safety messaging;
- minimal decorative imagery.

The visual tone should be organized, calm, serious, and modern. Avoid playful illustration, decorative gradients, glassmorphism, excessive blur, neon colors, oversized marketing typography inside clinical workspaces, or consumer-wellness styling.

## Theme

Use color sparingly:

- Teal is reserved for primary actions, active navigation, selected controls, focus indicators, and selected clinical metrics.
- Clinical state colors are restricted to text, icons, badges, borders, and narrow accent stripes. Do not use them as large page or card fills.
- Primary body copy always uses the ink tokens, not teal.
- Clinical dashboards may be information-dense; patient-facing surfaces, if added, must use more whitespace and larger touch targets.

### Theme-mode status

FR-03 and NFR-03 require a functional dark/light toggle. The existing reference palette below supplies light tokens only; the dark palette is an implementation design task, not an unresolved product-scope decision.

- Define deliberate semantic tokens for both themes; do not invert light colors mechanically.
- Persist the user's theme preference and expose the required toggle.
- Validate contrast, focus, slider states, comparison tables and print readability in both themes during implementation.
- The values below are visual references, not proof that all text/control combinations are accessible.

## Canonical Color System

The canonical palette is the teal/neutral system used in the shared design references and implemented most directly by Dashboard, Diagnosis, Severity, and Suicide Risk.

Use repository-aligned token names:

```css
:root {
  --primary: #0A9E8F;
  --primary-hover: #088A7D;
  --primary-light: #E6F6F5;
  --on-primary: #FFFFFF;

  --ink: #111827;
  --ink-muted: #6B7280;
  --ink-subtle: #9CA3AF;

  --canvas: #FFFFFF;
  --surface-1: #F9FAFB;
  --surface-2: #F3F4F6;

  --border: #E5E7EB;
  --border-strong: #D1D5DB;

  --urgent: #DC2626;
  --urgent-bg: #FEF2F2;

  --warning: #D97706;
  --warning-bg: #FFFBEB;

  --normal: #059669;
  --normal-bg: #ECFDF5;

  --follow-up: #7C3AED;
  --follow-up-bg: #F5F3FF;

  --info: #0284C7;
  --info-bg: #F0F9FF;
}
```

### Color roles

| Role                                      | Token                            | Use                                                |
| ----------------------------------------- | -------------------------------- | -------------------------------------------------- |
| Main page and card background             | `--canvas`                       | Primary reading surface                            |
| Sidebar, inset region, alternate panel    | `--surface-1`                    | Low-emphasis structure                             |
| Hover, disabled, selected-neutral surface | `--surface-2`                    | Secondary state                                    |
| Main text                                 | `--ink`                          | Body copy, headings, values                        |
| Supporting text                           | `--ink-muted`                    | Instructions, metadata, helper text                |
| Placeholder and low-emphasis text         | `--ink-subtle`                   | Disabled and tertiary information                  |
| Primary action and selected state         | `--primary`                      | CTA, selected navigation, focus, progress          |
| Primary hover                             | `--primary-hover`                | Pointer hover and pressed emphasis                 |
| Soft selected state                       | `--primary-light`                | Selected pills, tags, metric accents               |
| Critical state                            | `--urgent` / `--urgent-bg`       | Urgent finding, destructive action, blocking error |
| Caution state                             | `--warning` / `--warning-bg`     | Review required, stale or incomplete data          |
| Normal state                              | `--normal` / `--normal-bg`       | Complete, available, within range                  |
| Follow-up state                           | `--follow-up` / `--follow-up-bg` | Ongoing care or scheduled follow-up                |
| Informational state                       | `--info` / `--info-bg`           | Neutral guidance and provenance                    |

### Color usage rules

- Use teal for primary actions, active navigation, selected controls, progress, and focus indicators.
- Use `--ink` for normal body text. Do not use teal as general paragraph text.
- Pair every clinical state color with visible text and, where practical, an icon or shape.
- Prefer a state-colored border, icon, badge, or narrow left stripe over a large saturated fill.
- Light semantic background tints are acceptable for banners and safety cards when the state must remain continuously visible.
- Never use red and green as the only distinction between two clinical outcomes.

### Contrast constraint

`#0A9E8F` against white is approximately `3.33:1`, insufficient for ordinary small text, including white button labels. Select a darker accessible action fill or compliant label treatment and verify the actual rendered sizes/contrast; boldness alone does not fix small text. Use readable ink-compatible state text and explicit noncolor cues. The existing palette is not a completed accessibility verification.



## Typography

```css
:root {
  --font-sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace;
}
```

### Type roles

| Role                  | Size and weight                               | Use                                                        |
| --------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| Major workspace title | `28–32px`, `600–700`, line-height `1.15–1.25` | Dashboard, risk assessment, major standalone workspace     |
| Module/page title     | `20–26px`, `600–700`                          | Diagnosis, Severity, Treatment Plan sections               |
| Section title         | `16–20px`, `600–700`                          | Cards, form sections, safety panels                        |
| Clinical body         | `14–15px`, `400`, line-height `1.5–1.65`      | Instructions, descriptions, findings                       |
| Label/caption         | `11–13px`, `600–800`                          | Field labels, metadata, table headers, status kickers      |
| Numeric/code data     | `12–15px`, mono                               | Patient IDs, assessed scores, exact CPT percentages, versions and timestamps |
| Large score/metric    | `22–32px`, mono, `600–700`                    | Completed PANSS totals, supplied assessment findings and dashboard metrics |

Uppercase labels are used for compact metadata and section kickers. Keep tracking restrained; the implemented repository frequently uses uppercase labels without large letter spacing.

## Border Radius

```css
:root {
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-pill: 9999px;
}
```

Implementation guidance:

- Dense buttons and inputs may use `6px` where matching Dashboard, DDI Checker, Medical History, or Suicide Risk.
- Standard controls should normally use `8px`.
- Main clinical cards should use `8px` or `12px`.
- Major overlays may use `10–16px`.
- Status pills, score choices, and compact chips use pill radius.
- Do not introduce highly rounded consumer-app cards or mixed arbitrary radii within one surface.

## Spacing, Elevation, and Motion

### Spacing scale

```css
:root {
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;
  --space-20: 80px;
  --space-24: 96px;
}
```

Use an 8px base rhythm. Use 4px for internal micro-gaps, not for main page spacing.

Typical repository values:

- page padding: `16–28px` on dense workspaces;
- card padding: `14–24px`;
- primary form/card padding: `24–32px`;
- column gap: `18–24px`;
- mobile page padding: `10–16px`;
- section separation: `16–28px`.

### Shadows

```css
:root {
  --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-elevated: 0 4px 12px rgba(0, 0, 0, 0.08);
  --shadow-overlay: 0 20px 60px rgba(0, 0, 0, 0.12);
}
```

Use subtle borders as the primary surface separator. Shadows should remain restrained. Do not use large floating shadows on every card.

### Motion

```css
:root {
  --motion-fast: 100ms;
  --motion-base: 180ms;
  --motion-slow: 300ms;
  --motion-easing: cubic-bezier(0.4, 0, 0.2, 1);
  --motion-decelerate: cubic-bezier(0, 0, 0.2, 1);
}
```

- Use `100ms` for immediate selection feedback.

- Use `180ms` for hover, border, background, and ordinary state changes.

- Use `300ms` only for deliberate progress or expansion.

- Use content-shaped skeletons where the repository already does so.

- Honor `prefers-reduced-motion` by suppressing shimmer and movement.

- Urgent information must not disappear automatically.

- Use semantic HTML as the baseline contract.

- Reuse the module's existing framework rather than introducing a second UI runtime.

- Do not introduce a new cross-project component library as an incidental change.

- Keep browser URLs relative to the gateway.

- In unified deployment, use the gateway-owned application navigation for the `X-INSIGHT` wordmark, role-appropriate top-level routes, current-route state, authenticated identity, and sign-out. Module-local headers may retain workflow context, but must not reproduce role routing or authentication behavior.

- Scope CSS to the module root when embedding or when selector collision is possible.

- Treat embeddability as module-specific. Diagnosis explicitly supports an embedded root and suppresses standalone chrome; do not assume every module already has the same mount/unmount contract.

## Required workflow and access presentation

- After every successful physician login display: “This application is a research prototype and must not be used as the sole basis for treating patients.” Register displays “Contact administrator”.
- Shared surfaces show demographics and signed records. Only the active draft author can view clinical draft content and derived probabilities/results or edit, adjust/reset/retry, accept and sign it. Enforce this on the server as well as through controls.
- One patient has at most one open draft. Another physician sees a generic conflict, without draft clinical details; the author can resume. Retained deactivated-author drafts continue occupying that slot.
- Registration and follow-up lead from saved assessments/history to the original proposal, optional CPT adjustment/comparison, acceptance and signed secondary plan. Notes are attributed/timestamped on every page and never affect algorithms.
- PANSS/C-SSRS start unanswered; skipping is “not assessed”. No default minimum scores or invented risk score. Diagnosis bypass needs no reason; below-threshold treatment generation carries a warning.
- Medications come from the bundled demo catalog only, without regimen/status fields or free-text additions. Show “coverage unavailable” for catalog drugs/pairs lacking local interaction coverage.
- Show saving/saved/failed states; only server-acknowledged values are durable. Explicit discard needs confirmation. Signed clinical content, plans and accepted CPTs/results are read-only. Any active physician may append an independently attributed, dated addendum to any signed encounter.
- Carry forward provisional archive behavior, discarded-content retention and physician-print permission from system-design.md §15; visual context does not resolve those policies.

## Complete CPT probability review

### Panel content and accessibility

For each successfully completed question, display its question/recommendation, pinned network version and dedicated CPT panel. A failed original question has no adjustable baseline. Keep every CPT value reachable, including root distributions and large tables, using node/full-parent-state-row grouping, folding/search/pagination as needed without omitting values.

Each state has a keyboard-adjustable 0%–100% slider, exact original/current percentage readouts, visible percentage-point difference and row total. Original values are “LLM-estimated”; identify physician-adjusted values, including automatically redistributed states. A one-state row shows 100% and explains the mathematical constraint. Network output probabilities are read-only results, not sliders. Proposed numerical precision/step comes from system-design.md §§7.4/8.1, not from visual rounding.

Show redistribution immediately: preserve the selected value and allocate the remainder among other states in the same row proportionally to their immediately preceding values, or equally when their prior total is zero. Other rows stay unchanged. Display canonical values whose total is 100%, with visible changes to other states. The application is authoritative for deterministic redistribution.

### Comparison and calculation state

Show original and latest successfully adjusted output/recommendation side by side. A probability edit need not change recommendation wording. Keep the immutable original proposal, adjusted templated recommendations and physician-authored secondary plan distinguishable.

Use explicit text for **unchanged**, **recalculating**, **successfully recalculated**, or **failed**, plus a separate **out of date** input-freshness indicator. Keep sliders responsive during local calculation. If previous output is displayed while newer values are unsolved, label it “earlier revision”; never imply it belongs to current CPTs. Ignore older responses by revision identifiers.

Completed adjustments and their calculation state autosave and survive page navigation/resume/failure. Per-question “Reset to original values” restores that baseline and result without LLM/MCP calls or deleting history; other panels stay unchanged. Failed local calculations retain adjusted values and prior successful results with clear error and local Retry/Reset actions. CPT edits do not restart initial generation or other questions.

### Acceptance and changed patient inputs

Require explicit author acceptance of exact current probabilities/results for every applicable question, including unchanged ones, before sign-off. Pending, failed or stale current values cannot be accepted or signed. Probability edits/reset and relevant patient-data changes invalidate affected acceptance.

Relevant patient-variable/applicability edits mark affected analysis out of date and require regeneration/new original baselines. Retain prior history and do not silently carry old adjustments forward. Unaffected questions stay valid. Notes do not trigger regeneration; reset cannot make stale patient inputs current.

Signed chart/report presentation preserves original and final accepted CPTs/results, versions, physician/timestamps and adjustment indicators. Complete CPT tables remain printable; adjusted-then-reset records show final equality separately from adjustment history. Shared print views exclude private drafts.
