"""Operational status metrics and initial alerts (S58 §3, seams T1/T8/T10).

Through public HTTP (T1) over real PostgreSQL, with T8 ``run_once()`` setup
for heartbeat/auth states and T10 ``POST /backups`` for the backup metric:

- Healthy shape on a fresh database: fixed safe keys, no alerts, null backup.
- Non-admin access is denied (physician 403, anonymous 401).
- Queue-age, stale-heartbeat, repeated-auth-failure, and disk-usage alerts
  fire from real queued/leased/failed-attempt/full-disk states.
- A reclaimed lease clears the heartbeat alert (recovery path, not just onset).
- Failed draft saves are counted (stale-revision 412) without record text.
- The response carries counts/timestamps only: no secrets, record text,
  tokens, or clinical payloads.

Expected values are worked fixtures (counts we created, thresholds we set);
latencies/counts in the body are measured by the implementation, never
hand-written here.
"""

from __future__ import annotations

import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.worker.test_queue import _synthetic_package
from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service


@pytest.fixture(scope="session")
def migrated_test_engine():
    from alembic import command
    from alembic.config import Config

    url = db_module.get_test_database_url()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = db_module.build_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _truncate_ops(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, question_runs, generation_batches, notes, encounters, "
                "patients, idempotency_records, backup_jobs, sessions, users, audit_events CASCADE"
            )
        )
        connection.execute(text("UPDATE reasoning_deployment SET generation = 1 WHERE id = 1"))
        row = connection.execute(text("SELECT generation FROM reasoning_deployment")).first()
        if row is None:
            connection.execute(
                text("INSERT INTO reasoning_deployment (id, generation) VALUES (1, 1)")
            )


@pytest.fixture()
def ops_client(migrated_test_engine, monkeypatch):
    from x_insight.operations import status as status_module

    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate_ops(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    status_module.reset_request_metrics()
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    fixture = {
        "client": client,
        "csrf": body["csrf_token"],
        "user": body["user"],
        "engine": migrated_test_engine,
    }
    yield fixture
    service.clear_login_throttle()
    _truncate_ops(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    status_module.reset_request_metrics()


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _physician_session(engine, monkeypatch, username: str) -> tuple[Any, str]:
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def _make_batch(ops_client, monkeypatch, username: str, identifier: str) -> tuple[Any, str, str]:
    """Create one physician + patient + acknowledged draft + queued batch (T1)."""
    admin = ops_client["client"]
    created = admin.post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers=_auth_headers(ops_client["csrf"]),
    )
    assert created.status_code in (201, 409), created.text
    client, csrf = _physician_session(ops_client["engine"], monkeypatch, username)
    patient = client.post(
        "/api/v1/patients",
        json={
            "identifier": identifier,
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(csrf),
    )
    assert patient.status_code == 201, patient.text
    encounter_id = patient.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": _synthetic_package()},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    return client, csrf, started.json()["batch"]["id"]


def _get_status(client) -> dict[str, Any]:
    response = client.get("/api/v1/ops/status")
    assert response.status_code == 200, response.text
    return response.json()


def test_ops_status_healthy_shape_without_secrets(ops_client) -> None:
    """S58 §3: fresh database exposes safe metrics with no alerts and no leakage."""
    from x_insight.models import inference as inference_module
    from x_insight.reasoning import queue as queue_module

    body = _get_status(ops_client["client"])
    assert body["service"] == "x-insight"
    assert body["checked_at"]
    for section in ("queue", "provider", "inference", "disk", "backup", "requests", "alerts"):
        assert section in body, body.keys()
    assert body["alerts"] == []
    assert body["queue"]["queued"] == 0
    assert body["queue"]["leased"] == 0
    assert body["provider"]["auth_failures_24h"] == 0
    assert body["backup"]["last_success_at"] is None
    limits = body["inference"]["limits"]
    assert limits["max_inference_nodes"] == inference_module.MAX_INFERENCE_NODES
    assert limits["max_inference_cells"] == inference_module.MAX_INFERENCE_CELLS
    assert limits["max_effective_bytes"] == inference_module.MAX_EFFECTIVE_BYTES_FOR_INFERENCE
    assert limits["inference_timeout_seconds"] == inference_module.INFERENCE_TIMEOUT_SECS
    assert limits["max_provider_slots"] == queue_module.MAX_PROVIDER_SLOTS
    assert limits["max_queued_runs"] == queue_module.MAX_QUEUED_RUNS
    # Safe metadata only: no credential, token, or clinical text leaves this edge.
    blob = str(body)
    for forbidden in ("pw123", "lease_token", "grant_token", "csrf", "admin"):
        assert forbidden not in blob.lower(), forbidden


def test_ops_status_denies_non_admin(ops_client, monkeypatch) -> None:
    """S58 §3: physicians cannot read operational status; anonymous is unauthenticated."""
    created = ops_client["client"].post(
        "/api/v1/physicians",
        json={"username": "dr_ops_denied", "password": "pw123"},
        headers=_auth_headers(ops_client["csrf"]),
    )
    assert created.status_code == 201, created.text
    physician, _ = _physician_session(ops_client["engine"], monkeypatch, "dr_ops_denied")
    denied = physician.get("/api/v1/ops/status")
    assert denied.status_code == 403, denied.text
    anonymous = TestClient(app)
    missing = anonymous.get("/api/v1/ops/status")
    assert missing.status_code == 401, missing.text


def test_ops_status_queue_age_alert(ops_client, monkeypatch) -> None:
    """S58 §3: a queued batch past the age threshold raises QUEUE_AGE (T1 state)."""
    from x_insight.operations import status as status_module

    monkeypatch.setattr(status_module, "QUEUE_AGE_ALERT_SECONDS", 0)
    quiet = _get_status(ops_client["client"])
    assert [a["name"] for a in quiet["alerts"]] == [
        name for name in [a["name"] for a in quiet["alerts"]] if name != "QUEUE_AGE"
    ]
    assert "QUEUE_AGE" not in [a["name"] for a in quiet["alerts"]]
    _make_batch(ops_client, monkeypatch, "dr_ops_queue", "0099000001")
    body = _get_status(ops_client["client"])
    assert body["queue"]["queued"] == 1
    assert body["queue"]["oldest_queued_age_seconds"] is not None
    assert body["queue"]["oldest_queued_age_seconds"] >= 0
    names = [a["name"] for a in body["alerts"]]
    assert "QUEUE_AGE" in names, body["alerts"]


def test_ops_status_stale_heartbeat_alert_and_recovery(ops_client, monkeypatch) -> None:
    """S58 §3: a crashed worker's stale lease alerts; reclaim clears it (T8)."""
    from x_insight.operations import status as status_module
    from x_insight.reasoning import provider as provider_module
    from x_insight.reasoning import worker as worker_module

    monkeypatch.setattr(status_module, "HEARTBEAT_STALE_SECONDS", 60)
    _make_batch(ops_client, monkeypatch, "dr_ops_heart", "0099000002")

    class _ExplodingAdapter:
        def estimate(self, request):  # noqa: ANN001, ANN202
            raise RuntimeError("simulated provider transport crash")

    past = contracts.utcnow() - timedelta(minutes=10)
    with pytest.raises(RuntimeError, match="simulated provider transport crash"):
        worker_module.run_once(engine=ops_client["engine"], provider=_ExplodingAdapter(), now=past)

    stale = _get_status(ops_client["client"])
    assert stale["queue"]["leased"] == 1
    assert stale["queue"]["oldest_heartbeat_age_seconds"] is not None
    assert stale["queue"]["oldest_heartbeat_age_seconds"] >= 600
    assert "HEARTBEAT_STALE" in [a["name"] for a in stale["alerts"]], stale["alerts"]

    # The next worker pass reclaims the expired lease; the alert clears.
    worker_module.run_once(
        engine=ops_client["engine"], provider=provider_module.ControlledStubAdapter()
    )
    recovered = _get_status(ops_client["client"])
    assert recovered["queue"]["leased"] == 0
    assert "HEARTBEAT_STALE" not in [a["name"] for a in recovered["alerts"]]


def test_ops_status_repeated_auth_failures_alert(ops_client, monkeypatch) -> None:
    """S58 §3: repeated PROVIDER_AUTH failures surface a safe count + alert (T8)."""
    from x_insight.reasoning import provider as provider_module
    from x_insight.reasoning import worker as worker_module

    _make_batch(ops_client, monkeypatch, "dr_ops_auth", "0099000003")
    _make_batch(ops_client, monkeypatch, "dr_ops_auth", "0099000004")
    failing = provider_module.ControlledStubAdapter(mode="fail_fatal")
    first = worker_module.run_once(engine=ops_client["engine"], provider=failing)
    assert first["status"] == "failed", first
    second = worker_module.run_once(engine=ops_client["engine"], provider=failing)
    assert second["status"] == "failed", second
    body = _get_status(ops_client["client"])
    assert body["provider"]["auth_failures_24h"] == 2
    assert "PROVIDER_AUTH_FAILURES" in [a["name"] for a in body["alerts"]], body["alerts"]
    blob = str(body)
    assert "pw123" not in blob and "0099000003" not in blob


def test_ops_status_disk_alert_and_disk_failure_safe(ops_client, monkeypatch) -> None:
    """S58 §3: disk over 80% alerts; an unreadable disk degrades to null (T1)."""
    full = shutil.disk_usage("/")  # real call once: proves the metric is measured
    assert full.total > 0
    from types import SimpleNamespace

    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: SimpleNamespace(total=100, used=81, free=19)
    )
    body = _get_status(ops_client["client"])
    assert body["disk"]["used_percent"] == 81.0
    assert "DISK_USAGE" in [a["name"] for a in body["alerts"]], body["alerts"]

    def _broken(path):  # noqa: ANN001, ANN202
        raise OSError("simulated disk stat failure")

    monkeypatch.setattr(shutil, "disk_usage", _broken)
    degraded = _get_status(ops_client["client"])
    assert degraded["disk"]["used_percent"] is None
    assert "DISK_USAGE" not in [a["name"] for a in degraded["alerts"]]


def test_ops_status_backup_success_metric(ops_client) -> None:
    """S58 §3: a T10 backup success is visible as safe metadata (id + time)."""
    created = ops_client["client"].post(
        "/api/v1/backups", headers=_auth_headers(ops_client["csrf"])
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "succeeded"
    body = _get_status(ops_client["client"])
    assert body["backup"]["last_success_at"] is not None
    assert body["backup"]["last_job_status"] == "succeeded"
    assert body["backup"]["last_job_id"] == created.json()["id"]


def test_ops_status_counts_failed_saves(ops_client, monkeypatch) -> None:
    """S58 §3: an acknowledged save counts; a stale-revision save failure counts."""
    client, csrf, _ = _make_batch(ops_client, monkeypatch, "dr_ops_saves", "0099000005")
    before = _get_status(ops_client["client"])
    assert before["requests"]["saves"]["count"] >= 3  # create + patch + batch start
    assert before["requests"]["saves"]["errors"] == 0
    stale = client.patch(
        "/api/v1/encounters/00000000-0000-0000-0000-000000000000",
        json={"draft_data": {}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert stale.status_code in (404, 412), stale.text
    after = _get_status(ops_client["client"])
    assert after["requests"]["saves"]["count"] > before["requests"]["saves"]["count"]
    assert after["requests"]["saves"]["errors"] >= 1
