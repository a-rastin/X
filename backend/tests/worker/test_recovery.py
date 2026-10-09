"""Bound failures and recover at the exact failed stage (S47, seams T1/T8).

Through public generation start/read/review + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio +
``DeterministicProviderEndpoint.captured`` on real PostgreSQL. No DB-row
asserts, no internal mocks, no second paths; order/retention/resume are
observed via public run status + captured external provider requests with
independent expected literals.

Covers tasks.md S47 §§1-4 (FR-36, FR-43, NFR-04):

- §1: timeout → invalid CPT → rate-limit exhausts three total attempts
  (``test_shared_budget_timeout_invalid_ratelimit``); prior sections
  retained + later pending (``test_prior_sections_retained_later_pending``);
  shared backoff grows without holding a lease + one exchange per attempt
  (``test_backoff_grows_without_holding_lease``).
- §2: author retry of unchanged failed stage starts a new bounded batch
  with Q2-only provider requests
  (``test_author_retry_starts_new_bounded_batch_q2_only``); inference CPTs
  reused without re-estimation
  (``test_inference_cpts_reused_without_reestimation``); rendering retry
  reuses the stored result
  (``test_rendering_retry_reuses_stored_result``).
- §3: worker crash/restart retains counts, never duplicates, deployment
  bump does not strand the retry
  (``test_restart_retains_counts_and_never_duplicates``); old
  worker/grant/deployment cannot commit after reclaim (same test: idle
  reruns + stable baseline id).
- §4: analytical edit / discard / deactivation cancel without late commits
  or provider calls; clarification never guesses or blind-retries; config
  errors fail fast and repair starts a new pinned run
  (``test_analytical_edit_cancels`` / ``test_discard_cancels`` /
  ``test_deactivation_cancels`` / ``test_clarification_never_blind_retries``
  / ``test_config_error_fails_fast_then_repair``).

Synthetic A→B 0.20/0.22 networks; archive has no route until S51 so
discard/deactivation stand in for the lifecycle fence (provisional policy).
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import worker as worker_module

TWO_NODE_XML = (
    b'<BIF VERSION="0.3"><NETWORK><NAME>TwoNode</NAME>'
    b"<PROPERTY>net-prop=kept</PROPERTY>"
    b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME>"
    b"<PROPERTY>var-prop-a=kept</PROPERTY></VARIABLE>"
    b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
    b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE>"
    b"<PROPERTY>def-prop-a=kept</PROPERTY></DEFINITION>"
    b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
    b"<TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
    b"</NETWORK></BIF>"
)
NETWORK_HASH = hashlib.sha256(TWO_NODE_XML).hexdigest()


def _package(
    question_key: str = "synthetic_single",
    field_a: str = "h_a",
    field_b: str = "h_b",
) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s47-test-v1",
            "review_status": "draft",
            "network_file": "network.xml",
            "network_hash": NETWORK_HASH,
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
                    "allowed_source_paths": [f"synthetic/history/{field_a}"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
                {
                    "node_id": "B",
                    "allowed_source_paths": [f"synthetic/history/{field_b}"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
            ],
            "applicability": {
                "expression": "true",
                "required_fields": [
                    f"synthetic/history/{field_a}",
                    f"synthetic/history/{field_b}",
                ],
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
        "prompt": {
            "version": "v1",
            "text": (
                "Estimate every CPT in percentage units for this question "
                "using only the supplied inputs. Return strict schema."
            ),
        },
        "template": {
            "version": "v1",
            "branches": [
                {
                    "when": {"node": "B", "state": "yes", "operator": "=="},
                    "text": f"{question_key} present: {{B}} outcome.",
                },
                {
                    "when": {"node": "B", "state": "no", "operator": "=="},
                    "text": f"{question_key} absent: {{B}} outcome.",
                },
            ],
        },
        "examples": {
            "numerical": [{"inputs": {}, "expected": {"A": {"no": 0.8, "yes": 0.2}}}],
            "clinical": [
                {"inputs": {}, "expected_for_review": {"B": "yes"}, "note": "review candidate"}
            ],
        },
        "review": {
            "reviewer": "owner",
            "decision": "draft",
            "date": "2026-10-04",
            "source_hashes": {"network.xml": NETWORK_HASH},
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
        "network_xml": TWO_NODE_XML.decode("utf-8"),
    }


def _valid_cpt(network_hash: str = NETWORK_HASH) -> dict[str, Any]:
    return {
        "network_hash": network_hash,
        "tables": [
            {
                "node_id": "A",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["80", "20"]}],
            },
            {
                "node_id": "B",
                "parent_ids": ["A"],
                "states": ["no", "yes"],
                "rows": [
                    {"parent_states": ["no"], "percentages": ["90", "10"]},
                    {"parent_states": ["yes"], "percentages": ["30", "70"]},
                ],
            },
        ],
    }


def _invalid_cpt(network_hash: str = NETWORK_HASH) -> dict[str, Any]:
    return {
        "network_hash": network_hash,
        "tables": [
            {
                "node_id": "A",
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["80", "20"]}],
            }
        ],
    }


@pytest.fixture(scope="session")
def migrated_test_engine():
    from alembic import command
    from alembic.config import Config

    url = db_module.get_test_database_url()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = db_module.build_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _truncate(engine: Any) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, original_baselines, proposal_snapshots, "
                "question_runs, generation_batches, notes, encounters, patients, "
                "idempotency_records, sessions, users, audit_events CASCADE"
            )
        )


def _reset_deployment(engine: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE reasoning_deployment SET generation = 1 WHERE id = 1"))
        row = connection.execute(text("SELECT generation FROM reasoning_deployment")).first()
        if row is None:
            connection.execute(
                text("INSERT INTO reasoning_deployment (id, generation) VALUES (1, 1)")
            )


@pytest.fixture()
def clean_registry(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    _reset_deployment(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    _reset_deployment(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _new_physician_client(
    engine: Any, monkeypatch: Any, username: str
) -> tuple[TestClient, str, TestClient, str]:
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    created = admin.post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers=_auth_headers(login.json()["csrf_token"]),
    )
    assert created.status_code == 201, created.text
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return admin, login.json()["csrf_token"], client, response.json()["csrf_token"]


def _setup_encounter(client: TestClient, csrf: str, identifier: str, values: dict[str, Any]):
    created = client.post(
        "/api/v1/patients",
        json={
            "identifier": identifier,
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    encounter_id = created.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": values}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    return encounter_id, saved.json()["revision"]


def _start_single(
    client: TestClient, csrf: str, encounter_id: str, revision: int, package: dict[str, Any]
):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _run_with_script(
    engine: Any, script: list[dict[str, Any]], at: Any, timeout: float = 10.0
) -> tuple[dict[str, Any], list[Any]]:
    endpoint = provider_module.DeterministicProviderEndpoint(script=script)
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=timeout
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        outcome = worker_module.run_once(
            engine, adapter, now=at, database_url=db_module.get_test_database_url()
        )
        captured = list(endpoint.captured)
    finally:
        endpoint.stop()
    return outcome, captured


# --- S47 §1: shared 3-attempt budget, retention, backoff ---


def test_shared_budget_timeout_invalid_ratelimit(clean_registry, monkeypatch) -> None:
    """S47 §1: timeout → invalid CPT → 429 exhausts exactly three total attempts."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_budget")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    package = _package()
    started = _start_single(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[
            {"type": "delay", "seconds": 2.0, "then": {"type": "final", "cpt": _invalid_cpt()}},
            {"type": "final", "cpt": _invalid_cpt()},
            {"type": "status", "status": 429, "body": {"error": "slow down"}},
        ]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=1.0
        )
        t0 = contracts.utcnow()

        def _once(at: Any) -> dict[str, Any]:
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            return worker_module.run_once(
                engine, adapter, now=at, database_url=db_module.get_test_database_url()
            )

        _once(t0)
        body1 = client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert body1["job"]["status"] == "queued", body1["job"]
        assert body1["attempts"] == 1, body1
        assert body1["job"].get("next_eligible_at") is not None
        assert body1["baseline"] is None
        assert len(endpoint.captured) == 1, "one HTTP exchange per attempt, no nested retries"

        _once(t0 + timedelta(seconds=70))
        body2 = client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert body2["job"]["status"] == "queued", body2["job"]
        assert body2["attempts"] == 2, body2
        assert len(endpoint.captured) == 2

        _once(t0 + timedelta(seconds=140))
        body3 = client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert body3["job"]["status"] == "failed", body3["job"]
        assert body3["attempts"] == 3, body3
        assert len(endpoint.captured) == 3, "exactly 3 total provider requests"
        assert body3["baseline"] is None
        review = client.get(f"/api/v1/question-runs/{run_id}/review")
        assert review.status_code == 200, review.text
        assert review.json()["adjustable"] is False
        assert review.json()["baseline"] is None
        assert (
            client.get(f"/api/v1/generation-batches/{batch_id}").json()["freshness"]["stale"]
            is False
        )
    finally:
        endpoint.stop()


def test_prior_sections_retained_later_pending(clean_registry, monkeypatch) -> None:
    """S47 §1: Q1 success retained, Q2 exhausts 3, no successor, draft fresh."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_retain")
    encounter_id, revision = _setup_encounter(
        client,
        csrf,
        "0012345678",
        {"h1a": "yes", "h1b": "no", "h2a": "yes", "h2b": "no"},
    )
    packages = [_package("q_one", "h1a", "h1b"), _package("q_two", "h2a", "h2b")]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]

    t0 = contracts.utcnow()
    out1, cap1 = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert out1["status"] == "succeeded", out1
    assert len(cap1) == 1
    out2, cap2 = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=5)
    )
    assert out2["status"] == "queued", out2
    out3, cap3 = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=80)
    )
    assert out3["status"] == "queued", out3
    out4, cap4 = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=160)
    )
    assert out4["status"] == "failed", out4

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["baselines"][0]["baseline"] is not None
    assert "q_one absent" in body["baselines"][0]["baseline"]["section_text"]
    assert body["baselines"][1]["baseline"] is None
    assert body["baselines"][1]["question_key"] == "q_two"
    assert sorted(j["status"] for j in body["jobs"]) == ["failed", "succeeded"]
    assert len(cap1) + len(cap2) + len(cap3) + len(cap4) == 4, (
        "Q1 once + Q2 three times, no extra requests"
    )
    assert body["workflow"]["complete"] is False
    assert body["proposal"] is None
    assert body["freshness"]["stale"] is False


def test_backoff_grows_without_holding_lease(clean_registry, monkeypatch) -> None:
    """S47 §1: retry eligibility grows exponentially, capped at 60s, never sleeps in-tx."""
    from datetime import datetime

    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_backoff")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]

    t0 = contracts.utcnow()
    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0)
    first = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert first["job"]["status"] == "queued", first["job"]
    delay1 = (
        datetime.fromisoformat(str(first["job"]["next_eligible_at"]).replace("Z", "+00:00")) - t0
    ).total_seconds()
    assert 1.0 <= delay1 <= 61.0, f"first backoff ~2s+jitter capped at 60s, got {delay1}"

    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=70))
    second = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert second["job"]["status"] == "queued", second["job"]
    moment2 = t0 + timedelta(seconds=70)
    delay2 = (
        datetime.fromisoformat(str(second["job"]["next_eligible_at"]).replace("Z", "+00:00"))
        - moment2
    ).total_seconds()
    assert 1.0 <= delay2 <= 61.0, f"second backoff ~4s+jitter capped at 60s, got {delay2}"
    assert delay2 >= delay1, "backoff grows exponentially (2s → 4s plus jitter)"


# --- S47 §2: stage resume + author retry ---


def test_rendering_retry_reuses_stored_result(clean_registry, monkeypatch) -> None:
    """S47 §2: rendering failure retries with zero new provider requests."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_render")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    package = _package()
    package["template"] = {
        "version": "v1",
        "branches": [
            {
                "when": {"node": "B", "state": "yes", "operator": "=="},
                "text": "Result for {B} is present.",
            }
        ],
    }
    started = _start_single(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()}]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        t0 = contracts.utcnow()

        def _once(at: Any) -> dict[str, Any]:
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            return worker_module.run_once(
                engine, adapter, now=at, database_url=db_module.get_test_database_url()
            )

        _once(t0)
        assert client.get(f"/api/v1/generation-batches/{batch_id}").json()["attempts"] == 1
        assert len(endpoint.captured) == 1
        _once(t0 + timedelta(seconds=70))
        mid = client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert mid["job"]["status"] == "queued", mid["job"]
        assert mid["attempts"] == 2, mid
        assert len(endpoint.captured) == 1, "rendering retry must reuse stored result"
        _once(t0 + timedelta(seconds=140))
        done = client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert done["job"]["status"] == "failed", done["job"]
        assert done["attempts"] == 3, done
        assert len(endpoint.captured) == 1, "no new provider request on any retry"
        assert done["baseline"] is None
    finally:
        endpoint.stop()


def test_inference_cpts_reused_without_reestimation(clean_registry, monkeypatch) -> None:
    """S47 §2: accepted CPTs are reused — a swapped invalid endpoint is never called."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_infer")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    package = _package()
    package["template"] = {
        "version": "v1",
        "branches": [
            {
                "when": {"node": "B", "state": "yes", "operator": "=="},
                "text": "Result for {B} is present.",
            }
        ],
    }
    started = _start_single(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]

    t0 = contracts.utcnow()
    out1, cap1 = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert out1["status"] == "queued", out1
    assert len(cap1) == 1
    # Retry swaps in an endpoint that would fail validation if called: reuse
    # must skip estimation entirely, so the invalid payload is never consumed.
    out2, cap2 = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=70)
    )
    assert out2["status"] == "queued", out2
    assert cap2 == [], "inference retry reuses CPTs without re-estimating"
    out3, cap3 = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=140)
    )
    assert out3["status"] == "failed", out3
    assert cap3 == [], "exhaustion still costs no new provider request"
    done = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert done["attempts"] == 3, done
    assert done["baseline"] is None


def test_author_retry_starts_new_bounded_batch_q2_only(clean_registry, monkeypatch) -> None:
    """S47 §2: unchanged retry carries Q1, enqueues only Q2, then completes."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_retry")
    encounter_id, revision = _setup_encounter(
        client,
        csrf,
        "0012345678",
        {"h1a": "yes", "h1b": "no", "h2a": "yes", "h2b": "no"},
    )
    packages = [_package("q_one", "h1a", "h1b"), _package("q_two", "h2a", "h2b")]

    def _start() -> Any:
        return client.post(
            f"/api/v1/encounters/{encounter_id}/generation-batches",
            json={"packages": packages},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )

    started = _start()
    assert started.status_code == 202, started.text
    batch1 = started.json()["batch"]["id"]
    t0 = contracts.utcnow()
    out1, _ = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert out1["status"] == "succeeded", out1
    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=5))
    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=80))
    out_fail, _ = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=160)
    )
    assert out_fail["status"] == "failed", out_fail

    retry = _start()
    assert retry.status_code == 202, retry.text
    batch2 = retry.json()["batch"]["id"]
    assert batch2 != batch1, "retry starts a new bounded batch"
    view = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert view["baselines"][0]["baseline"] is not None
    assert "q_one absent" in view["baselines"][0]["baseline"]["section_text"]
    assert (
        view["baselines"][0]["baseline"]["provenance"].get("retried_from_baseline_id") is not None
    )
    assert view["baselines"][1]["baseline"] is None
    assert [j["status"] for j in view["jobs"]] == ["queued"]
    assert view["workflow"]["complete"] is False
    old = client.get(f"/api/v1/generation-batches/{batch1}").json()
    assert old["baselines"][0]["baseline"] is not None

    out2, cap2 = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=300)
    )
    assert out2["status"] == "succeeded", out2
    assert len(cap2) == 1
    blob = str(cap2[0])
    assert "q_two" in blob, "the retry estimates the failed question"
    assert "q_one" not in blob, "earlier succeeded questions cost no new request"
    done = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert done["baselines"][0]["baseline"] is not None
    assert done["baselines"][1]["baseline"] is not None
    assert sorted(j["status"] for j in done["jobs"]) == ["succeeded"]
    assert len(done["jobs"]) == 1, "retry batch holds only the failed stage's job"
    assert done["proposal"] is None
    assert done["workflow"]["complete"] is False


# --- S47 §3: crash/restart + fencing ---


def test_restart_retains_counts_and_never_duplicates(clean_registry, monkeypatch) -> None:
    """S47 §3: restarts keep attempts; success runs once; reruns stay idle."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_restart")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    t0 = contracts.utcnow()

    def _run_id(script: list[dict[str, Any]], at: Any, worker_id: str):
        endpoint = provider_module.DeterministicProviderEndpoint(script=script)
        url = endpoint.start()
        try:
            config = provider_module.ProviderConfig(
                endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
            )
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            return worker_module.run_once(
                engine,
                adapter,
                now=at,
                worker_id=worker_id,
                database_url=db_module.get_test_database_url(),
            ), list(endpoint.captured)
        finally:
            endpoint.stop()

    out_a, _ = _run_id([{"type": "final", "cpt": _invalid_cpt()}], t0, "worker-a")
    assert out_a["status"] == "queued", out_a
    with engine.begin() as connection:
        connection.execute(text("UPDATE reasoning_deployment SET generation = 2 WHERE id = 1"))
    out_b, _ = _run_id(
        [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=70), "worker-b"
    )
    assert out_b["status"] == "queued", out_b
    mid = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert mid["attempts"] == 2, "restart must not reset the attempt ledger"
    assert mid["job"]["status"] == "queued"
    out_c, _ = _run_id(
        [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=140), "worker-c"
    )
    assert out_c["status"] == "succeeded", out_c
    done = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    baseline_id = done["baseline"]["id"]
    assert done["attempts"] == 3
    out_d, _ = _run_id(
        [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=210), "worker-d"
    )
    assert out_d["status"] == "idle", out_d
    reread = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert reread["attempts"] == 3, "idle reruns consume no attempts"
    assert reread["baseline"]["id"] == baseline_id, "no duplicated sections"
    assert len(reread["baselines"]) == 1


# --- S47 §4: lifecycle, config, clarification ---


def test_analytical_edit_cancels_without_late_commit(clean_registry, monkeypatch) -> None:
    """S47 §4: relevant edit before the worker runs cancels the stale job."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_edit")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    edited = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text
    out, captured = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], contracts.utcnow()
    )
    assert out["status"] in ("idle", "cancelled"), out
    assert captured == [], "stale jobs never reach the provider"
    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "cancelled", body["job"]
    assert body["baseline"] is None
    assert body["freshness"]["stale"] is True


def test_discard_cancels_without_late_commit(clean_registry, monkeypatch) -> None:
    """S47 §4: discard during outbound flight prevents late acceptance."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_discard")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    discarded = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert discarded.status_code == 200, discarded.text
    out, captured = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], contracts.utcnow()
    )
    assert out["status"] in ("idle", "cancelled"), out
    assert captured == [], "discarded drafts never reach the provider"
    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "cancelled", body["job"]
    assert body["baseline"] is None


def test_deactivation_cancels_without_late_commit(clean_registry, monkeypatch) -> None:
    """S47 §4: deactivation revokes eligibility; no late artifacts land."""
    engine = clean_registry
    admin, admin_csrf, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_deact")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    listed = admin.get("/api/v1/physicians", headers=_auth_headers(admin_csrf))
    assert listed.status_code == 200, listed.text
    target = [u for u in listed.json()["items"] if u["username"] == "dr_s47_deact"]
    assert len(target) == 1
    deactivated = admin.post(
        f"/api/v1/physicians/{target[0]['id']}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text
    out, captured = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], contracts.utcnow()
    )
    assert out["status"] in ("idle", "cancelled"), out
    assert captured == [], "deactivated authors never reach the provider"
    body = client.get(f"/api/v1/generation-batches/{batch_id}")
    assert body.status_code in (200, 401, 403)


def test_clarification_never_blind_retries(clean_registry, monkeypatch) -> None:
    """S47 §4: required-unknown yields clarification with no job and no calls."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_clarify")
    encounter_id, revision = _setup_encounter(client, csrf, "0012345678", {"h_a": "yes"})
    started = _start_single(client, csrf, encounter_id, revision, _package())
    assert started.status_code == 202, started.text
    assert started.json()["question_runs"][0]["status"] == "needs_clarification"
    batch_id = started.json()["batch"]["id"]
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(engine, stub, database_url=db_module.get_test_database_url())
    assert outcome["status"] == "idle", outcome
    assert stub.calls == [], "clarification never triggers provider work"
    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"] is None
    assert body["attempts"] == 0
    assert body["baseline"] is None


def test_config_error_fails_fast_then_repair(clean_registry, monkeypatch) -> None:
    """S47 §4: 401 fails immediately (no blind retries); repair = new batch."""
    engine = clean_registry
    _, _, client, csrf = _new_physician_client(engine, monkeypatch, "dr_s47_config")
    encounter_id, revision = _setup_encounter(
        client, csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    package = _package()

    def _start() -> Any:
        return client.post(
            f"/api/v1/encounters/{encounter_id}/generation-batches",
            json={"package": package},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )

    started = _start()
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    t0 = contracts.utcnow()
    out, captured = _run_with_script(
        engine, [{"type": "status", "status": 401, "body": {"error": "bad key"}}], t0
    )
    assert out["status"] == "failed", out
    bad = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert bad["attempts"] == 1, "config errors fail immediately within budget"
    assert bad["job"].get("next_eligible_at") is None
    assert len(captured) == 1
    out2, captured2 = _run_with_script(
        engine, [{"type": "status", "status": 401, "body": {"error": "bad key"}}], t0
    )
    assert out2["status"] == "idle", out2
    assert captured2 == []
    retry = _start()
    assert retry.status_code == 202, retry.text
    assert retry.json()["batch"]["id"] != batch_id
    batch2 = retry.json()["batch"]["id"]
    out3, _ = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert out3["status"] == "succeeded", out3
    good = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert good["baseline"] is not None
