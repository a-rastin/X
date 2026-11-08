# New case Package C checkpoint — 2026-11-09

## Scope and implementation

- Added `ddi/lexicomp.py`: validates the supplied CSV bytes, structure, corpus
  counts and 22 exact model outcomes; installs the source bytes, catalog and
  evidence in one transaction with bounded insertion batches and an immutable
  content-derived version. Repeated installation reuses the release.
- Normal DDI bootstrap installs Lexicomp beside historical DDInter releases;
  the generic default excludes the new profile, and `current_version()` supplies
  the explicit new-case pin. The Docker image includes both source files.
- Profile-aware checking keeps A distinct from missing coverage, returns ratings
  and verbatim notes, and provides candidate-versus-current batch checking.
  The generic report renderer displays Lexicomp ratings and escaped notes.
- Source SHA-256: `31ce2fb78b0bbf364786e25b2c5f26ccca29ea50657f7faba02bb9fd18828255`.
  Dataset selection is authorized by the supplied plan, not clinical review.

## Verification

- Disposable PostgreSQL 16 container on localhost port 55433 with Alembic head.
  `tests/ddi/test_lexicomp.py`: 3 passed, including corpus, five rating examples,
  symmetric and missing-pair lookup, legacy DDInter pin and rollback.
- `tests/ddi/test_ddinter_release.py tests/http/test_ddi.py`: 43 passed.
- `npm --prefix web run build` and `npm --prefix web run test-web`: passed
  (19 web tests); build reports the existing large-chunk warning.
- Changed-file Ruff format and lint checks and `mypy src` passed. `make check`
  stopped on pre-existing formatting violations in `tests/http/test_patients.py`
  and `tests/models/test_new_case_sources.py`; no unrelated files were changed.
- Running the DB tests from the repository root fails because their shared
  Alembic fixture requires the `backend/` working directory; rerun there passed.

## Handoff

Packages A and B remain incomplete. There is no new-case workflow to consume the
pin or freeze candidate evidence into proposals/signatures yet; integrate those
in later packages and retain `check_candidates` rather than generating
candidate-to-candidate pairs. Browser, accessibility, recovery/restore, full
repository verification and clinical acceptance were not performed. Preserve the
pre-existing unrelated edit to `project-documents/Add-New-Patient.mmd`.
