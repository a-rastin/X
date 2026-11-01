# backend/src/x_insight/reasoning/queue.py

Source SHA-256: f87152865acb8deb3c1852d50202d47557395e650c3e3ed542559a83dabb129d

## Purpose
All queue mechanics for generation work: global admission, durable leases, fairness, attempt
ledger, retry/backoff, MCP grants and fencing. No CPT logic lives here.

## Important Symbols
- Bounds: `LEASE_SECONDS=120`, `HEARTBEAT_INTERVAL_SECONDS=15`, `MAX_PROVIDER_SLOTS=2`,
  `MAX_LOCAL_SLOTS=2`, `MAX_QUEUED_RUNS=100`, `MAX_ATTEMPTS=3`.
- `check_start_admission()` — reuse by fingerprint, else supersede stale active jobs, else
  `429 QUEUE_FULL`.
- `claim_next_job()` / `commit_job_result()` — claim creates lease + attempt + grant;
  commit is fenced on lease token, unexpired lease and deployment generation.
- `is_retryable_error()` + `NON_RETRYABLE_ERROR_CODES` — config/content failures
  (auth, model, capability, destination denied, package invalid, hash mismatch) fail
  immediately; `compute_retry_delay()` returns `2*2^(n-1)` + jitter, capped at 60 s.
- `lock_deployment()` / `get_deployment_generation()` — singleton `reasoning_deployment` row
  is both the admission lock and the fencing generation.
- `insert_successor_job()`, `reclaim_expired_leases()`, `get_batch_queue_view()`, `safe_job()`
  (never leaks tokens).

## Behavior and Constraints
- **No transaction is held across the provider call** — claim commits the lease, the work runs
  outside any transaction, and the result commits separately, fenced.
- Eligibility is re-checked at commit: a stale draft/author/patient **cancels** the job and
  commits no artifact.
- One active job per batch, one queued job at a time; fairness is by oldest grant then FIFO
  (`reasoning_fairness`), with `SKIP LOCKED` for rival workers.
- The batch's provider settings revision is pinned once (`_pinned_provider_revision`), so a
  later settings save cannot hijack running jobs.
- Completed generation attempts emit `generation.attempt.success` or `.failed` in the
  completion transaction, including stale cancellation and lease reclaim. Coordinator
  completion uses the same audit helper; local calculations retain their separate events.
- Reclaim locks expired job rows before touching attempt/grant state; a concurrent committed
  heartbeat is rechecked against the expiry predicate when the row lock is acquired.

## Related Files
- `backend/src/x_insight/reasoning/worker.py`, `coordinator.py`, `tables.py`.
