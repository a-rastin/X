# X-INSIGHT one-host production runbook (S57)

Topology (plan.md §3.1): `edge` (Caddy: TLS + static bundle + same-origin
`/api` proxy, the only externally reachable service) → `app` (FastAPI) +
`worker` (`python -m x_insight.reasoning` polling loop, spawning private MCP
stdio subprocesses internally) → `db` (PostgreSQL 16-alpine). No public DB or
MCP ports. The browser uses relative `/api/v1` routes at the same origin.
The worker is a single sequential polling loop today; plan §3.1's two
provider slots are capacity tuning that rides S58 measurement.

Pinned images/runtimes (see Dockerfiles for digests; verified 2026-10-11):

- app/worker/migrate: `python:3.12-slim` (CPython 3.12.15) + `uv==0.12.16`
  locked install (`backend/uv.lock`); non-root UID 10001
- edge build: `node:22-alpine` (Node 22 / npm 10); edge runtime: `caddy:2-alpine`
- db: `postgres:16-alpine` (PostgreSQL 16.x)

## 1. Fresh install (clean disposable host)

```sh
cp .env.example .env.prod          # then set POSTGRES_PASSWORD (URL-safe secret)
make prod-build                     # SITE_ADDRESS etc. come from .env.prod
make prod-up                        # starts db + app + worker + edge
make prod-migrate                   # explicit migration step (alembic upgrade head)
curl http://localhost/api/v1/health # {"status":"ok","service":"x-insight"}
curl http://localhost/api/v1/ready  # {"status":"ready",...} after migrate
```

Login with `admin` / `admin` once, then change the password: the singleton
seed runs one time at app startup and a changed password is never recreated
on restart (S03). `/health` is liveness (no database access, always 200 when
the process runs); `/ready` is readiness (200 only with a reachable,
schema-compatible database; 503 `MAINTENANCE` while a restore holds the
maintenance lock, S56).

Missing provider or content bundle: with no `X_INSIGHT_PROVIDER_ENDPOINT` /
`X_INSIGHT_PROVIDER_MODEL`, generation jobs fail closed with
`PROVIDER_NOT_CONFIGURED` (no retries consumed, draft retained) while
patient charts, search, and every admin surface stay usable. No test/stub
provider is ever enabled in production.

HTTPS outside localhost: set `SITE_ADDRESS=https://<public-host>`; Caddy
terminates TLS with automatic certificates and redirects `:80` to https.
Loopback stays plain HTTP. Session cookies are `Secure` whenever the public
scheme is https (uvicorn `--proxy-headers` + edge `X-Forwarded-Proto`).

## 2. Environment and key handling

- `.env.example` holds placeholders only — never commit a real `.env.prod`.
  `POSTGRES_PASSWORD` must be URL-safe (embedded in `DATABASE_URL`); the
  deployment encryption key for provider credentials (S42) is held outside
  the database and is never included in backups (plan §10.2).
- Provider settings: `X_INSIGHT_PROVIDER_ENDPOINT` (full POST URL of an
  OpenAI-compatible endpoint), `X_INSIGHT_PROVIDER_MODEL`, optional
  `X_INSIGHT_PROVIDER_CAPABILITY` (`schema`|`json`). Bearer-key persistence
  arrives with S42; until then only keyless endpoints (e.g. an explicitly
  allowed local model endpoint) can be configured — otherwise leave both
  empty for honest unavailable generation.
- Recovery state (`prod_recovery` volume, shared by app AND worker):
  backup staging, restore staging, the S56 maintenance lock, and the
  external operator recovery log.

## 3. Backup before upgrade

Take a consistent full backup through the admin Backup surface (S54) and
confirm the downloadable archive before touching anything. Upgrades:

```sh
make prod-build                       # build the new images
# backup via admin UI/API first (see above); note the backup id
docker compose -f compose.prod.yaml up -d db app worker edge
make prod-migrate                     # forward-only to the new schema
curl http://localhost/api/v1/ready
```

Test migrations on both a fresh database and the previous schema state
before touching production (plan §12.2): e.g. `alembic downgrade -1` on a
disposable copy, then `make prod-migrate`, then login/read/replay checks.

## 4. Compatible application/schema rollback

- Application rollback: redeploy the previous image tag
  (`APP_IMAGE_TAG=<prior> make prod-up`); the prior app refuses an
  incompatible schema honestly (`/ready` 503 `SCHEMA_INCOMPATIBLE`) instead
  of half-working.
- Schema rollback for a compatible step down: stop app/worker, then run the
  one-off `migrate` compose service with an Alembic entrypoint (`migrate`
  below is the compose service name — Alembic has no `migrate` subcommand —
  and `downgrade -1` replaces the service's default `upgrade head` command):
  `docker compose -f compose.prod.yaml run --rm --entrypoint .venv/bin/alembic migrate downgrade -1`.
  Only compatible downgrades are safe; otherwise restore the pre-upgrade
  backup with the S56 staged-restore procedure (validate → confirm exact
  digest → quiesce/pre-restore backup → switch/restart → verify → reopen),
  which revokes sessions/grants, fences old workers, and keeps maintenance
  held until verified healthy.

## 5. Migration failure recovery

Migrations run inside transactions (PostgreSQL DDL is transactional): a
failed `make prod-migrate` leaves the database at its prior version — the
app keeps serving that version (`/ready` goes 503 only if the schema is
forward-incompatible, never with partial DDL). Recover by:

1. `docker compose -f compose.prod.yaml logs migrate` — read the Alembic error.
2. `docker compose -f compose.prod.yaml run --rm --entrypoint .venv/bin/alembic migrate current`
   — confirm the database is still at the prior version (`migrate` is the
   compose service name; `current` replaces its default `upgrade head`).
3. Fix forward (new image with a corrected migration) or roll back the
   application image (§4). Never hand-edit schema or `alembic_version`.
4. Re-run `make prod-migrate`, then verify `/ready`, login, and a
   patient read before reopening to users.

## 6. Restart semantics (independent restarts)

Each service restarts independently; acknowledged state survives because it
is journaled in PostgreSQL (named volume `prod_pgdata`), never in process
memory:

```sh
docker compose -f compose.prod.yaml restart app     # HTTP only
docker compose -f compose.prod.yaml restart worker  # polling only
docker compose -f compose.prod.yaml restart db      # database only
```

- Restarting `app`: acknowledged drafts, signed records, and admin state
  persist; in-flight requests fail over to a retryable error and the
  browser autosave surfaces failed (never fake-saved) state.
- Restarting `worker`: durable jobs keep attempts and terminal artifacts;
  expired leases are reclaimed and eligible work resumes after lease
  recovery (S47); old fencing tokens can never commit after reclaim.
- Restarting `db`: app `/ready` reports 503 `DB_UNAVAILABLE` (retryable)
  while unreachable and returns to 200 without operator action; the worker
  polling loop keeps retrying its next pass.
- `/health` vs `/ready`: health proves the process is alive; ready proves
  the database/schema (and non-maintenance) state. Behind maintenance,
  `/ready` is 503 `MAINTENANCE` while `/health` stays 200.

## 7. Stopping processes

```sh
make prod-stop        # == docker compose down; volumes (database + recovery) are kept
```

Never `docker compose -f compose.prod.yaml down -v` in production — that
deletes `prod_pgdata` (the database) and `prod_recovery`. For a full
teardown of a disposable drill host only, `down -v` is the documented
destructive reset.

## 8. Logs contain no record text or secrets

- The app runs with `--no-access-log` (access lines would log request
  paths, which embed patient/record IDs). Structured app/worker logs carry
  only safe metadata: request/run/question/job IDs and status codes — never
  projections, prompts, CPTs, notes, names, identifiers, cookies, or keys.
- Verify after any change: `make prod-logs` and grep for a known drill
  patient name/identifier and for `POSTGRES_PASSWORD`/token shapes; any hit
  is a release-blocking defect. Edge access entries are discarded at both
  the site and default Caddy loggers (and uvicorn runs `--no-access-log`);
  the only residual URI echo is a proxy error line when the upstream is
  down — the grep above covers that path too. (S58 adds safe operational
  metrics; no clinical payloads ever enter logs or audit metadata.)

## 9. Capacity and operational status (S58)

Provisional targets (plan §11 — assumptions until measured on the
operator's own host): ~20 ordinary HTTP req/s, ordinary p95 < 1s,
2 vCPU / 4 GB RAM / 20 GB disk reference, 2 provider slots, restore drill
< 30 min. No SLA is promised; measure before claiming capacity.

- Synthetic load: `make test-load` (defaults: 50 patients, 4 workers,
  5 provider samples; report in `$LOAD_REPORT`). Scale with
  `LOAD_PATIENTS=10000 LOAD_WORKERS=8 make test-load`. Synthetic `9xxx`
  identifiers and letter-only names only — never real records. The report
  records host, dataset, duration, per-operation p95 (ordinary requests),
  and provider-path latency separately (deterministic stub stand-in; live
  LLM latency is NOT measured — keep provider p95 out of ordinary SLOs).
- Status: `GET /api/v1/ops/status` (admin only) exposes queue age /
  heartbeat, provider attempt/auth/retry counters, inference limits +
  local failures, staging-disk usage, last backup success, per-group
  request gauges, and the alert list. Counts/timestamps only. The same
  snapshot renders in the browser on the admin Status page (auto-refresh).
- Initial alerts: `QUEUE_AGE` (eligible work older than 5 min),
  `HEARTBEAT_STALE` (leased job silent over 2 min — check the worker
  process, then watch the next pass reclaim it), `PROVIDER_AUTH_FAILURES`
  (repeated auth failures in 24h — fix settings; repair starts a new
  pinned run), `DISK_USAGE` (staging disk over 80% — free space or move
  `BACKUP_STAGING_DIR` before the next backup).
- Admission limits (measured headroom, S58): at most 64 inference nodes,
  131 072 CPT cells, 1 MiB effective XML, 30 s per inference, 2 provider
  + 2 local slots, 100 queued runs, 3 attempts. Largest admitted content
  network measures 31 nodes / 4 509 cells / ~161 KB request / ~93 KB
  output / ~1.4 s inference — limits hold 2–29× headroom. Over-limit
  work fails closed with `inference_resource` (rejection diagnostics are
  preserved, never truncated into success).
