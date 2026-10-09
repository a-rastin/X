"""Durable worker entry point (S44+S45, seam T8; plan.md §§8.4-8.5, 8.2-8.3, 7.4, 9).

One ``run_once()`` used by tests and the polling process: reclaim expired
leases, claim one fairly-ordered eligible job (short transaction, SKIP
LOCKED), execute outside any DB transaction, then commit with fencing
token + deployment generation checks.

- S44 path (``ControlledStubAdapter``): deterministic stub outside any tx,
  then ``queue.commit_job_result`` (queue mechanics, no CPT work).
- S45 path (``BoundedProviderAdapter``): full synthetic question via
  ``coordinator.execute_claimed_job`` — real MCP → controlled provider →
  all-CPT validation → effective XML → empty-evidence inference → template
  section with atomic baseline creation (no fake succeeded endpoint).

No DB lock/transaction is held while awaiting the provider. Restart
retains attempts + terminal artifacts (all rows persistent). Old tokens
never commit after reclaim. Local-calculation jobs use separate capacity
(S48b proof later); this worker only executes generation jobs.
"""

from __future__ import annotations

import uuid
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

    S44 stub providers keep the original minimal-request path; S45 bounded
    providers delegate to the coordinator full pipeline (same entry point).

    Returns one of:
    - ``{'status': 'idle'}`` (no eligible work)
    - ``{'status': 'busy', ...}`` (slots saturated, work stays queued)
    - ``{'status': 'succeeded'|'failed'|'cancelled', 'job_id': ...}``
    - ``{'status': 'fencing_failed', ...}`` (old token lost the race)
    All observable via public run status (GET batch); no SQL-row asserts.
    """
    moment = now or contracts.utcnow()
    wid = worker_id or uuid.uuid4().hex[:12]
    engine = engine or db_module.get_engine()
    provider = provider or provider_module.ControlledStubAdapter()

    with db_module.session_scope(engine) as session:
        queue_module.reclaim_expired_leases(session, now=moment)

    claimed: dict[str, Any] | None
    with db_module.session_scope(engine) as session:
        claimed = queue_module.claim_next_job(session, wid, now=moment)

    if claimed is None:
        return {"status": "idle", "worker_id": wid}
    if "busy" in claimed:
        return {"status": "busy", "worker_id": wid}
    assert "job" in claimed and "lease_token" in claimed
    job = claimed["job"]
    run = claimed["run"]
    batch = claimed["batch"]
    lease_token = str(claimed["lease_token"])
    grant_token = str(claimed.get("grant_token") or "")

    # S45 path: bounded estimation with real MCP + full pipeline. The
    # per-job grant binds this claim; rebuild the adapter around it so a
    # probe-constructed adapter (empty grant) still crosses real stdio.
    if isinstance(provider, provider_module.BoundedProviderAdapter):
        from x_insight.reasoning import coordinator as coordinator_module

        per_job = provider_module.BoundedProviderAdapter(
            provider.config,
            grant_token=grant_token,
            database_url=database_url or provider.database_url,
        )
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
    }
