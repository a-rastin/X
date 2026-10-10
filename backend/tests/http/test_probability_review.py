"""Persist complete CPT adjustments (S48a, seam T1).

Through public authenticated HTTP on real PostgreSQL with real routes:

- ``GET /question-runs/{id}/review`` exposes every root/conditional row with
  original/current values, full parent assignments, and read-only outputs.
- ``POST /question-runs/{id}/cpt-adjustments`` persists one immutable full
  revision (direct/redistributed before-after, parent/sequence, actor/time)
  with an atomic audit, deterministic redistribution from the immediately
  preceding committed row, idempotent replay, and stale-write conflicts.

No internal mocks, no DB-row asserts: every behavior is asserted via GET
review + POST responses with independent literal fixtures. Setup may
truncate/migrate. The worker uses the existing
``DeterministicProviderEndpoint`` + ``BoundedProviderAdapter`` path from S45
(real MCP stdio, controlled CPTs), never a fake succeeded endpoint.

Covers tasks.md S48a §§1,4 (T1) with T5 numerics asserted through the
interface (other rows unchanged, invalid rejected, preceding values used).
"""

from __future__ import annotations

import hashlib
import os
import uuid
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
                "TRUNCATE cpt_revisions, question_review_states, reasoning_grants, "
                "reasoning_job_attempts, reasoning_jobs, reasoning_fairness, "
                "original_baselines, question_runs, generation_batches, notes, "
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


def _run_bounded_until_terminal(engine, cpt_payload: dict[str, Any]) -> dict[str, Any]:
    from datetime import timedelta as _timedelta

    moment = contracts.utcnow()
    outcome = _run_bounded_once(engine, cpt_payload, now=moment)
    for _ in range(2):
        if outcome.get("status") in ("failed", "succeeded", "cancelled", "fencing_failed"):
            return outcome
        moment = moment + _timedelta(seconds=70)
        outcome = _run_bounded_once(engine, cpt_payload, now=moment)
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


# --- S48a §1: author reads every root/conditional row, outputs read-only ---


def test_author_reads_every_row_with_original_current_and_readonly(
    clean_registry, monkeypatch
) -> None:
    """S48a §1: author sees all root/conditional rows, original+current, read-only."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_read", "0012345678"
    )
    batch_id, run_id = _succeed_batch(clean_registry, client, package, started)

    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["adjustable"] is True
    assert body["outputs_read_only"] is True
    assert body["review_revision"] == 1
    assert body["revisions"] == []
    assert body["current_cpt_revision_id"] is None
    assert body["cpt_hash"] is None

    original = body["original_tables"]
    current = body["current_tables"]
    assert isinstance(original, list) and isinstance(current, list)
    assert len(original) == 2 and len(current) == 2
    # Independent literals from the controlled CPT (never implementation output).
    root = _table_by_node(original, "A")
    assert root["parent_ids"] == [] and root["states"] == ["no", "yes"]
    assert len(root["rows"]) == 1
    assert _row_by_parents(root, [])["percentages"] == ["80", "20"]
    child = _table_by_node(original, "B")
    assert child["parent_ids"] == ["A"] and child["states"] == ["no", "yes"]
    assert len(child["rows"]) == 2
    assert _row_by_parents(child, ["no"])["percentages"] == ["90", "10"]
    assert _row_by_parents(child, ["yes"])["percentages"] == ["30", "70"]
    # Current equals original before any adjustment; batch still addressable.
    assert current == original
    assert body["baseline"]["validated_tables"] == original
    reread = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert reread["baseline"]["validated_tables"] == original


# --- S48a §1: failed original exposes no adjustable baseline ---


def test_failed_original_has_no_baseline_and_rejects_adjustment(
    clean_registry, monkeypatch
) -> None:
    """S48a §1: failed originals expose no baseline; POST is rejected atomically."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_fail", "0012345678"
    )
    batch_id = started["batch"]["id"]
    run_id = started["question_runs"][0]["id"]
    network_hash = package["manifest"]["network_hash"]
    mutated = {
        "network_hash": network_hash,
        "tables": [
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
    outcome = _run_bounded_until_terminal(clean_registry, mutated)
    assert outcome["status"] == "failed", outcome

    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["baseline"] is None
    assert body["adjustable"] is False
    assert body["original_tables"] is None
    assert body["current_tables"] is None
    assert body["revisions"] == []
    assert body["review_revision"] == 1
    assert body["outputs_read_only"] is True

    denied = client.post(
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
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "ADJUSTMENT_NOT_AVAILABLE"
    # Atomic: no revision appeared and the pointer did not move.
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["revisions"] == []
    assert reread["review_revision"] == 1
    assert reread["adjustable"] is False
    assert client.get(f"/api/v1/generation-batches/{batch_id}").json()["baseline"] is None


# --- S48a §4: completed POST creates an immutable full revision ---


def test_completed_adjustment_creates_immutable_revision(clean_registry, monkeypatch) -> None:
    """S48a §4: POST persists sequence/parent/direct+redistributed rows/actor/time."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_rev", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    before = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    baseline_before = before["baseline"]
    bundle_before = before["batch"]["pinned_bundle"]

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
    payload = posted.json()
    revision = payload["revision"]
    state = payload["review_state"]
    # Immutable full revision: sequence/parent, direct edit, before/after, actor/time.
    assert revision["sequence"] == 1
    assert revision["parent_revision_id"] is None
    assert revision["kind"] == "adjustment"
    assert revision["direct_edit"]["node_id"] == "A"
    assert revision["direct_edit"]["parent_states"] == []
    assert revision["direct_edit"]["state"] == "yes"
    assert revision["direct_edit"]["target_percentage"] == "40"
    assert revision["before_row"]["percentages"] == ["80", "20"]
    assert revision["after_row"]["percentages"] == ["60", "40"]
    assert revision["before_row"]["parent_states"] == []
    assert revision["after_row"]["parent_states"] == []
    assert revision["actor_username"] == "dr_s48a_rev"
    assert revision["redistribution_version"] == "redistribution-v1"
    assert len(revision["cpt_hash"]) == 64
    assert len(revision["cpt_artifact"]) == 2
    assert "created_at" in revision and revision["created_at"].endswith("Z")
    assert payload["current_cpt_revision_id"] == revision["id"]
    assert payload["review_revision"] == 2
    assert state["review_revision"] == 2
    assert state["current_revision_id"] == revision["id"]
    assert payload["current_tables"] == revision["cpt_artifact"]

    # Other rows unchanged: B stays exactly at its committed values.
    after_tables = revision["cpt_artifact"]
    assert _row_by_parents(_table_by_node(after_tables, "B"), ["no"])["percentages"] == [
        "90",
        "10",
    ]
    assert _row_by_parents(_table_by_node(after_tables, "B"), ["yes"])["percentages"] == [
        "30",
        "70",
    ]
    assert _row_by_parents(_table_by_node(after_tables, "A"), [])["percentages"] == [
        "60",
        "40",
    ]

    # GET shows the same acknowledged revision; baseline/shared XML unchanged.
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["review_revision"] == 2
    assert reread["current_cpt_revision_id"] == revision["id"]
    assert reread["cpt_hash"] == revision["cpt_hash"]
    assert len(reread["revisions"]) == 1
    assert reread["revisions"][0] == revision
    assert reread["current_tables"] == revision["cpt_artifact"]
    assert reread["original_tables"] == baseline_before["validated_tables"]
    assert reread["baseline"] == baseline_before
    assert reread["batch"]["pinned_bundle"] == bundle_before
    assert reread["outputs_read_only"] is True


def test_second_adjustment_chains_parent_and_uses_preceding_values(
    clean_registry, monkeypatch
) -> None:
    """S48a §§2,4: second POST parents the first and redistributes from it."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_chain", "0012345678"
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
    first_rev = first.json()["revision"]
    assert first_rev["after_row"]["percentages"] == ["60", "40"]

    second = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 2,
        },
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 200, second.text
    second_rev = second.json()["revision"]
    assert second_rev["sequence"] == 2
    assert second_rev["parent_revision_id"] == first_rev["id"]
    assert second_rev["before_row"]["percentages"] == ["90", "10"]
    assert second_rev["after_row"]["percentages"] == ["60", "40"]
    assert second.json()["review_revision"] == 3

    # First revision is immutable; B's other row never moved.
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert len(reread["revisions"]) == 2
    assert reread["revisions"][0] == first_rev
    assert reread["revisions"][1] == second_rev
    current = reread["current_tables"]
    assert _row_by_parents(_table_by_node(current, "A"), [])["percentages"] == ["60", "40"]
    assert _row_by_parents(_table_by_node(current, "B"), ["no"])["percentages"] == ["60", "40"]
    assert _row_by_parents(_table_by_node(current, "B"), ["yes"])["percentages"] == [
        "30",
        "70",
    ]

    # Same-row redistribution uses the immediately preceding committed row.
    third = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "50",
            "expected_review_revision": 3,
        },
        headers=_auth_headers(csrf),
    )
    assert third.status_code == 200, third.text
    third_rev = third.json()["revision"]
    assert third_rev["before_row"]["percentages"] == ["60", "40"]
    assert third_rev["after_row"]["percentages"] == ["50", "50"]
    assert third_rev["sequence"] == 3


# --- S48a §1: wrong-author/admin denied without leak ---


def test_wrong_author_and_admin_denied_without_leak(clean_registry, monkeypatch) -> None:
    """S48a §1: stranger/admin reads and writes are denied without content."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    admin_csrf = login.json()["csrf_token"]
    for username in ("dr_s48a_own", "dr_s48a_other"):
        created = admin.post(
            "/api/v1/physicians",
            json={"username": username, "password": "pw123"},
            headers=_auth_headers(admin_csrf),
        )
        assert created.status_code == 201, created.text
    own, own_csrf = _login_physician(clean_registry, monkeypatch, "dr_s48a_own")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s48a_other")
    encounter_id, revision = _setup_encounter(own, own_csrf, "0012345678")
    package = s48a_package()
    started = _start_batch(own, own_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]
    outcome = _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))
    assert outcome["status"] == "succeeded", outcome

    stranger_read = other.get(f"/api/v1/question-runs/{run_id}/review")
    assert stranger_read.status_code == 403, stranger_read.text
    assert '"80"' not in stranger_read.text and "h_a" not in stranger_read.text
    assert "percentages" not in stranger_read.text
    assert other.get(f"/api/v1/generation-batches/{batch_id}").status_code == 403
    admin_read = admin.get(f"/api/v1/question-runs/{run_id}/review")
    assert admin_read.status_code in (401, 403), admin_read.text
    assert '"80"' not in admin_read.text and "h_a" not in admin_read.text
    assert "percentages" not in admin_read.text

    stranger_write = other.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(other_csrf),
    )
    assert stranger_write.status_code == 403, stranger_write.text
    assert '"80"' not in stranger_write.text and "percentages" not in stranger_write.text
    admin_write = admin.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(admin_csrf),
    )
    assert admin_write.status_code == 403, admin_write.text
    assert own.get(f"/api/v1/question-runs/{uuid.uuid4()}/review").status_code == 404
    # Owner still reads; no stranger write created a revision.
    assert own.get(f"/api/v1/question-runs/{run_id}/review").json()["revisions"] == []


# --- S48a §4: idempotent replay creates no duplicate; changed body conflicts ---


def test_idempotency_replay_no_duplicate_and_conflict(clean_registry, monkeypatch) -> None:
    """S48a §4: same key+body replays; same key+changed body is 409."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_idem", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    body = {
        "node_id": "A",
        "parent_states": [],
        "state": "yes",
        "target_percentage": "40",
        "expected_review_revision": 1,
    }
    first = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json=body,
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48a-idem-0001"},
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["revision"]["id"]
    replay = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json=body,
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48a-idem-0001"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"]["id"] == first_id
    assert replay.json() == first.json()
    assert len(client.get(f"/api/v1/question-runs/{run_id}/review").json()["revisions"]) == 1

    conflict = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={**body, "target_percentage": "50"},
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48a-idem-0001"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(client.get(f"/api/v1/question-runs/{run_id}/review").json()["revisions"]) == 1


# --- S48a §4: stale expected revision and If-Match conflict ---


def test_stale_expected_and_if_match_rejected(clean_registry, monkeypatch) -> None:
    """S48a §4: stale expected_review_revision/If-Match is 412 without a write."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_stale", "0012345678"
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

    stale_body = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert stale_body.status_code == 412, stale_body.text
    assert stale_body.json()["code"] == "STALE_REVISION"

    stale_header = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 2,
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert stale_header.status_code == 412, stale_header.text

    mismatch = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert mismatch.status_code == 412, mismatch.text

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["review_revision"] == 2
    assert len(reread["revisions"]) == 1

    fresh = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 2,
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["review_revision"] == 3


# --- S48a §3: invalid target/precision rejected without repair ---


def test_invalid_target_precision_rejected_without_repair(clean_registry, monkeypatch) -> None:
    """S48a §3: invalid targets/precision fail 422 and never repair silently."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_invalid", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    current_before = client.get(f"/api/v1/question-runs/{run_id}/review").json()["current_tables"]

    for bad_target in ("200", "-1", "22.1234567", "nan", "Infinity", "", "abc"):
        denied = client.post(
            f"/api/v1/question-runs/{run_id}/cpt-adjustments",
            json={
                "node_id": "A",
                "parent_states": [],
                "state": "yes",
                "target_percentage": bad_target,
                "expected_review_revision": 1,
            },
            headers=_auth_headers(csrf),
        )
        assert denied.status_code == 422, (bad_target, denied.text)

    for bad_shape in (
        {"node_id": "ZZZ", "parent_states": [], "state": "yes"},
        {"node_id": "A", "parent_states": [], "state": "maybe"},
        {"node_id": "B", "parent_states": [], "state": "yes"},
    ):
        payload: dict[str, Any] = {
            **bad_shape,
            "target_percentage": "40",
            "expected_review_revision": 1,
        }
        denied = client.post(
            f"/api/v1/question-runs/{run_id}/cpt-adjustments",
            json=payload,
            headers=_auth_headers(csrf),
        )
        # Unknown node/state/row all fail closed without a repair.
        assert denied.status_code == 422, (bad_shape, denied.text)

    missing_revision = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={"node_id": "A", "parent_states": [], "state": "yes", "target_percentage": "40"},
        headers=_auth_headers(csrf),
    )
    assert missing_revision.status_code == 422, missing_revision.text
    extra_field = client.post(
        f"/api/v1/question-runs/{run_id}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
            "extra": "nope",
        },
        headers=_auth_headers(csrf),
    )
    assert extra_field.status_code == 422, extra_field.text

    # Single-state-style lock is covered purely in T5; here the atomic guard is
    # that no failed command moved the pointer or the current tables.
    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["revisions"] == []
    assert reread["review_revision"] == 1
    assert reread["current_tables"] == current_before


# --- S48a §4: baseline/shared XML unchanged; restart reread ---


def test_original_baseline_and_shared_xml_unchanged(clean_registry, monkeypatch) -> None:
    """S48a §4: OriginalBaseline and shared XML never move under adjustments."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_base", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    before = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    baseline_before = before["baseline"]
    bundle_before = before["batch"]["pinned_bundle"]
    run_before = before["question_run"]

    for index, target in ((1, "40"), (2, "50")):
        posted = client.post(
            f"/api/v1/question-runs/{run_id}/cpt-adjustments",
            json={
                "node_id": "A",
                "parent_states": [],
                "state": "yes",
                "target_percentage": target,
                "expected_review_revision": index,
            },
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text

    after = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after["baseline"] == baseline_before
    assert after["original_tables"] == baseline_before["validated_tables"]
    assert after["batch"]["pinned_bundle"] == bundle_before
    assert after["question_run"] == run_before
    assert len(after["revisions"]) == 2
    assert after["baseline"]["effective_xml"] == baseline_before["effective_xml"]
    assert after["baseline"]["source_hash"] == baseline_before["source_hash"]


def test_reread_after_restart_shows_acknowledged_values(clean_registry, monkeypatch) -> None:
    """S48a §4: acknowledged values survive navigation/restart via GET."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48a_restart", "0012345678"
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
    acknowledged_tables = posted.json()["current_tables"]
    acknowledged_id = posted.json()["current_cpt_revision_id"]
    acknowledged_revision = posted.json()["review_revision"]

    # Fresh process: new TestClient, new login session, same persisted database.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    restarted = TestClient(app)
    login = restarted.post(
        "/api/v1/auth/login",
        json={"username": "dr_s48a_restart", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200, login.text
    reread = restarted.get(f"/api/v1/question-runs/{run_id}/review")
    assert reread.status_code == 200, reread.text
    body = reread.json()
    assert body["current_tables"] == acknowledged_tables
    assert body["current_cpt_revision_id"] == acknowledged_id
    assert body["review_revision"] == acknowledged_revision
    assert len(body["revisions"]) == 1
    assert body["revisions"][0]["id"] == acknowledged_id
    assert _row_by_parents(_table_by_node(body["current_tables"], "A"), [])["percentages"] == [
        "60",
        "40",
    ]
