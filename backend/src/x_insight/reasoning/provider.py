"""Controlled external provider adapter stub interface (S44; S43/S45 complete it).

At this stage the worker exercises preparation/claim/recovery with real
snapshot/gate logic (``snapshots.py``) plus this deterministic stub — no
network, no MCP transport, no full CPT validation/inference/rendering
(those arrive in S43/S45). The stub is the only faked external dependency
(plan §12.1 allows faking the external provider, controlled time, and
necessary OS failures); the queue's own collaborators are never mocked.

The interface mirrors the future estimation contract so S43 can fill it
without changing worker call sites: one ``estimate`` call per attempt,
outside any DB transaction, with explicit success/failure mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderRequest:
    """Minimal estimation input: persisted projection + pinned contract."""

    question_key: str
    projection: dict[str, Any]
    projection_hash: str
    prompt_version: str
    question_run_id: str
    batch_id: str


@dataclass(frozen=True)
class ProviderResult:
    """Stub outcome: success carries a candidate payload, failure an error."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False


class ProviderAdapter(Protocol):
    """External estimation interface (one call per attempt, no DB access)."""

    def estimate(self, request: ProviderRequest) -> ProviderResult: ...


class ControlledStubAdapter:
    """Deterministic test stub: fixed success or injected failure.

    ``mode='succeed'`` returns a minimal candidate artifact referencing the
    projection hash (S45 replaces this with full CPT validation). Other
    modes simulate bounded failures for recovery slices without network.
    """

    def __init__(self, mode: str = "succeed") -> None:
        if mode not in ("succeed", "fail_retryable", "fail_fatal"):
            raise ValueError("mode must be succeed|fail_retryable|fail_fatal")
        self.mode = mode
        self.calls: list[ProviderRequest] = []

    def estimate(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        if self.mode == "succeed":
            return ProviderResult(
                ok=True,
                payload={
                    "schema_version": "stub-estimation-v1",
                    "question_key": request.question_key,
                    "projection_hash": request.projection_hash,
                    "note": "controlled stub; full CPT estimation lands in S43/S45",
                },
            )
        if self.mode == "fail_retryable":
            return ProviderResult(ok=False, error_code="PROVIDER_TIMEOUT", retryable=True)
        return ProviderResult(ok=False, error_code="PROVIDER_AUTH", retryable=False)
