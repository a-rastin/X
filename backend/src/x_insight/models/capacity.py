"""Admission measurement for exact-inference capacity (S58 §2; plan.md §11).

Public interface (seam T5 — tests and callers use the same functions):

- :func:`measure_model` — validate one model's complete CPT payload,
  convert it to the run-local effective artifact, and run genuine bounded
  exact inference while measuring request/output sizes, wall time, and
  peak child-process RSS. Returns counts/sizes/timings plus the posterior
  probabilities (needed to prove the run happened and to justify limits).
- :func:`check_admission` — pre-flight the measurement against the enforced
  resource constants (owned by :mod:`x_insight.models.inference`, repeated
  here by import, never forked). Rejection reasons name the preserved
  ``inference_resource`` diagnostic the pipeline itself raises.

What this module does and does not promise:

- Success means "measured under the pinned pipeline"; it is never an
  executability claim beyond the pipeline nor clinical validity.
- Failures are never wrapped: invalid CPTs raise the pipeline's
  :class:`CptValidationError` (``cpt_invalid``) and over-limit artifacts
  raise its :class:`InferenceError` (``inference_resource``) with measured
  counts, so rejection diagnostics stay exactly what admission enforces.
- RSS uses stdlib :mod:`resource` ``RUSAGE_CHILDREN`` around the bounded
  inference call: the delta's peak is this measurement's child high-water
  mark (cumulative process semantics are documented, not hidden).
"""

from __future__ import annotations

import json
import resource
import time
from collections.abc import Mapping
from typing import Any

from x_insight.models import inference as inference_module
from x_insight.models.inference import build_effective_artifact, infer_effective
from x_insight.models.validation import validate


def _child_peak_rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) * 1024


def measure_model(
    xml_bytes: bytes,
    cpt_payload: Mapping[str, Any],
    *,
    query_nodes: list[str] | tuple[str, ...] | None = None,
    timeout_secs: float | None = None,
) -> dict[str, Any]:
    """Measure one model's complete CPT sizes and exact-inference cost.

    ``xml_bytes`` is the registered source document; ``cpt_payload`` is the
    complete CPT request in pipeline contract shape. Returns ``nodes``,
    ``cells``, ``request_bytes`` (canonical JSON of the payload),
    ``output_bytes`` (effective artifact), ``inference_seconds``,
    ``peak_child_rss_bytes``, ``posterior_nodes`` (query order), and
    ``posteriors`` (node → probabilities, proving a genuine run).
    Invalid payloads raise ``CptValidationError``; over-limit artifacts
    raise ``InferenceError('inference_resource', ...)`` — both unwrapped.
    """
    document = validate(bytes(xml_bytes))
    request_bytes = len(json.dumps(dict(cpt_payload), sort_keys=True).encode("utf-8"))
    artifact = build_effective_artifact(document, cpt_payload)
    output_bytes = len(bytes(artifact.effective_bytes))
    nodes = len(artifact.tables)
    cells = sum(len(table.states) * max(len(table.rows), 1) for table in artifact.tables)
    rss_before = _child_peak_rss_bytes()
    started = time.perf_counter()
    budget = timeout_secs if timeout_secs is not None else inference_module.INFERENCE_TIMEOUT_SECS
    try:
        result = infer_effective(artifact, query_nodes=query_nodes, timeout_secs=budget)
    finally:
        elapsed = time.perf_counter() - started
        rss_after = _child_peak_rss_bytes()
    return {
        "nodes": int(nodes),
        "cells": int(cells),
        "request_bytes": int(request_bytes),
        "output_bytes": int(output_bytes),
        "inference_seconds": float(elapsed),
        "peak_child_rss_bytes": int(max(rss_after, rss_before)),
        "posterior_nodes": [str(node) for node in result.query_nodes],
        "posteriors": {
            posterior.node_id: [float(v) for v in posterior.probabilities]
            for posterior in result.posteriors
        },
    }


def check_admission(measured: Mapping[str, Any]) -> dict[str, Any]:
    """Pre-flight a measurement against the pipeline's enforced limits.

    Returns ``{"admitted": bool, "reasons": [...]}``; each reason names the
    ``inference_resource`` diagnostic the pipeline raises at the same
    boundary, so the pre-check can never admit what enforcement rejects.
    """
    reasons: list[str] = []
    nodes = int(measured.get("nodes", 0))
    cells = int(measured.get("cells", 0))
    output_bytes = int(measured.get("output_bytes", 0))
    if nodes > inference_module.MAX_INFERENCE_NODES:
        reasons.append(
            f"nodes {nodes} exceeds limit {inference_module.MAX_INFERENCE_NODES} "
            "(diagnostic inference_resource)"
        )
    if cells > inference_module.MAX_INFERENCE_CELLS:
        reasons.append(
            f"cells {cells} exceeds limit {inference_module.MAX_INFERENCE_CELLS} "
            "(diagnostic inference_resource)"
        )
    if output_bytes > inference_module.MAX_EFFECTIVE_BYTES_FOR_INFERENCE:
        reasons.append(
            f"output {output_bytes} bytes exceeds limit "
            f"{inference_module.MAX_EFFECTIVE_BYTES_FOR_INFERENCE} "
            "(diagnostic inference_resource)"
        )
    return {"admitted": not reasons, "reasons": reasons}
