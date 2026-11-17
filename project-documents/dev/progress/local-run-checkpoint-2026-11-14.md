# Local run checkpoint — 2026-11-14

Base revision `b8518be` (`main`, "Implement follow up plan"); working tree clean.
The committed application was built and started locally with `make dev` using the
repository-pinned Node 22.23.2 runtime on `PATH`; the global Node default was left
unchanged. No code, configuration, or deployment was modified.

## Build and startup

| Step | Result |
| --- | --- |
| `uv sync --locked --group dev` | Already current (78 packages checked) |
| `npm ci` artifacts (`web/node_modules`, root `node_modules`) | Present |
| `npm --prefix web run build` | Passed; only the existing large-chunk warning |
| `make dev` database containers | Healthy; volumes `x_pgdata`/`x_pgtestdata` preserved |
| Startup migration + provisioning | Reached schema `0032`; bundled releases reused: DDInter `ddi-c45e2051cf16`, Lexicomp `ddi-2adbb775258d` |
| API / Vite / worker | Listening on `:8000`, `:5173`; worker polling normally |

Verification from a separate shell:

- `GET /api/v1/health` → 200 `{"status":"ok","service":"x-insight"}`
- `GET /api/v1/ready` → 200, schema `0032`
- `http://127.0.0.1:5173/` → 200, serves the INSIGHT shell

## One startup blocker and its non-destructive fix

The first `make dev` attempt exited during startup with
`password authentication failed for user "x_insight"`: the disposable volumes were
initialized with an older password than the current `.env`, so TCP authentication
failed even though both containers reported healthy.

Fixed without dropping any data. The postgres image trusts local socket connections,
so each role's password was reset from inside its own container over the socket:

```sh
set -a; . ./.env; set +a
docker compose exec -T db      psql -U "$POSTGRES_USER" -d postgres \
  -c "ALTER USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';"
docker compose exec -T db-test psql -U "$POSTGRES_USER" -d postgres \
  -c "ALTER USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';"
```

Both volumes kept their contents and the second `make dev` start succeeded. `.env`
was not modified. The documented `docker compose down -v` reset remains the fallback
when the original credential is unknown and the disposable data need not survive.

## Remaining

This checkpoint covers local build and run only. Package H's public mixed legacy/v4/v5
backup-and-commit-restore drill and release acceptance remain open, as do the broader
exhaustive matrices recorded in the follow-up B/F/G checkpoint.

## Follow-up 2026-11-17 — Codex workflow handoff

The Codex devflow workflow assets, installed skills, and installer state are being
checked into the repository alongside this previously untracked checkpoint. This
does not change the outstanding release and acceptance work noted above.
