"""Production worker entry point (S57, seams T8/T1/T10; plan.md §§3, 11).

Through public HTTP + ``run_once()`` on real PostgreSQL (synthetic
single-question scope, same fixtures as the S44 queue suite):

- S57 §2/§3: with no provider endpoint configured, the production adapter
  selection fails closed — the generation job ends ``failed`` with
  ``PROVIDER_NOT_CONFIGURED`` (no retry consumed, no stub success), while
  the draft, directory, and admin reads stay usable.
- S57 §3: adapter selection never yields the S44 ControlledStubAdapter in
  production (configured → real BoundedProviderAdapter, else fail-closed).
- S57 §1: the ``python -m x_insight.reasoning`` polling entry point runs
  ``run_once()`` and stops when asked (supervised-drill bound).
- S57 §1/§3 (static, T10): prod topology self-check — only the edge binds
  host ports, no public DB/MCP, persistent volumes, polling entry point,
  no dev override or test stub; plus packaging hygiene (pinned non-root
  images, ``--proxy-headers``/``--no-access-log``, TLS edge, relative
  browser routes, placeholder env, Make targets, runbook sections).
- S57 §§2-3 (T8): ``PROVIDER_NOT_CONFIGURED``/``MISSING_PACKAGE``/
  ``PACKAGE_INVALID`` are non-retryable config errors; blank or half-set
  operator env never selects a real adapter or the test stub.
- S57 §4 (T8): polling logs carry UUID-only outcomes, never record text.
- S57 §4 (T1/T8): fail-closed state survives independent app/worker
  restarts with the single recorded attempt kept.

Behavior assertions read public run status; no private-helper mirrors, no
`page.route` mocks. Fixture setup reuses the S44 queue-suite helpers.
Static guards use stdlib only and mirror ``deploy/check.sh``.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.worker.test_queue import (
    _read_batch,
    _setup_batch,
    admin_client,
    clean_registry,
    migrated_test_engine,
)
from x_insight import db as db_module
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import worker as worker_module

__all__ = ["admin_client", "clean_registry", "migrated_test_engine"]


def _prod_root() -> Path:
    """Repository root for static packaging guards (stdlib only, no yaml dep)."""
    return Path(__file__).resolve().parents[3]


def _compose_service_blocks(compose_text: str) -> dict[str, str]:
    """Split compose.prod.yaml into per-service blocks (two-space headers).

    A block ends at the next service header or at the next column-0 section
    (e.g. ``volumes:``), mirroring the awk ranges in deploy/check.sh.
    """
    blocks: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in compose_text.splitlines():
        header = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if header is not None:
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = header.group(1)
            buf = []
        elif re.match(r"^[^\s#]", line) is not None:
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = None
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf)
    return blocks


def _production_adapter(monkeypatch):
    monkeypatch.delenv(worker_module.PROVIDER_ENDPOINT_ENV_VAR, raising=False)
    monkeypatch.delenv(worker_module.PROVIDER_MODEL_ENV_VAR, raising=False)
    monkeypatch.delenv(worker_module.PROVIDER_CAPABILITY_ENV_VAR, raising=False)
    return worker_module.build_adapter_from_env()


def test_unconfigured_worker_fails_closed_without_retry(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S57 §2/§3: missing provider → failed (not retried) generation, usable charts/admin."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    adapter = _production_adapter(monkeypatch)
    assert isinstance(adapter, worker_module.UnconfiguredProvider)

    client, csrf, batch_id, encounter_id, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_prod_noprov", "0012345678"
    )
    first = worker_module.run_once(engine=clean_registry, provider=adapter)
    assert first["status"] == "failed", first

    body = _read_batch(client, batch_id)
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["job"]["status"] == "failed"
    assert payload["job"]["result"] == {}
    attempts = payload["attempts"]
    attempt_count = attempts if isinstance(attempts, int) else len(attempts)
    assert attempt_count == 1

    # No retry is consumed: a second pass finds no eligible work.
    second = worker_module.run_once(engine=clean_registry, provider=adapter)
    assert second["status"] == "idle", second
    again = _read_batch(client, batch_id).json()
    again_count = (
        again["attempts"] if isinstance(again["attempts"], int) else len(again["attempts"])
    )
    assert again_count == 1
    # Fail-closed, not backoff: no future eligibility is scheduled.
    assert again["job"]["status"] == "failed"
    assert again["job"]["next_eligible_at"] is None

    # Charts/admin remain usable: author reads the retained draft, the
    # shared directory lists the patient, admin reads stay live.
    draft = client.get(f"/api/v1/encounters/{encounter_id}")
    assert draft.status_code == 200, draft.text
    directory = client.get("/api/v1/patients")
    assert directory.status_code == 200, directory.text
    assert any(p["identifier"] == "0012345678" for p in directory.json()["items"])
    health = admin_client["client"].get("/api/v1/health")
    assert health.status_code == 200


def test_adapter_selection_never_yields_test_stub(monkeypatch) -> None:
    """S57 §3: configured → real bounded adapter; unconfigured → fail-closed; never the stub."""
    monkeypatch.delenv(worker_module.PROVIDER_ENDPOINT_ENV_VAR, raising=False)
    monkeypatch.delenv(worker_module.PROVIDER_MODEL_ENV_VAR, raising=False)
    assert isinstance(worker_module.build_adapter_from_env(), worker_module.UnconfiguredProvider)
    monkeypatch.setenv(worker_module.PROVIDER_ENDPOINT_ENV_VAR, "http://localhost:11434/v1")
    monkeypatch.setenv(worker_module.PROVIDER_MODEL_ENV_VAR, "synthetic-test-model")
    selected = worker_module.build_adapter_from_env()
    assert isinstance(selected, provider_module.BoundedProviderAdapter)
    assert not isinstance(selected, provider_module.ControlledStubAdapter)


def test_poll_entry_point_runs_once_and_stops(clean_registry, monkeypatch) -> None:
    """S57 §1: ``poll_forever`` executes the polling loop and honors its stop bound."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    _production_adapter(monkeypatch)
    worker_module.poll_forever(engine=clean_registry, poll_seconds=0, stop_after=1)


def test_prod_topology_only_edge_publishes_ports() -> None:
    """S57 §1 (T10): prod topology — only the edge binds host ports (plan §3.1).

    Static packaging guard for the same invariants ``deploy/check.sh``
    enforces: edge/app/worker/db exist, only the edge publishes 80/443, the
    app/worker/db publish no host ports (no public DB/MCP), the database and
    shared recovery volumes persist, the worker runs the polling entry point,
    and no dev override or test-provider stub is wired into production.
    Expected ports/services come from plan §3.1, not implementation output.
    """
    compose_text = (_prod_root() / "compose.prod.yaml").read_text()
    blocks = _compose_service_blocks(compose_text)
    for service in ("edge", "app", "worker", "db"):
        assert service in blocks, f"prod compose must define service '{service}'"
    assert re.search(r'^\s+-\s+"?80:80"?\s*$', blocks["edge"], re.M), "edge must publish 80"
    assert re.search(r'^\s+-\s+"?443:443"?\s*$', blocks["edge"], re.M), "edge must publish 443"
    for service in ("app", "worker", "db"):
        assert not re.search(r"^\s+ports\s*:", blocks[service], re.M), (
            f"prod '{service}' must publish no host ports (edge-only ingress)"
        )
    assert "prod_pgdata:/var/lib/postgresql/data" in compose_text
    assert "prod_recovery" in blocks["app"] and "prod_recovery" in blocks["worker"]
    assert "db:5432" in compose_text, "prod DATABASE_URL stays on the internal network"
    assert "x_insight.reasoning" in blocks["worker"]
    for banned in ("ControlledStub", "TEST_", "VITE_", "target: development"):
        assert banned not in compose_text, f"prod compose must not contain {banned!r}"


def test_prod_packaging_hygiene() -> None:
    """S57 §§1/3 (T10): pinned images, TLS edge, log hygiene, documented ops.

    Static guard mirroring ``deploy/check.sh``: digest-pinned non-root app
    image running uvicorn with ``--proxy-headers`` (edge terminates TLS) and
    ``--no-access-log`` (access lines would log patient/record IDs), pinned
    edge stages, Caddy same-origin ``/api`` proxy with an operative TLS redir
    and discarded access logs, relative ``/api/v1`` browser routes only,
    placeholder-only env example, prod Make targets, and runbook sections
    (backup-before-upgrade, rollback, migration recovery, stopping, key
    handling). Expectations come from plan §§3/11.
    """
    root = _prod_root()
    app_docker = (root / "deploy" / "Dockerfile.app").read_text()
    assert re.search(r"^FROM [^ ]+@sha256:[0-9a-f]{64}", app_docker, re.M)
    assert re.search(r"^USER [^r]", app_docker, re.M), "prod image must drop to non-root"
    assert "--proxy-headers" in app_docker
    assert "--no-access-log" in app_docker
    edge_docker = (root / "deploy" / "Dockerfile.edge").read_text()
    assert len(re.findall(r"^FROM [^ ]+@sha256:[0-9a-f]{64}", edge_docker, re.M)) >= 2
    for text in (app_docker, edge_docker):
        lowered = text.lower()
        assert "jwt" not in lowered and "api_key" not in lowered
        assert "password" not in lowered, "prod Dockerfiles must not embed credentials"
    caddy = (root / "deploy" / "Caddyfile").read_text()
    assert "reverse_proxy" in caddy and "/api" in caddy
    # Operative HTTPS/log-secrecy directives only (comments stripped, mirroring
    # deploy/check.sh): prose about TLS must not satisfy these guards.
    operative = re.sub(r"#.*", "", caddy)
    assert re.search(r"^\s*redir\s", operative, re.M), (
        "Caddyfile needs an operative http→https redir (plan §11)"
    )
    assert "https://" in operative, "Caddyfile needs an operative https redirect target"
    assert re.search(r"output\s+discard", operative), (
        "Caddyfile must discard edge access logs (S57 §4)"
    )
    assert re.search(r"exclude\s+.*http\.log\.access", operative), (
        "Caddyfile must exclude access entries from the default logger"
    )
    ts_sources = [path for path in (root / "web" / "src").rglob("*.ts*") if path.is_file()]
    absolute = [
        str(path) for path in ts_sources if re.search(r"https?://", path.read_text(errors="ignore"))
    ]
    assert absolute == [], f"web sources must not contain absolute http(s) origins: {absolute}"
    configured = [
        str(path)
        for path in ts_sources
        if re.search(
            r":5173|PUBLIC_ORIGIN|VITE_[A-Za-z_]*API|API_BASE_URL|baseURL|API_ORIGIN",
            path.read_text(errors="ignore"),
        )
    ]
    assert configured == [], f"web sources must not configure an absolute API origin: {configured}"
    env_example = (root / ".env.example").read_text()
    for key in (
        "POSTGRES_PASSWORD",
        "SITE_ADDRESS",
        "X_INSIGHT_PROVIDER_ENDPOINT",
        "X_INSIGHT_PROVIDER_MODEL",
    ):
        assert re.search(rf"^#? ?{key}=", env_example, re.M), f".env.example must document {key}"
    assert "CHANGEME" in env_example or "localhost" in env_example
    # Placeholder-only assert mirroring deploy/check.sh: every sensitive
    # assignment is empty or carries an obvious placeholder marker.
    for line in env_example.splitlines():
        if re.match(r"^(POSTGRES_PASSWORD|DATABASE_URL|.*SECRET.*|.*API_KEY.*)=", line):
            assert re.search(r"=(.*CHANGEME.*|.*(example|127\.0\.0\.1).*|)$", line), (
                f"tracked .env.example holds a real-looking secret: {line.split('=', 1)[0]}"
            )
    makefile = (root / "Makefile").read_text()
    for target in (
        "prod-build",
        "prod-up",
        "prod-migrate",
        "prod-stop",
        "prod-logs",
        "prod-check",
    ):
        assert re.search(rf"^{target}:", makefile, re.M), f"Makefile must define '{target}'"
    readme = (root / "deploy" / "README.md").read_text()
    lowered_readme = readme.lower()
    assert "backup" in lowered_readme and "upgrade" in lowered_readme
    assert "rollback" in lowered_readme
    assert "migration failure" in lowered_readme or "failed migration" in lowered_readme
    assert "stop" in lowered_readme
    assert "password" in lowered_readme or "secret" in lowered_readme or "key" in lowered_readme


def test_fail_closed_errors_are_non_retryable_config(monkeypatch) -> None:
    """S57 §§2-3 (T8): missing provider/bundle fails closed without retry.

    The one-line ``queue.py`` contract (``PROVIDER_NOT_CONFIGURED`` in
    ``NON_RETRYABLE_ERROR_CODES``) plus its bundle-missing siblings: config
    errors never consume backoff or a second attempt, while genuinely
    transient codes stay retryable. The fail-closed adapter carries the exact
    ``PROVIDER_NOT_CONFIGURED``/non-retryable contract, and blank or
    half-set operator env never selects a real adapter — nor the S44 test
    stub — so no test provider runs in production.
    """
    from x_insight.reasoning import queue as queue_module

    assert "PROVIDER_NOT_CONFIGURED" in queue_module.NON_RETRYABLE_ERROR_CODES
    for code in ("PROVIDER_NOT_CONFIGURED", "MISSING_PACKAGE", "PACKAGE_INVALID"):
        assert queue_module.is_retryable_error(code) is False, code
    assert queue_module.is_retryable_error(None) is True
    assert queue_module.is_retryable_error("PROVIDER_TIMEOUT") is True

    adapter = worker_module.UnconfiguredProvider()
    outcome = adapter.estimate(
        provider_module.ProviderRequest(
            question_key="synthetic_snapshot",
            projection={},
            projection_hash="0" * 64,
            prompt_version="v1",
            question_run_id="00000000-0000-0000-0000-000000000000",
            batch_id="00000000-0000-0000-0000-000000000001",
        )
    )
    assert outcome.ok is False
    assert outcome.error_code == "PROVIDER_NOT_CONFIGURED"
    assert outcome.retryable is False

    for endpoint, model in (("", ""), ("   ", "   "), ("http://localhost:11434/v1", ""), ("", "m")):
        monkeypatch.setenv(worker_module.PROVIDER_ENDPOINT_ENV_VAR, endpoint)
        monkeypatch.setenv(worker_module.PROVIDER_MODEL_ENV_VAR, model)
        selected = worker_module.build_adapter_from_env()
        assert isinstance(selected, worker_module.UnconfiguredProvider)
        assert not isinstance(selected, provider_module.ControlledStubAdapter)


def test_worker_logs_carry_no_record_text_or_secrets(
    admin_client, clean_registry, monkeypatch, caplog
) -> None:
    """S57 §4 (T8): polling logs carry UUID-only outcomes, never record text.

    Through the production polling entry with a real failed-closed batch on
    real PostgreSQL: the emitted worker records must reference the batch by
    its safe ID while the patient identifier, login password, and projection
    material stay out of the logs. (Access-line hygiene is pinned statically
    by ``--no-access-log`` in ``test_prod_packaging_hygiene``.)
    """
    import logging

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    _production_adapter(monkeypatch)
    _, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_prod_logs", "0012345678"
    )
    with caplog.at_level(logging.WARNING, logger="x_insight.reasoning.worker"):
        worker_module.poll_forever(engine=clean_registry, poll_seconds=0, stop_after=1)
    records = [record for record in caplog.records if record.name == "x_insight.reasoning.worker"]
    assert records, "polling loop must log its UUID-only outcome"
    blob = "\n".join(record.getMessage() for record in records)
    assert batch_id in blob, "outcome logs reference the batch by safe ID"
    for sentinel in ("0012345678", "dr_prod_logs", "pw123", "POSTGRES_PASSWORD", "h_a", "csrf"):
        assert sentinel not in blob, f"worker logs must not contain {sentinel!r}"


def test_fail_closed_survives_app_and_worker_restart(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S57 §4 (T1/T8): fail-closed state survives independent restarts.

    Acknowledged drafts live in PostgreSQL, never in process memory: after
    the worker fails a generation closed, a fresh HTTP client (new app
    process against the same database) still reads the acknowledged draft
    revision, directory, and terminal failed job — and a worker pass under a
    new worker id (restarted polling process) finds no eligible work and
    commits nothing, keeping the single recorded attempt.
    """
    from fastapi.testclient import TestClient

    from x_insight.app import app as app_module

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    adapter = _production_adapter(monkeypatch)
    _, _, batch_id, encounter_id, revision = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_prod_restart", "0012345678"
    )
    first = worker_module.run_once(engine=clean_registry, provider=adapter)
    assert first["status"] == "failed", first

    fresh = TestClient(app_module)
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_prod_restart", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200, login.text
    draft = fresh.get(f"/api/v1/encounters/{encounter_id}")
    assert draft.status_code == 200, draft.text
    assert draft.json()["revision"] == revision
    directory = fresh.get("/api/v1/patients")
    assert directory.status_code == 200, directory.text
    assert any(p["identifier"] == "0012345678" for p in directory.json()["items"])
    batch = fresh.get(f"/api/v1/generation-batches/{batch_id}")
    assert batch.status_code == 200, batch.text
    assert batch.json()["job"]["status"] == "failed"
    assert batch.json()["job"]["next_eligible_at"] is None

    again = worker_module.run_once(
        engine=clean_registry, provider=adapter, worker_id="worker-after-restart"
    )
    assert again["status"] == "idle", again
    reread = fresh.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert reread["job"]["status"] == "failed"
    attempts = reread["attempts"]
    assert (attempts if isinstance(attempts, int) else len(attempts)) == 1
