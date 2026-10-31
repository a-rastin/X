"""Durable worker entry point (S44+S45+S48b, seam T8; plan.md §§8.4-8.5, 9.1).

One ``run_once()`` used by tests and the polling process: reclaim expired
leases, claim one fairly-ordered eligible job (short transaction, SKIP
LOCKED), execute outside any DB transaction, then commit with fencing
token + deployment generation checks.

- S48b local path (tried first): revision-bound local recalculation with
  separate capacity (``MAX_LOCAL_SLOTS``). Runs only the fixed network/
  template/configuration with empty evidence; no provider/MCP access, no
  DDI, no other run. Provider saturation never blocks this path.
- S44 path (``ControlledStubAdapter``): deterministic stub outside any tx,
  then ``queue.commit_job_result`` (queue mechanics, no CPT work).
- S45 path (``BoundedProviderAdapter``): full synthetic question via
  ``coordinator.execute_claimed_job`` — real MCP → controlled provider →
  all-CPT validation → effective XML → empty-evidence inference → template
  section with atomic baseline creation (no fake succeeded endpoint).

No DB lock/transaction is held while awaiting the provider or while
running local inference. Restart retains attempts + terminal artifacts
(all rows persistent). Old tokens never commit after reclaim. Superseded
local successes remain historical; only the current revision moves
derived state.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine

from x_insight import contracts
from x_insight import db as db_module
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import queue as queue_module


def run_once(
    engine: Engine | None = None,
    provider: provider_module.ProviderAdapter | None = None,
    now: datetime | None = None,
    worker_id: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Execute one durable work unit (reclaim → claim → execute → commit).

    Deterministic clock adapter: pass ``now`` to control lease deadlines,
    heartbeats, and fencing in tests (defaults to ``utcnow`` in production).
    The same ``now`` drives reclaim, claim, and commit in this call, so
    expiry/reclaim probes are fully deterministic. Production polling
    passes no clock (real time). ``database_url`` overrides the MCP read
    URL for this call (tests pass the isolated test URL; production uses
    the adapter value or environment).

    S48b local jobs run first (separate slots, no provider/MCP); S44 stub
    providers keep the original minimal-request path; S45 bounded providers
    delegate to the coordinator full pipeline (same entry point).

    Returns one of:
    - ``{'status': 'idle'}`` (no eligible work)
    - ``{'status': 'busy', ...}`` (slots saturated, work stays queued)
    - ``{'status': 'succeeded'|'failed'|'cancelled'|'queued', 'job_id': ...}``
      (``queued`` = retryable failure, backoff persisted in
      ``next_eligible_at``; the next ``run_once()`` past eligibility
      consumes the next shared-budget attempt; local jobs return
      ``succeeded``/``failed``/``cancelled`` with ``job_class``)
    - ``{'status': 'fencing_failed', ...}`` (old token lost the race)
    - ``{'status': 'maintenance', ...}`` (S56: filesystem maintenance lock
      held — quiesced, claims nothing so no mixed old/new traffic)
    All observable via public run status (GET batch/review); no SQL-row asserts.
    """
    moment = now or contracts.utcnow()
    wid = worker_id or uuid.uuid4().hex[:12]
    engine = engine or db_module.get_engine()
    provider = provider or provider_module.ControlledStubAdapter()

    # S56 §1 quiesce (T8): behind maintenance the worker claims nothing.
    try:
        from x_insight.operations import restore as restore_module

        if restore_module.is_maintenance_active():
            return {"status": "maintenance", "worker_id": wid}
    except Exception:
        pass

    with db_module.session_scope(engine) as session:
        queue_module.reclaim_expired_leases(session, now=moment)

    # S48b local first: independent slots, no provider/MCP. Provider
    # saturation (generation busy) never blocks this claim.
    local_claimed: dict[str, Any] | None
    with db_module.session_scope(engine) as session:
        local_claimed = queue_module.claim_next_local_job(session, wid, now=moment)
    local_busy = local_claimed is not None and "busy" in local_claimed
    if local_claimed is not None and "job" in local_claimed:
        assert "lease_token" in local_claimed
        return _execute_local_claim(engine, local_claimed, moment, wid)

    claimed: dict[str, Any] | None
    with db_module.session_scope(engine) as session:
        claimed = queue_module.claim_next_job(session, wid, now=moment)

    if claimed is None:
        if local_busy:
            return {"status": "busy", "worker_id": wid, "job_class": "local_calculation"}
        return {"status": "idle", "worker_id": wid}
    if "busy" in claimed:
        if local_busy:
            return {"status": "busy", "worker_id": wid}
        # Generation saturated but local had no work (None): report busy
        # so callers see provider pressure; local progress already tried.
        return {"status": "busy", "worker_id": wid}
    assert "job" in claimed and "lease_token" in claimed
    job = claimed["job"]
    run = claimed["run"]
    batch = claimed["batch"]
    lease_token = str(claimed["lease_token"])
    grant_token = str(claimed.get("grant_token") or "")

    # Resolve after claim so the job's pinned settings revision is known.
    # Local jobs already returned and never reach this.
    if isinstance(provider, ProductionProvider):
        provider = resolve_production_provider(engine, job, database_url)

    # S45 path: bounded estimation with real MCP + full pipeline. The
    # per-job grant binds this claim; rebuild the adapter around it so a
    # probe-constructed adapter (empty grant) still crosses real stdio.
    if isinstance(provider, provider_module.BoundedProviderAdapter):
        from x_insight.reasoning import coordinator as coordinator_module

        per_job = provider_module.BoundedProviderAdapter(
            provider.config,
            grant_token=grant_token,
            database_url=database_url or provider.database_url,
            api_key=provider.api_key,
        )
        with _generation_heartbeat(engine, job["id"], lease_token, now):
            return coordinator_module.execute_claimed_job(
                engine, job, run, batch, lease_token, per_job, moment, wid
            )

    # S44 stub path (unchanged): minimal request, deterministic stub.
    # Provider runs outside any DB transaction or lock (real projection +
    # pinned contract only; stub is deterministic).
    projection = run.get("projection") or {}
    request = provider_module.ProviderRequest(
        question_key=str(run.get("question_key", "")),
        projection=dict(projection) if isinstance(projection, dict) else {},
        projection_hash=str(run.get("projection_hash", "")),
        prompt_version=str((batch.get("pinned_bundle") or {}).get("prompt_version", "v1")),
        question_run_id=str(run.get("id")),
        batch_id=str(batch.get("id")),
    )
    result = provider.estimate(request)

    with db_module.session_scope(engine) as session:
        try:
            committed = queue_module.commit_job_result(
                session,
                job["id"],
                lease_token,
                provider_ok=bool(result.ok),
                provider_payload=dict(result.payload),
                provider_error=result.error_code,
                failure_details=result.failure_details(),
                now=moment,
                retryable=bool(result.retryable),
                stage="estimating_cpts",
            )
        except contracts.ContractError as exc:
            if exc.code in (
                "FENCING_TOKEN_MISMATCH",
                "LEASE_EXPIRED",
                "DEPLOYMENT_FENCED",
            ):
                return {
                    "status": "fencing_failed",
                    "worker_id": wid,
                    "job_id": str(job["id"]),
                    "code": exc.code,
                }
            raise
    return {
        "status": str(committed.get("status")),
        "worker_id": wid,
        "job_id": str(committed.get("id")),
        "batch_id": str(committed.get("batch_id")),
        "error_code": result.error_code,
    }


def _execute_local_claim(
    engine: Engine,
    claimed: dict[str, Any],
    moment: datetime,
    worker_id: str,
) -> dict[str, Any]:
    """Execute one claimed local job (no provider/MCP, fixed snapshot only).

    Runs outside any DB transaction; commits with lease + revision fencing
    so older responses never replace current pointers (superseded successes
    remain historical). Numerical/resource/template failures preserve
    current CPTs and the earlier success (no result row, job failed).
    """
    from x_insight.probability_review import service as review_service

    job = claimed["job"]
    run = claimed["run"]
    batch = claimed["batch"]
    revision = claimed.get("revision")
    lease_token = str(claimed["lease_token"])
    if not isinstance(revision, dict) or not revision:
        # Revision vanished after claim (should not happen; fence as failed).
        with db_module.session_scope(engine) as session:
            try:
                committed = queue_module.commit_local_result(
                    session,
                    job["id"],
                    lease_token,
                    succeeded=False,
                    result_payload=None,
                    error_code="REVISION_MISSING",
                    now=moment,
                )
            except contracts.ContractError as exc:
                if exc.code in (
                    "FENCING_TOKEN_MISMATCH",
                    "LEASE_EXPIRED",
                    "DEPLOYMENT_FENCED",
                ):
                    return {
                        "status": "fencing_failed",
                        "worker_id": worker_id,
                        "job_id": str(job["id"]),
                        "code": exc.code,
                        "job_class": queue_module.LOCAL_CALCULATION_CLASS,
                    }
                raise
        return {
            "status": str(committed.get("status")),
            "worker_id": worker_id,
            "job_id": str(committed.get("id")),
            "batch_id": str(committed.get("batch_id")),
            "job_class": queue_module.LOCAL_CALCULATION_CLASS,
        }
    try:
        payload = review_service.execute_local_revision(revision, run, batch, worker_id)
    except ValueError as exc:
        text = str(exc)
        code = text.split(":", 1)[0].strip() or "CALCULATION_FAILED"
        with db_module.session_scope(engine) as session:
            try:
                committed = queue_module.commit_local_result(
                    session,
                    job["id"],
                    lease_token,
                    succeeded=False,
                    result_payload=None,
                    error_code=code,
                    now=moment,
                )
            except contracts.ContractError as fence:
                if fence.code in (
                    "FENCING_TOKEN_MISMATCH",
                    "LEASE_EXPIRED",
                    "DEPLOYMENT_FENCED",
                ):
                    return {
                        "status": "fencing_failed",
                        "worker_id": worker_id,
                        "job_id": str(job["id"]),
                        "code": fence.code,
                        "job_class": queue_module.LOCAL_CALCULATION_CLASS,
                    }
                raise
        return {
            "status": str(committed.get("status")),
            "worker_id": worker_id,
            "job_id": str(committed.get("id")),
            "batch_id": str(committed.get("batch_id")),
            "job_class": queue_module.LOCAL_CALCULATION_CLASS,
        }
    with db_module.session_scope(engine) as session:
        try:
            committed = queue_module.commit_local_result(
                session,
                job["id"],
                lease_token,
                succeeded=True,
                result_payload=dict(payload),
                error_code=None,
                now=moment,
            )
        except contracts.ContractError as exc:
            if exc.code in (
                "FENCING_TOKEN_MISMATCH",
                "LEASE_EXPIRED",
                "DEPLOYMENT_FENCED",
            ):
                return {
                    "status": "fencing_failed",
                    "worker_id": worker_id,
                    "job_id": str(job["id"]),
                    "code": exc.code,
                    "job_class": queue_module.LOCAL_CALCULATION_CLASS,
                }
            raise
    return {
        "status": str(committed.get("status")),
        "worker_id": worker_id,
        "job_id": str(committed.get("id")),
        "batch_id": str(committed.get("batch_id")),
        "job_class": queue_module.LOCAL_CALCULATION_CLASS,
    }


logger = logging.getLogger(__name__)


@contextmanager
def _generation_heartbeat(engine: Engine, job_id: Any, lease_token: str, now: datetime | None):
    """Keep the lease and MCP grant alive across slow HTTP/tool/inference work.

    Each heartbeat uses a short independent transaction. Explicit test clocks
    remain fixed; production uses the current UTC time. Joining before leaving
    prevents a renewal thread surviving the work unit.
    """
    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(queue_module.HEARTBEAT_INTERVAL_SECONDS):
            try:
                with db_module.session_scope(engine) as session:
                    live = queue_module.heartbeat_job(session, job_id, lease_token, now=now)
                if not live:
                    return
            except Exception:
                logger.warning("generation heartbeat failed")
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()


# S57 production worker entry point (plan.md §3; compose.prod.yaml `worker`
# runs `python -m x_insight.reasoning`). The polling loop reuses run_once();
# adapter selection below is the only production-specific behavior: a
# configured endpoint yields the real BoundedProviderAdapter, otherwise the
# worker fails closed (PROVIDER_NOT_CONFIGURED, non-retryable) so generation
# is honestly unavailable while charts/admin stay usable. The S44
# ControlledStubAdapter is never selected here — no test provider runs in
# production. A job pinned to a saved settings revision uses that row's
# endpoint, model, and decrypted bearer. Env endpoint/model is only the
# keyless fallback for jobs that were queued before any settings existed.

PROVIDER_ENDPOINT_ENV_VAR = "X_INSIGHT_PROVIDER_ENDPOINT"
PROVIDER_MODEL_ENV_VAR = "X_INSIGHT_PROVIDER_MODEL"
PROVIDER_CAPABILITY_ENV_VAR = "X_INSIGHT_PROVIDER_CAPABILITY"
WORKER_POLL_SECONDS_ENV_VAR = "X_INSIGHT_WORKER_POLL_SECONDS"
DEFAULT_POLL_SECONDS = 5.0


class UnconfiguredProvider:
    """Fail-closed adapter: generation cannot call a provider.

    Every estimation attempt returns a non-retryable configuration error so
    the job fails with a clear stage/error code, the draft is retained, and
    ordinary record work is unaffected. Local recalculation jobs never touch
    this adapter (separate slots, no provider/MCP).
    """

    def __init__(self, error_code: str = "PROVIDER_NOT_CONFIGURED") -> None:
        self.error_code = error_code

    def estimate(self, request: provider_module.ProviderRequest) -> provider_module.ProviderResult:
        return provider_module.ProviderResult(ok=False, error_code=self.error_code, retryable=False)


class ProductionProvider:
    """Marker. ``run_once`` replaces this with the job's pinned settings."""

    def estimate(self, request: provider_module.ProviderRequest) -> provider_module.ProviderResult:
        return provider_module.ProviderResult(
            ok=False, error_code="PROVIDER_NOT_CONFIGURED", retryable=False
        )


def resolve_production_provider(
    engine: Engine,
    job: dict[str, Any],
    database_url: str | None = None,
) -> provider_module.ProviderAdapter:
    """Saved revision for a pinned job; env only when the job has no pin."""
    from x_insight.reasoning import provider_config

    diagnostics = job.get("diagnostics")
    pinned = None
    if isinstance(diagnostics, dict):
        pinned = diagnostics.get("provider_settings_revision")
    if not isinstance(pinned, int):
        return build_adapter_from_env()
    with db_module.session_scope(engine) as session:
        loaded = provider_config.open_runtime(session, pinned)
        timeout = provider_config.runtime_timeout(session, pinned)
    if isinstance(loaded, str):
        return UnconfiguredProvider(loaded)
    endpoint, model, api_key = loaded
    capability = (os.environ.get(PROVIDER_CAPABILITY_ENV_VAR) or "schema").strip() or "schema"
    if capability not in ("schema", "json"):
        capability = "schema"
    return provider_module.BoundedProviderAdapter(
        provider_module.ProviderConfig(
            endpoint_url=endpoint,
            model=model,
            capability=capability,
            timeout_seconds=timeout,
            local_endpoint_approved=bool(getattr(endpoint, "local_endpoint_approved", False)),
            trusted_proxy_approved=bool(getattr(endpoint, "trusted_proxy_approved", False)),
        ),
        database_url=database_url,
        api_key=api_key,
    )


def build_adapter_from_env() -> provider_module.ProviderAdapter:
    """Select the production estimation adapter from operator environment."""
    endpoint = (os.environ.get(PROVIDER_ENDPOINT_ENV_VAR) or "").strip()
    model = (os.environ.get(PROVIDER_MODEL_ENV_VAR) or "").strip()
    if not endpoint or not model:
        return UnconfiguredProvider()
    capability = (os.environ.get(PROVIDER_CAPABILITY_ENV_VAR) or "schema").strip() or "schema"
    return provider_module.BoundedProviderAdapter(
        provider_module.ProviderConfig(endpoint_url=endpoint, model=model, capability=capability)
    )


def poll_forever(
    engine: Engine | None = None,
    poll_seconds: float | None = None,
    stop_after: int | None = None,
) -> None:
    """Run run_once() until SIGTERM/SIGINT (production polling process).

    ``poll_seconds`` defaults to X_INSIGHT_WORKER_POLL_SECONDS (5s); sleeps
    in 0.2s slices so shutdown is prompt. ``stop_after`` caps iterations for
    supervised drills (None = run until signalled). Only safe metadata is
    logged (status/worker/job/batch ids); never projections, prompts, or
    record content.
    """
    if poll_seconds is None:
        try:
            poll_seconds = float(os.environ.get(WORKER_POLL_SECONDS_ENV_VAR, "") or 0) or (
                DEFAULT_POLL_SECONDS
            )
        except ValueError:
            poll_seconds = DEFAULT_POLL_SECONDS
    stop = {"requested": False}

    def _request_stop(signum: int, frame: Any) -> None:
        stop["requested"] = True

    previous_term = signal.signal(signal.SIGTERM, _request_stop)
    previous_int = signal.signal(signal.SIGINT, _request_stop)
    try:
        adapter = ProductionProvider()
        logger.warning("worker polling started adapter=%s", type(adapter).__name__)
        iterations = 0
        while not stop["requested"]:
            outcome = run_once(engine=engine, provider=adapter)
            logger.warning(
                "worker outcome status=%s job_id=%s batch_id=%s",
                outcome.get("status"),
                outcome.get("job_id"),
                outcome.get("batch_id"),
            )
            iterations += 1
            if stop_after is not None and iterations >= stop_after:
                return
            deadline = time.monotonic() + max(0.0, float(poll_seconds))
            while not stop["requested"] and time.monotonic() < deadline:
                time.sleep(0.2)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
