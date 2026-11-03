# Issue 32 — high-severity npm advisory

Status: **analysis and plan; not fixed**. Audit evidence is from 2026-11-03 and may change as advisories are published.

## Root cause and impact

`make setup` runs two separate `npm ci` commands. The root e2e project currently audits clean, but `web/package.json` pins the direct development dependency `vite` to `8.3.2`, and `web/package-lock.json` locks the same version. A clean install faithfully reproduces that vulnerable version; it is not an npm install failure or an application-code defect.

Running `npm audit --json` in `web/` reported one high-severity vulnerable package, Vite, with six advisory entries. The high-severity [GHSA-587h-7wmj-h5pm](https://github.com/advisories/GHSA-587h-7wmj-h5pm) affects Vite 8.3.0–8.3.2: on Windows a crafted editor file path can bypass an existence check and, combined with the related escaping issue, lead to command injection when an attacker controls the file argument. The advisory identifies 8.3.3 as patched. This describes the advisory's conditions, not proof of exploitation in this app. Vite is used for the development server and frontend build; the high advisory is Windows-specific, while the audit also reports other Vite advisories, including one affecting non-Windows paths. The root project audit returned zero vulnerabilities.

## Smallest fix and evidence

Update the exact Vite pin from `8.3.2` to **`8.3.3`**, the first patch outside the reported vulnerable range. `@vitejs/plugin-react` already accepts `^8.0.0`, and the registry reports that Vite 8.3.3 requires Node `^20.19.0 || >=22.12.0`; use the project's documented Node 22.23.2 or a compatible version. npm's audit suggested 8.3.5, but a temporary copy of the frontend manifest and lockfile updated to 8.3.3 returned `found 0 vulnerabilities` from `npm audit --audit-level=high`. This verifies the candidate lockfile against the current registry, not the application's behavior after installation. No project dependencies were changed during analysis.

## Implementation scope — complete change list

1. Change only the Vite exact version in `web/package.json` to `8.3.3` and regenerate `web/package-lock.json` with npm, reviewing the diff for necessary Vite-related lockfile changes. For example, from `web/`: `npm install --save-dev --save-exact vite@8.3.3`. Avoid `npm audit fix --force`, broad upgrades, and unrelated lockfile churn.
2. Update the Vite version in the `Makefile` pinned-runtime comment so it remains accurate. Historical session reports are dated evidence and should not be rewritten.
3. Record implementation results and any remaining advisory or blocker in the relevant progress report and index. If a current high advisory cannot be resolved by a non-major change, document its identifier, affected version, reason and options; if a major upgrade is required, stop and list the options instead of performing it.

No application source, Vite configuration, root package files, unrelated dependencies, or new tests are needed for this dependency-only fix. If compatibility checks expose a separate issue, document it before expanding scope.

## Verification after the change

- From `web/`, run `npm ci` and `npm audit --audit-level=high`; also audit the root lockfile with the same command. Record results and any remaining advisory and reason. Recheck current registry data rather than assuming the dated result still applies.
- Run `make test-web`, `npm --prefix web run build`, `npx tsc --noEmit`, and the full `make test-e2e` without a `TEST` selector, using the configured Chromium and Firefox projects. Prepare the required isolated backend/database and browser dependencies; record failures or unavailable prerequisites rather than claiming a pass. The default Playwright configuration excludes the dedicated fresh-install and public-provider suites; report those exclusions and run them if applicable to the walkthrough environment.
- Perform the requested application walkthrough against a suitable test stack using synthetic data: sign in, register a patient, go through assessments and proposal/CPT review, sign, and open a follow-up. The existing `e2e/clinical-workflows.spec.ts` and [S59 handoff](../project-documents/dev/progress/s59.md) provide a repeatable synthetic journey; distinguish automated coverage from any manual walkthrough and record what actually ran. Never use real patient data for this check.
- Acceptance requires a clean high-level audit or an explicitly documented remaining advisory with its reason, and passing frontend tests and walkthrough. If any check is blocked, leave acceptance open and state the blocker.

## Analysis verification limits

Both audits and the isolated candidate-lockfile audit ran on Node 22.23.4/npm 10.9.9. The frontend tests, build, clean install, and walkthrough were not run for this analysis; they must run after the actual change. No code or dependency files were modified.
