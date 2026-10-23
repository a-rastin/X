# X-INSIGHT backend

S01 bootstrap: minimal FastAPI process with public health check.

Run from repo root via `make` targets (see `Makefile`).

Local loop (same as [S01](../project-documents/dev/progress/s01.md)): `make setup`, copy `.env.example` to `.env`, `make dev` (disposable DBs + backend `:8000` + web `:5173`), `make stop` (`docker compose down`; `down -v` drops data). Ports/placeholders live in `Makefile` header, `.env.example`, and `compose.yaml`; if host PG occupies `5432`, override `DB_PORT` in `.env`. Never commit `.env`.
Apply schema migrations and bundled approved DDInter data with `make migrate`
(`DATABASE_URL` from `.env`). It runs `python -m x_insight.operations.migrate`:
Alembic first, then the explicit DDI bootstrap. Plain `alembic upgrade head`
applies schema only.
S21 models validation (T5-only, no DB/HTTP/migration): from `backend/`, `uv run pytest tests/models -q` (`lxml==6.1.3` explicit safe parser); existing schema checks via `python3 ../project-documents/bayesian-networks/test_schema.py`. See [S21](../project-documents/dev/progress/s21.md).

DDI tooling runs from `backend/`: `uv run python -m x_insight.ddi build-ddinter
--sources ~/ddinter --output /tmp/ddinter-candidate` builds the complete supplied
CSV review bundle without a database/provider. Owner-reviewed publication uses
the existing `publish --staging ... --manifest ...` subcommand and a privileged
connection. Follow [DDInter operations](../docs/ddinter-operations.md) for exact
review gates, migration `0022`, verification, default selection and rollback.
The committed owner-reviewed release `ddi-c45e2051cf16` is installed on a database
with no released DDI dataset during normal migration. It rebuilds from ordinary
Git source files without Git LFS or a provider; allow several minutes on first
setup. Repeats reuse the release, and existing custom releases are preserved.
For schema-only installations, `uv run python -m x_insight.ddi bootstrap` performs
the explicit data step using `DATABASE_URL`; see the runbook for default-selection
rules and failure recovery. New candidates still require their own owner review.
Current schema head is `0022`; no new release tables were added.
