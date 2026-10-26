# X-INSIGHT production application image (S57, plan.md §3.1).
#
# Pinned base: python:3.12-slim
# (python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1,
#  CPython 3.12.15; local dev in S01 verified 3.12.14 — prod records 3.12.15).
#
# One image, three commands (see compose.prod.yaml):
#   app     uvicorn x_insight.app:app            (default CMD below)
#   worker  python -m x_insight.reasoning         (production polling loop, T8)
#   migrate python -m x_insight.operations.migrate (schema + approved DDI)
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
COPY content/assessments ./content/assessments
COPY content/history ./content/history
COPY content/questions ./content/questions
COPY content/bundles ./content/bundles
# Original sources and approval are ordinary Git files; the archived LFS
# evidence JSONL is deterministically rebuilt only during initial provisioning.
COPY content/ddi/ddinter/2026-10-21/sources ./content/ddi/ddinter/2026-10-21/sources
COPY content/ddi/ddinter/2026-10-21/candidate_dataset.json content/ddi/ddinter/2026-10-21/owner-reviewed-manifest.json ./content/ddi/ddinter/2026-10-21/
ENV X_INSIGHT_CONTENT_DIR=/srv/app/content \
    X_INSIGHT_ASSESSMENTS_DIR=/srv/app/content/assessments
# Sources are copied before sync so the locked project install sees the real
# package (a manifest-only sync would bake an empty wheel). The uv cache
# mount keeps rebuilds fast without shipping the cache in the image.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

RUN useradd -m -u 10001 app \
 && mkdir -p /var/lib/x-insight/recovery/backups /var/lib/x-insight/recovery/restores /var/lib/x-insight/secrets \
 && chmod 700 /var/lib/x-insight/secrets \
 && chown -R app:app /srv/app /var/lib/x-insight
USER app

EXPOSE 8000
# --proxy-headers: the edge terminates TLS and sets X-Forwarded-Proto, so
# request.url.scheme (and Secure session cookies) follow the public scheme.
# --no-access-log: access lines would log request paths (patient/record IDs);
# only safe structured app logs remain (S58 adds safe operational metrics).
CMD [".venv/bin/uvicorn", "x_insight.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
