"""B02 operator retry policy: bounded configuration and unchanged defaults."""

import pytest

from x_insight.reasoning import queue


def test_retry_defaults(monkeypatch):
    for suffix in ("MAX_ATTEMPTS", "RETRY_BASE_SECONDS", "RETRY_MAX_SECONDS"):
        monkeypatch.delenv("X_INSIGHT_PROVIDER_" + suffix, raising=False)
    assert queue.retry_settings() == (3, 2.0, 60.0)
    assert 2 <= queue.compute_retry_delay(1) <= 3
    assert 4 <= queue.compute_retry_delay(2) <= 5
    assert queue.compute_retry_delay(2, 1000) == 60


def test_retry_overrides(monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_BASE_SECONDS", "10")
    monkeypatch.setenv("X_INSIGHT_PROVIDER_RETRY_MAX_SECONDS", "20")
    assert queue.retry_settings() == (1, 10, 20)
    assert 10 <= queue.compute_retry_delay(1) <= 11
    assert queue.compute_retry_delay(2) == 20


@pytest.mark.parametrize(
    "suffix,value",
    [
        ("MAX_ATTEMPTS", "0"),
        ("MAX_ATTEMPTS", "11"),
        ("MAX_ATTEMPTS", "1.5"),
        ("RETRY_BASE_SECONDS", "-1"),
        ("RETRY_BASE_SECONDS", "nan"),
        ("RETRY_MAX_SECONDS", "inf"),
        ("RETRY_MAX_SECONDS", "901"),
        ("RETRY_MAX_SECONDS", "1"),
    ],
)
def test_invalid_retry_settings(monkeypatch, suffix, value):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_" + suffix, value)
    with pytest.raises(ValueError, match="Provider retry settings"):
        queue.retry_settings()
