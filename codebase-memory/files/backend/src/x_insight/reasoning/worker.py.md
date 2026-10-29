# backend/src/x_insight/reasoning/worker.py

Source SHA-256: 5a41053cabf3f7df330443c9c20dd9a7dcd1284027e176a7a21cc5b3442d7152
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Background worker: poll the durable queue and execute one unit of work per pass. Started as a
separate process with `python -m x_insight.reasoning`.

## Important Symbols
- `poll_forever()` — installs SIGTERM/SIGINT handlers and loops `run_once()` every
  `X_INSIGHT_WORKER_POLL_SECONDS` (default 5 s), sleeping in 0.2 s slices for prompt
  shutdown; logs only status/worker/job/batch ids.
- `run_once()` — quiesces while restore maintenance is active, reclaims expired leases, runs
  a **local-calculation job first**, then claims and executes a provider job. Returns
  `idle`/`busy`/`succeeded|failed|cancelled|queued`/`fencing_failed`/`maintenance`.
- `resolve_production_provider()` — uses the job's pinned settings revision, else the
  environment adapter, else `UnconfiguredProvider`, which fails **closed** with non-retryable
  `PROVIDER_NOT_CONFIGURED`.
- `ProductionProvider` — a marker only; `run_once` replaces it with a real adapter bound to
  the claim's per-job grant.
- `_execute_local_claim()` — hands off to `probability_review.service.execute_local_revision`.

## Behavior and Constraints
- The worker shares the jobs table with local recalculation but that class has separate slots
  and never reaches the provider or MCP.
- Environment: `X_INSIGHT_PROVIDER_ENDPOINT`, `_MODEL`, `_CAPABILITY`, `_DATA_KEY`,
  `X_INSIGHT_WORKER_POLL_SECONDS`.

## Related Files
- `backend/src/x_insight/reasoning/__main__.py` — thin `poll_forever()` entry point.
- `backend/src/x_insight/reasoning/queue.py`, `coordinator.py`.
- `deploy/README.md`, `docs/installation.md` — the worker runs as its own container/service.
