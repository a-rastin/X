"""Recalculate locally with revision integrity, retry, and reset (S48b, seams T1/T8).

Through public authenticated HTTP (T1) + ``run_once()`` worker queue/status
(T8) on real PostgreSQL with real routes. No DB-row asserts, no internal
mocks, no private-helper mirror tests: every behavior is asserted via GET
review/batch + POST adjustment/reset/retry + ``run_once()`` outcomes with
independent literal fixtures. Setup may truncate/migrate.

Synthetic two-node A→B network (``80/20``, ``90/10``, ``30/70``) with
``DeterministicProviderEndpoint`` + ``BoundedProviderAdapter`` for the
original baseline (real MCP stdio path from S45); local recalculation uses
only the fixed network/template/configuration with empty evidence and never
touches provider/MCP (``ControlledStubAdapter.calls == []``).

Independent worked literals (never implementation output):
- Baseline empty-evidence ``P(A=yes)=0.20``, ``P(B=yes)=0.22``.
- After ``A yes 20→40`` (``[80,20]→[60,40]``): ``P(A)=[0.60,0.40]``,
  ``P(B=yes)=0.10*0.60+0.70*0.40=0.34`` → ``B=[0.66,0.34]``.

Covers tasks.md S48b §§1-4. Defaults: T=100_000_000, redistribution-v1,
review_revision starts 1.
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


def s48a_package() -> dict[str, Any]:
    digest = hashlib.sha256(TWO_NODE_XML).hexdigest()
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": "synthetic_single",
            "title": "Synthetic single question",
            "workflow": "registration",
            "version": "s45-test-v1",
            "review_status": "draft",
            "network_file": "network.xml",
            "network_hash": digest,
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
                    "text": "Result for {B} is present.",
                },
                {
                    "when": {"node": "B", "state": "no", "operator": "=="},
                    "text": "Result for {B} is absent.",
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
            "source_hashes": {"network.xml": digest},
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


def valid_cpt(network_hash: str) -> dict[str, Any]:
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
                "TRUNCATE calculation_results, cpt_revisions, question_review_states, "
                "reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, original_baselines, proposal_snapshots, "
                "question_runs, generation_batches, notes, encounters, patients, "
                "idempotency_records, sessions, users, audit_events CASCADE"
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


def _setup_encounter(client, csrf, identifier: str):
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
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    return encounter_id, saved.json()["revision"]


def _start_batch(client, csrf, encounter_id: str, revision: int, package: dict[str, Any]):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _run_bounded_once(engine, cpt_payload: dict[str, Any], now: Any = None):
    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": cpt_payload}]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        outcome = worker_module.run_once(
            engine, adapter, now=now, database_url=db_module.get_test_database_url()
        )
    finally:
        endpoint.stop()
    return outcome


def _new_physician_batch(clean_registry, monkeypatch, username: str, identifier: str):
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
    encounter_id, revision = _setup_encounter(client, csrf, identifier)
    package = s48a_package()
    started = _start_batch(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    return client, csrf, encounter_id, revision, package, started.json()


def _succeed_batch(clean_registry, client, package, started):
    network_hash = package["manifest"]["network_hash"]
    outcome = _run_bounded_once(clean_registry, valid_cpt(network_hash))
    assert outcome["status"] == "succeeded", outcome
    batch_id = started["batch"]["id"]
    run_id = started["question_runs"][0]["id"]
    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["baseline"] is not None, body
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["adjustable"] is True
    return batch_id, run_id


def _table_by_node(tables: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    for entry in tables:
        if entry.get("node_id") == node_id:
            return entry
    raise AssertionError(f"missing table {node_id!r} in {tables!r}")


def _row_by_parents(table: dict[str, Any], parents: list[str]) -> dict[str, Any]:
    for row in table.get("rows", []):
        if list(row.get("parent_states", [])) == list(parents):
            return row
    raise AssertionError(f"missing row {parents!r} in table {table.get('node_id')!r}")


def _posteriors_by_node(posteriors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry.get("node_id")): entry for entry in posteriors}


# --- S48b §2: newer adjustment fences older response; superseded stays historical ---


def test_sequential_rev1_rev2_fences_and_flips_displayed_ids(clean_registry, monkeypatch) -> None:
    """S48b §2: rev1 success, rev2 pending shows rev1 displayed, then flips to rev2."""
    import pytest as _pytest

    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_seq", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]
    assert rev1["after_row"]["percentages"] == ["60", "40"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome1 = worker_module.run_once(clean_registry, stub)
    assert outcome1["status"] == "succeeded", outcome1
    assert stub.calls == []

    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "50",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    rev2 = second.json()["revision"]
    assert rev2["after_row"]["percentages"] == ["50", "50"]
    assert rev2["parent_revision_id"] == rev1["id"]
    assert rev2["sequence"] == 2

    # After rev1 success, rev2 pending: current stays rev2/50,50, displayed is rev1.
    mid = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert mid["calculation_state"] == "recalculating"
    assert mid["current_cpt_revision_id"] == rev2["id"]
    assert mid["displayed_result_revision_id"] == rev1["id"]
    assert mid["current_result_matches"] is False
    assert mid["calculation_result"] is None
    assert mid["displayed_result"] is not None
    assert mid["displayed_result"]["cpt_revision_id"] == rev1["id"]
    assert _posteriors_by_node(mid["displayed_result"]["posteriors"])["B"][
        "probabilities"
    ] == _pytest.approx([0.66, 0.34], abs=1e-6)
    assert _row_by_parents(_table_by_node(mid["current_tables"], "B"), ["no"])["percentages"] == [
        "50",
        "50",
    ]
    assert mid["input_freshness"]["stale"] is False
    assert mid["review_revision"] == 3

    outcome2 = worker_module.run_once(clean_registry, stub)
    assert outcome2["status"] == "succeeded", outcome2
    assert outcome2.get("job_class") == "local_calculation"
    assert stub.calls == []

    done = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["current_cpt_revision_id"] == rev2["id"]
    assert done["displayed_result_revision_id"] == rev2["id"]
    assert done["current_result_matches"] is True
    assert done["calculation_result"]["cpt_revision_id"] == rev2["id"]
    assert done["displayed_result"] == done["calculation_result"]
    # Independent literal: P(B=yes)=0.50*0.60+0.70*0.40=0.58.
    by_node = _posteriors_by_node(done["calculation_result"]["posteriors"])
    assert by_node["A"]["probabilities"] == _pytest.approx([0.60, 0.40], abs=1e-6)
    assert by_node["B"]["probabilities"] == _pytest.approx([0.42, 0.58], abs=1e-6)
    assert "present" in done["calculation_result"]["section_text"]
    assert len(done["calculation_results"]) == 2
    assert [r["cpt_revision_id"] for r in done["calculation_results"]] == [
        rev1["id"],
        rev2["id"],
    ]
    assert len(done["revisions"]) == 2


def test_older_response_fenced_never_labeled_current(clean_registry, monkeypatch) -> None:
    """S48b §2: coalesced rev1+rev2; older success stays historical until rev2 solves."""
    import pytest as _pytest

    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_fence", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]
    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "50",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    rev2 = second.json()["revision"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    older = worker_module.run_once(clean_registry, stub)
    assert older["status"] == "succeeded", older
    assert stub.calls == []

    # Older success fenced: current is still rev2, earlier never labeled current.
    fenced = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert fenced["calculation_state"] == "recalculating"
    assert fenced["current_cpt_revision_id"] == rev2["id"]
    assert fenced["displayed_result_revision_id"] == rev1["id"]
    assert fenced["current_result_matches"] is False
    assert fenced["calculation_result"] is None
    assert fenced["displayed_result"]["cpt_revision_id"] == rev1["id"]
    assert _posteriors_by_node(fenced["displayed_result"]["posteriors"])["B"][
        "probabilities"
    ] == _pytest.approx([0.66, 0.34], abs=1e-6)
    assert len(fenced["calculation_results"]) == 1
    assert fenced["calculation_results"][0]["cpt_revision_id"] == rev1["id"]
    assert len(fenced["local_jobs"]) == 2
    assert sorted(j["status"] for j in fenced["local_jobs"]) == ["queued", "succeeded"]

    newer = worker_module.run_once(clean_registry, stub)
    assert newer["status"] == "succeeded", newer
    done = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["current_cpt_revision_id"] == rev2["id"]
    assert done["displayed_result_revision_id"] == rev2["id"]
    assert done["current_result_matches"] is True
    assert len(done["calculation_results"]) == 2
    # History order is creation order; superseded rev1 remains first.
    assert [r["cpt_revision_id"] for r in done["calculation_results"]] == [
        rev1["id"],
        rev2["id"],
    ]


# --- S48b §1: completed adjustment queues exact revision and runs fixed snapshot ---


def test_completed_adjustment_runs_fixed_snapshot_with_empty_evidence(
    clean_registry, monkeypatch
) -> None:
    """S48b §1: adjustment marks recalculating, queues exact revision, local-only success."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_fixed", "0012345678"
    )
    batch_id, run_id = _succeed_batch(clean_registry, client, package, started)
    network_hash = package["manifest"]["network_hash"]

    posted = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert posted.status_code == 200, posted.text
    revision = posted.json()["revision"]
    assert revision["after_row"]["percentages"] == ["60", "40"]
    assert posted.json()["review_revision"] == 2

    # Atomically recalculating: exact current revision/hash, no success yet.
    pending = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert pending["calculation_state"] == "recalculating"
    assert pending["current_cpt_revision_id"] == revision["id"]
    assert pending["cpt_hash"] == revision["cpt_hash"]
    assert pending["current_result_matches"] is False
    assert pending["calculation_result"] is None
    assert pending["review_revision"] == 2
    assert pending["input_freshness"]["stale"] is False
    assert len(pending["local_jobs"]) == 1
    assert pending["local_jobs"][0]["job_class"] == "local_calculation"
    assert pending["local_jobs"][0]["status"] == "queued"
    assert pending["local_job"] is not None
    assert pending["local_job"]["job_class"] == "local_calculation"

    # Local-only execution: stub records zero provider calls, no endpoint needed.
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert outcome.get("job_class") == "local_calculation"
    assert stub.calls == [], "local path must never touch the provider"

    done = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["current_cpt_revision_id"] == revision["id"]
    assert done["displayed_result_revision_id"] == revision["id"]
    assert done["current_result_matches"] is True
    assert done["local_job"]["status"] == "succeeded"
    # Exact saved revision/hash bound to the immutable result.
    assert len(done["calculation_results"]) == 1
    result = done["calculation_result"]
    assert result is not None
    assert result["cpt_revision_id"] == revision["id"]
    assert result["cpt_hash"] == revision["cpt_hash"]
    assert done["calculation_results"][0] == result
    assert done["displayed_result"] == result
    # Only the fixed network/template/configuration with empty evidence.
    assert result["network_hash"] == network_hash
    assert result["query_nodes"] == ["A", "B"]
    assert result["template_version"] == "v1"
    assert result["provenance"]["engine"] == "local_empty_evidence"
    by_node = _posteriors_by_node(result["posteriors"])
    assert by_node["A"]["probabilities"] == pytest.approx([0.60, 0.40], abs=1e-6)
    assert by_node["B"]["probabilities"] == pytest.approx([0.66, 0.34], abs=1e-6)
    assert by_node["A"]["states"] == ["no", "yes"]
    assert "absent" in result["section_text"]
    assert "no 66.00%" in result["section_text"]
    assert len(result["effective_hash"]) == 64
    # Baseline 80/20 preserved; current shows the committed 60/40.
    assert done["baseline"]["validated_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert _row_by_parents(_table_by_node(done["current_tables"], "A"), [])["percentages"] == [
        "60",
        "40",
    ]
    reread_batch = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert reread_batch["baseline"]["validated_tables"][0]["rows"][0]["percentages"] == [
        "80",
        "20",
    ]
    assert "lease_token" not in str(done) and "grant_token" not in str(done)


def test_provider_mcp_disabled_and_unrelated_runs_unchanged(clean_registry, monkeypatch) -> None:
    """S48b §1: no provider/MCP needed; other patient/DDI/generation untouched."""
    client_a, csrf_a, _, _, package_a, started_a = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_main", "0012345678"
    )
    batch_a, run_a = _succeed_batch(clean_registry, client_a, package_a, started_a)
    client_b, _, _, _, package_b, started_b = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_other", "0012345679"
    )
    batch_b, run_b = _succeed_batch(clean_registry, client_b, package_b, started_b)

    posted = client_a.post(
        f"/api/v1/question-runs/{run_a}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_a),
    )
    assert posted.status_code == 200, posted.text

    # Provider/MCP disabled: a failing stub would record calls; local ignores it.
    failing_stub = provider_module.ControlledStubAdapter(mode="fail_fatal")
    outcome = worker_module.run_once(clean_registry, failing_stub)
    assert outcome["status"] == "succeeded", outcome
    assert outcome.get("job_class") == "local_calculation"
    assert failing_stub.calls == [], "local progress must not consume provider calls"

    main = client_a.get(f"/api/v1/question-runs/{run_a}/review").json()
    assert main["calculation_state"] == "successfully_recalculated"
    by_node = _posteriors_by_node(main["calculation_result"]["posteriors"])
    assert by_node["A"]["probabilities"] == pytest.approx([0.60, 0.40], abs=1e-6)
    assert by_node["B"]["probabilities"] == pytest.approx([0.66, 0.34], abs=1e-6)

    # Unrelated question: no revisions, still shows the original 80/20 baseline.
    other = client_b.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert other["revisions"] == []
    assert other["calculation_state"] == "unchanged"
    assert other["calculation_results"] == []
    assert other["current_cpt_revision_id"] is None
    assert other["review_revision"] == 1
    assert _row_by_parents(_table_by_node(other["current_tables"], "A"), [])["percentages"] == [
        "80",
        "20",
    ]
    other_batch = client_b.get(f"/api/v1/generation-batches/{batch_b}").json()
    assert other_batch["baseline"]["posteriors"][0]["probabilities"] == pytest.approx(
        [0.80, 0.20], abs=1e-6
    )
    assert other_batch["job"]["status"] == "succeeded"
    # Adjusted run's original generation job is untouched (still succeeded).
    main_batch = client_a.get(f"/api/v1/generation-batches/{batch_a}").json()
    assert main_batch["job"]["status"] == "succeeded"


# --- S48b §3: failure retains CPTs, retry same revision, reset reuses baseline ---


def _absent_only_package() -> dict[str, Any]:
    """Variant with a single absent branch to force a public-seam local failure.

    Baseline ``P(B=yes)=0.22`` (argmax no) and ``A 60/40 → P(B)=0.34`` (no)
    both render; flipping ``B|no`` to a yes-majority makes argmax yes with no
    matching branch (``TEMPLATE_NO_MATCH``), preserving CPTs + earlier success.
    """
    package = s48a_package()
    package["template"] = {
        "version": "v1",
        "branches": [
            {
                "when": {"node": "B", "state": "no", "operator": "=="},
                "text": "Result for {B} is absent.",
            },
        ],
    }
    return package


def _new_physician_batch_with_package(
    clean_registry, monkeypatch, username: str, identifier: str, package: dict[str, Any]
):
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
    encounter_id, revision = _setup_encounter(client, csrf, identifier)
    started = _start_batch(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    return client, csrf, encounter_id, revision, package, started.json()


def test_numerical_failure_retains_cpts_and_labels_earlier_success(
    clean_registry, monkeypatch
) -> None:
    """S48b §3: failed local preserves current CPTs; earlier success stays labeled."""
    import pytest as _pytest

    package = _absent_only_package()
    client, csrf, _, _, _, started = _new_physician_batch_with_package(
        clean_registry, monkeypatch, "dr_s48b_fail", "0012345678", package
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    ok1 = worker_module.run_once(clean_registry, stub)
    assert ok1["status"] == "succeeded", ok1
    assert stub.calls == []

    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "80",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    rev2 = second.json()["revision"]
    # Independent literal: [90,10] yes→80 gives [20,80].
    assert rev2["after_row"]["percentages"] == ["20", "80"]

    failed = worker_module.run_once(clean_registry, stub)
    assert failed["status"] == "failed", failed
    assert failed.get("job_class") == "local_calculation"
    assert stub.calls == [], "failed locals must not consume provider calls"

    body = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert body["calculation_state"] == "failed"
    assert body["current_cpt_revision_id"] == rev2["id"]
    assert body["displayed_result_revision_id"] == rev1["id"]
    assert body["current_result_matches"] is False
    assert body["calculation_result"] is None
    assert body["displayed_result"] is not None
    assert body["displayed_result"]["cpt_revision_id"] == rev1["id"]
    assert _posteriors_by_node(body["displayed_result"]["posteriors"])["B"][
        "probabilities"
    ] == _pytest.approx([0.66, 0.34], abs=1e-6)
    # Current CPTs retained (unsolved values preserved, not rolled back).
    assert _row_by_parents(_table_by_node(body["current_tables"], "B"), ["no"])["percentages"] == [
        "20",
        "80",
    ]
    assert _row_by_parents(_table_by_node(body["current_tables"], "A"), [])["percentages"] == [
        "60",
        "40",
    ]
    # Only the earlier success has a result row; the failed revision has none.
    assert len(body["calculation_results"]) == 1
    assert body["calculation_results"][0]["cpt_revision_id"] == rev1["id"]
    assert body["baseline"]["validated_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert body["local_job"] is not None
    assert body["local_job"]["status"] == "failed"


def test_local_retry_targets_same_revision_without_reestimation(
    clean_registry, monkeypatch
) -> None:
    """S48b §3: retry requeues the exact current revision; no provider estimation."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_retry", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    posted = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert posted.status_code == 200, posted.text
    revision = posted.json()["revision"]

    # Retry while still queued: same revision/hash, same job, no duplicate work.
    retry_queued = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 2, "current_cpt_revision_id": revision["id"]},
        headers=_auth_headers(csrf),
    )
    assert retry_queued.status_code == 200, retry_queued.text
    assert retry_queued.json()["current_cpt_revision_id"] == revision["id"]
    assert retry_queued.json()["cpt_hash"] == revision["cpt_hash"]
    assert retry_queued.json()["review_revision"] == 2
    assert retry_queued.json()["job"]["job_class"] == "local_calculation"
    queued_job_id = retry_queued.json()["job"]["id"]
    mid = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(mid["local_jobs"]) == 1, "retry must coalesce, not duplicate the job"
    assert mid["local_jobs"][0]["id"] == queued_job_id

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert stub.calls == []

    done = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["calculation_result"]["cpt_revision_id"] == revision["id"]

    # Retry after success is idempotent: same job, same result, still no estimation.
    retry_solved = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 2, "current_cpt_revision_id": revision["id"]},
        headers=_auth_headers(csrf),
    )
    assert retry_solved.status_code == 200, retry_solved.text
    assert retry_solved.json()["job"]["id"] == queued_job_id
    assert retry_solved.json()["job"]["status"] == "succeeded"
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(reread["calculation_results"]) == 1
    assert len(reread["local_jobs"]) == 1
    assert stub.calls == []

    # Retry of the failed revision (absent-only package) stays on the same row.
    fail_package = _absent_only_package()
    fclient, fcsrf, _, _, _, fstarted = _new_physician_batch_with_package(
        clean_registry, monkeypatch, "dr_s48b_retryf", "0012345680", fail_package
    )
    _, frun = _succeed_batch(clean_registry, fclient, fail_package, fstarted)
    fposted = fclient.post(
        f"/api/v1/question-runs/{frun}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(fcsrf),
    )
    assert fposted.status_code == 200, fposted.text
    # Force the no-match failure: flip B|no to yes-majority, then fail once.
    fsecond = fclient.post(
        f"/api/v1/question-runs/{frun}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "80",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(fcsrf),
    )
    assert fsecond.status_code == 200, fsecond.text
    frev2 = fsecond.json()["revision"]
    fstub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, fstub)["status"] == "succeeded"
    assert worker_module.run_once(clean_registry, fstub)["status"] == "failed"
    fretry = fclient.post(
        f"/api/v1/question-runs/{frun}/retry-calculation",
        json={"expected_review_revision": 3, "current_cpt_revision_id": frev2["id"]},
        headers=_auth_headers(fcsrf),
    )
    assert fretry.status_code == 200, fretry.text
    assert fretry.json()["current_cpt_revision_id"] == frev2["id"]
    assert fretry.json()["cpt_hash"] == frev2["cpt_hash"]
    assert fstub.calls == [], "retry must not re-estimate CPTs via the provider"


def test_reset_reuses_verified_baseline_and_retains_history(clean_registry, monkeypatch) -> None:
    """S48b §3: reset is baseline-equal with explicit reuse; history retained."""
    import pytest as _pytest

    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_reset", "0012345678"
    )
    batch_id, run_id = _succeed_batch(clean_registry, client, package, started)
    baseline_before = client.get(f"/api/v1/question-runs/{run_id}/review").json()["baseline"]
    assert baseline_before is not None

    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []

    reset = client.post(
        f"/api/v1/question-runs/{run_id}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    payload = reset.json()
    reset_rev = payload["revision"]
    assert reset_rev["kind"] == "reset"
    assert reset_rev["sequence"] == 2
    assert reset_rev["parent_revision_id"] == rev1["id"]
    assert payload["review_revision"] == 3
    # Baseline-equal artifact with verified original-result reuse (no execution).
    assert reset_rev["cpt_artifact"] == baseline_before["validated_tables"]
    assert payload["calculation_result"]["reused_from_baseline_id"] == baseline_before["id"]
    assert payload["calculation_result"]["cpt_revision_id"] == reset_rev["id"]
    assert stub.calls == [], "reset must reuse without provider/MCP calls"

    body = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert body["calculation_state"] == "successfully_recalculated"
    assert body["current_cpt_revision_id"] == reset_rev["id"]
    assert body["displayed_result_revision_id"] == reset_rev["id"]
    assert body["current_result_matches"] is True
    assert body["review_revision"] == 3
    assert body["current_tables"] == baseline_before["validated_tables"]
    assert body["calculation_result"]["reused_from_baseline_id"] == baseline_before["id"]
    # Reused values equal the original independent literals.
    by_node = _posteriors_by_node(body["calculation_result"]["posteriors"])
    assert by_node["A"]["probabilities"] == _pytest.approx([0.80, 0.20], abs=1e-6)
    assert by_node["B"]["probabilities"] == _pytest.approx([0.78, 0.22], abs=1e-6)
    assert body["calculation_result"]["section_text"] == baseline_before["section_text"]
    # History retained: adjustment + reset, both results present.
    assert len(body["revisions"]) == 2
    assert [r["kind"] for r in body["revisions"]] == ["adjustment", "reset"]
    assert len(body["calculation_results"]) == 2
    assert body["baseline"] == baseline_before
    assert body["input_freshness"]["stale"] is False


def test_stale_inputs_still_require_regeneration(clean_registry, monkeypatch) -> None:
    """S48b §3: relevant edits stale the review; reset/retry cannot make it current."""
    client, csrf, encounter_id, revision, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_stale", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    posted = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert posted.status_code == 200, posted.text
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"

    edited = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text

    stale = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert stale["freshness"]["stale"] is True
    assert stale["input_freshness"]["stale"] is True
    # Solved values stay solved for the old snapshot, but inputs are stale.
    assert stale["calculation_state"] == "successfully_recalculated"
    assert stale["current_result_matches"] is True

    # Reset of a stale run still leaves it stale (reset never regenerates).
    reset = client.post(
        f"/api/v1/question-runs/{run_id}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    after_reset = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after_reset["input_freshness"]["stale"] is True
    assert after_reset["freshness"]["stale"] is True

    # Local retry of the stale revision still executes the fixed snapshot
    # (no regeneration) but stays stale.
    retry = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={
            "expected_review_revision": after_reset["review_revision"],
            "current_cpt_revision_id": after_reset["current_cpt_revision_id"],
        },
        headers=_auth_headers(csrf),
    )
    assert retry.status_code == 200, retry.text
    assert stub.calls == []
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["input_freshness"]["stale"] is True


def test_reset_retry_conflict_semantics(clean_registry, monkeypatch) -> None:
    """S48b §3: reset/retry carry 409/412/422 without touching history."""
    import uuid as _uuid

    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_conf", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]
    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "50",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    rev2 = second.json()["revision"]

    # Stale expected revision is 412 for both commands.
    stale_reset = client.post(
        f"/api/v1/question-runs/{run_id}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert stale_reset.status_code == 412, stale_reset.text
    stale_retry = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 2, "current_cpt_revision_id": rev2["id"]},
        headers=_auth_headers(csrf),
    )
    assert stale_retry.status_code == 412, stale_retry.text

    # Superseded revision id is 409 (retry targets the current revision only).
    superseded = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 3, "current_cpt_revision_id": rev1["id"]},
        headers=_auth_headers(csrf),
    )
    assert superseded.status_code == 409, superseded.text

    # Malformed revision id is 422; unknown run is 404.
    malformed = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 3, "current_cpt_revision_id": "not-a-uuid"},
        headers=_auth_headers(csrf),
    )
    assert malformed.status_code == 422, malformed.text
    assert client.get(f"/api/v1/question-runs/{_uuid.uuid4()}/review").status_code == 404

    # No failed command moved the pointer or deleted history.
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["review_revision"] == 3
    assert reread["current_cpt_revision_id"] == rev2["id"]
    assert len(reread["revisions"]) == 2


# --- S48b §4: restart, lifecycle fencing, isolated capacity, coalescing ---


def test_restart_resumes_eligible_durable_job_with_idempotency(clean_registry, monkeypatch) -> None:
    """S48b §4: queued local survives restart; idempotent replay never duplicates."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_restart", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48b-restart-0001"},
    )
    assert first.status_code == 200, first.text
    revision = first.json()["revision"]
    replay = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48b-restart-0001"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"]["id"] == revision["id"]
    assert len(client.get(f"/api/v1/question-runs/{run_id}/review").json()["local_jobs"]) == 1

    # Restart: fresh process (new TestClient + login), same persisted database.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    restarted = TestClient(app)
    login = restarted.post(
        "/api/v1/auth/login",
        json={"username": "dr_s48b_restart", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200, login.text
    pending = restarted.get(f"/api/v1/question-runs/{run_id}/review")
    assert pending.status_code == 200, pending.text
    assert pending.json()["calculation_state"] == "recalculating"
    assert pending.json()["current_cpt_revision_id"] == revision["id"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert stub.calls == []

    reread = restarted.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["calculation_state"] == "successfully_recalculated"
    assert reread["current_cpt_revision_id"] == revision["id"]
    assert reread["calculation_result"]["cpt_revision_id"] == revision["id"]
    assert len(reread["calculation_results"]) == 1


def test_deactivation_and_discard_prevent_late_publication(clean_registry, monkeypatch) -> None:
    """S48b §4: deactivated author / discarded draft cancels locals without results."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    admin_csrf = login.json()["csrf_token"]
    for username in ("dr_s48b_deact", "dr_s48b_disc"):
        created = admin.post(
            "/api/v1/physicians",
            json={"username": username, "password": "pw123"},
            headers=_auth_headers(admin_csrf),
        )
        assert created.status_code == 201, created.text

    def _login(username: str):
        c = TestClient(app)
        r = c.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "pw123", "role": "physician"},
        )
        assert r.status_code == 200, r.text
        return c, r.json()["csrf_token"]

    deact_client, deact_csrf = _login("dr_s48b_deact")
    disc_client, disc_csrf = _login("dr_s48b_disc")
    deact_enc, deact_rev = _setup_encounter(deact_client, deact_csrf, "0012345678")
    disc_enc, disc_rev = _setup_encounter(disc_client, disc_csrf, "0012345679")
    package = s48a_package()
    deact_started = _start_batch(deact_client, deact_csrf, deact_enc, deact_rev, package)
    assert deact_started.status_code == 202, deact_started.text
    disc_started = _start_batch(disc_client, disc_csrf, disc_enc, disc_rev, package)
    assert disc_started.status_code == 202, disc_started.text
    assert (
        _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))["status"]
        == "succeeded"
    )
    # Second baseline needs its own generation (one run_once per batch).
    assert (
        _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))["status"]
        == "succeeded"
    )
    deact_run = deact_started.json()["question_runs"][0]["id"]
    disc_run = disc_started.json()["question_runs"][0]["id"]
    deact_posted = deact_client.post(
        f"/api/v1/question-runs/{deact_run}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(deact_csrf),
    )
    assert deact_posted.status_code == 200, deact_posted.text
    disc_posted = disc_client.post(
        f"/api/v1/question-runs/{disc_run}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(disc_csrf),
    )
    assert disc_posted.status_code == 200, disc_posted.text

    # Deactivate one author; discard the other draft.
    listed = admin.get("/api/v1/physicians", headers=_auth_headers(admin_csrf))
    assert listed.status_code == 200, listed.text
    target = [u for u in listed.json()["items"] if u["username"] == "dr_s48b_deact"]
    assert len(target) == 1
    deactivated = admin.post(
        f"/api/v1/physicians/{target[0]['id']}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text
    discarded = disc_client.post(
        f"/api/v1/encounters/{disc_enc}/discard",
        json={"confirm": True},
        headers={**_auth_headers(disc_csrf), "If-Match": contracts.format_etag(disc_rev)},
    )
    assert discarded.status_code == 200, discarded.text

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    first_out = worker_module.run_once(clean_registry, stub)
    assert first_out["status"] in ("cancelled", "idle"), first_out
    second_out = worker_module.run_once(clean_registry, stub)
    assert second_out["status"] in ("cancelled", "idle"), second_out
    assert stub.calls == [], "fenced locals must never reach the provider"

    # Discarded draft stays readable history: no result published, job cancelled.
    disc_review = disc_client.get(f"/api/v1/question-runs/{disc_run}/review")
    assert disc_review.status_code == 200, disc_review.text
    disc_body = disc_review.json()
    assert disc_body["calculation_state"] == "failed"
    assert disc_body["current_result_matches"] is False
    assert disc_body["calculation_result"] is None
    assert disc_body["calculation_results"] == []
    assert disc_body["current_tables"][0]["rows"][0]["percentages"] == ["60", "40"]
    assert disc_body["local_job"]["status"] == "cancelled"

    # Reactivate the deactivated author, then reread: still no late publication.
    reactivated = admin.post(
        f"/api/v1/physicians/{target[0]['id']}/reactivate",
        headers=_auth_headers(admin_csrf),
    )
    assert reactivated.status_code == 200, reactivated.text
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh = TestClient(app)
    relogin = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_s48b_deact", "password": "pw123", "role": "physician"},
    )
    assert relogin.status_code == 200, relogin.text
    deact_review = fresh.get(f"/api/v1/question-runs/{deact_run}/review")
    assert deact_review.status_code == 200, deact_review.text
    deact_body = deact_review.json()
    assert deact_body["calculation_state"] == "failed"
    assert deact_body["calculation_results"] == []
    assert deact_body["current_result_matches"] is False
    assert deact_body["local_job"]["status"] == "cancelled"


def test_provider_saturation_permits_independent_local_progress(
    clean_registry, monkeypatch
) -> None:
    """S48b §4: two leased generations busy; local still succeeds with zero calls."""
    from x_insight.reasoning import queue as queue_module

    # Local batch first so its baseline succeeds before saturation batches queue.
    client_c, csrf_c, _, _, package_c, started_c = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_sat_c", "0012345683"
    )
    batch_c, run_c = _succeed_batch(clean_registry, client_c, package_c, started_c)
    posted = client_c.post(
        f"/api/v1/question-runs/{run_c}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_c),
    )
    assert posted.status_code == 200, posted.text
    revision = posted.json()["revision"]

    client_a, _, _, _, _, started_a = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_sat_a", "0012345681"
    )
    client_b, _, _, _, _, started_b = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_sat_b", "0012345682"
    )

    # Saturate the provider slots via T8 queue claims (setup only).
    with db_module.session_scope(clean_registry) as session:
        first = queue_module.claim_next_job(session, "worker-sat-a")
    assert first is not None and "job" in first, first
    with db_module.session_scope(clean_registry) as session:
        second = queue_module.claim_next_job(session, "worker-sat-b")
    assert second is not None and "job" in second, second
    assert first["job"]["id"] != second["job"]["id"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert outcome.get("job_class") == "local_calculation"
    assert stub.calls == [], "provider saturation must not consume local calls"

    done = client_c.get(f"/api/v1/question-runs/{run_c}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["calculation_result"]["cpt_revision_id"] == revision["id"]
    # Saturated generations stay leased/busy; local progress was independent.
    batch_a = client_a.get(f"/api/v1/generation-batches/{started_a['batch']['id']}").json()
    batch_b = client_b.get(f"/api/v1/generation-batches/{started_b['batch']['id']}").json()
    assert batch_a["job"]["status"] == "leased"
    assert batch_b["job"]["status"] == "leased"
    assert batch_a["queue"]["busy"] is True
    assert batch_a["queue"]["leased_count"] == 2
    batch_c_view = client_c.get(f"/api/v1/generation-batches/{batch_c}").json()
    assert batch_c_view["job"]["status"] == "succeeded"


def test_superseded_pending_may_coalesce_without_deleting_revisions(
    clean_registry, monkeypatch
) -> None:
    """S48b §4: queued superseded jobs wait; retry coalesces; revisions never deleted."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48b_coal", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert first.status_code == 200, first.text
    rev1 = first.json()["revision"]
    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "50",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    rev2 = second.json()["revision"]

    pending = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(pending["revisions"]) == 2
    assert len(pending["local_jobs"]) == 2

    # Explicit retry of the current revision coalesces onto the same queued row.
    retry = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 3, "current_cpt_revision_id": rev2["id"]},
        headers=_auth_headers(csrf),
    )
    assert retry.status_code == 200, retry.text
    coalesced = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(coalesced["revisions"]) == 2, "coalescing must never delete revisions"
    assert len(coalesced["local_jobs"]) == 2, "retry reuses the queued row"
    assert coalesced["calculation_results"] == []

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []
    done = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(done["revisions"]) == 2
    assert len(done["calculation_results"]) == 2
    assert [r["cpt_revision_id"] for r in done["calculation_results"]] == [
        rev1["id"],
        rev2["id"],
    ]
    assert done["current_cpt_revision_id"] == rev2["id"]
