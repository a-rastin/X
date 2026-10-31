"""B21: real provider latency must appear in UTC attempt timestamps."""

import time

import pytest
from sqlalchemy import text

from tests.worker.test_single_question import (
    _new_physician_batch,
    clean_registry,
    migrated_test_engine,
)
from x_insight.reasoning import provider, worker

__all__ = ["clean_registry", "migrated_test_engine"]


@pytest.mark.parametrize("http_status,bounded", [(200, False), (503, False), (503, True)])
def test_attempt_records_five_second_provider_span(
    clean_registry, monkeypatch, http_status, bounded
):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "1")
    _, _, _, _, _, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_b21", "9000000022"
    )

    class FiveSecondProvider:
        def estimate(self, request):
            time.sleep(5)
            return provider.ProviderResult(
                ok=http_status == 200,
                error_code=None if http_status == 200 else "PROVIDER_TRANSIENT",
            )

    adapter = FiveSecondProvider()
    if bounded:
        monkeypatch.setattr(
            provider.BoundedProviderAdapter, "estimate", FiveSecondProvider.estimate
        )
        adapter = provider.BoundedProviderAdapter(
            provider.ProviderConfig(endpoint_url="https://provider.invalid", model="test")
        )
    outcome = worker.run_once(clean_registry, adapter)
    assert outcome["status"] == ("succeeded" if http_status == 200 else "failed"), outcome
    with clean_registry.connect() as connection:
        row = (
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
    span = (row["finished_at"] - row["started_at"]).total_seconds()
    assert 4 <= span <= 6
    assert row["elapsed_seconds"] == pytest.approx(span, abs=0.001)
    assert row["started_at"].utcoffset().total_seconds() == 0
    assert row["finished_at"].utcoffset().total_seconds() == 0
