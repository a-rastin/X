import json
from types import SimpleNamespace

import pytest

from x_insight.reasoning.provider import _assistant_text, validate_strict_response


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"content": '{"value": 1}'}, 1),
        ({"content": None, "reasoning_content": '{"value": 2}'}, 2),
        ({"content": '{"value": 1}', "reasoning_content": '{"value": 2}'}, 1),
        ({"content": {"value": 1}, "reasoning_content": '{"value": 2}'}, 2),
        ({"content": 'Result: ```json\n{"value": 3}\n```'}, 3),
        ({"content": 'Earlier {"value": 1}; final {"value": 4}'}, 4),
        ({"content": 'Final {"value": 5, "text": "a } brace"}'}, 5),
    ],
)
def test_select_and_parse_response(message, expected, monkeypatch):
    monkeypatch.setattr(
        "x_insight.models.inference.validate_cpts",
        lambda *_: SimpleNamespace(valid=True),
    )
    result, reason = validate_strict_response(
        _assistant_text(message), network_hash="hash", nodes=[]
    )
    assert reason is None
    assert result is not None and result["value"] == expected


@pytest.mark.parametrize(
    "content", [None, "", '{"value":', '{"value": NaN}', json.dumps([{"value": 1}])]
)
def test_reject_unparseable_responses(content, monkeypatch):
    monkeypatch.setattr(
        "x_insight.models.inference.validate_cpts",
        lambda *_: SimpleNamespace(valid=True),
    )
    result, reason = validate_strict_response(content, network_hash="hash", nodes=[])
    assert result is None
    assert reason is not None


def test_invalid_envelope_logs_only_length_and_shape(monkeypatch, caplog):
    from x_insight.reasoning.provider import BoundedProviderAdapter, ProviderConfig

    class InvalidResponse:
        status_code = 200
        content = b"private response text"

        def json(self):
            raise ValueError("private response text")

    monkeypatch.setattr("x_insight.reasoning.provider.post_json", lambda *_: InvalidResponse())
    adapter = BoundedProviderAdapter(ProviderConfig(endpoint_url="http://localhost", model="test"))
    body, failure = adapter._post_once({})
    assert body is None
    assert failure is not None and failure.error_code == "PROVIDER_INVALID_RESPONSE"
    assert "length=21" in caplog.text and "envelope_shape=invalid" in caplog.text
    assert "private response text" not in caplog.text


@pytest.mark.parametrize(
    ("body", "shape"),
    [
        ({"choices": [{"message": {"content": "private broken response"}}]}, "content_shape=str"),
        (None, "envelope_shape=NoneType"),
    ],
)
def test_estimation_parse_failure_is_retryable_and_private(body, shape, monkeypatch, caplog):
    from x_insight.reasoning.provider import BoundedProviderAdapter, ProviderConfig, ProviderRequest

    monkeypatch.setattr("x_insight.reasoning.provider._contract_nodes", lambda _: [])
    monkeypatch.setattr(
        "x_insight.reasoning.provider.build_outbound_payload", lambda *_: {"messages": []}
    )
    adapter = BoundedProviderAdapter(ProviderConfig(endpoint_url="http://localhost", model="test"))
    monkeypatch.setattr(adapter, "_mcp_read", lambda _: ({"projection_hash": "hash"}, None))
    monkeypatch.setattr(
        adapter,
        "_post_once",
        lambda _: (body, None),
    )
    request = ProviderRequest(
        question_key="test",
        projection={},
        projection_hash="hash",
        prompt_version="v1",
        question_run_id="test",
        batch_id="test",
        prompt_text="test",
        network_hash="hash",
    )
    result = adapter.estimate(request)
    assert not result.ok and result.error_code == "PROVIDER_INVALID_RESPONSE" and result.retryable
    assert "length=" in caplog.text and shape in caplog.text
    assert "private broken response" not in caplog.text
