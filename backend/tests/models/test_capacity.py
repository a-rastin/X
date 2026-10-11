"""Admission measurement for exact-inference capacity (S58 §2, seam T5).

Through the public model interface only (``validate`` + capacity
measurement over ``validate_cpts`` / ``build_effective_artifact`` /
``infer_effective``); no HTTP, no DB, no provider/MCP. Synthetic fixtures
only — the same two-node and asymmetric networks the S23 suite proves.

- A valid model's measurement carries real sizes (CPT request bytes,
  effective output bytes), cell/node counts, and measured CPU time +
  peak child RSS from a genuine bounded inference run (posteriors prove
  the run happened: P(A=yes)=0.20, P(B=yes)=0.22).
- Invalid CPTs preserve the ``cpt_invalid`` rejection (never normalized).
- An over-limit artifact preserves the ``inference_resource`` diagnostic
  with the measured count and the configured limit.
- The admission pre-check admits the measured fixtures and rejects
  over-limit measurements against the same enforced constants.

Expected values are independently worked literals, never implementation
output.
"""

from __future__ import annotations

import copy

import pytest

from tests.models.test_cpt_inference import _two_node_xml, _valid_two_payload
from x_insight.models import capacity as capacity_module
from x_insight.models.inference import CptValidationError, InferenceError
from x_insight.models.validation import validate


def test_measure_two_node_model_reports_real_sizes_and_run() -> None:
    """S58 §2: complete CPT request/output sizes + measured inference cost."""
    document = validate(_two_node_xml())
    payload = _valid_two_payload(document.source_sha256)
    measured = capacity_module.measure_model(bytes(document.source_bytes), payload)
    assert measured["nodes"] == 2
    assert measured["cells"] == 6  # A: 2 + B: 2 states x 2 rows.
    assert measured["request_bytes"] > 100
    assert measured["output_bytes"] > 500
    assert measured["inference_seconds"] >= 0.0
    assert measured["peak_child_rss_bytes"] > 0
    assert measured["posterior_nodes"] == ["A", "B"]
    # The measurement ran genuine exact inference, not a stub.
    by_node = {name: probs for name, probs in measured["posteriors"].items()}
    assert abs(by_node["A"][1] - 0.20) <= 1e-6
    assert abs(by_node["B"][1] - 0.22) <= 1e-6
    assert abs(sum(by_node["A"]) - 1.0) <= 1e-6


def test_measure_invalid_cpts_preserves_rejection() -> None:
    """S58 §2: malformed estimates are rejected, never silently repaired."""
    document = validate(_two_node_xml())
    payload = copy.deepcopy(_valid_two_payload(document.source_sha256))
    payload["tables"][1]["rows"][0]["percentages"] = ["90", "5"]  # inexact total
    with pytest.raises(CptValidationError) as caught:
        capacity_module.measure_model(bytes(document.source_bytes), payload)
    assert caught.value.code == "cpt_invalid"


def test_measure_over_limit_preserves_resource_diagnostic(monkeypatch) -> None:
    """S58 §2: an artifact past the cell budget fails with measured numbers."""
    from x_insight.models import inference as inference_module

    monkeypatch.setattr(inference_module, "MAX_INFERENCE_CELLS", 5)
    document = validate(_two_node_xml())
    payload = _valid_two_payload(document.source_sha256)
    with pytest.raises(InferenceError) as caught:
        capacity_module.measure_model(bytes(document.source_bytes), payload)
    assert caught.value.code == "inference_resource"
    assert "6 cells" in caught.value.message
    assert "limit 5" in caught.value.message


def test_admission_precheck_uses_enforced_limits(monkeypatch) -> None:
    """S58 §2: the pre-check admits measured fixtures, rejects over-limit ones."""
    from x_insight.models import inference as inference_module

    document = validate(_two_node_xml())
    payload = _valid_two_payload(document.source_sha256)
    measured = capacity_module.measure_model(bytes(document.source_bytes), payload)
    admitted = capacity_module.check_admission(measured)
    assert admitted["admitted"] is True
    assert admitted["reasons"] == []

    over = dict(measured, cells=inference_module.MAX_INFERENCE_CELLS + 1)
    refused = capacity_module.check_admission(over)
    assert refused["admitted"] is False
    assert any("cells" in reason for reason in refused["reasons"])
    assert any("diagnostic" in reason for reason in refused["reasons"])
