#!/bin/sh
# S57 one-host production packaging self-check (installation mechanics only).
# Static packaging assertions; live behavior is proven by the disposable-host
# drill recorded in project-documents/dev/progress/s57.md (T1/T8/T9/T10).
# Fails nonzero with the first violated guarantee. No secrets are read here:
# only placeholder shapes in tracked files are inspected.
set -eu

fail() { echo "deploy/check FAILED: $1" >&2; exit 1; }
pass() { echo "deploy/check OK: $1"; }

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

# --- §1: pinned production images -------------------------------------------
grep -Eq '^FROM [^ ]+@sha256:[0-9a-f]{64}' deploy/Dockerfile.app \
  || fail "Dockerfile.app must pin its base image with a digest"
grep -Eq '^USER [^r]' deploy/Dockerfile.app \
  || fail "Dockerfile.app must drop to a non-root USER"
grep -Eq -- '--proxy-headers' deploy/Dockerfile.app \
  || fail "Dockerfile.app must run uvicorn with --proxy-headers (edge terminates TLS)"
grep -Eq '^FROM [^ ]+@sha256:[0-9a-f]{64}' deploy/Dockerfile.edge \
  || fail "Dockerfile.edge must pin its base images with digests"
! grep -Ei 'POSTGRES_PASSWORD=.{0,3}[^$:{]|JWT|API_KEY=.+|password.+admin' deploy/Dockerfile.app deploy/Dockerfile.edge \
  || fail "Dockerfiles must not embed credentials"

# --- §1: Compose topology ----------------------------------------------------
[ -f compose.prod.yaml ] || fail "compose.prod.yaml is missing"
for svc in edge app worker db; do
  grep -Eq "^  $svc:" compose.prod.yaml || fail "compose.prod.yaml must define service '$svc'"
done
# No public DB/MCP ports: only the edge publishes host ports.
! awk '/^  db:/,/^  [a-z]/' compose.prod.yaml | grep -Eq '^\s+ports:' \
  || fail "prod db service must publish no host ports"
! awk '/^  worker:/,/^  [a-z]/' compose.prod.yaml | grep -Eq '^\s+ports:' \
  || fail "prod worker service must publish no host ports"
! awk '/^  app:/,/^  [a-z]/' compose.prod.yaml | grep -Eq '^\s+ports:' \
  || fail "prod app service must publish no host ports (edge-only ingress)"
grep -Eq '^\s+-\s+"?80:80"?\s*$' compose.prod.yaml \
  || fail "prod edge must publish port 80"
grep -Eq '^\s+-\s+"?443:443"?\s*$' compose.prod.yaml \
  || fail "prod edge must publish port 443"
# Persistent database volume (named volume mounted at PGDATA).
grep -Eq 'prod_pgdata:/var/lib/postgresql/data' compose.prod.yaml \
  || fail "prod db must persist a named volume at /var/lib/postgresql/data"
grep -Eq '^volumes:' compose.prod.yaml \
  || fail "compose.prod.yaml must declare persistent volumes"
# No dev/test provider override in production.
! grep -Ei 'ControlledStub|TEST_|target:\s*development|VITE_' compose.prod.yaml \
  || fail "prod compose must not enable dev overrides or the test provider stub"
# Worker runs the production polling entry point, never the stub directly.
grep -Eq 'x_insight.reasoning' compose.prod.yaml \
  || fail "prod worker must run the x_insight.reasoning polling entry point"
# Structural validation with a dummy secret (the :? guard requires it set).
POSTGRES_PASSWORD=check-only-placeholder docker compose -f compose.prod.yaml config >/dev/null \
  || fail "compose.prod.yaml does not validate with POSTGRES_PASSWORD set"

# --- §1: edge terminates TLS, proxies /api at the same origin ----------------
[ -f deploy/Caddyfile ] || fail "deploy/Caddyfile is missing"
grep -Eq 'reverse_proxy' deploy/Caddyfile \
  || fail "Caddyfile must reverse-proxy the API"
grep -Eq '/api' deploy/Caddyfile \
  || fail "Caddyfile must route /api to the app at the same origin"
# Operative HTTPS enforcement (plan §11): strip `#` comments first, so prose
# about TLS can never satisfy this — only real directives count.
stripped_caddy="$(sed 's/#.*//' deploy/Caddyfile)"
printf '%s\n' "$stripped_caddy" | grep -Eq '^[[:space:]]*redir[[:space:]]' \
  || fail "Caddyfile must contain an operative http→https redir directive (plan §11)"
printf '%s\n' "$stripped_caddy" | grep -Eq 'https://' \
  || fail "Caddyfile must contain an operative https redirect target"
# S57 §4 log secrecy (operative): the edge discards access lines (request URIs
# embed patient/record IDs) and keeps them off the default logger; the app
# image disables uvicorn access lines for the same reason.
printf '%s\n' "$stripped_caddy" | grep -Eq 'output[[:space:]]+discard' \
  || fail "Caddyfile must discard edge access logs (log { output discard })"
printf '%s\n' "$stripped_caddy" | grep -Eq 'exclude[[:space:]]+.*http\.log\.access' \
  || fail "Caddyfile must exclude access entries from the default logger"
grep -Eq -- '--no-access-log' deploy/Dockerfile.app \
  || fail "Dockerfile.app must run uvicorn with --no-access-log (paths embed record IDs)"
# Browser uses relative origin routes at the same origin: no absolute
# http(s) origins, dev hosts/ports, or configured API origins in web
# sources (they would bypass the edge's same-origin /api proxy).
! grep -rnE 'https?://' web/src --include='*.ts' --include='*.tsx' \
  || fail "web sources must not contain absolute http(s) origins (relative /api/v1 only)"
! grep -rnE ':5173|PUBLIC_ORIGIN|VITE_[A-Za-z_]*API|API_BASE_URL|baseURL|API_ORIGIN' web/src --include='*.ts' --include='*.tsx' \
  || fail "web sources must not configure an absolute API origin"

# --- §2/§3: env placeholders only, documented operations ----------------------
for key in POSTGRES_PASSWORD SITE_ADDRESS X_INSIGHT_PROVIDER_ENDPOINT X_INSIGHT_PROVIDER_MODEL; do
  grep -Eq "^#? ?$key=" .env.example || fail ".env.example must document placeholder $key"
done
# Tracked .env.example holds placeholders only: every sensitive assignment
# must be empty or carry an obvious placeholder marker (CHANGEME/example/
# 127.0.0.1 anywhere in the value). This MUST fail loudly (never `|| true`):
# a real secret value here is a release-blocking defect.
if grep -E '^(POSTGRES_PASSWORD|DATABASE_URL|.*SECRET.*|.*API_KEY.*)=' .env.example \
    | grep -Ev '=(.*CHANGEME.*|.*(example|127\.0\.0\.1).*|)$'; then
  fail "tracked .env.example contains a real-looking secret (placeholders only)"
fi
pass "no tracked real-looking secrets"
grep -Eq 'CHANGEME|example|localhost|127\.0\.0\.1' .env.example \
  || fail ".env.example values must be obvious placeholders"
for target in prod-build prod-up prod-migrate prod-stop prod-logs prod-check; do
  grep -Eq "^$target:" Makefile || fail "Makefile must define target '$target'"
done
[ -f deploy/README.md ] || fail "deploy/README.md is missing"
grep -Ei -q 'backup.before.upgrade' deploy/README.md \
  || fail "deploy/README.md must document backup-before-upgrade"
grep -Ei -q 'rollback' deploy/README.md \
  || fail "deploy/README.md must document rollback"
grep -Ei -q 'migration failure|failed migration' deploy/README.md \
  || fail "deploy/README.md must document migration failure recovery"
grep -Ei -q 'stopping|stop the stack' deploy/README.md \
  || fail "deploy/README.md must document stopping processes"
grep -Ei -q 'encryption key|secret|POSTGRES_PASSWORD' deploy/README.md \
  || fail "deploy/README.md must document environment/key handling"

pass "one-host production packaging self-check green"
