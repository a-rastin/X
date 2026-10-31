"""B02 acceptance: actual worker, MCP, HTTP, ledger and one safe log per failure."""

import json

import pytest
from sqlalchemy import text

from tests.support.fake_provider import fake_provider
from tests.worker.test_single_question import (
    _new_physician_batch,
    clean_registry,
    migrated_test_engine,
    valid_cpt,
)
from x_insight import db
from x_insight.reasoning import provider, worker

__all__ = ["clean_registry", "migrated_test_engine"]


@pytest.fixture(scope="module", autouse=True)
def release_b02_connections(migrated_test_engine):
    # The imported session fixture otherwise retains this module's idle pool
    # until the entire suite ends, consuming the disposable server's slots.
    yield
    migrated_test_engine.dispose()


@pytest.mark.parametrize(
    "mode,category,status,exception,subcode",
    [
        (
            {"disconnect_before_headers": True},
            "disconnect",
            None,
            "RemoteDisconnected",
            "REMOTE_DISCONNECTED",
        ),
        ({"disconnect": True}, "disconnect", 200, "IncompleteRead", "INCOMPLETE_READ"),
        ({"status": 503}, "http_status", 503, None, "HTTP_503"),
    ],
)
def test_failed_attempt_metadata(
    clean_registry, monkeypatch, caplog, mode, category, status, exception, subcode
):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "1")
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_b02", "9000000020"
    )
    with fake_provider(cpt=valid_cpt(package["manifest"]["network_hash"]), **mode) as endpoint:
        adapter = provider.BoundedProviderAdapter(
            provider.ProviderConfig(
                endpoint_url=endpoint.url, model="test", local_endpoint_approved=True
            ),
            database_url=db.get_test_database_url(),
            api_key="fictional-test-token",
        )
        outcome = worker.run_once(clean_registry, adapter)
        assert endpoint.requests
    assert outcome["status"] == "failed", outcome
    state = client.get(f"/api/v1/generation-batches/{started['batch']['id']}").json()
    assert state["baseline"] is None
    with clean_registry.connect() as connection:
        row = dict(
            connection.execute(
                text(
                    "SELECT a.* FROM reasoning_job_attempts a "
                    "JOIN reasoning_jobs j ON a.job_id=j.id WHERE j.batch_id=:batch"
                ),
                {"batch": started["batch"]["id"]},
            )
            .mappings()
            .one()
        )
    assert row["failure_class"] == category
    assert row["http_status"] == status
    assert row["exception_type"] == exception
    assert row["error_subcode"] == subcode
    assert row["error_code"] == "PROVIDER_TRANSIENT"
    assert 0 < row["elapsed_seconds"] < 30
    records = [r for r in caplog.records if r.getMessage().startswith("Provider attempt failed: ")]
    assert len(records) == 1
    details = json.loads(records[0].getMessage().partition(": ")[2])
    for key in (
        "failure_class",
        "http_status",
        "exception_type",
        "error_subcode",
        "elapsed_seconds",
        "error_code",
    ):
        assert details[key] == row[key]
    private = json.dumps(row, default=str) + records[0].getMessage()
    for value in (
        "fictional-test-token",
        "Test Patient Alpha",
        package["prompt"]["text"],
        "Authorization",
        "9000000020",
    ):
        assert value not in private


def test_retry_budget_is_persisted_and_each_failure_is_logged(clean_registry, monkeypatch, caplog):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_BASE_SECONDS", "0")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_MAX_SECONDS", "0")
    _, _, _, _, package, _ = _new_physician_batch(
        clean_registry, monkeypatch, "dr_b02_retry", "9000000021"
    )
    # Changing the deployment setting cannot shrink an already queued job's budget.
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "1")
    with fake_provider(cpt=valid_cpt(package["manifest"]["network_hash"]), status=503) as endpoint:
        adapter = provider.BoundedProviderAdapter(
            provider.ProviderConfig(
                endpoint_url=endpoint.url, model="test", local_endpoint_approved=True
            ),
            database_url=db.get_test_database_url(),
        )
        assert worker.run_once(clean_registry, adapter)["status"] == "queued"
        assert worker.run_once(clean_registry, adapter)["status"] == "failed"
        assert len(endpoint.requests) == 2
    with clean_registry.connect() as connection:
        rows = (
            connection.execute(text("SELECT * FROM reasoning_job_attempts ORDER BY attempt_index"))
            .mappings()
            .all()
        )
    assert len(rows) == 2
    assert all(r["failure_class"] == "http_status" and r["http_status"] == 503 for r in rows)
    records = [r for r in caplog.records if r.getMessage().startswith("Provider attempt failed: ")]
    assert len(records) == 2
    assert {json.loads(r.getMessage().partition(": ")[2])["attempt_id"] for r in records} == {
        str(r["id"]) for r in rows
    }


def test_three_failed_attempts_have_audit_events(clean_registry, monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_BASE_SECONDS", "0")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_MAX_SECONDS", "0")
    _, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_b20", "9000000022"
    )
    with fake_provider(cpt=valid_cpt(package["manifest"]["network_hash"]), status=503) as endpoint:
        adapter = provider.BoundedProviderAdapter(
            provider.ProviderConfig(
                endpoint_url=endpoint.url, model="test", local_endpoint_approved=True
            ),
            database_url=db.get_test_database_url(),
        )
        assert [worker.run_once(clean_registry, adapter)["status"] for _ in range(3)] == [
            "queued",
            "queued",
            "failed",
        ]
    with clean_registry.connect() as connection:
        attempts = (
            connection.execute(
                text(
                    "SELECT a.* FROM reasoning_job_attempts a JOIN reasoning_jobs j ON j.id=a.job_id "
                    "WHERE j.batch_id=:batch ORDER BY a.attempt_index"
                ),
                {"batch": started["batch"]["id"]},
            )
            .mappings()
            .all()
        )
        events = (
            connection.execute(
                text(
                    "SELECT operation, details FROM audit_events WHERE details->>'batch_id'=:batch "
                    "AND operation LIKE 'generation.%'"
                ),
                {"batch": started["batch"]["id"]},
            )
            .mappings()
            .all()
        )
    attempt_events = [
        event for event in events if event["operation"] == "generation.attempt.failed"
    ]
    assert len(attempt_events) == 3
    assert len([event for event in events if event["operation"] == "generation.run.failed"]) == 1
    by_index = {event["details"]["attempt_index"]: event["details"] for event in attempt_events}
    for attempt in attempts:
        details = by_index[attempt["attempt_index"]]
        assert details["failure_class"] == attempt["failure_class"]
        assert details["elapsed_seconds"] == attempt["elapsed_seconds"]


def test_0025_to_0026_preserves_existing_attempt(clean_registry, monkeypatch):
    """Round-trip only the new migration in the isolated test DB, retaining a row."""
    from alembic import command
    from alembic.config import Config

    _new_physician_batch(clean_registry, monkeypatch, "dr_b02_migration", "9000000022")
    assert (
        worker.run_once(clean_registry, provider.ControlledStubAdapter("fail_fatal"))["status"]
        == "failed"
    )
    with clean_registry.connect() as connection:
        before = dict(
            connection.execute(
                text(
                    "SELECT id, job_id, attempt_index, outcome, error_code, diagnostics "
                    "FROM reasoning_job_attempts"
                )
            )
            .mappings()
            .one()
        )
    cfg = Config("alembic.ini")
    try:
        command.downgrade(cfg, "0025")
        command.upgrade(cfg, "0026")
        with clean_registry.connect() as connection:
            row = dict(
                connection.execute(text("SELECT * FROM reasoning_job_attempts")).mappings().one()
            )
        assert {key: row[key] for key in before} == before
        assert all(
            row[key] is None
            for key in (
                "failure_class",
                "http_status",
                "exception_type",
                "error_subcode",
                "elapsed_seconds",
            )
        )
    finally:
        command.upgrade(cfg, "head")
