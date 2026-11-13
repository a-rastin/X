# Follow-up B/F/G integration — 2026-11-13

Packages **B, F and G are implemented and verified in the working tree**. This continuation
supersedes the incomplete B/F/G handoffs linked from the progress index. It does
not mark the separate Package H release/public mixed-history restore drill complete.

Base HEAD: `6ec1d93d073e4e5daf6d0f5d748a72d2679194e9`; working-tree changes are
uncommitted. No user application database was migrated and no deployment was made.
Tests use synthetic records in disposable PostgreSQL 16 databases at schema
`0032`, with the bundled oral model and Lexicomp release `ddi-2adbb775258d`.
Node `22.23.2`/npm `10.9.8` are used explicitly for final browser/static checks.
The acceptance API/web processes and disposable database container were removed
after verification. The pinned Node runtime and Playwright browsers remain in their
normal tool caches; the global Node default was not changed.

B now validates follow-up raw state, exact content/assessment/model/DDI identity,
canonical proposal fingerprints, source/baseline ownership and hashes, and v5
signatures with exactly one proposal reference. Existing schema/version defaults
and legacy/v4 archive gates remain intact. A real generated v5 archive is
accepted by `validate_archive_bytes`; malformed signed/source hashes are rejected.

F now exposes author-owned proposal generation/read/freshness and atomic v5
confirmation. Proposals retain the source distribution, trace, exclusions,
interaction evidence and independent advice, and are reused for identical saved
inputs without updates. Final text uses the existing independent revision store.
Confirmation rechecks exact assessment bytes and all content/dependencies, active
account/archive state, owned current inputs, encounter/final-text revisions and
supported outcome. Account-first locking matches deactivation discard; patient
then encounter locking matches draft creation. Replay and races produce one
signature and a consistent next baseline. Audits capture carry source, manual
answer/reset attribution, generation, final-text writes and confirmation.

G now dispatches new `follow-up-v1` drafts to the bilingual seven-step wizard,
with frozen dated history, optional PANSS, nested C-SSRS, required/conditional
history and branch questions, simple effects, pinned medication/main-formulation
selection and separate original/final plans. Unknown versions remain closed.
Chart, addenda and escaped reports support v5. Reload can recover a committed
confirmation after its response was lost. New drafts activate this workflow;
existing stored legacy identities remain unchanged.

Integration also corrected required carried-condition/main-formulation validation,
explicit current overrides matching inherited answers, independent reproductive
advice, and shared autosave normalization: server metadata is rebased without
losing typing made during the save. Regression checks preserve the legacy/New
case contracts; fixtures recreating historical workflows explicitly select their
pre-rollout state.

## Verification

All selected final checks pass. Backend commands run from `backend/` with
`TEST_DATABASE_URL` pointing only to the disposable acceptance databases.
Browser commands use the acceptance stack at `127.0.0.1:15139` with its API at
`127.0.0.1:18039`, on the separate synthetic browser database. No live provider
credential is required. The Node 22 binary directory prefixes `PATH`.

| Check | Result |
| --- | --- |
| `make check` | Ruff format/lint, mypy, web build and root TypeScript pass; existing Vite large-chunk warning only |
| `make prod-check` | Packaging/content-path/secret checks pass |
| `npm --prefix web run test-web` | 24 pass, including normalization/in-flight edits and translated historical labels |
| `.venv/bin/pytest -q tests/http/test_follow_up_plans.py tests/assessments/test_follow_up_plan_inputs.py tests/assessments/test_follow_up_rules.py` | 50 pass: real proposals/ranking/signing/addenda/archive validation, author/workflow guards, null final text, stale inputs/settings, corrupt content, exact assessment-byte changes, concurrent replay and create/settings/archive/retain/discard races |
| `.venv/bin/pytest -q tests/assessments/test_follow_up_state.py tests/assessments/test_follow_up_baseline.py tests/models/test_follow_up_candidates.py tests/models/test_follow_up_sources.py tests/operations/test_follow_up_packaging.py` | 28 pass |
| `.venv/bin/pytest -q tests/http/test_established_followup.py tests/http/test_exports.py` | 24 pass: existing signed legacy workflows, structured historical evidence, escaped longitudinal exports |
| `.venv/bin/pytest -q tests/recovery` | 58 pass: supported historical archives and public recovery paths |
| `E2E_BASE_URL=<acceptance stack> npx playwright test e2e/follow-up-v1.spec.ts --workers=1` before adding the separate branch matrix | 12 pass: EN/FA × Chromium/Firefox, model/review/sign/chart, light/dark, axe without blanket suppression, frozen v5 baseline, lab toggles/manual No, conditional antidepressant, main removal, direct-hash validation, 412 reconciliation, lost generation/signing responses and reload |
| Same browser selector with `--grep 'original branches'` | 4 pass, each exercising 18 real public-HTTP/UI branch and effect-precedence scenarios; 72 scenario steps across EN/FA and both browsers |
| `e2e/new-case.spec.ts` in the combined 24-test browser run | 12 pass, including v4 signing, personal settings and lost-response recovery; the other 12 were the then-current follow-up journeys |

The current follow-up browser file contains all 16 tests. The earlier broader
104-pass backend selector additionally covered follow-up state and latest-source
ordering, legacy follow-up, New case state/plans and staged older schemas. Later
follow-up-specific changes were checked by the focused commands above; counts
are individual runs and are not an aggregate of unique tests.

A disposable clone of the synthetic browser database was made representable at
0031 by removing only its v5 fixtures. Alembic `0032 → 0031 → head` then preserved
**8 v4 signed snapshots, 50 legacy encounters and 12 New case encounters** exactly:
IDs, stored snapshot JSON text/hashes, workflow, raw draft, lifecycle and revision.
The clone returned to `0032` with an empty follow-up proposal table. This proves
preservation of those existing records, not Package H's public mixed-v5 restore.

The generated v5 archive round trip uses actual bundled model/catalog data,
a v5 signature, an addendum and a subsequent v5 baseline. It is **24,761,247 bytes**
compressed, **200,833,237 bytes** expanded across four archive members. Validation
accepts it and rejects altered signature/source hashes and forged evaluated
inputs. Original and edited final text remain separate, and addenda leave signed
hashes unchanged.

Final source/test/config identity: `1d755c3fc900ce9a9cc66e3f0beaadfaf5f118460e26466529d39cb2e6e144c0`
over 408 files. It hashes sorted repository-relative paths, a NUL, each
file's SHA-256 and a newline. Scope: tracked and untracked files under
`backend/src`, `backend/tests`, `backend/migrations`, `web/src`, `web/tests`,
`e2e`, plus Makefile, Playwright/TypeScript/Vite configs and root/backend/web
package/lock files. Documentation/memory is excluded so this report does not
self-reference. The workflow manifest hash is
`d21aa9f5d580b579497cdee64f8d15b127a2d87ba2b8b00056a019cd0bc65b44`.

An initial broad backend selector was interrupted after 45 passes and 13
failures caused by old fixtures assuming all newly created follow-ups were legacy;
the explicit legacy fixtures and new-default expectations were corrected and
rerun. That interrupted run is not passing evidence. The first recovery run had
57 passes and a native inference-worker crash; a focused rerun reached the stale
legacy-fixture expectation, corrected before the clean 58-pass rerun. Node 26
Playwright workers also crashed in V8 scavenging before test bodies; final browser
runs use the repository-pinned Node 22 without changing the global runtime.

## Remaining release work

Package H's public full mixed legacy/v4/v5 backup-and-commit-restore drill,
post-restore next-visit/report/privacy checks, full backend suite and deployment
acceptance remain separate. Existing A/C/D/E exhaustive matrices are not declared
complete by this checkpoint. No legal/source/medical approval gate was introduced.

See [API contracts](../../../docs/follow-up-api.md) and the
[user guide](../../../docs/user-guide.md#start-a-follow-up-encounter).
