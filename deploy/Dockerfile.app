# X-INSIGHT production application image (S57, plan.md §3.1).
#
# Pinned base: python:3.12-slim
# (python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1,
#  CPython 3.12.15; local dev in S01 verified 3.12.14 — prod records 3.12.15).
#
# One image, three commands (see compose.prod.yaml):
#   app     uvicorn x_insight.app:app            (default CMD below)
#   worker  python -m x_insight.reasoning         (production polling loop, T8)
#   migrate alembic upgrade head                 (explicit migration step)
#
# No credentials are baked in: DATABASE_URL/provider/staging paths all arrive
# as container environment (see .env.example placeholders + deploy/README.md).
FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy
WORKDIR /srv/app

RUN pip install --no-cache-dir "uv==0.12.16"
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
# Sources are copied before sync so the locked project install sees the real
# package (a manifest-only sync would bake an empty wheel). The uv cache
# mount keeps rebuilds fast without shipping the cache in the image.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

RUN useradd -m -u 10001 app \
 && mkdir -p /var/lib/x-insight/recovery/backups /var/lib/x-insight/recovery/restores \
 && chown -R app:app /srv/app /var/lib/x-insight/recovery
USER app

EXPOSE 8000
# --proxy-headers: the edge terminates TLS and sets X-Forwarded-Proto, so
# request.url.scheme (and Secure session cookies) follow the public scheme.
# --no-access-log: access lines would log request paths (patient/record IDs);
# only safe structured app logs remain (S58 adds safe operational metrics).
CMD [".venv/bin/uvicorn", "x_insight.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
