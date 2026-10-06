# X-INSIGHT backend

S01 bootstrap: minimal FastAPI process with public health check.

Run from repo root via `make` targets (see `Makefile`).

Local loop (same as `project-documents/dev/progress-tracker.md` S01): `make setup`, copy `.env.example` to `.env`, `make dev` (disposable DBs + backend `:8000` + web `:5173`), `make stop` (`docker compose down`; `down -v` drops data). Ports/placeholders live in `Makefile` header, `.env.example`, and `compose.yaml`; if host PG occupies `5432`, override `DB_PORT` in `.env`. Never commit `.env`.
Apply DB migrations with `make migrate` (`DATABASE_URL` from `.env`).
S21 models validation (T5-only, no DB/HTTP/migration): from `backend/`, `uv run pytest tests/models -q` (`lxml==6.1.3` explicit safe parser); existing schema checks via `python3 ../project-documents/bayesian-networks/test_schema.py`. See `project-documents/dev/progress-tracker.md` S21.
