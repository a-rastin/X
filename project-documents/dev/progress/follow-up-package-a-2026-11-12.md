# Follow-up Package A checkpoint — 2026-11-12

Base revision: `70639396fd9e39be9707065b93e108ad81d5f7cd`; changes are
uncommitted. Package A is **partial**, not an integrated follow-up workflow.

Added `content/follow-up/v1/` with its own manifest, original graph node and
terminal map, decision priorities, effect overlays, independent recommendations,
16 ordered oral-model factor sources and explicit supported oral/LAI equivalences.
The original graph and New case assets remain unchanged; existing labs, oral
model, formulation inventory and Lexicomp source are referenced by path and hash.
`cases/follow_up_content.py` loads these assets offline with content-root override,
hash checks and basic structural/equivalence validation, without approval fields.
The rule descriptions are a contract for later evaluator work, not a functioning
decision engine. No application endpoint currently calls the loader.

Validation from the uncommitted working tree:

- `backend/.venv/bin/ruff check` on the new Python files passed; Ruff formatting
  applied to both files.
- `cd backend && .venv/bin/pytest -q tests/models/test_follow_up_sources.py tests/models/test_new_case_sources.py`
  passed: 2 tests. This covers copied content outside the repository working
  directory, graph node/terminal inventory, factor order, LAI/catalog links,
  altered content rejection and a malformed-content case.
- The expanded selector including `tests/models/test_oral_antipsychotic.py`
  initially had 4 passes and 1 setup error because localhost:5433 was unavailable.
  Repeated against a freshly created disposable `postgres:16-alpine` container
  with a synthetic test database: 5 passed. The fixture migrated its isolated
  database; the container was removed. No user application database was migrated.

Still open: proposal generation without approval metadata cannot be tested until
the follow-up evaluator/proposal packages D–F exist; the content loader is not
yet integrated into those packages. Image packaging is package H work; only a
copied content tree and `X_INSIGHT_CONTENT_DIR` were tested. Graph path behavior,
complete structural/schema validation, signed records, historical compatibility,
and full acceptance are not established by these source tests. Packages B–H remain
pending. No commit or deployment was made.
