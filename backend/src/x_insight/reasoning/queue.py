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


def find_reusable_batch(
    session: Session,
    *,
    encounter_id: uuid.UUID,
    fingerprint: str,
    question_key: str,
) -> dict[str, Any] | None:
    """Latest reusable batch for the encounter with same fingerprint + question.

    Same fingerprint implies same facts + pinned package (fingerprint covers
    both), so repeated triggers reuse the run instead of duplicating it.
    Only active batches reuse: ready runs with a queued/leased job, or runs
    without a job yet (non-ready gate or pre-S44 backfill). Terminal jobs
    (cancelled/failed/succeeded) never reuse — callers admit a fresh batch
    so dead history stays history. Returns the batch row or None.
    """
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
        run_row = (
            session.execute(
                select(reasoning_tables.question_runs).where(
                    reasoning_tables.question_runs.c.batch_id == batch["id"],
                    reasoning_tables.question_runs.c.question_key == question_key,
                )
            )
            .mappings()
            .first()
        )
        if run_row is None:
            continue
        run = dict(run_row)
        if str(run.get("status")) != "ready":
            return batch
        job_row = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.question_run_id == run["id"]
                )
            )
            .mappings()
            .first()
        )
        if job_row is None:
            return batch
        if str(dict(job_row).get("status")) in ACTIVE_JOB_STATUSES:
            return batch
        # Terminal job: keep looking at older batches, never resurrect this one.
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
    question_key: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Enforce single-active + queued-cap before inserting a new batch.

    Returns a reusable batch when the same fingerprint already exists.
    A different fingerprint supersedes the prior active generation (old
    jobs cancelled, history retained) so sequential edits keep S40 history
    while never leaving two active generations. Simultaneous triggers with
    the same fingerprint reuse; truly concurrent different-fingerprint
    triggers serialize on the encounter + deployment locks, the loser
    supersedes the winner (one active, history kept). Raises 429 when the
    global queued cap is reached. Drafts/jobs are never deleted here.
    Must run under the encounter lock with the deployment row locked.
    """
    reusable = find_reusable_batch(
        session,
        encounter_id=encounter_id,
        fingerprint=fingerprint,
        question_key=question_key,
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
    question_key: str,
    deployment_generation: int,
    now: datetime | None = None,
) -> None:
    """Backfill a queued job for a reused ready batch that predates S44."""
    run = (
        session.execute(
            select(reasoning_tables.question_runs).where(
                reasoning_tables.question_runs.c.batch_id == batch["id"],
                reasoning_tables.question_runs.c.question_key == question_key,
            )
        )
        .mappings()
        .first()
    )
    if run is None:
        return
    run_dict = dict(run)
    if str(run_dict.get("status")) != "ready":
        return
    insert_initial_job(
        session,
        batch_id=batch["id"],
        question_run_id=run_dict["id"],
        deployment_generation=deployment_generation,
        now=now,
    )


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

    - ``job`` is the safe job shape or None (non-ready batches have no job).
    - ``queue`` carries leased/queued counts, this job's FIFO position
      among queued generation jobs, and ``busy`` (slots saturated).
    Never exposes lease/grant tokens.
    """
    _ = now
    job = get_job_for_batch(session, batch_id)
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


def _is_job_eligible(
    session: Session,
    job: dict[str, Any],
    batch: dict[str, Any],
    run: dict[str, Any],
    now: datetime,
) -> tuple[bool, str]:
    """Real snapshot/gate/account/lifecycle checks before expensive work.

    Uses ``snapshots`` helpers (never mocked): encounter must be draft,
    author active, run gate ready, and current fingerprint must match the
    frozen batch (relevant edits supersede; note-only stays eligible).
    Returns (eligible, reason).
    """
    from x_insight.cases import encounters as encounters_service
    from x_insight.identity import service as identity_service
    from x_insight.reasoning import snapshots as snapshots_service

    encounter = encounters_service.get_encounter(session, batch["encounter_id"])
    if encounter is None or encounter.get("lifecycle") != "draft":
        return False, "encounter not draft"
    author = identity_service.get_user_by_id(session, batch["author_id"])
    if author is None or not author.get("active", False):
        return False, "author inactive"
    if str(run.get("status")) != "ready":
        return False, f"run {run.get('status')} not eligible"
    next_at = job.get("next_eligible_at")
    if next_at is not None and isinstance(next_at, datetime) and next_at > now:
        return False, "not yet eligible"
    draft_data = encounter.get("draft_data") or {}
    pinned = batch.get("pinned_bundle") or {}
    try:
        current_fp, _ = snapshots_service.compute_analysis_fingerprint(draft_data, pinned)
    except Exception:
        return False, "fingerprint error"
    if current_fp != str(batch.get("fingerprint")):
        return False, "stale fingerprint"
    return True, "eligible"


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
        eligible, _ = _is_job_eligible(session, job, batch, run, moment)
        if not eligible:
            # Stale/ineligible: cancel to keep one active + history (same
            # supersede rule as start admission).
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
) -> dict[str, Any]:
    """Commit a provider attempt result with fencing (separate transaction).

    Fencing: job must still be leased with the same token, unexpired, and
    the deployment generation must match. Old tokens after reclaim never
    commit (409). Re-checks draft/author/freshness: stale/ineligible jobs
    cancel without committing late artifacts. Returns the committed job.
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
    session.execute(
        update(reasoning_tables.reasoning_jobs)
        .where(reasoning_tables.reasoning_jobs.c.id == job_id)
        .values(
            lease_token=None,
            status=final_status,
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
