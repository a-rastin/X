"""Synthetic load probe over public interfaces (S58 §1; plan.md §§11, 12.2).

``make test-load`` entry point (``python -m x_insight.operations.load_probe``).
Exercises ONLY synthetic data through the public HTTP interface (T1) with an
optional T8 provider phase, then records the environment and results:

- Ordinary phase (always): register N synthetic patients, save one draft
  revision each, run directory searches and draft-read polls with
  representative concurrency. Every latency is client-measured with
  ``perf_counter``; the report carries per-operation count/avg/p50/p95/max.
- Provider phase (only with an explicit executor): time bounded worker
  executions separately so provider-path latency never mixes into the
  ordinary p95. The default executor uses the deterministic
  ``ControlledStubAdapter`` (queue-path stand-in); live LLM latency is NOT
  measured here and the report says so.

No fabricated numbers: counts come from observed responses, latencies from
measured clocks, host/dataset/duration from the actual run. Synthetic
identifiers live in the ``9xxx`` range and letter-only names so they never
collide with real records; passwords/secrets never enter the report.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

TOOL_VERSION = "s58-v1"

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_PATIENTS = 50
DEFAULT_WORKERS = 4

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_HEADER = "X-CSRF-Token"


class ClientSession:
    """One authenticated session over an httpx-like or TestClient-like client.

    Both clients expose ``request(method, url, ...)`` with ``status_code``
    and ``json()`` responses, so this single wrapper serves live runs
    (httpx, absolute URLs) and seam tests (TestClient, paths).
    """

    def __init__(self, client: Any, csrf_token: str = "", base_url: str = "") -> None:
        self._client = client
        self._csrf = csrf_token
        self._base_url = (base_url or "").rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base_url or "testclient"

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}" if self._base_url else path
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() in _MUTATING_METHODS and self._csrf:
            headers.setdefault(_CSRF_HEADER, self._csrf)
        return self._client.request(method, url, headers=headers, **kwargs)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def percentile(samples: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile over measured samples (stdlib only)."""
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


def _stats(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "max": None}
    total = sum(samples)
    return {
        "count": len(samples),
        "avg": total / len(samples),
        "p50": percentile(samples, 50),
        "p95": percentile(samples, 95),
        "max": max(samples),
    }


def _family_name(counter: int) -> str:
    """Letter-only unique family name (names reject digits/punctuation)."""
    letters = []
    value = max(1, counter)
    while value:
        value, rest = divmod(value - 1, 26)
        letters.append(chr(ord("A") + rest))
    return "Probe" + "".join(reversed(letters))


def _synthetic_batch_package() -> dict[str, Any]:
    """Minimal synthetic estimation package (synthetic scope only)."""
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": "synthetic_snapshot",
            "title": "Synthetic snapshot question",
            "workflow": "registration",
            "version": "load-probe-v1",
            "review_status": "draft",
            "network_file": "network.xml",
            "network_hash": "0" * 64,
            "source_refs": ["synthetic/source.md"],
            "declared_node_order": ["A", "B"],
            "variables": [
                {
                    "node_id": "A",
                    "kind": "nature",
                    "patient_value_type": "tristate",
                    "states": ["no", "yes"],
                    "ordered_parents": [],
                },
                {
                    "node_id": "B",
                    "kind": "nature",
                    "patient_value_type": "tristate",
                    "states": ["no", "yes"],
                    "ordered_parents": ["A"],
                },
            ],
            "patient_mappings": [
                {
                    "node_id": "A",
                    "allowed_source_paths": ["synthetic/history/h_a"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
                {
                    "node_id": "B",
                    "allowed_source_paths": ["synthetic/history/h_b"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
            ],
            "applicability": {
                "expression": "true",
                "required_fields": ["synthetic/history/h_a", "synthetic/history/h_b"],
                "unknown_policy": "needs_clarification",
            },
            "cpt_contract": {
                "nodes": [
                    {"node_id": "A", "parent_ids": [], "states": ["no", "yes"]},
                    {"node_id": "B", "parent_ids": ["A"], "states": ["no", "yes"]},
                ]
            },
            "query_nodes": ["A", "B"],
            "execution_evidence": {},
            "prompt_version": "v1",
            "template_version": "v1",
        },
        "prompt": {"version": "v1", "text": "Estimate every CPT. Return strict schema."},
        "template": {
            "version": "v1",
            "branches": [
                {
                    "when": {"node": "B", "state": "yes", "operator": "=="},
                    "text": "Result for {B} is present.",
                },
            ],
        },
        "examples": {
            "numerical": [{"inputs": {}, "expected": {"A": {"no": 0.8, "yes": 0.2}}}],
            "clinical": [
                {
                    "inputs": {},
                    "expected_for_review": {"B": "yes"},
                    "note": "synthetic review candidate",
                }
            ],
        },
        "review": {
            "reviewer": "owner",
            "decision": "draft",
            "date": "2026-10-04",
            "source_hashes": {"network.xml": "0" * 64},
            "assumptions": ["synthetic only"],
            "source_comparison": "synthetic",
            "explicit_graph": "A -> B",
            "reference_table_provenance": "synthetic",
            "estimation_instructions": "estimate every CPT",
            "result_mapping": "B yes/no",
            "numerical_examples": "two-node",
            "clinical_examples": "synthetic",
            "admission_measurements": "synthetic",
            "open_assumptions": "synthetic",
        },
    }


def _timed(session: ClientSession, method: str, path: str, **kwargs: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    try:
        return session.request(method, path, **kwargs), time.perf_counter() - started
    except Exception:
        raise


def _worker_slice(
    factory: Callable[[], ClientSession],
    indices: list[int],
    id_start: int,
    searches_each: int,
    polls_each: int,
) -> dict[str, Any]:
    """Register/save/search/poll one slice of synthetic patients (one session)."""
    session = factory()
    samples: dict[str, list[float]] = {
        "create_patient": [],
        "save_draft": [],
        "search": [],
        "poll": [],
    }
    created = 0
    conflicts = 0
    errors: list[dict[str, Any]] = []
    encounters: list[dict[str, Any]] = []
    try:
        for position, counter in enumerate(indices):
            identifier = str(id_start + counter)
            response, elapsed = _timed(
                session,
                "POST",
                "/api/v1/patients",
                json={
                    "identifier": identifier,
                    "given_name": "Load",
                    "family_name": _family_name(counter),
                    "sex": "F" if position % 2 == 0 else "M",
                    "age": 18 + (counter % 82),
                    "clinical_status": "first_time" if position % 2 == 0 else "established",
                },
            )
            if response.status_code == 409:
                conflicts += 1
                continue
            if response.status_code != 201:
                errors.append(
                    {"op": "create_patient", "status": response.status_code, "id": identifier}
                )
                continue
            samples["create_patient"].append(elapsed)
            created += 1
            body = response.json()
            encounter_id = body["draft"]["id"]
            saved, save_elapsed = _timed(
                session,
                "PATCH",
                f"/api/v1/encounters/{encounter_id}",
                json={
                    "draft_data": {
                        "history": {"values": {"h_a": "yes", "h_b": "no"}},
                        "gate": "true",
                    }
                },
                headers={"If-Match": '"1"'},
            )
            if saved.status_code != 200:
                errors.append({"op": "save_draft", "status": saved.status_code, "id": identifier})
                continue
            samples["save_draft"].append(save_elapsed)
            encounters.append({"encounter_id": encounter_id, "revision": saved.json()["revision"]})
            for _ in range(searches_each):
                found, search_elapsed = _timed(
                    session, "GET", f"/api/v1/patients?query={identifier[-4:]}&limit=25"
                )
                if found.status_code != 200:
                    errors.append({"op": "search", "status": found.status_code})
                else:
                    samples["search"].append(search_elapsed)
            for _ in range(polls_each):
                polled, poll_elapsed = _timed(session, "GET", f"/api/v1/encounters/{encounter_id}")
                if polled.status_code != 200:
                    errors.append({"op": "poll", "status": polled.status_code})
                else:
                    samples["poll"].append(poll_elapsed)
    finally:
        session.close()
    return {
        "samples": samples,
        "created": created,
        "conflicts": conflicts,
        "errors": errors,
        "encounters": encounters,
    }


def run_load(
    session_factory: Callable[[], ClientSession],
    config: dict[str, Any],
    provider_exec: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run the synthetic load and return the measured report (no fabrication).

    ``session_factory`` yields one authenticated physician session per
    worker thread. ``provider_exec`` performs one bounded provider-path
    execution per requested sample (timed here) and returns its outcome
    mapping; outcomes are tallied per status. Without it the provider
    phase reports ``skipped`` honestly instead of inventing numbers.
    """
    patients = int(config.get("patients", DEFAULT_PATIENTS))
    workers = max(1, int(config.get("workers", DEFAULT_WORKERS)))
    searches_each = int(config.get("searches_each", 2))
    polls_each = int(config.get("polls_each", 2))
    provider_samples = int(config.get("provider_samples", 0))
    id_start = int(config.get("patient_id_start", 9000000001))
    started_at = datetime.now(UTC)
    run_started = time.perf_counter()

    indices = list(range(patients))
    slices = [indices[i::workers] for i in range(workers)]
    merged: dict[str, list[float]] = {
        "create_patient": [],
        "save_draft": [],
        "search": [],
        "poll": [],
    }
    created_total = 0
    conflicts_total = 0
    errors: list[dict[str, Any]] = []
    encounters: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_worker_slice, session_factory, part, id_start, searches_each, polls_each)
            for part in slices
        ]
        for future in futures:
            part_result = future.result()
            for key, values in part_result["samples"].items():
                merged[key].extend(values)
            created_total += part_result["created"]
            conflicts_total += part_result["conflicts"]
            errors.extend(part_result["errors"])
            encounters.extend(part_result["encounters"])

    provider_section: dict[str, Any]
    if provider_exec is None or provider_samples <= 0 or not encounters:
        provider_section = {
            "skipped": (
                "no provider executor supplied"
                if provider_exec is None
                else "no provider samples requested or no encounters created"
            )
        }
        provider_count = 0
    else:
        setup = session_factory()
        batch_ids: list[str] = []
        try:
            for entry in encounters[:provider_samples]:
                started = setup.request(
                    "POST",
                    f"/api/v1/encounters/{entry['encounter_id']}/generation-batches",
                    json={"package": _synthetic_batch_package()},
                    headers={"If-Match": f'"{entry["revision"]}"'},
                )
                if started.status_code == 202:
                    batch_ids.append(started.json()["batch"]["id"])
                else:
                    errors.append({"op": "provider_batch_start", "status": started.status_code})
        finally:
            setup.close()
        provider_latencies: list[float] = []
        provider_outcomes: dict[str, int] = {}
        for _ in batch_ids:
            begun = time.perf_counter()
            outcome = provider_exec()
            provider_latencies.append(time.perf_counter() - begun)
            # The worker claims fairly-ordered eligible work (plan §8.4):
            # under a shared backlog these executions may serve older
            # authors' jobs, not the batches just started. Outcomes are
            # recorded per status so a no-work pass can never masquerade
            # as a fast execution.
            status = (
                str(outcome.get("status"))
                if isinstance(outcome, dict) and outcome.get("status") is not None
                else "unknown"
            )
            provider_outcomes[status] = provider_outcomes.get(status, 0) + 1
        provider_count = len(batch_ids)
        provider_section = {
            "adapter": "ControlledStubAdapter",
            "note": (
                "Deterministic queue-path stand-in; measures claim/execute/commit "
                "overhead only. Live LLM latency is NOT measured here."
            ),
            "samples": provider_count,
            "outcomes": provider_outcomes,
            **_stats(provider_latencies),
        }

    duration = time.perf_counter() - run_started
    return {
        "tool": "load_probe",
        "tool_version": TOOL_VERSION,
        "base_url": getattr(session_factory, "base_url", "unknown"),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "started_at": started_at.isoformat(),
        "duration_seconds": duration,
        "synthetic": True,
        "dataset": {
            "patients_target": patients,
            "patients_created": created_total,
            "saves": len(merged["save_draft"]),
            "searches": len(merged["search"]),
            "polls": len(merged["poll"]),
            "provider_samples": provider_count,
            "conflicts": conflicts_total,
            "errors": errors[:10],
        },
        "ordinary": {key: _stats(values) for key, values in merged.items()},
        "provider": provider_section,
    }


def _httpx_factory(base_url: str, username: str, password: str) -> Callable[[], ClientSession]:
    import httpx

    def _build() -> ClientSession:
        client = httpx.Client(base_url=base_url, timeout=30.0)
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password, "role": "physician"},
        )
        response.raise_for_status()
        return ClientSession(client, response.json()["csrf_token"], base_url)

    _build.base_url = base_url  # type: ignore[attr-defined]
    return _build


def main(argv: list[str] | None = None) -> int:
    """CLI for ``make test-load`` (synthetic data only, records results)."""
    parser = argparse.ArgumentParser(description="X-INSIGHT synthetic load probe (S58 §1).")
    parser.add_argument("--base-url", default=os.environ.get("LOAD_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--patients", type=int, default=int(os.environ.get("LOAD_PATIENTS", "50")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("LOAD_WORKERS", "4")))
    parser.add_argument("--searches-each", type=int, default=2)
    parser.add_argument("--polls-each", type=int, default=2)
    parser.add_argument("--provider-samples", type=int, default=5)
    parser.add_argument("--patient-id-start", type=int, default=9000000001)
    parser.add_argument("--physician", default="load-probe")
    parser.add_argument("--admin-user", default=os.environ.get("LOAD_ADMIN_USER", "admin"))
    parser.add_argument("--admin-password", default=os.environ.get("LOAD_ADMIN_PASSWORD", "admin"))
    parser.add_argument("--output", default=os.environ.get("LOAD_REPORT", ""))
    args = parser.parse_args(argv)

    import httpx

    admin_client = httpx.Client(base_url=args.base_url, timeout=30.0)
    login = admin_client.post(
        "/api/v1/auth/login",
        json={"username": args.admin_user, "password": args.admin_password, "role": "admin"},
    )
    login.raise_for_status()
    admin = ClientSession(admin_client, login.json()["csrf_token"], args.base_url)
    physician = f"{args.physician}-{os.getpid()}"
    created = admin.request(
        "POST", "/api/v1/physicians", json={"username": physician, "password": "probe-only-x1"}
    )
    if created.status_code not in (201, 409):
        print(f"physician setup failed: {created.status_code} {created.text}", file=sys.stderr)
        return 2

    schema_version = None
    try:
        ready = admin_client.get("/api/v1/ready", timeout=10.0)
        if ready.status_code == 200:
            schema_version = ready.json().get("schema_version")
    except Exception:
        pass

    provider_exec: Callable[[], Any] | None = None
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url and args.provider_samples > 0:
        from x_insight import db as db_module
        from x_insight.reasoning import provider as provider_module
        from x_insight.reasoning import worker as worker_module

        engine = db_module.build_engine(database_url)
        adapter = provider_module.ControlledStubAdapter()

        def _execute() -> Any:
            return worker_module.run_once(engine=engine, provider=adapter)

        provider_exec = _execute

    factory = _httpx_factory(args.base_url, physician, "probe-only-x1")
    report = run_load(
        factory,
        {
            "patients": args.patients,
            "workers": args.workers,
            "searches_each": args.searches_each,
            "polls_each": args.polls_each,
            "provider_samples": args.provider_samples if provider_exec else 0,
            "patient_id_start": args.patient_id_start,
        },
        provider_exec=provider_exec,
    )
    report["schema_version"] = schema_version
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"load report written to {args.output}")
    else:
        print(text)
    ordinary = report["ordinary"]
    print(
        "summary: patients={created}/{target} duration={duration:.1f}s "
        "p95(create)={c:.3f}s p95(save)={s:.3f}s p95(search)={q:.3f}s p95(poll)={p:.3f}s".format(
            created=report["dataset"]["patients_created"],
            target=report["dataset"]["patients_target"],
            duration=report["duration_seconds"],
            c=(ordinary["create_patient"]["p95"] or 0),
            s=(ordinary["save_draft"]["p95"] or 0),
            q=(ordinary["search"]["p95"] or 0),
            p=(ordinary["poll"]["p95"] or 0),
        )
    )
    dataset_errors = report["dataset"]["errors"]
    return 1 if dataset_errors else 0


if __name__ == "__main__":
    sys.exit(main())
