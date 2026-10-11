"""Synthetic load probe over public interfaces (S58 §1, seams T1/T8).

Through authenticated HTTP (T1) over real PostgreSQL with T8 ``run_once()``
as the timed provider-phase executor: a small synthetic dataset (10
patients, thread-shared exactly like the real runner) proves the probe
records its environment and results without fabricating numbers — every
reported count matches an independently observed value (directory total,
batch reads), every latency is client-measured, and provider latency is
reported separately from ordinary requests. Only synthetic fixtures;
identifiers never collide with real ranges.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service
from x_insight.operations import load_probe as probe_module


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


@pytest.fixture()
def load_setup(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, question_runs, generation_batches, notes, encounters, "
                "patients, idempotency_records, sessions, users, audit_events CASCADE"
            )
        )
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()


def _login(username: str, password: str, role: str) -> probe_module.ClientSession:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "role": role}
    )
    assert response.status_code == 200, response.text
    return probe_module.ClientSession(client, response.json()["csrf_token"])


def test_load_probe_records_measured_small_dataset(load_setup, monkeypatch) -> None:
    """S58 §1: the runner records real counts/latencies at small scale (T1/T8)."""
    from x_insight.reasoning import provider as provider_module
    from x_insight.reasoning import worker as worker_module

    monkeypatch.setattr(db_module, "get_engine", lambda: load_setup)
    admin = _login("admin", "admin", "admin")
    created = admin.request(
        "POST", "/api/v1/physicians", json={"username": "dr_probe", "password": "pw123"}
    )
    assert created.status_code == 201, created.text

    import threading

    def _factory() -> probe_module.ClientSession:
        return _login("dr_probe", "pw123", "physician")

    lock = threading.Lock()
    executions = {"count": 0}

    def _provider_exec() -> Any:
        with lock:
            outcome = worker_module.run_once(
                engine=load_setup, provider=provider_module.ControlledStubAdapter()
            )
        executions["count"] += 1
        assert outcome["status"] in ("succeeded", "failed", "idle", "busy"), outcome
        return outcome

    report = probe_module.run_load(
        _factory,
        {
            "patients": 10,
            "workers": 2,
            "searches_each": 2,
            "polls_each": 2,
            "provider_samples": 2,
            "patient_id_start": 9000010001,
        },
        provider_exec=_provider_exec,
    )
    dataset = report["dataset"]
    assert dataset["patients_created"] == 10
    assert dataset["saves"] == 10
    assert dataset["searches"] == 20
    assert dataset["polls"] == 20
    assert dataset["provider_samples"] == 2
    assert dataset["errors"] == []
    assert executions["count"] == 2
    # Independently observed values agree: nothing is fabricated.
    directory = _login("dr_probe", "pw123", "physician").request("GET", "/api/v1/patients")
    assert directory.status_code == 200
    assert directory.json()["total"] == 10
    assert report["duration_seconds"] > 0
    for op in ("create_patient", "save_draft", "search", "poll"):
        stats = report["ordinary"][op]
        assert stats["count"] > 0
        assert 0 <= stats["p50"] <= stats["p95"]
        assert stats["p95"] <= stats["max"]
    assert report["provider"]["samples"] == 2
    assert report["provider"]["adapter"] == "ControlledStubAdapter"
    assert report["provider"]["outcomes"] == {"succeeded": 2}
    assert "ordinary" in report and "provider" in report
    assert report["synthetic"] is True
    blob = str(report)
    assert "pw123" not in blob


def test_load_probe_skips_provider_phase_without_executor(load_setup) -> None:
    """S58 §1: without a provider executor the probe says so honestly (T1)."""

    def _factory() -> probe_module.ClientSession:
        return _login("dr_probe_b", "pw123", "physician")

    admin = _login("admin", "admin", "admin")
    created = admin.request(
        "POST", "/api/v1/physicians", json={"username": "dr_probe_b", "password": "pw123"}
    )
    assert created.status_code == 201, created.text
    report = probe_module.run_load(
        _factory,
        {
            "patients": 2,
            "workers": 1,
            "searches_each": 1,
            "polls_each": 1,
            "provider_samples": 0,
            "patient_id_start": 9000020001,
        },
    )
    assert report["dataset"]["patients_created"] == 2
    assert "skipped" in report["provider"]
