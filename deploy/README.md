# X-INSIGHT one-host production runbook (S57)

For prerequisites, source checkout, environment setup, first startup and login,
use the [Installation and Running Guide](../docs/installation.md). This runbook
covers deployment operation, upgrades, backup, restore, and rollback. Current
provider-storage behavior is recorded in [S42](../project-documents/dev/progress/s42.md);
the dated rehearsal notes below retain their original scope.

For supplied DDInter data, use the [DDInter operations runbook](../docs/ddinter-operations.md)
for migration `0022`, source/evidence review, privileged atomic publication,
verification and default rollback. `DDI_DEFAULT_DATASET_VERSION` is forwarded to
both app and worker; changing its environment value requires recreating both
services. Existing report pins and selection associations retain their release.
The full review candidate is ready; genuine owner approval and publication into
an identified application target remain pending. The isolated full-volume
[acceptance evidence](../docs/verification/ddinter-2026-10-21/README.md) includes
backup/restore measurements and their limits. Take pre-upgrade backups using the
currently deployed version before migration; older `0021` archives remain supported.

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

Follow the [Docker installation steps](../docs/installation.md#docker-deployment-on-linux):
`prod-build`, `prod-up`, then explicit `prod-migrate`, using the same external
`PROD_ENV_FILE` throughout. Check both health and readiness through the edge.
These instructions do not close the S62 release gate.

Login with `admin` / `admin` once, then change the password: the singleton
seed runs one time at app startup and a changed password is never recreated
on restart (S03). `/health` is liveness (no database access, always 200 when
the process runs); `/ready` is readiness (200 only with a reachable,
schema-compatible database; 503 `MAINTENANCE` while a restore holds the
maintenance lock, S56).

Missing provider or content bundle: with no saved provider revision and no
`X_INSIGHT_PROVIDER_ENDPOINT` / `X_INSIGHT_PROVIDER_MODEL`, generation jobs
fail closed with `PROVIDER_NOT_CONFIGURED` (no retries consumed, draft
retained) while patient charts, search, and every admin surface stay usable.
No test/stub provider is ever enabled in production.

HTTPS outside localhost: set `SITE_ADDRESS=https://<public-host>`; Caddy
terminates TLS with automatic certificates and redirects `:80` to https.
Loopback stays plain HTTP. Session cookies are `Secure` whenever the public
scheme is https (uvicorn `--proxy-headers` + edge `X-Forwarded-Proto`).

2026-10-16 clean-checkout note. This does not replace the commands above.
`make prod-up` was not part of that drill. An archive of HEAD
`217b1567eaefeedbc71a164f91db65a616df71da` plus the dirty recovery Makefile
was installed with the locked `uv` and `npm` installs, migrated to head
`0018` on a throwaway PostgreSQL 16 database (removed afterward), and showed
one `admin` row before the app started. `GET /api/v1/health` and
`POST /api/v1/auth/login` (`admin` / `admin`) were both 200.
`make prod-check` and `make prod-build` exited 0. Local image ids, not a
registry push; app and worker share the app image:

- `x-insight-app:prod` `sha256:0577b391dab80800465ece25ffc06ca3792f6fefa9a66fc1f9e8dbae4160c04e` (1368697406 bytes, 2026-10-16T19:13:57Z)
- `x-insight-edge:prod` `sha256:4427696632ef3af9dd1b23dceee6785df23317bc1d7a577195e09a77ce83f87d` (93551187 bytes, 2026-10-16T18:47:47Z)

The build log also printed manifest list
`sha256:ae25b8fc792a626b17ea4ec6f54d885b3615762b9314d9f620afa88d400544b1`
while naming the app tag. The tag's inspected id after the build is
`0577b391…`, not `ae25b8fc…`. Full record:
[S62](../project-documents/dev/progress/s62.md).

## 2. Environment and key handling

- `.env.example` holds placeholders only. Keep the server environment file
  outside the checkout: `.env.prod` is not currently Git-ignored. The installation
  guide uses `X_INSIGHT_PROD_ENV_FILE="$HOME/.config/x-insight/production.env"`;
  define that shell variable in each operator terminal and pass
  `PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"` to every production Make command.
  Use a fresh shell without exported development configuration, which can
  override Compose environment-file values. `POSTGRES_PASSWORD` must be
  URL-safe because Compose embeds it in `DATABASE_URL`.
- Key escrow: S42 stores provider credentials using `X_INSIGHT_PROVIDER_DATA_KEY`
  (base64 of exactly 32 bytes), held outside the database and backup archive.
  Stored credentials cannot be decrypted without the matching deployment key;
  retain it separately for recovery. The Provider page accepts provider settings
  and API keys, not the deployment encryption key. Ordinary charts and
  administration do not require provider generation.
- Provider settings: save the full OpenAI-compatible POST URL, model, and API
  key on the Provider page. Generation uses the revision pinned on the job,
  decrypts the key with `X_INSIGHT_PROVIDER_DATA_KEY`, and sends it as a bearer
  token. The host must be in `X_INSIGHT_PROVIDER_ALLOWED_HOSTS`. Production
  Compose passes both variables to the app and the worker. Replacing or
  clearing the key cancels active generation jobs; local recalculation is
  independent. `X_INSIGHT_PROVIDER_ENDPOINT` / `MODEL` / optional `CAPABILITY`
  (`schema`|`json`) are the keyless fallback for jobs queued before any saved
  revision. Leave generation unconfigured unless a saved provider, or that
  keyless fallback, is deliberately compatible. A capability test is not by
  itself a successful generation.
- Recovery state (`prod_recovery` volume, shared by app AND worker):
  backup staging, restore staging, the S56 maintenance lock, and the
  external operator recovery log.

## 3. Backup before upgrade

Take a consistent full backup through the admin Backup surface (S54) and
confirm the downloadable archive before touching anything. Upgrades:

```sh
make prod-build PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
# backup via admin UI/API first (see above); note the backup id
make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
make prod-migrate PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
curl http://localhost/api/v1/ready
```

Test migrations on both a fresh database and the previous schema state
before touching production (plan §12.2): e.g. `alembic downgrade -1` on a
disposable copy, then `make prod-migrate`, then login/read/replay checks.

## 4. Compatible application/schema rollback

- Application rollback: redeploy the previous image tag
  (`APP_IMAGE_TAG=<prior> make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"`);
  replace `<prior>` with the retained image tag. The prior app refuses an
  incompatible schema honestly (`/ready` 503 `SCHEMA_INCOMPATIBLE`) instead
  of half-working.
- Schema rollback for a compatible step down: stop app/worker, then run the
  one-off `migrate` compose service with an Alembic entrypoint (`migrate`
  below is the compose service name — Alembic has no `migrate` subcommand —
  and `downgrade -1` replaces the service's default `upgrade head` command):
  `docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" run --rm --entrypoint .venv/bin/alembic migrate downgrade -1`.
  Only compatible downgrades are safe; otherwise restore the pre-upgrade
  backup with the S56 staged-restore procedure (validate → confirm exact
  digest → quiesce/pre-restore backup → switch/restart → verify → reopen),
  which revokes sessions/grants, fences old workers, and keeps maintenance
  held until verified healthy. S62 (2026-10-16) re-ran the public commit,
  session-revocation, and failed-switch rollback tests on the test database.
  That is not a second production-volume drill.

## 5. Migration failure recovery

Migrations run inside transactions (PostgreSQL DDL is transactional): a
failed `make prod-migrate` leaves the database at its prior version — the
app keeps serving that version (`/ready` goes 503 only if the schema is
forward-incompatible, never with partial DDL). Recover by:

1. Read the Alembic error in the `prod-migrate` terminal output; the migration
   container uses `--rm`, so its container logs are not retained afterward.
2. `docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" run --rm --entrypoint .venv/bin/alembic migrate current`
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
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" restart app
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" restart worker
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" restart db
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
make prod-stop PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
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
- Verify after any change: `make prod-logs PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"`
  and grep for a known drill
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

## 10. Known limits (S62 rehearsal, 2026-10-16)

The rehearsal did not remove the release limits. S42 subsequently implemented
provider administration; the runtime/environment integration boundary below is
current. Older dated rehearsal evidence remains unchanged.

- No high availability. No automatic backup schedule; take a backup before
  an upgrade (§3). Not clinical validation. S39.b approved the 11 question
  packages; the DDI corpus and other instruments stay as their own reports
  left them.
- Live provider calls were not part of this drill. S42 storage and the Provider
  page are implemented; saved configuration is not yet consumed by the worker,
  and production encryption-key/allowed-host wiring remains open. S58 did not
  measure live-LLM latency.
- `make test-recovery` runs `cd backend && uv run pytest tests/recovery`.
  It does not source `.env` and does not create or drop a volume. It needs
  `TEST_DATABASE_URL` when the test port is not 5433. On the rehearsal host,
  `db-test` is port 5443, database `x_insight_test`.
