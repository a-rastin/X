# X-INSIGHT Installation and Running Guide

Last updated: 2026-10-18

This guide covers installing the current source tree, starting the application,
checking readiness, and stopping or restarting it. Choose local development for
code changes and isolated evaluation, or the Docker deployment for a Linux host.
Upgrade and recovery procedures continue in the [operator runbook](../deploy/README.md).

> X-INSIGHT is a research prototype. Installation does not establish clinical
> validation or release readiness. Provider integration, clinical-content release,
> and acceptance work remain open in the [current handoff](../project-documents/dev/progress/s62.md#follow-up-2026-10-18--documentation-assessment-and-next-work).

## Contents

- [Choose an installation mode](#choose-an-installation-mode)
- [Get the source code](#get-the-source-code)
- [Local development](#local-development)
- [Docker deployment on Linux](#docker-deployment-on-linux)
- [Provider configuration and current limits](#provider-configuration-and-current-limits)
- [Verify the installation](#verify-the-installation)
- [Stopping, restarting, and upgrading](#stopping-restarting-and-upgrading)
- [Troubleshooting](#troubleshooting)

## Choose an installation mode

| | Local development | Docker deployment |
| --- | --- | --- |
| Intended use | Development and isolated evaluation | One-host deployment rehearsal or controlled research environment |
| Host tools | Git, Bash, GNU Make, Python 3.12, uv, Node.js 22+, npm, Docker Compose, curl | Git, Bash, GNU Make, Docker with Compose/build support, curl |
| API and worker | Host Python processes | Separate containers sharing one application image |
| Web interface | Vite on port 5173 | Built bundle served by Caddy on ports 80/443 |
| Database | PostgreSQL 16 development/test containers on ports 5432/5433 | Internal PostgreSQL container with no published database port |
| Host dependency installation | `make setup` | Not required; Python and Node dependencies are installed inside image builds |

Install the host tools through your operating system or organization-managed tool
installer before continuing. A separate host PostgreSQL installation is unnecessary.
Initial dependency installation and image builds require access to their package
and image registries. Paid provider credentials are not required to start the UI,
manage accounts, or inspect records.

Check common prerequisites:

```sh
git --version
make --version
docker --version
docker compose version
docker info > /dev/null
curl --version
```

`docker info` must succeed for the account running the installation. For local
development, also check `uv --version`, `node --version`, and `npm --version`.
The project requires Python 3.12; the local pin is in
[backend/.python-version](../backend/.python-version). Compose must support
`up --wait`; the image build uses Dockerfile cache mounts.

## Get the source code

```sh
git clone https://github.com/a-rastin/X.git
cd X
```

Use an authorized clone URL if repository authentication is required. If you
already have a checkout, open a terminal at its root instead. Keep the selected
revision consistent across the application, worker, migrations, and web bundle.
All commands below start at that root unless a block explicitly changes directory.

## Local development

### 1. Install locked dependencies

```sh
make setup
```

This installs the backend and development tools with `uv sync --locked --group dev`,
the web dependencies with `npm ci`, and the root Playwright tooling with `npm ci`.
It does not create a database or start services. Keep the checked-in lockfiles.

### 2. Create local configuration

On a fresh checkout:

```sh
cp .env.example .env
chmod 600 .env
```

If `.env` already exists, edit it instead of overwriting it. It is Git-ignored.
Set the database password and matching connection strings. The following values
are placeholders to replace in your own file:

```dotenv
POSTGRES_USER=x_insight
POSTGRES_PASSWORD=REPLACE_WITH_URL_SAFE_LOCAL_PASSWORD
POSTGRES_DB=x_insight
TEST_POSTGRES_DB=x_insight_test
DB_PORT=5432
TEST_DB_PORT=5433
DATABASE_URL=postgresql://x_insight:REPLACE_WITH_URL_SAFE_LOCAL_PASSWORD@localhost:5432/x_insight
TEST_DATABASE_URL=postgresql://x_insight:REPLACE_WITH_URL_SAFE_LOCAL_PASSWORD@localhost:5433/x_insight_test
```

Use the same password in all three password locations. A URL-safe password keeps
both shell loading and connection strings straightforward; otherwise quote shell
values and percent-encode the password inside each URL. Keep the development and
test database names distinct. Tests can replace or clear their database state.

If ports 5432/5433 are occupied, choose available `DB_PORT`/`TEST_DB_PORT` values
and update the respective URL ports. `BACKEND_PORT` and `WEB_PORT` select the API
and Vite ports (defaults 8000 and 5173). Unless explicitly configured,
`API_PROXY_TARGET` follows the selected backend port.

### 3. Start the API, web server, and databases

At the repository root, run this command and keep it running:

```sh
make dev
```

`make dev` loads `.env` itself. Explicit caller database URLs, database/API/web
ports, PostgreSQL credentials and DDI default selection take precedence over
that file. It starts `db` and `db-test`, waits for database health, then applies
schema migrations and approved DDI data automatically. Only after setup succeeds
does it start the reload-enabled API and Vite. It does not start a local worker.

The database-port preflight permits this project's already-running database
containers and rejects unrelated listeners. Stop a different stack or choose
free ports and update the corresponding database URLs before retrying.

### 4. Wait for automatic setup and verify

No separate migration command is required. After the foreground startup reports
the database and approved DDI are ready, verify from another terminal:

```sh
curl --fail http://localhost:8000/api/v1/health
curl --fail http://localhost:8000/api/v1/ready
```

Use the selected backend port if you overrode its default.

Automatic startup installs the bundled owner-approved DDInter release into a
database with no released DDI data.
It rebuilds offline from committed sources without Git LFS, manual publication,
or a model provider. The first rebuild/import may take several minutes; wait for
`READY: ddi-c45e2051cf16` (1,939 drugs, 222,383 source occurrences). Repeats reuse
the exact immutable release. Existing released datasets are preserved unless the
bundled version is explicitly configured; see the [bootstrap rules](ddinter-operations.md#fresh-installation-with-the-bundled-approved-release).

Current schema head is `0022`. Setup failure exits before either local server
starts; correct the reported error and run `make dev` again. `make migrate`
remains an optional operator tool for setup without launching servers. Plain
`alembic upgrade head` creates schema without provisioning DDI data; `/ready`
alone does not prove a DDI release is available. The public liveness endpoint can
return 200 even while the database is unavailable or incompatible.

### 5. Sign in

Open [http://localhost:5173](http://localhost:5173) and select the administrator
role. On a fresh database, use username `admin` and password `admin`. Change the
password before shared use. This account is seeded once; restarting the app does
not reset a changed password.

Create physician accounts through administrator account management. Physicians
use their own accounts; self-registration is unavailable. The initial UI and
account check does not require a model provider.

### 6. Start the background worker

To process queued generation and local-calculation work, open another terminal at the
repository root:

```sh
set -a
. ./.env
set +a
cd backend
uv run python -m x_insight.reasoning
```

Keep this process running. It uses the same application database as the API.
The worker launches private MCP subprocesses itself; no separately hosted MCP
server or inbound MCP port is required. Missing endpoint/model configuration
produces `PROVIDER_NOT_CONFIGURED` for generation. Local calculation does not
call the provider, but still needs the worker and an eligible successful original
execution.

## Docker deployment on Linux

### 1. Prepare the host and environment file

Use a fresh shell without exported development configuration. Shell environment
variables can override values passed through a Compose environment file. Ensure
ports 80/443 are available. For public HTTPS, point your domain at the host and
allow the required incoming HTTP/HTTPS traffic before starting Caddy.

Keep server credentials outside the checkout. The repository ignores `.env`, but
currently does **not** ignore `.env.prod`. For a fresh installation:

```sh
X_INSIGHT_PROD_ENV_FILE="$HOME/.config/x-insight/production.env"
umask 077
mkdir -p "$(dirname "$X_INSIGHT_PROD_ENV_FILE")"
cp .env.example "$X_INSIGHT_PROD_ENV_FILE"
chmod 600 "$X_INSIGHT_PROD_ENV_FILE"
```

If this file already exists, preserve and edit it. Define
`X_INSIGHT_PROD_ENV_FILE` again whenever you open a new operator shell; every
command below passes it explicitly to Make or Compose. Edit at least:

| Setting | Value to supply |
| --- | --- |
| `POSTGRES_PASSWORD` | A unique URL-safe database secret; Compose embeds it in the container connection URL |
| `POSTGRES_USER`, `POSTGRES_DB` | Normally keep both as `x_insight` |
| `SITE_ADDRESS` | `http://localhost` for a host-local rehearsal, or `https://your-domain.example` for public HTTPS |
| `APP_IMAGE_TAG` | An operator-selected image tag; the template defaults to `prod` |
| `X_INSIGHT_PROVIDER_ENDPOINT`, `X_INSIGHT_PROVIDER_MODEL` | Leave empty for an installation without generation; see the provider section before setting them |

Production Compose constructs the application URL using the internal hostname
`db:5432`. The template's host-local `DATABASE_URL`, `TEST_DATABASE_URL`, and local
port variables do not configure the production database connection or published
ports. Do not source the server environment file into your shell.

### 2. Check packaging and build

```sh
make prod-check
make prod-build PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
```

The build creates the application/worker image and Caddy/web image using the
checked-in Dockerfiles and lockfiles. The packaging check is static and does not
prove that the services can start. No host `make setup` is needed for this mode.

### 3. Start services with automatic setup

```sh
make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"
docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" ps
```

`prod-up` automatically runs the one-shot `migrate` setup service after database
health succeeds. Both API and worker require its successful completion before
starting; ordinary production Compose startup follows the same dependencies.
Setup applies schema and the bundled approved DDI data from ordinary Git inputs
included in the image. It rebuilds generated evidence locally without Git LFS.
No separate `prod-migrate` command is needed for normal startup; that command
remains available for operator diagnostics or deliberate setup without the app.
Inspect setup output with `docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" logs migrate`.
First setup may take several minutes. Verify both `/health` and `/ready` after
startup; the API container health check uses `/health` alone.

Only Caddy publishes host ports. The API and database remain on the internal
Compose network. Persistent volumes hold the database (`prod_pgdata`) and shared
backup/restore state (`prod_recovery`); actual Docker volume names include the
Compose project prefix.

### 4. Verify and sign in

For the host-local `SITE_ADDRESS=http://localhost` rehearsal:

```sh
curl --fail http://localhost/api/v1/health
curl --fail http://localhost/api/v1/ready
```

For HTTPS, use your configured origin instead:

```sh
curl --fail https://your-domain.example/api/v1/health
curl --fail https://your-domain.example/api/v1/ready
```

Replace the example hostname. Caddy serves the web interface at that same origin,
handles TLS for a configured public hostname, and proxies `/api` internally.
Sign in as `admin` / `admin` only on a fresh database, change the password, and
create physician accounts. Complete these setup steps before making the instance
available to other users.

## Provider configuration and current limits

Record access and administration can run without proposal generation. The current
provider paths have different behavior:

| Path | Current behavior |
| --- | --- |
| Generation worker | Uses the settings revision pinned on the job: saved endpoint, model, and decrypted bearer. Reloads that revision on each claim |
| Administrator Provider page | Saves encrypted credentials, displays masked revisions, and performs a capability check. Replace or clear cancels active generation jobs |
| Keyless fallback | Jobs queued before any saved revision still read `X_INSIGHT_PROVIDER_ENDPOINT`, `X_INSIGHT_PROVIDER_MODEL`, and `X_INSIGHT_PROVIDER_CAPABILITY`. That path sends no bearer |
| Production environment wiring | `compose.prod.yaml` passes `X_INSIGHT_PROVIDER_DATA_KEY` and `X_INSIGHT_PROVIDER_ALLOWED_HOSTS` to the API and the worker |

The saved endpoint is the full provider POST URL. Capability is `schema` or
`json` (default `schema`). If the endpoint rejects `response_format`, generation
retries that one request without it and still validates the JSON. A saved key
is not enough: the deployment key must decrypt it, and the host must stay on
the allowlist at call time. Local recalculation never calls the provider.

For local testing of the administrator settings surface, put the following in
your private `.env`, then reload it and restart the API:

| Variable | Requirement |
| --- | --- |
| `X_INSIGHT_PROVIDER_DATA_KEY` | Base64 encoding of exactly 32 random bytes; keep this deployment key outside the database and backup archives |
| `X_INSIGHT_PROVIDER_ALLOWED_HOSTS` | Comma-separated exact hostnames, without URL scheme/path; an empty list denies all destinations |

Capability checks also resolve DNS. Special-use addresses require the resolved
IP to be explicitly allowed, except `198.18.0.0/15` (fake-IP proxy range).
Redirects are not followed. The same allowlist restricts generation calls that use a saved revision.
Choose destinations under operator control; capability checks and generation
send an outbound request.

Keep the same deployment key available for encrypted-credential recovery; entering
the provider API key in the browser does not supply this encryption key. Production
Compose passes both variables into the API and the worker from the external
environment file. A container built before that wiring still needs a recreate.

The approved [11 question packages](../content/questions/review-confirmation.s39-v1.json)
do not by themselves release workflow bundles, history/instrument dependencies,
or the DDI corpus. A successful installation does not promise that a fresh
database can complete the clinical-generation workflow.

## Verify the installation

The basic installation check is:

1. `/api/v1/health` returns HTTP 200 and identifies `x-insight`.
2. `/api/v1/ready` returns HTTP 200 after migration to the expected schema.
3. The web interface loads, administrator login works, and physician accounts can
   be managed.
4. If background processing is needed, the worker is running and uses the intended
   database. Provider/content failures remain explicit rather than becoming mock
   successful proposals.
5. In a new registration draft, Medications shows `DDInter supplied CSV exports`,
   version `ddi-c45e2051cf16`, and `1939 drugs` after fresh setup. The normal setup
   command must have completed successfully; schema readiness alone does not check
   the DDI catalog. Severity-only details and unavailable missing-pair coverage remain.

For developer verification, use a disposable database and synthetic records.
From the repository root, load `.env` before backend/recovery checks:

```sh
set -a
. ./.env
set +a
make check
make test-backend TEST=tests/http/test_health.py
```

Additional commands are `make test-backend`, `make test-recovery`, and
`make test-load`. They are optional developer/rehearsal checks, not installation
steps for an instance containing records. Backend and recovery fixtures use
`TEST_DATABASE_URL`; the Make targets do not all load `.env` themselves.

For browser testing, install Chromium and Firefox with
`npx playwright install chromium firefox`. Use an isolated API backed by the test
database and stop the dev web server before running `make test-e2e`: the target
starts a preview on port 5173. `API_PROXY_TARGET` selects the API proxy target;
`E2E_BASE_URL` selects only the browser destination. Recovery browser scenarios
can replace the database behind that API. `make verify` includes these browser
tests as well as backend/static checks. Known failures and unrun acceptance
checks remain recorded in the [release report](../project-documents/dev/progress/s62.md).

## Stopping, restarting, and upgrading

| Task | Local development | Docker deployment |
| --- | --- | --- |
| Stop | `Ctrl+C` in API/web/worker terminals, then `make stop` | `make prod-stop PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"` |
| Start again | Run `make dev`, then start the separate worker | `make prod-up PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"` |
| Inspect services | `docker compose ps`; API/web/worker terminal output | `docker compose -f compose.prod.yaml --env-file "$X_INSIGHT_PROD_ENV_FILE" ps` |
| Application logs | Foreground process terminals | `make prod-logs PROD_ENV_FILE="$X_INSIGHT_PROD_ENV_FILE"` |

Stopping Compose preserves its volumes. Do not use `down -v` on a deployment whose
records or recovery artifacts must survive. Current production Compose declares
no automatic restart policy; arrange supervised startup or explicitly run
`prod-up` after a host restart.

`Ctrl+C`, or either local server exiting, stops the API/web launcher and its owned
child processes so their ports can be reused. The separately started worker must
be stopped separately. After changing local environment values, rerun `make dev`
and reload `.env` for the separate worker. After changing server environment
values, use `prod-up` to
reconcile container configuration; `docker compose restart` alone does not apply
new environment values. Rebuild images when source or locked dependencies change.

Before upgrading, download and verify a full backup through the administrator
Backup surface, retain the previous image/revision and environment configuration,
and keep the deployment key separately. Use the
[backup-before-upgrade and rollback procedure](../deploy/README.md#3-backup-before-upgrade).
For restore, use validation, exact-digest confirmation, maintenance/fencing, and
the documented recovery process. Restore revokes sessions, so users must sign in
again. Do not clear a maintenance lock as a generic startup fix.

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| `uv`, `node`, `npm`, `make`, or Compose is missing | Install the prerequisites for your chosen mode and repeat the version checks; host Python/Node are needed only for local development |
| Docker daemon or permission error | Confirm `docker info` succeeds for the installation account before retrying Compose |
| `make dev` reports an occupied database port | This project's running containers are allowed; stop the unrelated listener or choose free database ports and update both URLs |
| API/web ports are occupied | Stop the other process or choose available `BACKEND_PORT`/`WEB_PORT` values, then rerun `make dev` |
| `/health` is 200 but `/ready` is 503 `SCHEMA_INCOMPATIBLE` | Confirm the API uses the startup target database and matching revision. Normal startup applies schema automatically; current expected head is `0022`. Manual migration commands remain optional recovery tools |
| Medications shows `DDI dataset is unavailable.` after a fresh installation | Inspect `make dev` setup output or production `logs migrate`, confirm the API uses that database, and correct the reported setup error before restarting. See [DDI bootstrap recovery](ddinter-operations.md#fresh-installation-with-the-bundled-approved-release). An invalid `DDI_DEFAULT_DATASET_VERSION` still fails explicitly |
| `/ready` reports `DB_UNAVAILABLE` | Check database health and the API process's actual connection configuration; confirm password, hostname, port, and database name |
| Password changes in `.env` do not fix database login | PostgreSQL initialization variables do not change credentials inside an existing volume; use its existing credentials or a deliberate database password change |
| Vite loads but API calls fail | Check the API on port 8000 and `API_PROXY_TARGET`; restart Vite after changing its proxy environment |
| Admin login fails on a reused database | The original default password is not restored on restart; use the existing account credentials |
| Migration container exits with an error | Inspect production `logs migrate`; API/worker startup waits for setup success. Follow the runbook's migration-failure procedure, then rerun `prod-up` |
| Generation reports `PROVIDER_NOT_CONFIGURED` | The worker lacks an endpoint/model; configure its environment and restart it, accounting for the saved-settings integration gap |
| Generation or local calculation stays queued | Confirm the separate worker is running, connected to the same database, and not held by restore maintenance |
| Provider key cannot be saved | The API needs a valid deployment data key and an allowed destination; a browser API key alone is insufficient |
| Provider is saved/tested but generation still fails | Saved settings are not loaded by the current worker; review provider and content integration limits above |
| Production settings ignore the data key/allowed hosts in the environment file | The checked-in Compose file does not forward those two variables; this remains implementation work |
| `/ready` reports `MAINTENANCE` after restore | Follow the operator recovery/rollback procedure and verify state before reopening access |

## Related documentation

- [Project overview](../README.md)
- [Operator runbook: upgrades, backups, restore, and rollback](../deploy/README.md)
- [Progress index and current handoff](../project-documents/dev/progress/README.md)
- [Provider settings checkpoint](../project-documents/dev/progress/s42.md)
- [Content review ledger](../content/review-ledger.md)

This guide was checked against the repository configuration and entry points.
It does not record a new clean-host installation, live-provider test, or release
acceptance run; use the dated progress reports for execution evidence.
