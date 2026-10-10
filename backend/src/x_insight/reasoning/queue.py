"""Durable leased jobs and global admission (S44, seam T8; plan.md §§8.4-8.5).

Slice 1 covers atomic run+job creation, fingerprint reuse, single active
generation per encounter, queued-cap admission (100), and public queue
visibility. Claim/lease/fairness/worker execution lands in later slices
within this same session (one red→green slice at a time).

Operational defaults (plan §8.4, tunable engineering settings):
- heartbeat every 15s, lease 120s, 2 global provider slots, 100 queued runs.
- job classes: generation (provider slots) vs local_calculation (separate
  capacity reserved for S48b; schema carries the class, admission counts
  them separately, no local execution here).

Admission serialization: start and claim take ``reasoning_deployment``
``FOR UPDATE`` (single-row global lock). This serializes queued-cap and
slot checks across concurrent triggers/workers at this user count.
# ponytail: global lock, per-encounter/per-class locks if contention matters.

No DB lock/transaction is held while awaiting the provider: claim commits
the lease, the provider stub runs outside any transaction, then a separate
commit transaction fences on token + deployment generation.
"""

from __future__ import annotations

import hashlib
import random
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.reasoning import tables as reasoning_tables

HEARTBEAT_INTERVAL_SECONDS = 15
LEASE_SECONDS = 120
MAX_PROVIDER_SLOTS = 2
MAX_QUEUED_RUNS = 100
MAX_ATTEMPTS = 3

# S48b separate local capacity (plan §§8.4, 9.1; FR-43, NFR-06): provider
# saturation never consumes local slots. Tunable; 2 mirrors provider width
# while keeping inference bounded (one process at a time per worker call).
# ponytail: single constant, per-class counters reuse count_queued/leased.
MAX_LOCAL_SLOTS = 2
LOCAL_MAX_ATTEMPTS = 3

# S47 shared retry budget (plan §8.5): initial attempt + two retries, three
# total across transport/validation/stage failures. One backoff function
# serves every failure path (no nested adapter retries, no sleep holding a
# lease transaction — eligibility is a persisted timestamp instead).
RETRY_BASE_DELAY_SECONDS = 2.0
RETRY_MAX_DELAY_SECONDS = 60.0

# Config/content errors fail immediately; repair starts a new pinned run.
# Everything else retries within the shared 3-attempt budget.
NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "PROVIDER_AUTH",
        "PROVIDER_MODEL",
        "PROVIDER_CAPABILITY",
        "PROVIDER_BUDGET_EXCEEDED",
        "PROVIDER_TOOL_REJECTED",
        "PROVIDER_CONTEXT",
        "MISSING_PACKAGE",
        "PACKAGE_INVALID",
        "NETWORK_INVALID",
        "NETWORK_TOO_LARGE",
        "HASH_MISMATCH",
    }
)


def is_retryable_error(error_code: str | None) -> bool:
    """False for config/content errors, True for transient/stage failures."""
    if not error_code:
        return True
    return str(error_code) not in NON_RETRYABLE_ERROR_CODES


def compute_retry_delay(attempt_index: int, retry_after_seconds: float | None = None) -> float:
    """Exponential backoff + bounded jitter, capped at 60s (shared cap).

    ``attempt_index`` is the just-finished 1-based attempt (1 → ~2s,
    2 → ~4s). ``retry_after_seconds`` (provider Retry-After when known)
    raises the delay but never past the cap. Pure function (no sleep).
    """
    try:
        index = max(1, int(attempt_index))
    except Exception:
        index = 1
    delay = RETRY_BASE_DELAY_SECONDS * (2.0 ** (index - 1)) + random.uniform(0.0, 1.0)
    if retry_after_seconds is not None:
        try:
            hint = float(retry_after_seconds)
        except Exception:
            hint = 0.0
        if hint > 0:
            delay = max(delay, min(hint, RETRY_MAX_DELAY_SECONDS))
    return min(delay, RETRY_MAX_DELAY_SECONDS)


GENERATION_CLASS = "generation"
LOCAL_CALCULATION_CLASS = "local_calculation"

QUEUED = "queued"
LEASED = "leased"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_JOB_STATUSES = (QUEUED, LEASED)
TERMINAL_JOB_STATUSES = (SUCCEEDED, FAILED, CANCELLED)


def get_deployment_generation(session: Session) -> int:
    """Current deployment generation (persistent, fencing check source).

    Reads the singleton ``reasoning_deployment`` row; seeds ``1`` when
    missing (fresh test DBs that migrated before the seed). Callers that
    need admission serialization should lock it ``FOR UPDATE`` first;
    this helper is the unlocked read for commit-time fencing.
    """
    row = session.execute(select(reasoning_tables.reasoning_deployment)).mappings().first()
    if row is None:
        moment = contracts.utcnow()
        session.execute(
            insert(reasoning_tables.reasoning_deployment).values(
                id=1, generation=1, updated_at=moment
            )
        )
        session.flush()
        return 1
    return int(row["generation"])


def lock_deployment(session: Session) -> int:
    """Lock the deployment row (serializes admission) and return generation."""
    row = (
        session.execute(select(reasoning_tables.reasoning_deployment).with_for_update())
        .mappings()
        .first()
    )
    if row is None:
        moment = contracts.utcnow()
        session.execute(
            insert(reasoning_tables.reasoning_deployment).values(
                id=1, generation=1, updated_at=moment
            )
        )
        session.flush()
        return 1
    return int(row["generation"])


def count_queued(session: Session, job_class: str = GENERATION_CLASS) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(reasoning_tables.reasoning_jobs)
            .where(
                reasoning_tables.reasoning_jobs.c.job_class == job_class,
                reasoning_tables.reasoning_jobs.c.status == QUEUED,
            )
        ).scalar_one()
    )


def count_leased(session: Session, job_class: str = GENERATION_CLASS) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(reasoning_tables.reasoning_jobs)
            .where(
                reasoning_tables.reasoning_jobs.c.job_class == job_class,
                reasoning_tables.reasoning_jobs.c.status == LEASED,
            )
        ).scalar_one()
    )


def _ordered_batch_runs(session: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    """Ordered runs for one batch (S46 pinned position first)."""
    rows = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
                reasoning_tables.question_runs.c.position.asc(),
                reasoning_tables.question_runs.c.question_key.asc(),
                reasoning_tables.question_runs.c.created_at.asc(),
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _batch_jobs(session: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.batch_id == batch_id)
            .order_by(reasoning_tables.reasoning_jobs.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def find_reusable_batch(
    session: Session,
    *,
    encounter_id: uuid.UUID,
    fingerprint: str,
    question_key: str | None = None,
    question_keys: list[str] | None = None,
) -> dict[str, Any] | None:
    """Latest reusable batch for the encounter with same fingerprint + ordered keys.

    Same fingerprint implies same facts + pinned ordered packages + DDI pin
    (fingerprint covers all), so repeated triggers reuse the batch instead
    of duplicating it. Only active batches reuse: batches with a
    queued/leased job, or jobless non-ready batches (clarification or
    pre-S44 backfill). Terminal batches (all jobs
    cancelled/failed/succeeded, no active) never reuse — callers admit a
    fresh batch so dead history stays history. ``question_key`` stays for
    single-question callers; ``question_keys`` carries pinned order for
    workflows (both accepted, at least one required).
    Returns the batch row or None.
    """
    if question_keys is None:
        if question_key is None:
            raise ValueError("question_key or question_keys is required")
        requested = [question_key]
    else:
        requested = list(question_keys)
        if question_key is not None and requested != [question_key]:
            # Both supplied but disagree: prefer the explicit ordered list.
            pass
    rows = (
        session.execute(
            select(reasoning_tables.generation_batches)
            .where(
                reasoning_tables.generation_batches.c.encounter_id == encounter_id,
                reasoning_tables.generation_batches.c.fingerprint == fingerprint,
            )
            .order_by(reasoning_tables.generation_batches.c.created_at.desc())
        )
        .mappings()
        .all()
    )
    for entry in rows:
        batch = dict(entry)
        runs = _ordered_batch_runs(session, batch["id"])
        if [run.get("question_key") for run in runs] != requested:
            continue
        jobs = _batch_jobs(session, batch["id"])
        if not jobs:
            # Jobless batch: non-ready gates reuse (no job needed); ready
            # batches reuse for backfill (pre-S44 or pre-S46 gaps).
            return batch
        if any(str(job.get("status")) in ACTIVE_JOB_STATUSES for job in jobs):
            return batch
        # All jobs terminal: keep looking at older batches, never resurrect.
    return None


def has_active_generation(session: Session, encounter_id: uuid.UUID) -> bool:
    """True when the encounter owns a queued/leased generation job."""
    row = session.execute(
        select(reasoning_tables.reasoning_jobs.c.id)
        .select_from(
            reasoning_tables.reasoning_jobs.join(
                reasoning_tables.generation_batches,
                reasoning_tables.reasoning_jobs.c.batch_id
                == reasoning_tables.generation_batches.c.id,
            )
        )
        .where(
            reasoning_tables.generation_batches.c.encounter_id == encounter_id,
            reasoning_tables.reasoning_jobs.c.job_class == GENERATION_CLASS,
            reasoning_tables.reasoning_jobs.c.status.in_(ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    ).first()
    return row is not None


def cancel_active_jobs_for_encounter(
    session: Session, encounter_id: uuid.UUID, now: datetime | None = None
) -> int:
    """Cancel queued/leased generation jobs for one encounter (history kept).

    Relevant edits supersede the prior active generation: old batches stay
    readable history with cancelled jobs, only the new generation is active.
    Returns the cancelled count. Same transaction as the new batch insert.
    """
    moment = now or contracts.utcnow()
    active_ids = [
        row[0]
        for row in session.execute(
            select(reasoning_tables.reasoning_jobs.c.id)
            .select_from(
                reasoning_tables.reasoning_jobs.join(
                    reasoning_tables.generation_batches,
                    reasoning_tables.reasoning_jobs.c.batch_id
                    == reasoning_tables.generation_batches.c.id,
                )
            )
            .where(
                reasoning_tables.generation_batches.c.encounter_id == encounter_id,
                reasoning_tables.reasoning_jobs.c.job_class == GENERATION_CLASS,
                reasoning_tables.reasoning_jobs.c.status.in_(ACTIVE_JOB_STATUSES),
            )
        ).all()
    ]
    if not active_ids:
        return 0
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id.in_(active_ids))
        .values(lease_token=None, status=CANCELLED, updated_at=moment)
    )
    # Revoke live grants so late worker commits fail fencing (S41 transport
    # reuses this; S44 fencing checks job token + generation on commit).
    session.execute(
        update(reasoning_tables.reasoning_grants)
        .where(
            reasoning_tables.reasoning_grants.c.job_id.in_(active_ids),
            reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )
    return len(active_ids)


def check_start_admission(
    session: Session,
    *,
    encounter_id: uuid.UUID,
    fingerprint: str,
    question_key: str | None = None,
    question_keys: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Enforce single-active + queued-cap before inserting a new batch.

    Returns a reusable batch when the same fingerprint + ordered keys
    already exists. A different fingerprint supersedes the prior active
    generation (old jobs cancelled, history retained) so sequential edits
    keep S40 history while never leaving two active generations.
    Simultaneous triggers with the same fingerprint reuse; truly concurrent
    different-fingerprint triggers serialize on the encounter + deployment
    locks, the loser supersedes the winner (one active, history kept).
    Raises 429 when the global queued cap is reached. Drafts/jobs are never
    deleted here. Must run under the encounter lock with the deployment row
    locked.
    """
    reusable = find_reusable_batch(
        session,
        encounter_id=encounter_id,
        fingerprint=fingerprint,
        question_key=question_key,
        question_keys=question_keys,
    )
    if reusable is not None:
        return reusable
    if has_active_generation(session, encounter_id):
        # Supersede: cancel old actives in this same transaction, then admit.
        cancel_active_jobs_for_encounter(session, encounter_id, now=now)
    if count_queued(session) >= MAX_QUEUED_RUNS:
        raise contracts.ContractError(
            429,
            "QUEUE_FULL",
            "The generation queue is full. Retry shortly.",
            {},
            True,
        )
    return None


def insert_initial_job(
    session: Session,
    *,
    batch_id: uuid.UUID,
    question_run_id: uuid.UUID,
    deployment_generation: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Enqueue the first eligible job for a ready run (same transaction).

    Only ``ready`` gate runs get jobs; not_applicable/clarification batches
    are terminal without jobs. Idempotent per run (unique on run id):
    returns the existing job when one already exists (reuse path for
    pre-S44 batches). Returns None for ineligible runs.
    """
    moment = now or contracts.utcnow()
    existing = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.question_run_id == question_run_id
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        return dict(existing)
    job_id = uuid.uuid4()
    session.execute(
        insert(reasoning_tables.reasoning_jobs).values(
            id=job_id,
            batch_id=batch_id,
            question_run_id=question_run_id,
            job_class=GENERATION_CLASS,
            status=QUEUED,
            attempt_index=0,
            max_attempts=MAX_ATTEMPTS,
            lease_token=None,
            lease_deadline=None,
            last_heartbeat=None,
            deployment_generation=int(deployment_generation),
            next_eligible_at=None,
            diagnostics={},
            result=None,
            created_at=moment,
            updated_at=moment,
        )
    )
    session.flush()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    return dict(row)


def ensure_job_for_reused_batch(
    session: Session,
    *,
    batch: dict[str, Any],
    question_key: str | None = None,
    question_keys: list[str] | None = None,
    deployment_generation: int,
    now: datetime | None = None,
) -> None:
    """Backfill the first eligible job for a reused batch missing it.

    Single-question pre-S44 batches get their ready job; workflow batches
    get the earliest pending ready run (skips not_applicable, stops on
    clarification). Already-enqueued batches are untouched (idempotent per
    run via ``insert_initial_job``).
    """
    _ = question_key
    _ = question_keys
    runs = _ordered_batch_runs(session, batch["id"])
    for run in runs:
        status = str(run.get("status"))
        if status == "not_applicable":
            continue
        if status == "needs_clarification":
            return
        if status != "ready":
            return
        # First eligible ready run: backfill when it has no job and no baseline.
        existing_job = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if existing_job is not None:
            return
        existing_baseline = (
            session.execute(
                select(reasoning_tables.original_baselines).where(
                    reasoning_tables.original_baselines.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if existing_baseline is not None:
            # Already succeeded: look for the next pending successor instead.
            continue
        insert_initial_job(
            session,
            batch_id=batch["id"],
            question_run_id=run["id"],
            deployment_generation=deployment_generation,
            now=now,
        )
        return


def safe_job(row: dict[str, Any]) -> dict[str, Any]:
    """Public job shape: status/progress only, never fencing tokens."""
    result = row.get("result")
    return {
        "id": str(row["id"]),
        "batch_id": str(row["batch_id"]),
        "question_run_id": str(row["question_run_id"]),
        "job_class": str(row["job_class"]),
        "status": str(row["status"]),
        "attempt_index": int(row["attempt_index"]),
        "max_attempts": int(row["max_attempts"]),
        "lease_deadline": (
            contracts.serialize_utc(row["lease_deadline"])
            if row.get("lease_deadline") is not None
            else None
        ),
        "last_heartbeat": (
            contracts.serialize_utc(row["last_heartbeat"])
            if row.get("last_heartbeat") is not None
            else None
        ),
        "next_eligible_at": (
            contracts.serialize_utc(row["next_eligible_at"])
            if row.get("next_eligible_at") is not None
            else None
        ),
        "result": dict(result) if isinstance(result, dict) else result,
        "created_at": contracts.serialize_utc(row["created_at"]),
        "updated_at": contracts.serialize_utc(row["updated_at"]),
    }


def insert_successor_job(
    session: Session,
    *,
    batch_id: uuid.UUID,
    completed_run_id: uuid.UUID,
    deployment_generation: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Atomically enable the next eligible successor after one success.

    Called in the same transaction as the baseline insert + job success
    (persist result + rendered section before enabling next). Skips
    ``not_applicable`` (no job), stops on ``needs_clarification`` (later
    pending), enqueues the next pending ``ready`` run without a job or
    baseline. Returns the new/existing successor job or None (blocked/done).
    Only one queued job exists per batch at a time: no intra-run parallelism.
    """
    moment = now or contracts.utcnow()
    runs = _ordered_batch_runs(session, batch_id)
    completed_pos: int | None = None
    for run in runs:
        if str(run.get("id")) == str(completed_run_id):
            try:
                completed_pos = int(run.get("position", 0))
            except Exception:
                completed_pos = 0
            break
    if completed_pos is None:
        return None
    for run in runs:
        try:
            pos = int(run.get("position", 0))
        except Exception:
            pos = 0
        if pos <= int(completed_pos):
            continue
        status = str(run.get("status"))
        if status == "not_applicable":
            continue
        if status == "needs_clarification":
            return None
        if status != "ready":
            return None
        baseline = (
            session.execute(
                select(reasoning_tables.original_baselines).where(
                    reasoning_tables.original_baselines.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if baseline is not None:
            continue
        existing = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return dict(existing)
        return insert_initial_job(
            session,
            batch_id=batch_id,
            question_run_id=run["id"],
            deployment_generation=int(deployment_generation),
            now=moment,
        )
    return None


def get_job_for_batch(session: Session, batch_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.batch_id == batch_id)
            .order_by(reasoning_tables.reasoning_jobs.c.created_at.asc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_jobs_for_batch(session: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    """All jobs for one batch in creation order (S46 ordered successors)."""
    return _batch_jobs(session, batch_id)


def count_attempts(session: Session, job_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(reasoning_tables.reasoning_job_attempts)
            .where(reasoning_tables.reasoning_job_attempts.c.job_id == job_id)
        ).scalar_one()
    )


def get_batch_queue_view(
    session: Session, batch_id: uuid.UUID, now: datetime | None = None
) -> dict[str, Any]:
    """Public queue visibility for one batch (job + busy + position).

    - ``job`` is the safe job shape or None (non-ready batches have no job;
      kept as the first job for single-question compat).
    - ``jobs`` lists every job for the batch in creation order (S46 ordered
      successors; single-question batches hold one).
    - ``queue`` carries leased/queued counts, this job's FIFO position
      among queued generation jobs, and ``busy`` (slots saturated).
    Never exposes lease/grant tokens.
    """
    _ = now
    job = get_job_for_batch(session, batch_id)
    jobs = list_jobs_for_batch(session, batch_id)
    leased = count_leased(session)
    queued = count_queued(session)
    busy = leased >= MAX_PROVIDER_SLOTS
    position: int | None = None
    if job is not None and str(job.get("status")) == QUEUED:
        position = int(
            session.execute(
                select(func.count())
                .select_from(reasoning_tables.reasoning_jobs)
                .where(
                    reasoning_tables.reasoning_jobs.c.job_class == GENERATION_CLASS,
                    reasoning_tables.reasoning_jobs.c.status == QUEUED,
                    reasoning_tables.reasoning_jobs.c.created_at <= job["created_at"],
                )
            ).scalar_one()
        )
    return {
        "job": safe_job(job) if job is not None else None,
        "jobs": [safe_job(entry) for entry in jobs],
        "attempts": count_attempts(session, job["id"]) if job is not None else 0,
        "queue": {
            "busy": bool(busy),
            "leased_count": int(leased),
            "queued_count": int(queued),
            "max_provider_slots": int(MAX_PROVIDER_SLOTS),
            "max_queued_runs": int(MAX_QUEUED_RUNS),
            "queue_position": position,
        },
    }


def new_fencing_token() -> str:
    return secrets.token_hex(32)


def hash_grant_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def reclaim_expired_leases(session: Session, now: datetime | None = None) -> int:
    """Return expired leased jobs to queued, retaining attempts (fencing).

    Marks the in-flight started attempt as failed (LEASE_EXPIRED) and revokes
    its grant, so the old token can never commit after reclaim. Terminal
    rows are never touched. Returns the reclaimed count.
    """
    moment = now or contracts.utcnow()
    expired_ids = [
        row[0]
        for row in session.execute(
            select(reasoning_tables.reasoning_jobs.c.id).where(
                reasoning_tables.reasoning_jobs.c.status == LEASED,
                reasoning_tables.reasoning_jobs.c.lease_deadline < moment,
            )
        ).all()
    ]
    if not expired_ids:
        return 0
    for job_id in expired_ids:
        job = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        if job is None:
            continue
        job_dict = dict(job)
        token = job_dict.get("lease_token")
        if isinstance(token, str) and token:
            session.execute(
                update(reasoning_tables.reasoning_job_attempts)
                .where(
                    reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
                    reasoning_tables.reasoning_job_attempts.c.lease_token == token,
                    reasoning_tables.reasoning_job_attempts.c.outcome == "started",
                )
                .values(finished_at=moment, outcome="failed", error_code="LEASE_EXPIRED")
            )
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id.in_(expired_ids))
        .values(lease_token=None, status=QUEUED, updated_at=moment)
    )
    session.execute(
        update(reasoning_tables.reasoning_grants)
        .where(
            reasoning_tables.reasoning_grants.c.job_id.in_(expired_ids),
            reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )
    session.flush()
    return len(expired_ids)


def heartbeat_job(
    session: Session, job_id: uuid.UUID, lease_token: str, now: datetime | None = None
) -> bool:
    """Extend a live lease (worker heartbeat every 15s, lease 120s).

    Returns True when the lease was extended, False when fencing fails
    (wrong token, expired, terminal, or deployment changed — old worker
    must stop). Never extends terminal or reclaimed jobs.
    """
    moment = now or contracts.utcnow()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        return False
    job = dict(row)
    if str(job.get("status")) != LEASED:
        return False
    if str(job.get("lease_token") or "") != lease_token:
        return False
    deadline = job.get("lease_deadline")
    if deadline is None or not isinstance(deadline, datetime):
        return False
    if deadline.tzinfo is None:
        return False
    if deadline <= moment:
        return False
    if int(job.get("deployment_generation", 0)) != get_deployment_generation(session):
        return False
    new_deadline = moment + timedelta(seconds=LEASE_SECONDS)
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job_id)
        .values(last_heartbeat=moment, lease_deadline=new_deadline, updated_at=moment)
    )
    session.execute(
        update(reasoning_tables.reasoning_grants)
        .where(
            reasoning_tables.reasoning_grants.c.job_id == job_id,
            reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
        )
        .values(expires_at=new_deadline)
    )
    session.flush()
    return True


def _predecessors_complete(
    session: Session, batch_id: uuid.UUID, position: int
) -> tuple[bool, str]:
    """Ordered S46 check: every earlier run must be done or validly skipped.

    Earlier ``not_applicable`` is a valid skip (no baseline needed);
    earlier ``ready`` needs its immutable baseline (prior inference + section
    committed before this job); earlier ``needs_clarification`` blocks
    successors (later questions stay pending). Returns (ok, reason).
    """
    runs = _ordered_batch_runs(session, batch_id)
    for run in runs:
        try:
            run_pos = int(run.get("position", 0))
        except Exception:
            run_pos = 0
        if run_pos >= int(position):
            continue
        status = str(run.get("status"))
        if status == "not_applicable":
            continue
        if status == "needs_clarification":
            return False, "blocked by clarification"
        if status != "ready":
            return False, f"predecessor {run.get('question_key')} not eligible"
        baseline = (
            session.execute(
                select(reasoning_tables.original_baselines).where(
                    reasoning_tables.original_baselines.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if baseline is None:
            return False, "predecessor pending"
    return True, "predecessors complete"


def _is_job_eligible(
    session: Session,
    job: dict[str, Any],
    batch: dict[str, Any],
    run: dict[str, Any],
    now: datetime,
) -> tuple[bool, str]:
    """Real snapshot/gate/account/lifecycle + ordered checks before work.

    Uses ``snapshots`` helpers (never mocked): encounter must be draft,
    author active, run gate ready, current fingerprint must match the frozen
    batch (relevant edits supersede; note-only stays eligible), and S46
    predecessors must be complete (no intra-run parallelism: the next
    request is absent until prior inference and section commit).
    Returns (eligible, reason).
    """
    from x_insight.cases import encounters as encounters_service
    from x_insight.cases import patients as patients_service
    from x_insight.identity import service as identity_service
    from x_insight.reasoning import snapshots as snapshots_service

    encounter = encounters_service.get_encounter(session, batch["encounter_id"])
    if encounter is None or encounter.get("lifecycle") != "draft":
        return False, "encounter not draft"
    # S47 §4: analytical edits are covered by the fingerprint check below;
    # discard/archive/deactivation fence here so late commits never land.
    # Discard moves lifecycle off draft (above); archive flips the patient
    # flag (no archive route until S51); deactivation clears author active.
    patient_id = encounter.get("patient_id")
    if patient_id is not None:
        patient = patients_service.get_patient(session, patient_id)
        if patient is None or bool(patient.get("archived", False)):
            return False, "patient archived"
    author = identity_service.get_user_by_id(session, batch["author_id"])
    if author is None or not author.get("active", False):
        return False, "author inactive"
    if str(run.get("status")) != "ready":
        return False, f"run {run.get('status')} not eligible"
    next_at = job.get("next_eligible_at")
    if next_at is not None and isinstance(next_at, datetime) and next_at > now:
        return False, "not yet eligible"
    try:
        position = int(run.get("position", 0))
    except Exception:
        position = 0
    ok, reason = _predecessors_complete(session, batch["id"], position)
    if not ok:
        return False, reason
    draft_data = encounter.get("draft_data") or {}
    pinned = batch.get("pinned_bundle") or {}
    try:
        current_fp, _ = snapshots_service.compute_analysis_fingerprint(draft_data, pinned)
    except Exception:
        return False, "fingerprint error"
    if current_fp != str(batch.get("fingerprint")):
        return False, "stale fingerprint"
    return True, "eligible"


def is_job_eligible(
    session: Session,
    job: dict[str, Any],
    batch: dict[str, Any],
    run: dict[str, Any],
    now: datetime,
) -> tuple[bool, str]:
    """Public eligibility check for cross-module use (wraps the same checks)."""
    return _is_job_eligible(session, job, batch, run, now)


def claim_next_job(
    session: Session, worker_id: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Claim one fairly-ordered eligible generation job (short transaction).

    - Serializes on the deployment row (global slot check correct under
      concurrent workers).
    - ``SKIP LOCKED`` skips rows locked by rival workers; no transaction is
      held while awaiting the provider (caller commits this tx, calls the
      provider outside any tx, then commits the result separately).
    - Fair rotation: authors ordered by oldest grant (NULLS FIRST) then
      oldest job (FIFO within physician); persistent ``reasoning_fairness``
      avoids starvation across restarts.
    - Per-run exclusivity: one leased job per batch (single-question proof;
      S46 extends to ordered successors).
    Returns ``{'job', 'lease_token', 'grant_token', 'run', 'batch'}`` or
    ``None`` (idle) / ``{'busy': True}`` (slots saturated).
    """
    moment = now or contracts.utcnow()
    _ = worker_id
    deployment = lock_deployment(session)
    # Reclaim inside the claim path so expired-but-unreclaimed leases never
    # falsely report busy (deterministic `now`; run_once reclaim stays too).
    reclaim_expired_leases(session, now=moment)
    if count_leased(session) >= MAX_PROVIDER_SLOTS:
        return {"busy": True}
    candidates = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(
                reasoning_tables.reasoning_jobs.c.job_class == GENERATION_CLASS,
                reasoning_tables.reasoning_jobs.c.status == QUEUED,
                (
                    reasoning_tables.reasoning_jobs.c.next_eligible_at.is_(None)
                    | (reasoning_tables.reasoning_jobs.c.next_eligible_at <= moment)
                ),
            )
            .order_by(
                reasoning_tables.reasoning_jobs.c.created_at.asc(),
            )
            .limit(50)
        )
        .mappings()
        .all()
    )
    if not candidates:
        return None
    # Fair rotation in Python (small N≤100): group oldest job per author,
    # order authors by last grant (NULLS FIRST) then oldest job time.
    fairness_rows = {
        str(dict(row)["author_id"]): dict(row)
        for row in session.execute(select(reasoning_tables.reasoning_fairness)).mappings()
    }
    by_author: dict[str, dict[str, Any]] = {}
    for entry in candidates:
        job = dict(entry)
        batch_row = (
            session.execute(
                select(reasoning_tables.generation_batches).where(
                    reasoning_tables.generation_batches.c.id == job["batch_id"]
                )
            )
            .mappings()
            .first()
        )
        run_row = (
            session.execute(
                select(reasoning_tables.question_runs).where(
                    reasoning_tables.question_runs.c.id == job["question_run_id"]
                )
            )
            .mappings()
            .first()
        )
        if batch_row is None or run_row is None:
            continue
        batch = dict(batch_row)
        run = dict(run_row)
        author_key = str(batch["author_id"])
        if (
            author_key not in by_author
            or job["created_at"] < by_author[author_key]["job"]["created_at"]
        ):
            by_author[author_key] = {"job": job, "batch": batch, "run": run}
    gen_class = GENERATION_CLASS

    def _author_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, Any]:
        author_key, bundle = item
        fair = fairness_rows.get(author_key)
        last = None
        if fair is not None:
            last = fair.get("last_granted_at") if isinstance(fair, dict) else None
            if last is None and not isinstance(fair, dict):
                try:
                    last = fair["last_granted_at"]  # type: ignore[index]
                except Exception:
                    last = None
        # NULLS FIRST: None sorts before any datetime.
        return ((1, last) if last is not None else (0, moment), bundle["job"]["created_at"])

    ordered_authors = sorted(by_author.items(), key=_author_sort_key)
    chosen: dict[str, Any] | None = None
    for _, bundle in ordered_authors:
        job = bundle["job"]
        batch = bundle["batch"]
        run = bundle["run"]
        # Per-batch exclusivity: skip when another leased job owns this batch.
        other_leased = session.execute(
            select(reasoning_tables.reasoning_jobs.c.id)
            .where(
                reasoning_tables.reasoning_jobs.c.batch_id == batch["id"],
                reasoning_tables.reasoning_jobs.c.status == LEASED,
            )
            .limit(1)
        ).first()
        if other_leased is not None:
            continue
        eligible, reason = _is_job_eligible(session, job, batch, run, moment)
        if not eligible:
            # Ordered workflows: predecessor-pending/blocked is temporary —
            # leave queued for the successor path (never cancel future work).
            # Stale/account/lifecycle mismatches cancel (same supersede rule).
            if reason in ("predecessor pending", "blocked by clarification"):
                continue
            session.execute(
                update(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
                .values(lease_token=None, status=CANCELLED, updated_at=moment)
            )
            continue
        # Try to lock this exact row, skipping if a rival holds it.
        locked = (
            session.execute(
                select(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .first()
        )
        if locked is None:
            continue
        locked_job = dict(locked)
        if str(locked_job.get("status")) != QUEUED:
            continue
        chosen = {"job": locked_job, "batch": batch, "run": run}
        break
    if chosen is None:
        # No eligible work now; distinguish saturation (busy) from idle.
        if count_leased(session) >= MAX_PROVIDER_SLOTS:
            return {"busy": True}
        return None
    job = chosen["job"]
    batch = chosen["batch"]
    run = chosen["run"]
    lease_token = new_fencing_token()
    grant_raw = secrets.token_hex(32)
    deadline = moment + timedelta(seconds=LEASE_SECONDS)
    new_index = int(job.get("attempt_index", 0)) + 1
    if new_index > int(job.get("max_attempts", MAX_ATTEMPTS)):
        session.execute(
            update(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
            .values(lease_token=None, status=FAILED, updated_at=moment)
        )
        session.flush()
        return None
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
        .values(
            status=LEASED,
            lease_token=lease_token,
            lease_deadline=deadline,
            last_heartbeat=moment,
            deployment_generation=int(deployment),
            attempt_index=new_index,
            updated_at=moment,
        )
    )
    session.execute(
        insert(reasoning_tables.reasoning_job_attempts).values(
            id=uuid.uuid4(),
            job_id=job["id"],
            attempt_index=new_index,
            stage="preparing_question",
            lease_token=lease_token,
            deployment_generation=int(deployment),
            started_at=moment,
            finished_at=None,
            outcome="started",
            error_code=None,
            diagnostics={"worker_id": worker_id},
            result=None,
        )
    )
    session.execute(
        insert(reasoning_tables.reasoning_grants).values(
            id=uuid.uuid4(),
            job_id=job["id"],
            batch_id=batch["id"],
            question_run_id=run["id"],
            grant_token_hash=hash_grant_token(grant_raw),
            deployment_generation=int(deployment),
            revoked_at=None,
            expires_at=deadline,
            created_at=moment,
        )
    )
    # Persistent fair-rotation state (upsert).
    existing_fair = (
        session.execute(
            select(reasoning_tables.reasoning_fairness).where(
                reasoning_tables.reasoning_fairness.c.job_class == gen_class,
                reasoning_tables.reasoning_fairness.c.author_id == batch["author_id"],
            )
        )
        .mappings()
        .first()
    )
    if existing_fair is None:
        session.execute(
            insert(reasoning_tables.reasoning_fairness).values(
                job_class=gen_class,
                author_id=batch["author_id"],
                last_granted_at=moment,
            )
        )
    else:
        session.execute(
            update(reasoning_tables.reasoning_fairness)
            .where(
                reasoning_tables.reasoning_fairness.c.job_class == gen_class,
                reasoning_tables.reasoning_fairness.c.author_id == batch["author_id"],
            )
            .values(last_granted_at=moment)
        )
    session.flush()
    claimed = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job["id"]
            )
        )
        .mappings()
        .first()
    )
    assert claimed is not None
    return {
        "job": dict(claimed),
        "lease_token": lease_token,
        "grant_token": grant_raw,
        "run": dict(run),
        "batch": dict(batch),
    }


def commit_job_result(
    session: Session,
    job_id: uuid.UUID,
    lease_token: str,
    provider_ok: bool,
    provider_payload: dict[str, Any] | None,
    provider_error: str | None = None,
    now: datetime | None = None,
    retryable: bool = False,
    stage: str = "preparing_question",
    stage_artifact: dict[str, Any] | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    """Commit a provider attempt result with fencing (separate transaction).

    Fencing: job must still be leased with the same token, unexpired, and
    the deployment generation must match. Old tokens after reclaim never
    commit (409). Re-checks draft/author/freshness: stale/ineligible jobs
    cancel without committing late artifacts. Returns the committed job.

    S47 shared budget: retryable failures with attempts remaining return
    the job to ``queued`` with ``next_eligible_at`` (exponential backoff +
    bounded jitter, max 60s) instead of failing; the attempt ledger keeps
    the failed stage + error. Exhausted (attempts >= max) or non-retryable
    failures mark ``failed``. Success clears eligibility state. No sleep
    holds any transaction; the caller already finished outbound work.
    """
    moment = now or contracts.utcnow()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
    job = dict(row)
    if str(job.get("status")) != LEASED or str(job.get("lease_token") or "") != lease_token:
        raise contracts.ContractError(
            409,
            "FENCING_TOKEN_MISMATCH",
            "Lease expired or reclaimed; result not committed.",
            {"job_id": ["Lease expired or reclaimed."]},
        )
    deadline = job.get("lease_deadline")
    if not isinstance(deadline, datetime) or deadline <= moment:
        raise contracts.ContractError(
            409,
            "LEASE_EXPIRED",
            "Lease expired; result not committed.",
            {"job_id": ["Lease expired."]},
        )
    if int(job.get("deployment_generation", 0)) != get_deployment_generation(session):
        raise contracts.ContractError(
            409,
            "DEPLOYMENT_FENCED",
            "Deployment changed; result not committed.",
            {"job_id": ["Deployment changed."]},
        )
    batch = (
        session.execute(
            select(reasoning_tables.generation_batches).where(
                reasoning_tables.generation_batches.c.id == job["batch_id"]
            )
        )
        .mappings()
        .first()
    )
    run = (
        session.execute(
            select(reasoning_tables.question_runs).where(
                reasoning_tables.question_runs.c.id == job["question_run_id"]
            )
        )
        .mappings()
        .first()
    )
    if batch is None or run is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Batch or run missing.")
    batch_dict = dict(batch)
    run_dict = dict(run)
    eligible, _ = _is_job_eligible(session, job, batch_dict, run_dict, moment)
    if not eligible:
        session.execute(
            update(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .values(lease_token=None, status=CANCELLED, updated_at=moment)
        )
        session.execute(
            update(reasoning_tables.reasoning_job_attempts)
            .where(
                reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
                reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
                reasoning_tables.reasoning_job_attempts.c.outcome == "started",
            )
            .values(finished_at=moment, outcome="failed", error_code="STALE_INPUT")
        )
        session.execute(
            update(reasoning_tables.reasoning_grants)
            .where(
                reasoning_tables.reasoning_grants.c.job_id == job_id,
                reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
            )
            .values(revoked_at=moment)
        )
        session.flush()
        cancelled = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        assert cancelled is not None
        return dict(cancelled)
    final_status = SUCCEEDED if provider_ok else FAILED
    next_eligible: datetime | None = None
    if not provider_ok and bool(retryable) and is_retryable_error(provider_error):
        try:
            used = int(job.get("attempt_index", 0))
        except Exception:
            used = 0
        try:
            budget = int(job.get("max_attempts", MAX_ATTEMPTS))
        except Exception:
            budget = MAX_ATTEMPTS
        if used < budget:
            final_status = QUEUED
            delay = compute_retry_delay(used, retry_after_seconds)
            next_eligible = moment + timedelta(seconds=delay)
    existing_diag = job.get("diagnostics")
    merged_diag: dict[str, Any] = dict(existing_diag) if isinstance(existing_diag, dict) else {}
    if not provider_ok:
        merged_diag["last_error"] = provider_error or "PROVIDER_FAILED"
        merged_diag["last_stage"] = stage
        try:
            merged_diag["attempts_used"] = int(job.get("attempt_index", 0))
        except Exception:
            pass
        if isinstance(stage_artifact, dict) and stage_artifact:
            artifacts = merged_diag.get("stage_artifacts")
            if not isinstance(artifacts, dict):
                artifacts = {}
            else:
                artifacts = dict(artifacts)
            artifacts.update(dict(stage_artifact))
            merged_diag["stage_artifacts"] = artifacts
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job_id)
        .values(
            lease_token=None,
            status=final_status,
            next_eligible_at=next_eligible,
            diagnostics=merged_diag,
            result=dict(provider_payload or {}),
            updated_at=moment,
        )
    )
    session.execute(
        update(reasoning_tables.reasoning_job_attempts)
        .where(
            reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
            reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
            reasoning_tables.reasoning_job_attempts.c.outcome == "started",
        )
        .values(
            finished_at=moment,
            stage=stage,
            outcome="succeeded" if provider_ok else "failed",
            error_code=None if provider_ok else (provider_error or "PROVIDER_FAILED"),
            result=dict(provider_payload or {}),
        )
    )
    session.execute(
        update(reasoning_tables.reasoning_grants)
        .where(
            reasoning_tables.reasoning_grants.c.job_id == job_id,
            reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
        )
        .values(revoked_at=moment)
    )
    session.flush()
    committed = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    assert committed is not None
    return dict(committed)


# --- S48b local calculation queue (seam T8; plan §9.1, system-design §§8.3-8.4) ---
#
# Local jobs share ``reasoning_jobs``/attempts/leases with a separate
# ``job_class`` and capacity (``MAX_LOCAL_SLOTS``). Revision binding lives
# in the existing ``diagnostics`` JSONB (``cpt_revision_id``/``cpt_hash``)
# so no new job columns are needed; ``calculation_results`` (migration
# 0014) holds one immutable success row per revision. No provider/MCP
# context is created or consumed here.


def _local_revision_of(job: dict[str, Any]) -> tuple[str | None, str | None]:
    diag = job.get("diagnostics")
    if not isinstance(diag, dict):
        return None, None
    rev = diag.get("cpt_revision_id")
    h = diag.get("cpt_hash")
    return (str(rev) if rev else None, str(h) if h else None)


def list_local_jobs_for_run(session: Session, run_id: Any) -> list[dict[str, Any]]:
    """All local jobs for one run in creation order (history, no tokens)."""
    rows = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(
                reasoning_tables.reasoning_jobs.c.question_run_id == run_id,
                reasoning_tables.reasoning_jobs.c.job_class == LOCAL_CALCULATION_CLASS,
            )
            .order_by(reasoning_tables.reasoning_jobs.c.created_at.asc())
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def find_local_job_for_revision(
    session: Session, run_id: Any, revision_id: Any
) -> dict[str, Any] | None:
    """Latest local job for one run+revision (idempotent retry reuses it)."""
    wanted = str(revision_id)
    candidates = list_local_jobs_for_run(session, run_id)
    matches = [job for job in candidates if _local_revision_of(job)[0] == wanted]
    if not matches:
        return None
    # Latest by creation (requeue reuses the same row, so at most one
    # queued/leased; terminal duplicates coalesce to the newest).
    matches.sort(key=lambda job: str(job.get("created_at", "")))
    return matches[-1]


def insert_local_job(
    session: Session,
    *,
    batch_id: Any,
    question_run_id: Any,
    cpt_revision_id: Any,
    cpt_hash: str,
    deployment_generation: int,
    now: Any | None = None,
) -> dict[str, Any]:
    """Enqueue one revision-bound local job (caller's transaction).

    One row per new revision (revision UUIDs are fresh, so no duplicate
    check needed here). Retry for the same revision reuses via
    ``find_local_job_for_revision`` + ``requeue`` instead of inserting.
    Superseded queued jobs are left alone (fenced on commit, history kept;
    coalescing is allowed but not required).
    """
    from datetime import datetime as _datetime  # local import, no new dep

    _ = _datetime
    moment = now or contracts.utcnow()
    job_id = uuid.uuid4()
    session.execute(
        insert(reasoning_tables.reasoning_jobs).values(
            id=job_id,
            batch_id=batch_id,
            question_run_id=question_run_id,
            job_class=LOCAL_CALCULATION_CLASS,
            status=QUEUED,
            attempt_index=0,
            max_attempts=LOCAL_MAX_ATTEMPTS,
            lease_token=None,
            lease_deadline=None,
            last_heartbeat=None,
            deployment_generation=int(deployment_generation),
            next_eligible_at=None,
            diagnostics={
                "cpt_revision_id": str(cpt_revision_id),
                "cpt_hash": str(cpt_hash),
            },
            result=None,
            created_at=moment,
            updated_at=moment,
        )
    )
    session.flush()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    return dict(row)


def requeue_local_job(session: Session, job_id: Any, now: Any | None = None) -> dict[str, Any]:
    """Return a terminal local job to queued for explicit retry (same row).

    Keeps attempt history (ledger retains prior attempts); the next claim
    consumes the next attempt index. Only terminal rows requeue; active
    rows return unchanged (idempotent retry coalesces).
    """
    moment = now or contracts.utcnow()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
    job = dict(row)
    if str(job.get("job_class")) != LOCAL_CALCULATION_CLASS:
        raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
    if str(job.get("status")) in ACTIVE_JOB_STATUSES:
        return job
    if str(job.get("status")) not in TERMINAL_JOB_STATUSES:
        return job
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job_id)
        .values(lease_token=None, status=QUEUED, next_eligible_at=None, updated_at=moment)
    )
    session.flush()
    reread = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    assert reread is not None
    return dict(reread)


def _is_local_job_eligible(
    session: Session,
    job: dict[str, Any],
    batch: dict[str, Any],
    run: dict[str, Any],
    now: Any,
) -> tuple[bool, str]:
    """Author/draft/baseline/revision checks before local work (no fingerprint).

    Stale inputs stay eligible (fixed snapshot still calculates; freshness
    remains stale and blocks acceptance/signing). Deactivation/discard
    fences here so late commits never land. No provider/MCP checks.
    Returns (eligible, reason).
    """
    from x_insight.cases import encounters as encounters_service
    from x_insight.identity import service as identity_service
    from x_insight.probability_review import service as review_service
    from x_insight.reasoning import coordinator as coordinator_module

    encounter = encounters_service.get_encounter(session, batch["encounter_id"])
    if encounter is None or encounter.get("lifecycle") != "draft":
        return False, "encounter not draft"
    author = identity_service.get_user_by_id(session, batch["author_id"])
    if author is None or not author.get("active", False):
        return False, "author inactive"
    rev_id, rev_hash = _local_revision_of(job)
    if not rev_id or not rev_hash:
        return False, "missing revision binding"
    # Revision must still exist; hash must match the saved revision.
    revision_row = None
    for entry in review_service.list_revisions(session, run["id"]):
        if str(entry.get("id")) == rev_id:
            revision_row = entry
            break
    if revision_row is None:
        return False, "revision missing"
    if str(revision_row.get("cpt_hash", "")) != rev_hash:
        return False, "hash mismatch"
    stored = coordinator_module.get_baseline(session, run["id"])
    if stored is None:
        return False, "no baseline"
    next_at = job.get("next_eligible_at")
    try:
        from datetime import datetime as _dt

        if next_at is not None and isinstance(next_at, _dt) and next_at > now:
            return False, "not yet eligible"
    except Exception:
        pass
    return True, "eligible"


def claim_next_local_job(
    session: Session, worker_id: str, now: Any | None = None
) -> dict[str, Any] | None:
    """Claim one eligible local job (short transaction, separate capacity).

    - Local slots only (``MAX_LOCAL_SLOTS``); provider saturation never
      blocks this path and no provider grant is created.
    - Fair rotation per local class (``reasoning_fairness`` keyed by
      ``local_calculation``) then FIFO within physician.
    - Per-run exclusivity: one leased local per question run (superseded
      queued jobs wait; fenced on commit).
    Returns ``{'job', 'lease_token', 'run', 'batch', 'revision'}`` or None /
    ``{'busy': True}`` (local slots saturated).
    """
    moment = now or contracts.utcnow()
    _ = worker_id
    deployment = lock_deployment(session)
    reclaim_expired_leases(session, now=moment)
    if count_leased(session, LOCAL_CALCULATION_CLASS) >= MAX_LOCAL_SLOTS:
        return {"busy": True}
    candidates = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(
                reasoning_tables.reasoning_jobs.c.job_class == LOCAL_CALCULATION_CLASS,
                reasoning_tables.reasoning_jobs.c.status == QUEUED,
                (
                    reasoning_tables.reasoning_jobs.c.next_eligible_at.is_(None)
                    | (reasoning_tables.reasoning_jobs.c.next_eligible_at <= moment)
                ),
            )
            .order_by(reasoning_tables.reasoning_jobs.c.created_at.asc())
            .limit(50)
        )
        .mappings()
        .all()
    )
    if not candidates:
        return None
    fairness_rows = {
        str(dict(row)["author_id"]): dict(row)
        for row in session.execute(select(reasoning_tables.reasoning_fairness)).mappings()
    }
    by_author: dict[str, dict[str, Any]] = {}
    for entry in candidates:
        job = dict(entry)
        batch_row = (
            session.execute(
                select(reasoning_tables.generation_batches).where(
                    reasoning_tables.generation_batches.c.id == job["batch_id"]
                )
            )
            .mappings()
            .first()
        )
        run_row = (
            session.execute(
                select(reasoning_tables.question_runs).where(
                    reasoning_tables.question_runs.c.id == job["question_run_id"]
                )
            )
            .mappings()
            .first()
        )
        if batch_row is None or run_row is None:
            continue
        batch = dict(batch_row)
        run = dict(run_row)
        author_key = str(batch["author_id"])
        if (
            author_key not in by_author
            or job["created_at"] < by_author[author_key]["job"]["created_at"]
        ):
            by_author[author_key] = {"job": job, "batch": batch, "run": run}

    def _author_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, Any]:
        author_key, bundle = item
        fair = fairness_rows.get(author_key)
        last = None
        if fair is not None:
            last = fair.get("last_granted_at") if isinstance(fair, dict) else None
        return ((1, last) if last is not None else (0, moment), bundle["job"]["created_at"])

    ordered_authors = sorted(by_author.items(), key=_author_sort_key)
    chosen: dict[str, Any] | None = None
    for _, bundle in ordered_authors:
        job = bundle["job"]
        batch = bundle["batch"]
        run = bundle["run"]
        other_leased = session.execute(
            select(reasoning_tables.reasoning_jobs.c.id)
            .where(
                reasoning_tables.reasoning_jobs.c.question_run_id == run["id"],
                reasoning_tables.reasoning_jobs.c.job_class == LOCAL_CALCULATION_CLASS,
                reasoning_tables.reasoning_jobs.c.status == LEASED,
            )
            .limit(1)
        ).first()
        if other_leased is not None:
            continue
        eligible, reason = _is_local_job_eligible(session, job, batch, run, moment)
        if not eligible:
            if reason in ("not yet eligible",):
                continue
            session.execute(
                update(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
                .values(lease_token=None, status=CANCELLED, updated_at=moment)
            )
            continue
        locked = (
            session.execute(
                select(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
                .with_for_update(skip_locked=True)
            )
            .mappings()
            .first()
        )
        if locked is None:
            continue
        locked_job = dict(locked)
        if str(locked_job.get("status")) != QUEUED:
            continue
        chosen = {"job": locked_job, "batch": batch, "run": run}
        break
    if chosen is None:
        if count_leased(session, LOCAL_CALCULATION_CLASS) >= MAX_LOCAL_SLOTS:
            return {"busy": True}
        return None
    job = chosen["job"]
    batch = chosen["batch"]
    run = chosen["run"]
    lease_token = new_fencing_token()
    deadline = moment + timedelta(seconds=LEASE_SECONDS)
    new_index = int(job.get("attempt_index", 0)) + 1
    if new_index > int(job.get("max_attempts", LOCAL_MAX_ATTEMPTS)):
        session.execute(
            update(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
            .values(lease_token=None, status=FAILED, updated_at=moment)
        )
        session.flush()
        return None
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job["id"])
        .values(
            status=LEASED,
            lease_token=lease_token,
            lease_deadline=deadline,
            last_heartbeat=moment,
            deployment_generation=int(deployment),
            attempt_index=new_index,
            updated_at=moment,
        )
    )
    session.execute(
        insert(reasoning_tables.reasoning_job_attempts).values(
            id=uuid.uuid4(),
            job_id=job["id"],
            attempt_index=new_index,
            stage="calculating",
            lease_token=lease_token,
            deployment_generation=int(deployment),
            started_at=moment,
            finished_at=None,
            outcome="started",
            error_code=None,
            diagnostics={"worker_id": worker_id, "job_class": LOCAL_CALCULATION_CLASS},
            result=None,
        )
    )
    existing_fair = (
        session.execute(
            select(reasoning_tables.reasoning_fairness).where(
                reasoning_tables.reasoning_fairness.c.job_class == LOCAL_CALCULATION_CLASS,
                reasoning_tables.reasoning_fairness.c.author_id == batch["author_id"],
            )
        )
        .mappings()
        .first()
    )
    if existing_fair is None:
        session.execute(
            insert(reasoning_tables.reasoning_fairness).values(
                job_class=LOCAL_CALCULATION_CLASS,
                author_id=batch["author_id"],
                last_granted_at=moment,
            )
        )
    else:
        session.execute(
            update(reasoning_tables.reasoning_fairness)
            .where(
                reasoning_tables.reasoning_fairness.c.job_class == LOCAL_CALCULATION_CLASS,
                reasoning_tables.reasoning_fairness.c.author_id == batch["author_id"],
            )
            .values(last_granted_at=moment)
        )
    session.flush()
    claimed = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job["id"]
            )
        )
        .mappings()
        .first()
    )
    assert claimed is not None
    claimed_job = dict(claimed)
    rev_id, _ = _local_revision_of(claimed_job)
    found_revision: dict[str, Any] | None = None
    if rev_id is not None:
        from x_insight.probability_review import service as review_service

        for rev_entry in review_service.list_revisions(session, run["id"]):
            rev_dict = dict(rev_entry)
            if str(rev_dict.get("id")) == rev_id:
                found_revision = rev_dict
                break
    return {
        "job": claimed_job,
        "lease_token": lease_token,
        "run": dict(run),
        "batch": dict(batch),
        "revision": dict(found_revision) if found_revision is not None else None,
    }


def commit_local_result(
    session: Session,
    job_id: Any,
    lease_token: str,
    *,
    succeeded: bool,
    result_payload: dict[str, Any] | None = None,
    error_code: str | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    """Commit one local calculation with lease + revision fencing (one tx).

    Fencing first (lease token, deadline, deployment). Then author/draft
    eligibility: stale inputs stay eligible (fixed snapshot still solves),
    deactivation/discard cancels without publishing. Then revision fencing:
    the committing revision is compared to the current pointer; matches
    publish (insert immutable success row when succeeded, job succeeded/
    failed), superseded successes still insert as historical rows but never
    move current pointers (derived state keeps truthful labels). Duplicate
    success inserts are idempotent (existing row wins). Audit commits
    atomically with the job/result.
    """
    from x_insight.operations import audit as audit_module
    from x_insight.probability_review import tables as review_tables

    moment = now or contracts.utcnow()
    row = (
        session.execute(
            select(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
    job = dict(row)
    if str(job.get("job_class")) != LOCAL_CALCULATION_CLASS:
        raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
    if str(job.get("status")) != LEASED or str(job.get("lease_token") or "") != lease_token:
        raise contracts.ContractError(
            409,
            "FENCING_TOKEN_MISMATCH",
            "Lease expired or reclaimed; result not committed.",
            {"job_id": ["Lease expired or reclaimed."]},
        )
    deadline = job.get("lease_deadline")
    if not isinstance(deadline, datetime) or deadline <= moment:
        raise contracts.ContractError(
            409,
            "LEASE_EXPIRED",
            "Lease expired; result not committed.",
            {"job_id": ["Lease expired."]},
        )
    if int(job.get("deployment_generation", 0)) != get_deployment_generation(session):
        raise contracts.ContractError(
            409,
            "DEPLOYMENT_FENCED",
            "Deployment changed; result not committed.",
            {"job_id": ["Deployment changed."]},
        )
    batch = (
        session.execute(
            select(reasoning_tables.generation_batches).where(
                reasoning_tables.generation_batches.c.id == job["batch_id"]
            )
        )
        .mappings()
        .first()
    )
    run = (
        session.execute(
            select(reasoning_tables.question_runs).where(
                reasoning_tables.question_runs.c.id == job["question_run_id"]
            )
        )
        .mappings()
        .first()
    )
    if batch is None or run is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Batch or run missing.")
    batch_dict = dict(batch)
    run_dict = dict(run)
    eligible, _ = _is_local_job_eligible(session, job, batch_dict, run_dict, moment)
    if not eligible:
        session.execute(
            update(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .values(lease_token=None, status=CANCELLED, updated_at=moment)
        )
        session.execute(
            update(reasoning_tables.reasoning_job_attempts)
            .where(
                reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
                reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
                reasoning_tables.reasoning_job_attempts.c.outcome == "started",
            )
            .values(finished_at=moment, outcome="failed", error_code="STALE_INPUT")
        )
        session.flush()
        reread = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        assert reread is not None
        return dict(reread)
    rev_id, rev_hash = _local_revision_of(job)
    assert rev_id is not None and rev_hash is not None
    # Current pointer for revision fencing (no update here; state derives).
    state_row = (
        session.execute(
            select(review_tables.question_review_states).where(
                review_tables.question_review_states.c.question_run_id == job["question_run_id"]
            )
        )
        .mappings()
        .first()
    )
    current_rev = (
        str(state_row["current_revision_id"])
        if state_row is not None and state_row.get("current_revision_id") is not None
        else None
    )
    is_current = current_rev is not None and current_rev == rev_id
    final_status = SUCCEEDED if succeeded else FAILED
    if succeeded:
        payload = dict(result_payload or {})
        # Idempotent: existing success for this revision wins (no duplicate).
        try:
            rev_uuid = uuid.UUID(str(rev_id))
        except Exception:
            rev_uuid = None
        existing = None
        if rev_uuid is not None:
            existing = (
                session.execute(
                    select(review_tables.calculation_results).where(
                        review_tables.calculation_results.c.cpt_revision_id == rev_uuid
                    )
                )
                .mappings()
                .first()
            )
        if existing is None:
            # Fallback string compare for UUID typing edges.
            all_for_run = (
                session.execute(
                    select(review_tables.calculation_results).where(
                        review_tables.calculation_results.c.question_run_id
                        == job["question_run_id"]
                    )
                )
                .mappings()
                .all()
            )
            for entry in all_for_run:
                if str(dict(entry).get("cpt_revision_id")) == rev_id:
                    existing = entry
                    break
        if existing is None:
            try:
                reused_raw = payload.get("reused_from_baseline_id")
                reused_uuid = uuid.UUID(str(reused_raw)) if reused_raw else None
            except Exception:
                reused_uuid = None
            session.execute(
                insert(review_tables.calculation_results).values(
                    id=uuid.uuid4(),
                    question_run_id=job["question_run_id"],
                    batch_id=job["batch_id"],
                    cpt_revision_id=rev_uuid,
                    cpt_hash=str(payload.get("cpt_hash") or rev_hash),
                    network_hash=str(payload.get("network_hash") or ""),
                    network_version=str(payload.get("network_version") or ""),
                    template_version=str(payload.get("template_version") or ""),
                    query_nodes=list(payload.get("query_nodes") or []),
                    posteriors=list(payload.get("posteriors") or []),
                    section_text=str(payload.get("section_text") or ""),
                    effective_hash=str(payload.get("effective_hash") or ""),
                    effective_xml=str(payload.get("effective_xml") or ""),
                    reused_from_baseline_id=reused_uuid,
                    provenance=dict(payload.get("provenance") or {}),
                    created_at=moment,
                )
            )
        audit_module.record_audit(
            session,
            operation="calculation.success",
            actor="worker",
            request_id=str(job_id),
            details={
                "encounter_id": str(batch_dict.get("encounter_id")),
                "batch_id": str(job.get("batch_id")),
                "question_run_id": str(job.get("question_run_id")),
                "question_key": str(run_dict.get("question_key", "")),
                "cpt_revision_id": str(rev_id),
                "cpt_hash": str(rev_hash),
                "is_current": bool(is_current),
                "superseded": bool(not is_current),
            },
        )
    else:
        audit_module.record_audit(
            session,
            operation="calculation.failed",
            actor="worker",
            request_id=str(job_id),
            details={
                "encounter_id": str(batch_dict.get("encounter_id")),
                "batch_id": str(job.get("batch_id")),
                "question_run_id": str(job.get("question_run_id")),
                "question_key": str(run_dict.get("question_key", "")),
                "cpt_revision_id": str(rev_id),
                "cpt_hash": str(rev_hash),
                "error_code": str(error_code or "CALCULATION_FAILED"),
                "is_current": bool(is_current),
                "superseded": bool(not is_current),
            },
        )
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job_id)
        .values(
            lease_token=None,
            status=final_status,
            diagnostics={
                **(dict(job.get("diagnostics") or {})),
                **(
                    {"last_error": str(error_code or "CALCULATION_FAILED")} if not succeeded else {}
                ),
            },
            result=dict(result_payload or {})
            if succeeded
            else {"error": str(error_code or "CALCULATION_FAILED")},
            updated_at=moment,
        )
    )
    session.execute(
        update(reasoning_tables.reasoning_job_attempts)
        .where(
            reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
            reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
            reasoning_tables.reasoning_job_attempts.c.outcome == "started",
        )
        .values(
            finished_at=moment,
            stage="calculating",
            outcome="succeeded" if succeeded else "failed",
            error_code=None if succeeded else (error_code or "CALCULATION_FAILED"),
            result=dict(result_payload or {}) if succeeded else None,
        )
    )
    session.flush()
    committed = (
        session.execute(
            select(reasoning_tables.reasoning_jobs).where(
                reasoning_tables.reasoning_jobs.c.id == job_id
            )
        )
        .mappings()
        .first()
    )
    assert committed is not None
    return dict(committed)
