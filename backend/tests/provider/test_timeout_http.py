"""Wall-clock B01 acceptance through real MCP and HTTP (no inference stand-in)."""

import os
import time

import pytest

from tests.provider.test_estimation import (
    _provider_request,
    _setup_projection,
    _valid_cpt,
    admin_client,
    clean_registry,
    migrated_test_engine,
)
from tests.support.fake_provider import fake_provider
from x_insight.reasoning.provider import BoundedProviderAdapter, ProviderConfig

__all__ = ["admin_client", "clean_registry", "migrated_test_engine"]


@pytest.mark.skipif(os.getenv("B01_SLOW_TESTS") != "1", reason="B01_SLOW_TESTS=1: real 141s delay")
@pytest.mark.parametrize("timeout", [None, 60])
def test_141_second_http_delay(admin_client, clean_registry, monkeypatch, timeout):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_TIMEOUT_SECONDS", raising=False)
    setup = _setup_projection(
        admin_client, clean_registry, monkeypatch, username="dr_b01_http", identifier="9000000002"
    )
    network_hash = "dd" * 32
    with fake_provider(cpt=_valid_cpt(network_hash), delay=141) as endpoint:
        options = {} if timeout is None else {"timeout_seconds": timeout}
        adapter = BoundedProviderAdapter(
            ProviderConfig(
                endpoint_url=endpoint.url, model="test", local_endpoint_approved=True, **options
            ),
            grant_token=setup["grant"],
            database_url=setup["test_url"],
        )
        start = time.monotonic()
        result = adapter.estimate(_provider_request(setup, network_hash=network_hash))
        elapsed = time.monotonic() - start
        assert endpoint.requests, "must reach real HTTP"
        if timeout is None:
            assert result.ok, result
            assert elapsed >= 141
        else:
            assert not result.ok and result.error_code == "PROVIDER_TIMEOUT", result
            assert result.retryable
            assert 60 <= elapsed < 100


def test_mid_response_disconnect_is_reproduced(admin_client, clean_registry, monkeypatch):
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_b01_disconnect",
        identifier="9000000003",
    )
    network_hash = "dd" * 32
    with fake_provider(cpt=_valid_cpt(network_hash), disconnect=True) as endpoint:
        adapter = BoundedProviderAdapter(
            ProviderConfig(endpoint_url=endpoint.url, model="test", local_endpoint_approved=True),
            grant_token=setup["grant"],
            database_url=setup["test_url"],
        )
        result = adapter.estimate(_provider_request(setup, network_hash=network_hash))
        assert not result.ok and result.error_code == "PROVIDER_TRANSIENT", result
        assert result.retryable
