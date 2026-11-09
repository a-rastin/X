# New case Package H checkpoint — 2026-11-09

## Status: packaging, compatibility and final verification implemented; full product acceptance still blocked

Package H is the last work package. It does not implement the missing Packages
F/G (proposal persistence, v4 signing, new-case UI); those remain absent per
their own checkpoints. This package verifies and documents the
packaging/startup/backup compatibility of what exists, runs the available
verification matrix, and records the pre-existing issues honestly. No clinical
acceptance or release is claimed. All work was done in this authorized
implementation session, including the memory/rationale edits.

## Implemented

### Packaging and content-path wiring
`deploy/Dockerfile.app` already copies `content/new-case/` and
`content/ddi/lexicomp/` and sets `X_INSIGHT_CONTENT_DIR` (added in Packages
C/D). All five content readers — `models/workflow_release.content_root`,
`ddi/lexicomp.content_directory`, `ddi/bootstrap.bundled_directory`,
`models/registry.install_bundled_oral_model` and `cases/new_case` — resolve
through that one variable; `X_INSIGHT_ASSESSMENTS_DIR` is the separate
assessment-JSON root. Added `deploy/check.sh` assertions so the bundled content
and env var cannot regress, and a DB-free
`backend/tests/operations/test_new_case_packaging.py` (3 tests) covering reader
consistency and startup wiring.

### Startup compatibility
`python -m x_insight.operations.migrate` installs, idempotently, Alembic head
`0030`, the owner-approved DDInter release, the pinned Lexicomp release, the
SHA-256-pinned oral model and the deployment encryption key. Verified on a
**fresh** disposable database with no provider credential, no capability check
and no `DDI_DEFAULT_DATASET_VERSION`: both releases present
(`ddi-c45e2051cf16`, `ddi-2adbb775258d`), 350,139 evidence rows, 3,571 concepts,
oral model hash `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`
/ 16,126,022 bytes, deployment key 32 bytes mode 600. A second run reported
`reused=True` for both datasets (exit 0). Missing credentials for the optional
legacy provider cannot block the deterministic path; `PROVIDER_NOT_CONFIGURED`
remains a legacy-only, job-time failure.

### Backup/restore bound — measured, minimal change
A real full backup of a database holding both DDI releases produced a
403,534,237-byte `database.json` (385 MiB), 419,665,856 B total expanded
(400 MiB) and 43,735,171 B compressed (41.7 MiB). The old
`MAX_FILE_BYTES = 256 MiB` rejected it: `write_archive` raised and
`validate_archive_bytes` returned `archive entry database.json exceeds file
bound`. The 16 MB oral XML (`model_network_versions`) and the 25 MB Lexicomp
CSV (`ddi_source_documents`, 24,960,703 B) are carried in backup-covered tables
and were **not** omitted.

Raised only `MAX_FILE_BYTES` to 512 MiB (== the unchanged
`MAX_EXPANDED_BYTES`, the effective zip-bomb guard). `MAX_ARCHIVE_BYTES`
(100 MiB) and `MAX_EXPANDED_BYTES` (512 MiB) are unchanged and the measured
content fits both. `restore.MAX_FILE_BYTES` imports the value, so backup and
restore agree. After the change, `write_archive` and `validate_archive_bytes`
both accept the full archive (`ok: True`, `app_schema_version: 0030`, four zip
entries). Updated `tests/recovery/test_backup.py` bound assertions and added a
regression test recording the measured sizes. No content was dropped to fit.

### Documentation and rationale
- `README.md`: scoped LLM/CPT claims to the legacy workflow; documented the
  deterministic `new-case-v1` path; corrected the stale "not integrated"
  oral-model claim and the "`make test-web` unimplemented" claim; noted startup
  installs the new assets without provider credentials.
- `docs/user-guide.md`: added an honest "New case (experimental, backend-only)"
  section (no LLM/provider/slider; physician wizard not yet available).
- `docs/new-case-state.md`: added packaging/startup/backup compatibility.
- `deploy/README.md`: documented `LEXICOMP READY`, the pinned oral model,
  `X_INSIGHT_CONTENT_DIR` and provider-independence.
- `context/new-case.md` + `context/index.md`: added two Keep-the-Why decision
  entries and the `## N` index line.

### Preserved decision reasons (all five)
- Fully observed fixed BN row lookup — `context/new-case.md` (new entry).
- Persistent physician override — `context/registration-flow.md`,
  `835151e2-e7ed-472e-8268-58c9e420d266`.
- Explicit A-vs-missing coverage — `context/ddi.md`,
  `cb5a7307-7168-46ae-9d8d-249228e18ce2`.
- Separate contraception outputs — `context/registration-flow.md`,
  `835151e2-e7ed-472e-8268-58c9e420d266`.
- Historical snapshot retention — `context/new-case.md` (new entry, v4 additive
  shape).

## Verification (evidence)

Environment: disposable PostgreSQL 16 container `x-new-case-h-verify` on port
55439 (two databases `x_insight`, `x_insight_test`), backend `.venv`
(Python 3.12), Node 22.23.2 / npm 10.9.8, Docker 29.1.3. No provider
credentials, no live provider, no `DDI_DEFAULT_DATASET_VERSION`.

| Check | Result |
| --- | --- |
| `make prod-check` (`deploy/check.sh`) | green; new content/env assertions pass |
| `make test-web` | 20 passed |
| `npm --prefix web run build` | passed (existing large-chunk warning) |
| `npx tsc --noEmit` | passed |
| `.venv/bin/mypy src` | Success, 84 files |
| `ruff format --check` / `ruff check` / `git diff --check` on changed files | clean |
| new packaging test | 3 passed |
| focused new-case/lexicomp/oral/http/state/bound/startup suite | 123 passed, 14 subtests |
| full-content backup build + `validate_archive_bytes` | ok: True; 403,534,237 B `database.json`; 43,735,171 B archive |
| startup migrate on fresh DB, then re-run | exit 0; both releases + oral model; re-run `reused=True` |
| plan §18 broader HTTP suites (patients/identity/physicians/signing/medications/ddi) | 157 passed, 9 failed — all `tests/http/test_signing.py` with `NETWORK_INVALID` (pre-existing offline-generation baseline) |
| combined run of targeted suites + full `tests/recovery/` | 138 passed, 33 failed — all recovery/ with `schema_unavailable` (pre-existing generic XML schema path) |

Source/dataset hashes unchanged: oral XML
`f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`, Lexicomp CSV
`31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`. Schema head
`0030`.

## Pre-existing issues and remaining boundaries (unchanged)

- `make check` stops on pre-existing ruff format / E501 violations in
  `tests/http/test_patients.py` and `tests/models/test_new_case_sources.py`
  (flagged in the Package C checkpoint). Left untouched.
- Generic XML validation expects `project-documents/bayesian-networks/schema.xml`;
  it exists under `BNs-new/` and `old-BNs/` after the folder move. This is the
  `schema_unavailable` error breaking the recovery staging/commit suite and was
  recorded in the Package D/E handoffs. Resolve it in its own change. Because of
  it, **restore-over-live commit** was not exercised; archive validation and a
  real full backup were.
- Legacy signing/generation suites fail offline with `NETWORK_INVALID`; the
  unchanged legacy workflow needs a configured provider. It is not a New case
  gate.
- Packages F/G remain unimplemented: no `new_case_proposals` table, no v4
  signed snapshots, no new-case UI. Consequently "backup/restore on a dataset
  containing new-case proposal/signed rows" and full end-to-end acceptance
  cannot be verified yet. No bound was relaxed to force those nonexistent rows
  to fit, and no server guard was removed to simulate them.
- `MAX_EXPANDED_BYTES` (512 MiB) has ~93 MiB headroom over the measured 400 MiB
  total. If both DDI datasets grow beyond it, re-measure and raise that bound
  with a test rather than omitting content.

## Handoff

Continue with the remaining Packages B (physician name/settings), F (proposal
persistence, fingerprints, atomic v4 signing, signed chart/report) and G
(wizard, bilingual UI, browser acceptance), then rerun the full verification
matrix. Resolve the generic XML schema-path issue in its own change before
claiming recovery/signing acceptance. Do not present the deterministic path as a
complete clinical product; the fixed model is experimental and not clinically
validated. The old sign/generate/acknowledge routes still refuse `new-case-v1`
drafts until F/G exist.

## Follow-up — 2026-11-09: fix the two pre-existing blockers

The user authorized fixing the pre-existing issues recorded above. This
follow-up supersedes only those two bullets; the earlier report is retained as
history.

### Ruff violations (`make check`)

`ruff format` + `ruff check --fix` applied to
`tests/http/test_patients.py` (list literal reformat) and
`tests/models/test_new_case_sources.py` (import ordering, E501 line). Both are
formatting-only, no test behavior changed. `make check` now passes end-to-end
(ruff format/check, `mypy src`, web build, `npx tsc --noEmit`), exit 0.

### Stale `project-documents/bayesian-networks` path (`schema_unavailable`)

The folder was renamed to `old-BNs` on 2026-11-05 (bn-folder report);
`schema.xml` was copied byte-identically into `old-BNs` and `BNs-new`
(SHA `827b6f12164d3d0c9195ed35e2fc03ac4a6f019a12dbba90f6d8c4f5fcdb30b3`),
and all eleven `BN-*.xml` files live in `old-BNs`. Updated every stale
reference in `backend/` to `project-documents/old-BNs`:

- `models/validation.py` `SCHEMA_RELATIVE` (one-line value + explanatory
  comment), so generic XMLBIF admission resolves the schema.
- Thirteen `tests/models/*` modules that referenced `BN-*.xml` or the old
  directory; these are the legacy inference/package/schema suites, not just
  recovery.

`deploy/Dockerfile.app` had no `project-documents/` copy, so the schema was also
unavailable in the production image; added a `COPY` of
`project-documents/old-BNs/schema.xml` and a `deploy/check.sh` assertion guarding
it. `backend/README.md`'s `test_schema.py` command was updated to the same path.

Deliberately left untouched: the `content/questions/*` manifests and
`codebase-memory/index.json` still label `project-documents/bayesian-networks/…`
as historical provenance. Those are reviewed/immutable content records with
pinned source hashes and a generated knowledge-graph cache; rewriting their
path labels would rewrite history (or be regenerated), not fix runtime
validation.

`tests/recovery/` now passes **57/57**, including `test_restore_commit.py`, so
restore-over-live commit is exercised (the gap called out above is closed).
`make prod-check` passes with the new schema assertion. `make check`,
`git diff --check`, ruff and mypy are clean on all changed files.

### Verification gates (`make verify` legs)

`make verify` = `make check` + `make test-backend` + `make test-e2e`. All three
legs were launched once (no broad reruns) against a fresh disposable PostgreSQL
16 container, with a live API started via `python -m uvicorn` for the browser
leg. `make check` is green. The full backend suite (1,397 collected tests) and
the Playwright suite (chromium + firefox) were still running when this checkpoint
was committed; both retain known pre-existing offline-provider and
browser-environment failures and are not a release gate, so their tallies are
recorded in a later follow-up when they complete. A corrupted local
`node_modules/playwright` bundle (`babelBundle.js` had one stray `y` in place of
`}`) was restored from npm's cached `playwright-1.63.0.tgz`; this is a local
environment repair, not a tracked project change.

### Remaining boundary (unchanged)

Packages F/G remain unimplemented. "backup/restore with new-case proposal/signed
rows" and full clinical acceptance still cannot be verified; no bound was
relaxed and no server guard removed to fake those rows. Implementing F/G is a
separate, large body of work, not a defect fix.
