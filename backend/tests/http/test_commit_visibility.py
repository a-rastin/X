"""Commit-visibility over real HTTP (S60 frontend-unblock, seams T1/T9).

FastAPI runs yield-dependency teardown AFTER the response is sent, so the
``session_scope`` commit in :func:`x_insight.db.get_session` teardown landed
after fast follow-up reads: login 200 then immediate ``GET /me`` 401, and
physician-create then immediate login 401 (10/10 over loopback pre-fix).
TestClient-based suites never saw it (in-process teardown completes before
the test client returns), so this module drives a real loopback uvicorn
server with a real socket client: immediate succession IS the timing
control — no sleeps, no retries, no#settle waits.

Covers the S60 frontend blocker: every mutating route must commit BEFORE
its response is sent (see :class:`x_insight.db.CommittedRoute`), so the
very next request observes the writes. Synthetic users only; real
PostgreSQL (``TEST_DATABASE_URL``); never touches the dev stack/ports or
the dev database.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.orm import Session as _Session

from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service

BASE_TIMEOUT = 10.0
STARTUP_DEADLINE_SECONDS = 30.0
PAIRS_PER_SCENARIO = 5
# Responsiveness proof: every Session.commit is delayed by this long, so a
# mutating request holds its (slow) commit for ~2x this (route + teardown).
# A peer request that needs no commit must still complete well under it —
# unless the commit blocks the event loop. Generous margins, no fine timing.
SLOW_COMMIT_SECONDS = 1.5
RESPONSIVE_BOUND_SECONDS = 1.0


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


def _truncate(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE probability_acceptances, calculation_results, cpt_revisions, "
                "question_review_states, reasoning_grants, reasoning_job_attempts, "
                "reasoning_jobs, reasoning_fairness, original_baselines, "
                "proposal_snapshots, question_runs, generation_batches, "
                "encounter_addenda, signed_encounter_snapshots, secondary_plans, "
                "notes, encounters, patients, idempotency_records, sessions, users, "
                "audit_events, ddi_dataset_releases, ddi_source_documents, "
                "ddi_concepts, ddi_interaction_evidence CASCADE"
            )
        )


@pytest.fixture()
def clean_registry(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)


@pytest.fixture()
def _live_server(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    base_url, server, thread = start_live_server()
    try:
        yield base_url
    finally:
        stop_live_server(server, thread)


def start_live_server() -> tuple[str, object, object]:
    """Start a real loopback uvicorn for true process/thread separation.

    Shared by commit-visibility and cross-user acceptance probes that
    TestClient cannot observe (in-process teardown completes before the
    test client returns). Ephemeral port; caller owns stop via
    :func:`stop_live_server`. Uses the currently configured engine
    (monkeypatched test engine under pytest).
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    sockets = list(server.servers[0].sockets) if server.servers else []
    assert sockets, "no bound socket"
    port = int(sockets[0].getsockname()[1])
    return f"http://127.0.0.1:{port}", server, thread


def stop_live_server(server: object, thread: object) -> None:
    """Stop a server from :func:`start_live_server` (bounded join)."""
    assert isinstance(server, uvicorn.Server)
    assert isinstance(thread, threading.Thread)
    server.should_exit = True
    thread.join(timeout=30)


def _login(client: httpx.Client, username: str, password: str, role: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "role": role},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def test_login_then_me_immediate_over_real_http(_live_server, clean_registry) -> None:
    """S60 blocker: login 200 then immediate GET /me must be 200, every time."""
    with httpx.Client(base_url=_live_server, timeout=BASE_TIMEOUT) as client:
        for _ in range(PAIRS_PER_SCENARIO):
            _login(client, "admin", "admin", "admin")
            me = client.get("/api/v1/me")
            assert me.status_code == 200, me.text
            assert me.json()["user"]["username"] == "admin"


def test_physician_create_then_login_immediate_over_real_http(_live_server, clean_registry) -> None:
    """S60 blocker: created physicians can log in and read /me immediately."""
    with httpx.Client(base_url=_live_server, timeout=BASE_TIMEOUT) as client:
        admin_csrf = _login(client, "admin", "admin", "admin")
        for index in range(3):
            username = f"dr_s60_vis_{index}_{int(time.monotonic() * 1000) % 100000:05d}"
            created = client.post(
                "/api/v1/physicians",
                json={"username": username, "password": "pw123"},
                headers={"X-CSRF-Token": admin_csrf},
            )
            assert created.status_code == 201, created.text
            physician = httpx.Client(base_url=_live_server, timeout=BASE_TIMEOUT)
            try:
                _login(physician, username, "pw123", "physician")
                me = physician.get("/api/v1/me")
                assert me.status_code == 200, me.text
                assert me.json()["user"]["username"] == username
            finally:
                physician.close()


def test_event_loop_responsive_during_slow_commit(
    _live_server, clean_registry, monkeypatch
) -> None:
    """S60 review: a slow commit must not stall concurrent requests.

    Every ``Session.commit`` is delayed by a controlled fault
    (``SLOW_COMMIT_SECONDS``), so a mutating request holds its commit for
    ~2x that (route + teardown no-op). ``GET /health`` needs no database
    session, so it must complete within ``RESPONSIVE_BOUND_SECONDS``
    while the slow commit is provably in flight (signalled, never slept
    into). A loop-blocking commit makes the probe wait out the delay.
    """
    commit_started = threading.Event()
    real_commit = _Session.commit

    def _slow_commit(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        commit_started.set()
        time.sleep(SLOW_COMMIT_SECONDS)
        return real_commit(self, *args, **kwargs)

    monkeypatch.setattr(_Session, "commit", _slow_commit)

    outcome: dict[str, object] = {}

    def _slow_login() -> None:
        with httpx.Client(base_url=_live_server, timeout=BASE_TIMEOUT) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin", "role": "admin"},
            )
            outcome["slow_status"] = response.status_code
            outcome["slow_body"] = response.text

    thread = threading.Thread(target=_slow_login)
    thread.start()
    assert commit_started.wait(timeout=30), "slow commit must start"
    begun = time.perf_counter()
    with httpx.Client(base_url=_live_server, timeout=BASE_TIMEOUT) as probe:
        health = probe.get("/api/v1/health")
    elapsed = time.perf_counter() - begun
    thread.join(timeout=30)
    assert outcome.get("slow_status") == 200, outcome.get("slow_body")
    assert health.status_code == 200, health.text
    assert elapsed < RESPONSIVE_BOUND_SECONDS, (
        f"probe waited out the slow commit ({elapsed:.2f}s): event loop blocked"
    )
