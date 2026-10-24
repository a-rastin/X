#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$PWD"
pg_bin="${PG_BIN:-/usr/lib/postgresql/16/bin}"
pg_port="${DDI_E2E_PG_PORT:-55582}"
api_port="${DDI_E2E_API_PORT:-18082}"
web_port="${DDI_E2E_WEB_PORT:-15182}"
web_origin="http://localhost:$web_port"
run_dir="$(mktemp -d /tmp/x-insight-ddi-e2e.XXXXXX)"
pg_run=()
pg_user="$(id -un)"
if [ "$(id -u)" = 0 ]; then
  pg_run=(runuser -u postgres --)
  pg_user=postgres
  chmod 755 "$run_dir"
fi
mkdir "$run_dir/db" "$run_dir/socket"
if [ "$(id -u)" = 0 ]; then chown postgres:postgres "$run_dir/db" "$run_dir/socket"; fi
make_pid=""
cleanup() {
  if [ -n "$make_pid" ]; then
    kill -TERM -- "-$make_pid" 2>/dev/null || true
    wait "$make_pid" 2>/dev/null || true
  fi
  "${pg_run[@]}" "$pg_bin/pg_ctl" -D "$run_dir/db" -m fast -w stop >/dev/null 2>&1 || true
  echo "Fresh-install logs and source-only content retained in $run_dir"
}
trap cleanup EXIT
"${pg_run[@]}" "$pg_bin/initdb" -D "$run_dir/db" -A trust --no-locale -E UTF8 >"$run_dir/initdb.log"
"${pg_run[@]}" "$pg_bin/pg_ctl" -D "$run_dir/db" -l "$run_dir/db/server.log" -o "-h 127.0.0.1 -p $pg_port -k $run_dir/socket" -w start
"$pg_bin/createdb" -h 127.0.0.1 -p "$pg_port" -U "$pg_user" ddi_fresh_install
"$pg_bin/createdb" -h 127.0.0.1 -p "$pg_port" -U "$pg_user" ddi_fresh_install_test

# Intentionally copy only ordinary Git content; archived evidence is not needed.
bundle="ddi/ddinter/2026-10-21"
mkdir -p "$run_dir/content/$bundle"
cp -R content/"$bundle"/sources "$run_dir/content/$bundle/"
cp content/"$bundle"/candidate_dataset.json content/"$bundle"/owner-reviewed-manifest.json "$run_dir/content/$bundle/"
cp -R content/assessments content/history content/questions content/bundles "$run_dir/content/"
export DATABASE_URL="postgresql+psycopg://$pg_user@127.0.0.1:$pg_port/ddi_fresh_install"
export TEST_DATABASE_URL="postgresql+psycopg://$pg_user@127.0.0.1:$pg_port/ddi_fresh_install_test"
export X_INSIGHT_CONTENT_DIR="$run_dir/content"
export X_INSIGHT_ASSESSMENTS_DIR="$run_dir/content/assessments"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/x-insight-uv-cache}"
unset DDI_DEFAULT_DATASET_VERSION

# Exercise the actual Make recipe with an isolated .env. Only the external
# Docker boundary is adapted to the already-running private PostgreSQL server;
# schema/DDI setup, API, Vite, HTTP and browser operations all execute for real.
mkdir "$run_dir/checkout" "$run_dir/bin"
cp Makefile "$run_dir/checkout/Makefile"
ln -s "$repo/backend" "$run_dir/checkout/backend"
ln -s "$repo/web" "$run_dir/checkout/web"
cat >"$run_dir/checkout/.env" <<EOF
POSTGRES_USER=$pg_user
POSTGRES_PASSWORD=isolated_e2e_only
POSTGRES_DB=ddi_fresh_install
TEST_POSTGRES_DB=ddi_fresh_install_test
DDI_DEFAULT_DATASET_VERSION=
EOF
cat >"$run_dir/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -eu
case "$*" in
  'compose ps --status running -q db'|'compose ps --status running -q db-test')
    echo isolated-postgres-fixture ;;
  'compose up -d --wait db db-test')
    echo 'Docker adapter: private PostgreSQL fixture is already healthy.' ;;
  *) echo "Unexpected Docker operation: $*" >&2; exit 1 ;;
esac
EOF
chmod +x "$run_dir/bin/docker"
echo "Starting real make dev against private PostgreSQL :$pg_port (Docker boundary adapted)."
PATH="$run_dir/bin:$PATH" DB_PORT="$pg_port" TEST_DB_PORT="$pg_port" \
  BACKEND_PORT="$api_port" WEB_PORT="$web_port" API_PROXY_TARGET="http://127.0.0.1:$api_port" \
  setsid make -C "$run_dir/checkout" dev >"$run_dir/dev.log" 2>&1 &
make_pid=$!
echo "Private make dev process group: $make_pid; log: $run_dir/dev.log"
for attempt in {1..900}; do
  if ! kill -0 "$make_pid" 2>/dev/null; then
    cat "$run_dir/dev.log"
    echo 'make dev exited before the application became healthy.' >&2
    exit 1
  fi
  if curl -fsS "$web_origin/api/v1/health" >/dev/null 2>&1; then break; fi
  if (( attempt % 30 == 0 )); then echo "Waiting for automatic setup and application startup ($attempt seconds)."; fi
  sleep 1
done
curl -fsS "$web_origin/api/v1/health"
echo
echo 'make dev served the application; running genuine DDI registration checks in both browsers.'
E2E_BASE_URL="$web_origin" npx playwright test --config e2e/playwright.fresh-install.config.ts --workers=1 "$@"
