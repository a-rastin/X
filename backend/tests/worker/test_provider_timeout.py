"""B01: real HTTP delay + real MCP/DB + lease renewal through generation."""

import os
import threading
import time

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
from x_insight.reasoning import provider, queue, worker

__all__ = ["clean_registry", "migrated_test_engine"]


@pytest.mark.parametrize("timeout,expected", [(None, "succeeded"), (60, "queued")])
@pytest.mark.skipif(
    os.getenv("B01_SLOW_TESTS") != "1", reason="141s wall-clock acceptance; B01_SLOW_TESTS=1"
)
def test_141_second_provider(clean_registry, monkeypatch, timeout, expected):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", raising=False)
    _run_delayed(clean_registry, monkeypatch, 141, timeout, expected, reclaim=True)


def test_lease_renewed_during_provider_wait(clean_registry, monkeypatch):
    # Compress only lease/heartbeat time, keeping HTTP and the full pipeline real.
    monkeypatch.setattr(queue, "LEASE_SECONDS", 10)
    monkeypatch.setattr(queue, "HEARTBEAT_INTERVAL_SECONDS", 0.25)
    _run_delayed(clean_registry, monkeypatch, 12, None, "succeeded", reclaim=True)


def _run_delayed(engine, monkeypatch, delay, timeout, expected, reclaim):
    client, _, _, _, package, started = _new_physician_batch(
        engine, monkeypatch, "dr_b01", "9000000001"
    )
    cpt = valid_cpt(package["manifest"]["network_hash"])
    with fake_provider(cpt=cpt, delay=delay) as endpoint:
        kwargs = {} if timeout is None else {"timeout_seconds": timeout}
        adapter = provider.BoundedProviderAdapter(
            provider.ProviderConfig(
                endpoint_url=endpoint.url, model="test", local_endpoint_approved=True, **kwargs
            ),
            database_url=db.get_test_database_url(),
        )
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(worker.run_once(engine, adapter)))
        thread.start()
        try:
            assert endpoint.received.wait(30), (
                f"worker did not reach HTTP: {outcomes}; "
                f"{client.get(f'/api/v1/generation-batches/{started["batch"]["id"]}').json().get('job')}"
            )
            # An independent reclaimer runs while the provider is waiting.
            deadline = time.monotonic() + min(delay - 0.25, 125)
            while time.monotonic() < deadline and thread.is_alive():
                with db.session_scope(engine) as session:
                    queue.reclaim_expired_leases(session)
                time.sleep(0.2)
        finally:
            thread.join(timeout=delay + 35)
        assert not thread.is_alive()
        state = client.get(f"/api/v1/generation-batches/{started['batch']['id']}").json()
        with engine.connect() as connection:
            error = connection.execute(
                text("SELECT diagnostics->>'last_error' FROM reasoning_jobs WHERE id=:id"),
                {"id": state["job"]["id"]},
            ).scalar()
        assert outcomes and outcomes[0]["status"] == expected, (outcomes, error)
        if expected == "succeeded":
            assert state["baseline"] is not None
        else:
            assert error == "PROVIDER_TIMEOUT"
            assert state["baseline"] is None
