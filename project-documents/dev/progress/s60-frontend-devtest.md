# S60 frontend dev-test — 2026-10-16

Scope: independent verification of the four new `e2e/failure-recovery.spec.ts`
scenarios and the current frontend selector changes. No production code or
backend tests edited; no commits. Existing changes preserved.

## Test-only corrections

`e2e/failure-recovery.spec.ts`: removed fixed sleeps, repeated login/write
attempts, session-maturation waits, and post-acknowledgement read polling.
Provision/login/PATCH now fail on the first unexpected response. The saved
working note must be immediately GET-readable. This preserves, rather than
masks, the commit-visibility adversarial case. Renamed the strict PATCH helper
and corrected coverage comments (no slider, artifact-read, or server-sign
denial claim). Shared helpers and production code remain untouched.

## Commands and results

Run from repository root unless noted:

- `npm run build` in `web/`: **PASS**, frontend TypeScript + Vite, **71 modules**.
  Existing warning: minified JS chunk exceeds 500 kB (550.99 kB).
- `npx tsc --noEmit`: **FAIL**, **1 diagnostic**, pre-existing
  `e2e/signing.spec.ts:848` TS2352 (`Record<string, string>` cast includes
  numeric `secondary_plan_revision`). Outside this slice; not edited.
- `npx tsc --ignoreConfig --noEmit --target ES2022 --lib ES2022,DOM --module ESNext --moduleResolution bundler --strict --skipLibCheck --types node e2e/failure-recovery.spec.ts`:
  **PASS**, targeted spec and imported helpers.
  Initial targeted invocation without `--ignoreConfig` returned TS5112; rerun
  above uses TypeScript 7's required flag.
- `npx playwright test e2e/failure-recovery.spec.ts --project=chromium --project=firefox --workers=1 --retries=0 --reporter=line`
  redirected to `/tmp/opencode/s60_frontend_browser.log`: execution tool's
  120-second timeout interrupted the run after **6 completed failures**
  (all four Chromium, first two Firefox), with seventh test announced.
- `npx playwright test e2e/failure-recovery.spec.ts --project=firefox --grep 'stale run|markup renders' --workers=1 --retries=0 --reporter=line`
  redirected to `/tmp/opencode/s60_frontend_browser_remaining.log`:
  **2 failed**, completing the remaining Firefox scenarios. No passing
  behavior assertions: **all 8 distinct browser/project cases fail setup**.
  Browser versions: Chromium **153.0.8010.12**, Firefox **155.0**.
- `node /root/.claude/skills/browser-automation/browser.mjs http://localhost:5173 --snapshot --full`:
  HTTP 200, title `X-INSIGHT`, login form renders. Expected anonymous
  `/api/v1/me` 401 appears in network/console; no mount failure observed.

## Reproducible blocker for backend owner

Every completed case fails `ensurePhysicianLocal`: admin login returns 200,
then physician creation immediately returns **401 UNAUTHENTICATED** instead
of 201. Example request ID:
`0674322a-e1bf-4540-a664-8f4e1ca47697` (Chromium stale case).

Independent reproduction with Playwright APIRequestContext against
`http://localhost:5173` (Vite preview proxy to development backend):

1. POST `/api/v1/auth/login` with existing admin credentials: **200**.
2. Inspect storage state: session cookie present for `localhost`, path `/`,
   `secure=false`, `sameSite=Lax` (cookie value not recorded).
3. Immediately GET `/api/v1/me`: **401**.
4. POST `/api/v1/physicians` with returned CSRF and a unique idempotency key:
   **401**, request ID `4b46d074-76c8-4348-822d-f234c2573bc8`.

This establishes acknowledged login followed by unusable session, not its
root cause. Earlier spec comments attributed it to commit visibility; that
attribution is not independently confirmed here. Do not hide it with sleeps
or mutation retries. Browser behaviors remain unverified until owning agent
fixes the backend/stack issue and reruns the eight cases. No backend
regression duplicated and neither isolated S60 test DB was used.

## Proof boundaries (even after green)

- Wrong-author test exercises real author/stranger accounts, draft GET denial,
  no sentinel in rendered body, directory link, and an empty shared chart's
  demographics. It does **not** prove artifact reads/writes are denied or
  signed records/addenda visibility.
- Failed/stale/complete batches and question reviews are `page.route` fixtures;
  localStorage seeds the batch. These prove presentation/client gating only,
  not real provider failures, mixed retries, lease theft/crash, restore
  fencing, completed-artifact uniqueness, or backend stale enforcement.
- Failed-run case saves a real draft and reloads a browser page. It has no
  adjustable baseline/sliders, no simultaneous second physician edit, and
  no backend/worker process restart; those S60 requirements remain separate.
- Manual-sign case is a **client preflight** rejection in `SignPanel.tsx`
  (missing acceptance), before `signEncounter` POST. It cannot prove backend
  sign denial. It does save real plan/notes; proposal text comes from mocks.
- Literal `<b>`/`<i>` escaping checks guard DOM text rendering, not export CSV
  formula neutralization, executable payloads, actual model/MCP leakage,
  forged grants/destinations, XML entities, or archive traversal.

S60 frontend acceptance is **BLOCKED**, not green. Backend evidence and the
broader S60 matrix must be combined without upgrading these mock-backed
presentation checks into real-dependency or concurrency proof.

## Follow-up 2026-10-16 — strict rerun after backend fix

The preceding red results and blocker are historical. Backend owner reports
the commit-after-response defect fixed centrally with `CommittedRoute`, with
real-loopback regression evidence changing from two 401 failures to two
passes, and the development `:8000` reload applied. That backend test was
not duplicated here.

Exact rerun from repository root:

```bash
npx playwright test e2e/failure-recovery.spec.ts --project=chromium --project=firefox --workers=1 --retries=0 --reporter=line > /tmp/opencode/s60_frontend_browser_after_fix.log 2>&1
```

**PASS: 8 passed (1.8m), exit 0** — all four scenarios on Chromium and all
four on Firefox. The same strict spec was run without changes, masking
retries, fixed sleeps, session-maturation waits, or acknowledgement polling.
This time setup and all scenario assertions completed successfully.

The post-login 401 blocker is resolved for this browser rerun. Current
frontend browser slice is **GREEN within its documented proof boundaries**;
the broader S60 real-provider/concurrency/restart acceptance cannot be
inferred from these mock-backed checks. Earlier build/typecheck findings
remain as recorded: web build passed; whole-repo TypeScript has the unrelated
`e2e/signing.spec.ts:848` diagnostic.

This follow-up changes only this report and its index label. No production
code, test changes, backend regressions, isolated test DB access, or commits.

## Completed checkpoint follow-up — 2026-10-16

See the canonical [S60 completed report](s60.md) for combined backend/browser
evidence, final review closure, known carryovers, and S61 handoff. The browser
proof boundaries above remain unchanged; the original blocked state is
preserved as history, not the current checkpoint. No browser rerun was made
by this documentation follow-up.
