"""B02: real HTTP failure categories, with no response/exception text retained."""

import json
from http.client import RemoteDisconnected

import pytest

from tests.support.fake_provider import fake_provider
from x_insight.reasoning.provider import BoundedProviderAdapter, ProviderConfig


@pytest.mark.parametrize(
    "options,category,status,exception,subcode,code",
    [
        ({"status": 503}, "http_status", 503, None, "HTTP_503", "PROVIDER_TRANSIENT"),
        ({"status": 500}, "http_status", 500, None, "HTTP_500", "PROVIDER_TRANSIENT"),
        ({"status": 502}, "http_status", 502, None, "HTTP_502", "PROVIDER_TRANSIENT"),
        ({"status": 429}, "http_status", 429, None, "HTTP_429", "PROVIDER_RATE_LIMIT"),
        (
            {"disconnect": True},
            "disconnect",
            200,
            "IncompleteRead",
            "INCOMPLETE_READ",
            "PROVIDER_TRANSIENT",
        ),
    ],
)
def test_real_http_failure_details(options, category, status, exception, subcode, code):
    with fake_provider(cpt={"private": "Test Patient Alpha"}, **options) as endpoint:
        adapter = BoundedProviderAdapter(
            ProviderConfig(endpoint_url=endpoint.url, model="test", local_endpoint_approved=True),
            api_key="fictional-test-token",
        )
        _, result = adapter._post_once({"messages": ["Test Patient Alpha"]})
    assert result.error_code == code
    assert result.failure_class == category
    assert result.http_status == status
    assert result.exception_type == exception
    assert result.error_subcode == subcode
    assert "Test Patient Alpha" not in json.dumps(result.__dict__)
    assert "fictional-test-token" not in json.dumps(result.__dict__)


@pytest.mark.parametrize(
    "exception,category,code,subcode",
    [
        (
            RemoteDisconnected("private Test Patient Alpha"),
            "disconnect",
            "PROVIDER_TRANSIENT",
            "REMOTE_DISCONNECTED",
        ),
        (TimeoutError("private Test Patient Alpha"), "timeout", "PROVIDER_TIMEOUT", "TIMEOUT"),
        (OSError("private Test Patient Alpha"), "other", "PROVIDER_TRANSIENT", "TRANSPORT_ERROR"),
    ],
)
def test_exception_details(monkeypatch, exception, category, code, subcode):
    def fail(*_):
        raise exception

    monkeypatch.setattr("x_insight.reasoning.provider.post_json", fail)
    adapter = BoundedProviderAdapter(ProviderConfig(endpoint_url="http://localhost", model="test"))
    _, result = adapter._post_once({})
    assert result.failure_class == category
    assert result.exception_type == type(exception).__name__
    assert result.error_code == code
    assert result.error_subcode == subcode
    assert "Test Patient Alpha" not in json.dumps(result.__dict__)
