"""Operational status metrics and initial alerts (S58 §3; plan.md §11, FR-43).

Public shape (seam T1 — ``GET /api/v1/ops/status``, admin-only): counts and
timestamps only, never secrets, record text, tokens, or clinical payloads.

- Queue: queued/leased counts per job class, oldest eligible queue age, and
  oldest worker heartbeat age (both null when no job is waiting/leased).
- Provider: trailing-24h attempt totals/failures, ``PROVIDER_AUTH`` failures,
  and retries (attempts beyond the first per job) over generation jobs.
- Inference: the configured admission/resource limits (owned by
  :mod:`x_insight.models.inference` and :mod:`x_insight.reasoning.queue`;
  repeated here, never forked) plus trailing-24h local-calculation failures.
- Disk: backup staging filesystem usage (``shutil.disk_usage``); unreadable
  disks degrade to null, never 500.
- Backup: latest job id/status plus the most recent success time (null when
  no backup has ever succeeded).
- Requests: process-local per-group counts/errors/average latency maintained
  by the app middleware (resets on restart; gauges, not SLOs).
- Alerts: initial conditions from plan §11 — queue age over five minutes,
  missing heartbeat over two minutes, repeated provider auth failure, disk
  over 80%. Thresholds are module constants (tunable engineering settings).

Read-only: every query below is a SELECT over existing tables; this module
never mutates clinical or queue state.
"""

from __future__ import annotations

import shutil
import threading
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.models import inference as inference_module
from x_insight.operations import backup as backup_module
from x_insight.reasoning import queue as queue_module
from x_insight.reasoning import tables as reasoning_tables

# --- Initial alert thresholds (plan §11; tunable engineering settings) ---

#: Eligible queue age that pages the operator (observed, not a load promise).
QUEUE_AGE_ALERT_SECONDS = 300.0
#: Missing worker heartbeat that pages the operator (lease is 120s).
HEARTBEAT_STALE_SECONDS = 120.0
#: Repeated provider auth failures inside the trailing window that page.
PROVIDER_AUTH_ALERT_COUNT = 2
#: Trailing window for provider/local attempt counters.
ATTEMPT_WINDOW_HOURS = 24
#: Disk usage that pages the operator.
DISK_USED_PERCENT_ALERT = 80.0

_JOBS = reasoning_tables.reasoning_jobs
_ATTEMPTS = reasoning_tables.reasoning_job_attempts

# --- Process-local request metrics (middleware-fed; bounded fixed keys) ---

_SAVE_PREFIXES = (
    "/api/v1/patients",
    "/api/v1/encounters",
    "/api/v1/generation-batches",
    "/api/v1/question-runs",
)
_POLL_PREFIXES = (
    "/api/v1/generation-batches",
    "/api/v1/question-runs",
    "/api/v1/restores",
    "/api/v1/backups",
    "/api/v1/ops/",
    "/api/v1/ready",
    "/api/v1/health",
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_metrics_lock = threading.Lock()
_metrics: dict[str, dict[str, float]] = {}


def classify_request(method: str, path: str) -> str:
    """Group one request for the save/search/poll split (stdlib only).

    - ``saves``: mutating clinical writes (patients/encounters/batches/runs).
    - ``searches``: ``GET`` directory reads.
    - ``polls``: ``GET`` status/progress reads (batches, runs, recovery, ops).
    - ``other``: everything else (identity, admin, DDI checks, exports).
    """
    normalized = (path or "").rstrip("/") or "/"
    upper = (method or "").upper()
    if upper == "GET":
        if normalized == "/api/v1/patients" or normalized.startswith("/api/v1/patients/"):
            return "searches"
        if normalized.startswith(_POLL_PREFIXES):
            return "polls"
        return "other"
    if upper in _MUTATING_METHODS:
        if normalized.startswith(_SAVE_PREFIXES):
            return "saves"
        return "other"
    return "other"


def record_request(group: str, status_code: int, seconds: float) -> None:
    """Record one completed response (bounded: fixed group keys only)."""
    key = group if group in ("saves", "searches", "polls", "other") else "other"
    try:
        elapsed = max(0.0, float(seconds))
    except (TypeError, ValueError):
        elapsed = 0.0
    with _metrics_lock:
        entry = _metrics.setdefault(key, {"count": 0, "errors": 0, "total_seconds": 0.0})
        entry["count"] += 1
        if int(status_code) >= 400:
            entry["errors"] += 1
        entry["total_seconds"] += elapsed


def snapshot_requests() -> dict[str, dict[str, Any]]:
    """Per-group counts/errors/average latency (process-local gauges)."""
    with _metrics_lock:
        taken = {key: dict(value) for key, value in _metrics.items()}
    snapshot: dict[str, dict[str, Any]] = {}
    for key in ("saves", "searches", "polls", "other"):
        entry = taken.get(key, {"count": 0, "errors": 0, "total_seconds": 0.0})
        count = int(entry.get("count", 0))
        total = float(entry.get("total_seconds", 0.0))
        snapshot[key] = {
            "count": count,
            "errors": int(entry.get("errors", 0)),
            "avg_seconds": (total / count) if count else None,
        }
    return snapshot


def reset_request_metrics() -> None:
    """Clear process-local counters (tests; restarts clear them in production)."""
    with _metrics_lock:
        _metrics.clear()


def _age_seconds(moment: Any, earlier: Any) -> float | None:
    try:
        if moment is None or earlier is None:
            return None
        if getattr(earlier, "tzinfo", None) is None:
            return None
        return max(0.0, (moment - earlier).total_seconds())
    except Exception:
        return None


def _queue_section(session: Session, moment: Any) -> dict[str, Any]:
    queued = queue_module.count_queued(session)
    leased = queue_module.count_leased(session)
    local_queued = queue_module.count_queued(session, queue_module.LOCAL_CALCULATION_CLASS)
    local_leased = queue_module.count_leased(session, queue_module.LOCAL_CALCULATION_CLASS)
    oldest_created = session.execute(
        select(func.min(_JOBS.c.created_at)).where(
            _JOBS.c.job_class == queue_module.GENERATION_CLASS,
            _JOBS.c.status == queue_module.QUEUED,
        )
    ).scalar_one_or_none()
    heartbeats = session.execute(
        select(_JOBS.c.last_heartbeat, _JOBS.c.updated_at).where(
            _JOBS.c.job_class == queue_module.GENERATION_CLASS,
            _JOBS.c.status == queue_module.LEASED,
        )
    ).all()
    heartbeat_ages = [
        age
        for age in (
            _age_seconds(moment, row[0] if row[0] is not None else row[1]) for row in heartbeats
        )
        if age is not None
    ]
    return {
        "queued": int(queued),
        "leased": int(leased),
        "local_queued": int(local_queued),
        "local_leased": int(local_leased),
        "oldest_queued_age_seconds": _age_seconds(moment, oldest_created),
        "oldest_heartbeat_age_seconds": max(heartbeat_ages) if heartbeat_ages else None,
    }


def _provider_section(session: Session, moment: Any) -> dict[str, Any]:
    from x_insight.reasoning.provider_config import runtime_timeout

    window_start = moment - timedelta(hours=ATTEMPT_WINDOW_HOURS)
    rows = session.execute(
        select(_ATTEMPTS.c.outcome, _ATTEMPTS.c.error_code, _ATTEMPTS.c.attempt_index)
        .select_from(
            _ATTEMPTS.join(_JOBS, _ATTEMPTS.c.job_id == _JOBS.c.id),
        )
        .where(
            _JOBS.c.job_class == queue_module.GENERATION_CLASS,
            _ATTEMPTS.c.started_at >= window_start,
        )
    ).all()
    failed = sum(1 for row in rows if str(row[0]) == "failed")
    auth_failures = sum(1 for row in rows if row[1] == "PROVIDER_AUTH")
    retries = sum(1 for row in rows if int(row[2] or 0) >= 2)
    return {
        "attempts_24h": len(rows),
        "timeout_seconds": runtime_timeout(session),
        "failures_24h": int(failed),
        "auth_failures_24h": int(auth_failures),
        "retries_24h": int(retries),
    }


def _inference_section(session: Session, moment: Any) -> dict[str, Any]:
    window_start = moment - timedelta(hours=ATTEMPT_WINDOW_HOURS)
    local_failures = session.execute(
        select(func.count())
        .select_from(_JOBS)
        .where(
            _JOBS.c.job_class == queue_module.LOCAL_CALCULATION_CLASS,
            _JOBS.c.status == queue_module.FAILED,
            _JOBS.c.updated_at >= window_start,
        )
    ).scalar_one()
    return {
        "limits": {
            "max_inference_nodes": int(inference_module.MAX_INFERENCE_NODES),
            "max_inference_cells": int(inference_module.MAX_INFERENCE_CELLS),
            "max_effective_bytes": int(inference_module.MAX_EFFECTIVE_BYTES_FOR_INFERENCE),
            "inference_timeout_seconds": float(inference_module.INFERENCE_TIMEOUT_SECS),
            "max_provider_slots": int(queue_module.MAX_PROVIDER_SLOTS),
            "max_queued_runs": int(queue_module.MAX_QUEUED_RUNS),
            "max_local_slots": int(queue_module.MAX_LOCAL_SLOTS),
            "max_attempts": int(queue_module.MAX_ATTEMPTS),
        },
        "local_failures_24h": int(local_failures),
    }


def _disk_section() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(backup_module.staging_dir()))
        total = int(usage.total)
        used = int(usage.used)
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "used_percent": None}
    if total <= 0:
        return {"total_bytes": total, "used_bytes": used, "used_percent": None}
    return {
        "total_bytes": total,
        "used_bytes": used,
        "used_percent": round(used / total * 100.0, 1),
    }


def _backup_section(session: Session) -> dict[str, Any]:
    jobs_table = backup_module.backup_jobs
    latest = (
        session.execute(select(jobs_table).order_by(jobs_table.c.created_at.desc()).limit(1))
        .mappings()
        .first()
    )
    if latest is None:
        return {"last_job_id": None, "last_job_status": None, "last_success_at": None}
    success_at = session.execute(
        select(func.max(jobs_table.c.completed_at)).where(
            jobs_table.c.status == backup_module.STATUS_SUCCEEDED
        )
    ).scalar_one_or_none()
    completed: str | None = None
    if success_at is not None:
        try:
            completed = contracts.serialize_utc(success_at)
        except ValueError:
            completed = None
    return {
        "last_job_id": str(latest.get("id")),
        "last_job_status": str(latest.get("status")),
        "last_success_at": completed,
    }


def _alerts(
    queue: dict[str, Any], provider: dict[str, Any], disk: dict[str, Any]
) -> list[dict[str, Any]]:
    """Initial alert conditions (names + safe numeric detail, no payloads)."""
    found: list[dict[str, Any]] = []
    queue_age = queue.get("oldest_queued_age_seconds")
    if queue_age is not None and float(queue_age) > QUEUE_AGE_ALERT_SECONDS:
        found.append(
            {
                "name": "QUEUE_AGE",
                "message": "Eligible queue age exceeds 5 minutes.",
                "age_seconds": round(float(queue_age), 1),
            }
        )
    heartbeat_age = queue.get("oldest_heartbeat_age_seconds")
    if heartbeat_age is not None and float(heartbeat_age) > HEARTBEAT_STALE_SECONDS:
        found.append(
            {
                "name": "HEARTBEAT_STALE",
                "message": "A leased job missed its heartbeat for over 2 minutes.",
                "age_seconds": round(float(heartbeat_age), 1),
            }
        )
    if int(provider.get("auth_failures_24h", 0)) >= PROVIDER_AUTH_ALERT_COUNT:
        found.append(
            {
                "name": "PROVIDER_AUTH_FAILURES",
                "message": "Repeated provider authentication failures in 24h.",
                "count": int(provider.get("auth_failures_24h", 0)),
            }
        )
    used_percent = disk.get("used_percent")
    if used_percent is not None and float(used_percent) > DISK_USED_PERCENT_ALERT:
        found.append(
            {
                "name": "DISK_USAGE",
                "message": "Backup staging disk usage exceeds 80%.",
                "used_percent": float(used_percent),
            }
        )
    return found


def collect_status(session: Session, now: Any | None = None) -> dict[str, Any]:
    """Assemble the safe operational status snapshot (SELECTs only)."""
    moment = now or contracts.utcnow()
    queue = _queue_section(session, moment)
    provider = _provider_section(session, moment)
    inference = _inference_section(session, moment)
    disk = _disk_section()
    backup = _backup_section(session)
    return {
        "service": "x-insight",
        "checked_at": contracts.serialize_utc(moment),
        "queue": queue,
        "provider": provider,
        "inference": inference,
        "disk": disk,
        "backup": backup,
        "requests": snapshot_requests(),
        "alerts": _alerts(queue, provider, disk),
    }


class RequestMetricsMiddleware:
    """Outermost request gauge: counts/errors/latency per save/search/poll group.

    Pure ASGI wrapper around the whole stack so edge rejections (413/422)
    count too; fixed group keys keep memory bounded regardless of path
    cardinality. Records 500 when no response completes.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        group = classify_request(str(scope.get("method", "")), str(scope.get("path", "")))
        started = time.perf_counter()
        captured = {"status": 500}

        async def _send(message: dict) -> None:
            if message.get("type") == "http.response.start":
                try:
                    captured["status"] = int(message.get("status", 500))
                except (TypeError, ValueError):
                    captured["status"] = 500
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            record_request(group, captured["status"], time.perf_counter() - started)
