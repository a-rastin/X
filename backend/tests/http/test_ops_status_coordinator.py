"""Operational alert acceptance across the coordinator and real status endpoint."""

from fastapi.testclient import TestClient

from tests.support.fake_provider import fake_provider
from tests.worker.test_single_question import (
    _new_physician_batch,
    _run_bounded_once,
    clean_registry,
    migrated_test_engine,
    valid_cpt,
)
from x_insight import db
from x_insight.app import app
from x_insight.reasoning import provider, worker

__all__ = ["clean_registry", "migrated_test_engine"]


def test_three_terminal_runs_and_success(clean_registry, monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_BASE_SECONDS", "0")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_MAX_SECONDS", "0")
    admin = TestClient(app)
    assert (
        admin.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin", "role": "admin"}
        ).status_code
        == 200
    )

    def alerts():
        response = admin.get("/api/v1/ops/status")
        assert response.status_code == 200
        return [a for a in response.json()["alerts"] if a["name"] == "GENERATION_RUN_FAILURES"]

    for number in range(3):
        if number:
            monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "1")
        _, _, _, _, package, _ = _new_physician_batch(
            clean_registry, monkeypatch, f"dr_status_real{number}", f"90000000{number + 40}"
        )
        with fake_provider(
            cpt=valid_cpt(package["manifest"]["network_hash"]), status=503
        ) as endpoint:
            adapter = provider.BoundedProviderAdapter(
                provider.ProviderConfig(
                    endpoint_url=endpoint.url, model="test", local_endpoint_approved=True
                ),
                database_url=db.get_test_database_url(),
            )
            outcomes = [
                worker.run_once(clean_registry, adapter)["status"]
                for _ in range(3 if number == 0 else 1)
            ]
        assert outcomes == (["queued", "queued", "failed"] if number == 0 else ["failed"])
        assert bool(alerts()) == (number == 2)
    assert alerts()[0]["count"] == 3
    assert alerts()[0]["last_failure_class"] == "http_status"
    _, _, _, _, package, _ = _new_physician_batch(
        clean_registry, monkeypatch, "dr_status_real_success", "9000000043"
    )
    assert (
        _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))["status"]
        == "succeeded"
    )
    assert alerts() == []
