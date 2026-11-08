# New case Package A checkpoint — 2026-11-09

## Status: incomplete

The requested plan is `/home/amirali/.t3/userdata/artifacts/new-case-registration-plan-2026-11-09.md`.
Package A has not met its exit criteria. Do not begin Package B on the basis of
this checkpoint. The three clinical/formulation JSON files and the new-case
manifest, including its deterministic content hash, still need to be created
and validated against sections 4 and 10–13 of the plan. In particular, the
BP-lowering membership and injectable formulation labels require exact,
source-backed catalog matching; do not substitute guessed entries. No
clinical validation or deployment was performed.

## Baseline and completed work

- Before changes: clean `main` at `dc9c1ea5401d12eceea81961180396ccaa0ed12e`;
  expected migration head `0029`, matching the latest migration filename.
- Copied the supplied CSV and documented oral model byte-for-byte to the plan's
  runtime paths, added path-specific `-text` attributes, and added the
  Lexicomp source manifest. The original model remains intact.
- `python3 -c 'import runpy; runpy.run_path("backend/tests/models/test_new_case_sources.py")["test_new_case_bundled_source_inventory"]()'`
  passed. It checks both hashes, CSV size/header, 127,756 unique non-self
  pairs, 1,632 normalized names, all five rating counts and exact normalized
  coverage of all 22 model outcomes. It does not validate the model CPT or
  clinical content.
- Expected and observed CSV SHA-256: `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`.
  Expected and observed XML SHA-256: `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`.
- `make test-web` passed: 19 tests. `make check` stopped before checks because
  `uv` could not fetch `hatchling` from PyPI (connection timeout). Backend
  pytest likewise could not initialize its environment and was interrupted;
  baseline backend failures remain unknown. `git diff --check` passed.
- `.env.example` describes disposable `db-test` at localhost:5433; no `.env`
  is present, that port was unreachable, and `docker compose ps` could not
  interpolate the required `POSTGRES_PASSWORD`. No database was started or
  migrated. The actual database schema head was therefore not queried.
- Prior handoffs flag old Bayesian-network paths and browser tests using the
  removed working-note control. Those issues were not repaired or retested.

## Handoff

Finish the content files and manifests, assert all typed fields and source
references, verify all requested drug mappings including absent/qualified
names, and run the Package A exit checks. Establish an isolated test DB and
working Python dependencies before recording backend baseline results. Preserve
the distinction between source integration authorization and clinical review.

## Package B prerequisite recheck — 2026-11-09

At `59ecc39`, the worktree was clean and the latest migration file and
`EXPECTED_SCHEMA_VERSION` remained `0029`. The new-case directory still
contained only the oral XML: the workflow manifest and three clinical and
formulation JSON files were absent. The plan requires Package A's exit checks
before proceeding in order, and Package B depends on the formulation catalog
and trusted model profile for settings and restore validation. Package B was not
started; no schema or application changes were made in this recheck.

## Package B forced start — 2026-11-09

The user explicitly authorized proceeding despite Package A's incomplete exit
checks. Added standalone new-case name and encounter-year age validation in
`backend/src/x_insight/cases/new_case_demographics.py`, with boundary and
Unicode tests. The helpers are not yet connected to any request or persistence
path; no registration behavior has changed. The test command
`PYTHONPATH=backend/src backend/.venv/bin/python -m unittest discover -s backend/tests -p test_new_case_demographics.py -v`
passed (2 tests); `git diff --check` passed. The backend virtual environment
lacks SQLAlchemy and pytest, so integration and migration tests were not run.

Package B remains incomplete: migration and schema models, recovery and backup
compatibility, identity/settings routes, patient request and immutable workflow
assignment, and all integration checks remain to be implemented. The missing
formulation catalog and trusted oral-model validator remain dependencies; do
not substitute unverified clinical content or bypass backup validation.

## Package B environment follow-up — 2026-11-09

`uv sync --group dev` and a direct `uv pip install` timed out reaching PyPI.
The exact locked SQLAlchemy 2.1.3 and pytest 9.1.1 packages and supporting
backend environment were copied from the existing local Desktop checkout;
the editable import path was corrected to this checkout. This is a local
environment repair, not a reproducible network installation.

Using a disposable PostgreSQL 16 container and its isolated database,
`.venv/bin/python -m pytest tests/http/test_patients.py tests/test_new_case_demographics.py -q --disable-warnings --maxfail=1`
passed: 48 tests and 14 subtests. The container was stopped afterward. This
is a baseline only: the demographic helpers are still disconnected, and no
Package B migration, recovery changes, identity/settings routes or workflow
assignment have been implemented or verified.

## Dependency installation retry — 2026-11-09

PyPI became reachable. `uv pip install --reinstall --python backend/.venv/bin/python
'sqlalchemy==2.1.3' 'pytest==9.1.1'` succeeded. Its resolver upgraded
`iniconfig` and `pluggy`; they were subsequently restored to the versions
pinned by `backend/uv.lock` (2.3.0 and 1.6.0). Both requested packages import
successfully, and `backend/.venv/bin/python -m pytest
backend/tests/test_new_case_demographics.py -q` passed (2 tests, 14 subtests).
Package B implementation remains incomplete.
