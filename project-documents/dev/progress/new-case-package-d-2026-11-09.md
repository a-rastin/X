# New case Package D checkpoint — 2026-11-09

## Implemented

- Added a dedicated bounded, SHA-256-pinned XML loader and compact double-array
  for the fixed oral model. It validates the star structure, binary roots,
  ordered outcomes and parents, every probability and every row sum. Fully
  observed evidence selects the exact source row; ranking retains all original
  values/ranks, excludes zero and unavailable oral IDs, and returns up to five.
- The separate path is intentional: all 16 factors are observed in this
  workflow, so one target CPT row is exact; generic admission limits, empty
  evidence policy, worker and provider paths remain untouched. This is an
  experimental requested integration, not clinical approval or outcome evidence.
- Added a dedicated oral manifest and transactional, repeatable registry
  installation in normal migration setup. Stored XML bytes and source hash use
  existing backup-covered registry fields; review remains `awaiting_review`,
  and no existing workflow pointer is activated. The larger new-case manifest
  and integration consumers remain work for the other packages.
- Model SHA-256: `f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a`.

## Verification

- Disposable PostgreSQL 16 container, Alembic head: `TEST_DATABASE_URL=...55433... .venv/bin/pytest tests/models/test_oral_antipsychotic.py -q` from `backend/`: 3 passed, including registry repeat install, byte preservation and transaction rollback.
- Changed-file Ruff checks and `mypy src`: passed. Independent XML table slices
  checked six asymmetric indices, filtering, empty results and bad inputs.
- Combined legacy inference run: 4 passed, 42 failed because existing generic
  validation expects `project-documents/bayesian-networks/schema.xml`, which
  is absent after the prior folder move; it exists under `BNs-new` and
  `old-BNs`. No legacy policy or schema path was changed here.
- Local single-process measurement, same in-memory source bytes: cold load
  1.067 s, 1,000 warm rankings mean 0.078 ms, peak RSS 133,804 KiB including
  Python, source, XML parsing and validation temporaries. Not a server benchmark.

## Handoff

Packages A/B and the overall new-case workflow remain incomplete. Wire this
model to server-controlled evidence, manifest/version pins, physician settings,
proposal persistence and signing in later packages. Verify runtime content
availability, recovery/restore of large stored bytes, complete startup, full
repository checks and clinical acceptance; none is claimed by this checkpoint.
