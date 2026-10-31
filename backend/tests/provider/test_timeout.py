"""B01: operator timeout configuration, without a live provider or credentials."""

import pytest

from x_insight.reasoning.provider import ProviderConfig


def config(**kwargs):
    return ProviderConfig(
        endpoint_url="http://127.0.0.1:9/v1/chat/completions", model="test", **kwargs
    )


def test_timeout_defaults_to_300(monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", raising=False)
    assert config().timeout_seconds == 300


def test_timeout_environment_is_read_per_configuration(monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", "141")
    assert config().timeout_seconds == 141
    monkeypatch.setenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", "60")
    assert config().timeout_seconds == 60
    assert config(timeout_seconds=900).timeout_seconds == 900


@pytest.mark.parametrize("value", [0, -1, 9, 901, True, "nonsense", "nan", "inf", "-inf", ""])
def test_invalid_timeout_is_clear(value, monkeypatch):
    monkeypatch.setenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", str(value))
    with pytest.raises(
        ValueError, match="timeout_seconds must be a finite number within \\[10, 900\\]"
    ):
        config()


@pytest.mark.parametrize("value", [10, 60, 141, 300, 900])
def test_valid_timeout_range(value):
    assert config(timeout_seconds=value).timeout_seconds == value
