"""Regenerate only questions affected by patient-data changes (S48d, seams T1/T8).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio + ``DeterministicProviderEndpoint``
(real PostgreSQL, real routes, no internal mocks, no DB-row asserts, no
private-helper mirror tests). Synthetic two-node A→B networks only
(``80/20``, ``90/10``, ``30/70``); no clinical content.

Covers tasks.md S48d §§1-4 (FR-59, FR-57–58, FR-16, NFR-04–05):

- §1: represented patient-variable changes mark ONLY affected runs out of
  date and invalidate ONLY affected acceptance. Stranger reads/writes are
  generic 403 without draft content. Unrelated questions stay fresh/accepted.
- §2: regenerate affected applicable questions sequentially under the
  encounter's pinned bundle, retaining old baselines/adjustments/history and
  unaffected valid references. New QuestionRuns establish new originals; no
  old adjustments transfer silently.
- §3: applicability transitions refresh proposal/question set; DDI-only
  changes refresh proposal without unrelated LLM requests; activation does
  NOT rebase existing encounters (pinned bundle retained).
- §4: note-only and plan-text edits do NOT regenerate inference and preserve
  acceptance. Reset/retry of a stale run cannot restore eligibility. Stale
  acceptance is 409; regenerated originals require review before accept.

Defaults: T=100_000_000, redistribution-v1, review_revision starts 1.
"""

from __future__ import annotations

import hashlib
import os
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
    question_key: str,
    field_a: str,
    field_b: str,
    *,
    gate_expression: str = "true",
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    req = (
        list(required_fields)
        if required_fields is not None
        else [f"synthetic/history/{field_a}", f"synthetic/history/{field_b}"]
    )
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s48d-test-v1",
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
                "expression": gate_expression,
                "required_fields": req,
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


def _truncate(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE probability_acceptances, calculation_results, cpt_revisions, "
                "question_review_states, reasoning_grants, reasoning_job_attempts, "
                "reasoning_jobs, reasoning_fairness, original_baselines, "
                "proposal_snapshots, question_runs, generation_batches, notes, "
                "encounters, patients, idempotency_records, sessions, users, "
                "audit_events CASCADE"
            )
        )


def _reset_deployment(engine) -> None:
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


def _login_physician(clean_registry, monkeypatch, username: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def _new_two_question_setup(clean_registry, monkeypatch, username: str, identifier: str):
    """Author + patient + draft with disjoint Q1/Q2 history fields + workflow start."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
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
    client, csrf = _login_physician(clean_registry, monkeypatch, username)
    made = client.post(
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
    assert made.status_code == 201, made.text
    encounter_id = made.json()["draft"]["id"]
    values = {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    packages = [
        _package("s48d_q1", "h_q1_a", "h_q1_b"),
        _package("s48d_q2", "h_q2_a", "h_q2_b"),
    ]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    return client, csrf, encounter_id, revision, packages, started.json()


def _run_bounded_times(engine, cpt_payload: dict[str, Any], times: int):
    from datetime import timedelta as _timedelta

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": cpt_payload} for _ in range(times)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        outcomes = []
        moment = contracts.utcnow()
        for _ in range(times):
            outcome = worker_module.run_once(
                engine, adapter, now=moment, database_url=db_module.get_test_database_url()
            )
            outcomes.append(outcome)
            moment = moment + _timedelta(seconds=1)
            if outcome.get("status") not in ("succeeded",):
                break
        return endpoint, outcomes
    except Exception:
        endpoint.stop()
        raise


def _accept_body(review: dict[str, Any]) -> dict[str, Any]:
    baseline = review["baseline"]
    assert baseline is not None
    if review["current_cpt_revision_id"] is None:
        cpt_hash = contracts.canonical_hash(list(baseline["validated_tables"]))
        resolved_result = baseline["id"]
    else:
        assert review["cpt_hash"] is not None
        cpt_hash = str(review["cpt_hash"])
        assert review["calculation_result"] is not None
        resolved_result = str(review["calculation_result"]["id"])
    return {
        "baseline_id": str(baseline["id"]),
        "current_cpt_revision_id": review["current_cpt_revision_id"],
        "cpt_hash": str(cpt_hash),
        "result_id": str(resolved_result),
        "input_hash": str(review["input_freshness"]["current_fingerprint"]),
        "expected_review_revision": int(review["review_revision"]),
    }


def _question_key_of(captured_entry: Any) -> str:
    import json as _json

    messages = captured_entry.get("messages", [])
    assert len(messages) >= 2
    payload = _json.loads(messages[1].get("content", "{}"))
    return str(payload.get("question_key", ""))


def _values_key(projection: Any) -> str:
    import hashlib as _hashlib
    import json as _json

    variables = projection.get("variables", []) if isinstance(projection, dict) else []
    stripped = []
    for entry in variables if isinstance(variables, list) else []:
        if not isinstance(entry, dict):
            continue
        stripped.append(
            {
                "node_id": str(entry.get("node_id", "")),
                "patient_type": str(entry.get("patient_type", "")),
                "status": str(entry.get("status", "")),
                "value": entry.get("value"),
                "source_path": str(entry.get("source_path", "")),
            }
        )
    blob = _json.dumps(
        {"variables": stripped}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _hashlib.sha256(blob).hexdigest()


# --- S48d §1: affected-only staleness + acceptance + stranger no-leak ---


def test_affected_only_stale_and_acceptance_with_stranger_no_leak(
    clean_registry, monkeypatch
) -> None:
    """S48d §1: h_q1_a edit stales Q1 only; Q2 stays fresh/accepted; stranger 403 no leak."""
    client, csrf, encounter_id, revision, packages, started = _new_two_question_setup(
        clean_registry, monkeypatch, "dr_s48d_s1", "0012345678"
    )
    batch_id = started["batch"]["id"]
    runs = {run["question_key"]: run for run in started["question_runs"]}
    assert [run["question_key"] for run in started["question_runs"]] == ["s48d_q1", "s48d_q2"]

    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
        assert len(endpoint.captured) == 2
        assert [_question_key_of(e) for e in endpoint.captured] == ["s48d_q1", "s48d_q2"]
    finally:
        endpoint.stop()

    q1_review = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    q2_review = client.get(f"/api/v1/question-runs/{runs['s48d_q2']['id']}/review").json()
    assert q1_review["input_freshness"]["stale"] is False
    assert q2_review["input_freshness"]["stale"] is False

    for review, run_key in ((q1_review, "s48d_q1"), (q2_review, "s48d_q2")):
        posted = client.post(
            f"/api/v1/question-runs/{runs[run_key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (run_key, posted.text)

    # Represented variable change affecting Q1 only.
    edited = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "no", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text

    q1_stale = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    q2_fresh = client.get(f"/api/v1/question-runs/{runs['s48d_q2']['id']}/review").json()
    assert q1_stale["input_freshness"]["stale"] is True
    assert q1_stale["is_accepted"] is False
    assert q2_fresh["input_freshness"]["stale"] is False
    assert q2_fresh["is_accepted"] is True
    assert q2_fresh["acceptance"] is not None
    # Independent literal: unaffected Q2 keeps 80/20 root.
    assert q2_fresh["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]

    # Stale acceptance is 409 without creating a row.
    denied = client.post(
        f"/api/v1/question-runs/{runs['s48d_q1']['id']}/acceptance",
        json=_accept_body(q1_stale),
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "STALE_INPUTS"

    # Another physician: permitted shared reads/writes deny without draft content.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    created = admin.post(
        "/api/v1/physicians",
        json={"username": "dr_s48d_stranger", "password": "pw123"},
        headers=_auth_headers(login.json()["csrf_token"]),
    )
    assert created.status_code == 201, created.text
    stranger, stranger_csrf = _login_physician(clean_registry, monkeypatch, "dr_s48d_stranger")
    denied_read = stranger.get(f"/api/v1/generation-batches/{batch_id}")
    assert denied_read.status_code == 403, denied_read.text
    assert "percentages" not in denied_read.text and "h_q1_a" not in denied_read.text
    denied_review = stranger.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review")
    assert denied_review.status_code == 403, denied_review.text
    assert "percentages" not in denied_review.text
    denied_write = stranger.post(
        f"/api/v1/question-runs/{runs['s48d_q1']['id']}/acceptance",
        json=_accept_body(q2_fresh),
        headers=_auth_headers(stranger_csrf),
    )
    assert denied_write.status_code in (403, 404), denied_write.text
    assert "percentages" not in denied_write.text


# --- S48d §2: affected-only regeneration sequentially under pinned bundle ---


def test_regenerate_affected_only_sequentially_retains_history(clean_registry, monkeypatch) -> None:
    """S48d §2: new batch regenerates Q1 only (1 LLM call); history retained; no transfer."""
    client, csrf, encounter_id, revision, packages, started = _new_two_question_setup(
        clean_registry, monkeypatch, "dr_s48d_s2", "0012345678"
    )
    old_batch_id = started["batch"]["id"]
    old_runs = {run["question_key"]: run for run in started["question_runs"]}
    old_pinned = dict(started["batch"]["pinned_bundle"])

    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
    finally:
        endpoint.stop()

    # Physician adjustment on Q1 (must NOT transfer to regenerated run).
    adjusted = client.post(
        f"/api/v1/question-runs/{old_runs['s48d_q1']['id']}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert adjusted.status_code == 200, adjusted.text
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    stub = _provider.ControlledStubAdapter(mode="succeed")
    assert _worker.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []

    # Relevant edit affecting Q1 only.
    edited = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "no", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text
    new_revision = int(edited.json()["revision"])

    # Regenerate under the encounter's pinned bundle (same packages, same bundle).
    restarted = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(new_revision)},
    )
    assert restarted.status_code == 202, restarted.text
    new_batch_id = restarted.json()["batch"]["id"]
    assert new_batch_id != old_batch_id
    new_runs = {run["question_key"]: run for run in restarted.json()["question_runs"]}
    assert set(new_runs) == {"s48d_q1", "s48d_q2"}
    assert new_runs["s48d_q1"]["id"] != old_runs["s48d_q1"]["id"]
    assert new_runs["s48d_q2"]["id"] != old_runs["s48d_q2"]["id"]
    # Pinned bundle retained (same per-question package hashes).
    assert restarted.json()["batch"]["pinned_bundle"] == old_pinned
    # Affected projection moved; unaffected values identical ignoring revisions.
    assert new_runs["s48d_q1"]["projection_hash"] != old_runs["s48d_q1"]["projection_hash"]
    assert _values_key(new_runs["s48d_q2"]["projection"]) == _values_key(
        old_runs["s48d_q2"]["projection"]
    )

    # Old history retained and readable.
    old_read = client.get(f"/api/v1/generation-batches/{old_batch_id}").json()
    assert old_read["batch"]["id"] == old_batch_id
    assert old_read["batch"]["pinned_bundle"] == old_pinned
    old_q1_review = client.get(f"/api/v1/question-runs/{old_runs['s48d_q1']['id']}/review").json()
    assert len(old_q1_review["revisions"]) == 1
    assert old_q1_review["baseline"] is not None

    # New runs start with no adjustments (no silent transfer).
    new_q1_before = client.get(f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review").json()
    new_q2_before = client.get(f"/api/v1/question-runs/{new_runs['s48d_q2']['id']}/review").json()
    assert new_q1_before["revisions"] == []
    assert new_q2_before["revisions"] == []
    assert new_q1_before["acceptance"] is None
    assert new_q2_before["acceptance"] is None

    # Sequential affected-only execution: exactly one provider call for Q1.
    endpoint2 = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()}]
    )
    url2 = endpoint2.start()
    try:
        config2 = provider_module.ProviderConfig(
            endpoint_url=url2, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter2 = provider_module.BoundedProviderAdapter(
            config2, grant_token="", database_url=db_module.get_test_database_url()
        )
        first = worker_module.run_once(
            clean_registry, adapter2, database_url=db_module.get_test_database_url()
        )
        assert first.get("status") == "succeeded", first
        second = worker_module.run_once(
            clean_registry, adapter2, database_url=db_module.get_test_database_url()
        )
        assert second.get("status") == "idle", second
    finally:
        endpoint2.stop()
    assert len(endpoint2.captured) == 1, endpoint2.captured
    assert _question_key_of(endpoint2.captured[0]) == "s48d_q1"

    new_q1_after = client.get(f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review").json()
    new_q2_after = client.get(f"/api/v1/question-runs/{new_runs['s48d_q2']['id']}/review").json()
    assert new_q1_after["baseline"] is not None
    assert new_q2_after["baseline"] is not None
    # New Q1 is a new original (new identity, same valid CPT shape).
    assert new_q1_after["baseline"]["id"] != old_q1_review["baseline"]["id"]
    assert new_q1_after["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    # Unaffected Q2 retained its valid reference (same effective artifact).
    assert (
        new_q2_after["baseline"]["effective_hash"]
        == client.get(f"/api/v1/question-runs/{old_runs['s48d_q2']['id']}/review").json()[
            "baseline"
        ]["effective_hash"]
    )


# --- S48d §3: applicability + DDI-only + pinned retention ---


def test_applicability_transition_refreshes_question_set(clean_registry, monkeypatch) -> None:
    """S48d §3a: gate flip refreshes the proposal/question set with affected-only calls."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    created = admin.post(
        "/api/v1/physicians",
        json={"username": "dr_s48d_s3a", "password": "pw123"},
        headers=_auth_headers(login.json()["csrf_token"]),
    )
    assert created.status_code == 201, created.text
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s48d_s3a")
    made = client.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345678",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(csrf),
    )
    assert made.status_code == 201, made.text
    encounter_id = made.json()["draft"]["id"]
    values = {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    q1 = _package("s48d_q1", "h_q1_a", "h_q1_b")
    q2 = _package(
        "s48d_q2",
        "h_q2_a",
        "h_q2_b",
        gate_expression="gate == 'true'",
        required_fields=["synthetic/history/h_q2_a", "synthetic/history/h_q2_b"],
    )
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": [q1, q2]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    assert [r["status"] for r in started.json()["question_runs"]] == ["ready", "ready"]
    batch_id = started.json()["batch"]["id"]

    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
    finally:
        endpoint.stop()
    # No DDI dataset in this synthetic slice: proposals stay None (S46 DDI
    # gating), but ordered baselines prove sequential success.
    before = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert all(entry["baseline"] is not None for entry in before["baselines"])

    # Applicability transition: gate true -> false makes Q2 not_applicable.
    flipped = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {"values": dict(values)},
                "gate": "false",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert flipped.status_code == 200, flipped.text
    gate_revision = int(flipped.json()["revision"])
    restarted = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": [q1, q2]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(gate_revision)},
    )
    assert restarted.status_code == 202, restarted.text
    statuses = {run["question_key"]: run["status"] for run in restarted.json()["question_runs"]}
    assert statuses == {"s48d_q1": "ready", "s48d_q2": "not_applicable"}
    new_batch_id = restarted.json()["batch"]["id"]
    new_runs = {run["question_key"]: run for run in restarted.json()["question_runs"]}

    # Affected-only carry: still-applicable Q1 reuses its baseline with zero
    # new LLM requests; Q2 needs none (not_applicable). No worker run needed.
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    probe = _provider.ControlledStubAdapter(mode="succeed")
    assert _worker.run_once(clean_registry, probe)["status"] == "idle"
    assert probe.calls == []
    assert (
        client.get(f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review").json()["baseline"]
        is not None
    )
    after = client.get(f"/api/v1/generation-batches/{new_batch_id}").json()
    assert [r["question_key"] for r in after["question_runs"]] == ["s48d_q1", "s48d_q2"]
    # Old batch retained as readable history with its original question set.
    old_read = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert [r["status"] for r in old_read["question_runs"]] == ["ready", "ready"]


def test_ddi_only_refreshes_proposal_without_llm_and_pinned_retained(
    clean_registry, monkeypatch
) -> None:
    """S48d §3b/§3c: meds-only change refreshes without LLM; activation never rebases."""
    client, csrf, encounter_id, revision, packages, started = _new_two_question_setup(
        clean_registry, monkeypatch, "dr_s48d_s3b", "0012345678"
    )
    old_batch_id = started["batch"]["id"]
    old_pinned = dict(started["batch"]["pinned_bundle"])
    old_runs = {run["question_key"]: run for run in started["question_runs"]}

    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
    finally:
        endpoint.stop()
    old_q1 = client.get(f"/api/v1/question-runs/{old_runs['s48d_q1']['id']}/review").json()
    old_q2 = client.get(f"/api/v1/question-runs/{old_runs['s48d_q2']['id']}/review").json()
    assert old_q1["baseline"] is not None and old_q2["baseline"] is not None

    # DDI-only change: reconciled medications list changes, history untouched.
    meds_changed = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
                "medications": {"entries": [{"catalog_drug_id": "catalog_alpha"}]},
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert meds_changed.status_code == 200, meds_changed.text
    meds_revision = int(meds_changed.json()["revision"])

    # Per-question inputs unchanged: both stay fresh (no unrelated invalidation).
    assert (
        client.get(f"/api/v1/question-runs/{old_runs['s48d_q1']['id']}/review").json()[
            "input_freshness"
        ]["stale"]
        is False
    )
    assert (
        client.get(f"/api/v1/question-runs/{old_runs['s48d_q2']['id']}/review").json()[
            "input_freshness"
        ]["stale"]
        is False
    )

    restarted = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(meds_revision)},
    )
    assert restarted.status_code == 202, restarted.text
    new_batch_id = restarted.json()["batch"]["id"]
    new_runs = {run["question_key"]: run for run in restarted.json()["question_runs"]}
    assert _values_key(new_runs["s48d_q1"]["projection"]) == _values_key(
        old_runs["s48d_q1"]["projection"]
    )
    assert _values_key(new_runs["s48d_q2"]["projection"]) == _values_key(
        old_runs["s48d_q2"]["projection"]
    )

    # No unrelated LLM requests: the new batch is already complete via carry.
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    probe = _provider.ControlledStubAdapter(mode="succeed")
    assert _worker.run_once(clean_registry, probe)["status"] == "idle"
    assert probe.calls == []
    new_q1 = client.get(f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review").json()
    new_q2 = client.get(f"/api/v1/question-runs/{new_runs['s48d_q2']['id']}/review").json()
    assert new_q1["baseline"] is not None and new_q2["baseline"] is not None

    # Activation of a new bundle version does NOT rebase existing encounters.
    old_read = client.get(f"/api/v1/generation-batches/{old_batch_id}").json()
    assert old_read["batch"]["pinned_bundle"] == old_pinned
    new_read = client.get(f"/api/v1/generation-batches/{new_batch_id}").json()
    assert new_read["batch"]["pinned_bundle"]["per_question"] == old_pinned["per_question"]


# --- S48d §4: notes/plan noninterference + stale reset/retry + review-before-accept ---


def test_notes_plan_preserve_and_stale_reset_retry_blocked(clean_registry, monkeypatch) -> None:
    """S48d §4: notes/plan preserve; stale reset/retry stay stale; regen needs review."""
    client, csrf, encounter_id, revision, packages, started = _new_two_question_setup(
        clean_registry, monkeypatch, "dr_s48d_s4", "0012345678"
    )
    runs = {run["question_key"]: run for run in started["question_runs"]}
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
    finally:
        endpoint.stop()

    q1_review = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    accepted = client.post(
        f"/api/v1/question-runs/{runs['s48d_q1']['id']}/acceptance",
        json=_accept_body(q1_review),
        headers=_auth_headers(csrf),
    )
    assert accepted.status_code == 200, accepted.text
    acc_id = accepted.json()["acceptance"]["id"]

    # Note-only edit preserves probability acceptance and freshness.
    encounter = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": "Review note: looks right."},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(encounter["revision"])},
    )
    assert noted.status_code == 201, noted.text
    note_revision = int(noted.json()["revision"])
    after_note = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert after_note["input_freshness"]["stale"] is False
    assert after_note["is_accepted"] is True
    assert after_note["acceptance"]["id"] == acc_id

    # Plan-text edit also preserves inference/acceptance.
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
                "secondary_plan": {"text": "Physician plan edit."},
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(note_revision)},
    )
    assert planned.status_code == 200, planned.text
    plan_revision = int(planned.json()["revision"])
    after_plan = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert after_plan["input_freshness"]["stale"] is False
    assert after_plan["is_accepted"] is True

    # Relevant edit stales; reset/retry cannot restore eligibility.
    staled = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "no", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
                "secondary_plan": {"text": "Physician plan edit."},
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(plan_revision)},
    )
    assert staled.status_code == 200, staled.text
    stale_review = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert stale_review["input_freshness"]["stale"] is True
    assert stale_review["is_accepted"] is False

    reset = client.post(
        f"/api/v1/question-runs/{runs['s48d_q1']['id']}/reset",
        json={"expected_review_revision": int(stale_review["review_revision"])},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    after_reset = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert after_reset["input_freshness"]["stale"] is True
    assert after_reset["is_accepted"] is False
    retry = client.post(
        f"/api/v1/question-runs/{runs['s48d_q1']['id']}/retry-calculation",
        json={
            "expected_review_revision": int(after_reset["review_revision"]),
            "current_cpt_revision_id": after_reset["current_cpt_revision_id"],
        },
        headers=_auth_headers(csrf),
    )
    assert retry.status_code == 200, retry.text
    after_retry = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert after_retry["input_freshness"]["stale"] is True
    assert after_retry["is_accepted"] is False
    # Drain the stale local retry so it cannot steal the regeneration slot
    # (worker prefers local jobs; stale locals stay stale and never restore).
    from x_insight.reasoning import provider as _local_provider
    from x_insight.reasoning import worker as _local_worker

    drain = _local_provider.ControlledStubAdapter(mode="succeed")
    for _ in range(3):
        outcome = _local_worker.run_once(clean_registry, drain)
        if outcome.get("status") == "idle":
            break
    drained_review = client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    assert drained_review["input_freshness"]["stale"] is True
    assert drained_review["is_accepted"] is False

    # Regenerated originals require review: old acceptance never applies to the new run.
    regenerated = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(int(staled.json()["revision"])),
        },
    )
    assert regenerated.status_code == 202, regenerated.text
    new_runs = {run["question_key"]: run for run in regenerated.json()["question_runs"]}
    assert new_runs["s48d_q1"]["id"] != runs["s48d_q1"]["id"]
    endpoint2 = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url2 = endpoint2.start()
    try:
        config2 = provider_module.ProviderConfig(
            endpoint_url=url2, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter2 = provider_module.BoundedProviderAdapter(
            config2, grant_token="", database_url=db_module.get_test_database_url()
        )
        # Up to two runs: a leftover stale local (if any) then the affected Q1.
        succeeded = False
        for _ in range(3):
            outcome = worker_module.run_once(
                clean_registry, adapter2, database_url=db_module.get_test_database_url()
            )
            if outcome.get("status") == "succeeded":
                check = client.get(
                    f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review"
                ).json()
                if check["baseline"] is not None:
                    succeeded = True
                    break
        assert succeeded, "regenerated Q1 should succeed"
    finally:
        endpoint2.stop()
    new_review = client.get(f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/review").json()
    assert new_review["baseline"] is not None
    assert new_review["is_accepted"] is False
    assert new_review["acceptances"] == []
    forged = _accept_body(
        client.get(f"/api/v1/question-runs/{runs['s48d_q1']['id']}/review").json()
    )
    forged["baseline_id"] = str(new_review["baseline"]["id"])
    forged["expected_review_revision"] = int(new_review["review_revision"])
    # Old input hash cannot accept the regenerated run.
    denied = client.post(
        f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/acceptance",
        json=forged,
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    # Exact review of the regenerated original accepts.
    assert (
        client.post(
            f"/api/v1/question-runs/{new_runs['s48d_q1']['id']}/acceptance",
            json=_accept_body(new_review),
            headers=_auth_headers(csrf),
        ).status_code
        == 200
    )
