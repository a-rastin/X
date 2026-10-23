#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${PG_BIN:-/usr/lib/postgresql/16/bin}"
pg_port="${DDI_E2E_PG_PORT:-55582}"
api_port="${DDI_E2E_API_PORT:-18082}"
web_port="${DDI_E2E_WEB_PORT:-15182}"
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
api_pid=""
web_pid=""
cleanup() {
  if [ -n "$web_pid" ]; then kill "$web_pid" 2>/dev/null || true; fi
  if [ -n "$api_pid" ]; then kill "$api_pid" 2>/dev/null || true; fi
  "${pg_run[@]}" "$pg_bin/pg_ctl" -D "$run_dir/db" -m fast -w stop >/dev/null 2>&1 || true
  echo "Fresh-install logs and source-only content retained in $run_dir"
}
trap cleanup EXIT
"${pg_run[@]}" "$pg_bin/initdb" -D "$run_dir/db" -A trust --no-locale -E UTF8 >"$run_dir/initdb.log"
"${pg_run[@]}" "$pg_bin/pg_ctl" -D "$run_dir/db" -l "$run_dir/db/server.log" -o "-h 127.0.0.1 -p $pg_port -k $run_dir/socket" -w start
"$pg_bin/createdb" -h 127.0.0.1 -p "$pg_port" -U "$pg_user" ddi_fresh_install

# Intentionally copy only ordinary Git content; archived evidence is not needed.
bundle="ddi/ddinter/2026-10-21"
mkdir -p "$run_dir/content/$bundle"
cp -R content/"$bundle"/sources "$run_dir/content/$bundle/"
cp content/"$bundle"/candidate_dataset.json content/"$bundle"/owner-reviewed-manifest.json "$run_dir/content/$bundle/"
cp -R content/assessments content/history content/questions content/bundles "$run_dir/content/"
export DATABASE_URL="postgresql+psycopg://$pg_user@127.0.0.1:$pg_port/ddi_fresh_install"
export X_INSIGHT_CONTENT_DIR="$run_dir/content"
export X_INSIGHT_ASSESSMENTS_DIR="$run_dir/content/assessments"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/x-insight-uv-cache}"
unset DDI_DEFAULT_DATASET_VERSION
(cd backend && uv run python -m x_insight.operations.migrate) >"$run_dir/migrate.log" 2>&1
(cd backend && exec uv run uvicorn x_insight.app:app --host 127.0.0.1 --port "$api_port") >"$run_dir/api.log" 2>&1 &
api_pid=$!
npm --prefix web run build
(cd web && exec env API_PROXY_TARGET="http://127.0.0.1:$api_port" node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port "$web_port" --strictPort) >"$run_dir/web.log" 2>&1 &
web_pid=$!
for attempt in {1..60}; do
  if curl -fsS "http://127.0.0.1:$web_port/api/v1/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$web_port/api/v1/health"
E2E_BASE_URL="http://127.0.0.1:$web_port" npx playwright test --config e2e/playwright.fresh-install.config.ts --workers=1 "$@"
