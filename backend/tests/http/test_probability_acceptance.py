"""Accept the exact current result (S48c, seam T1).

Through public authenticated HTTP on real PostgreSQL with real routes:

- ``POST /question-runs/{id}/acceptance`` persists one author-only
  acceptance of the exact current baseline/revision/result/input hash with
  an atomic audit event. Wrong-author/admin reads and writes are denied
  without content leak; missing runs stay 404.
- ``GET /question-runs/{id}/review`` exposes the acceptance state: the
  current-matching acceptance (or null), whether current is accepted, and
  the retained acceptance history. Later edits/reset/regeneration
  invalidate via exact-match (no deletion); note-only edits preserve.

No internal mocks, no DB-row asserts: every behavior is asserted via GET
review + POST responses with independent literal fixtures. Setup may
truncate/migrate. Originals use the existing
``DeterministicProviderEndpoint`` + ``BoundedProviderAdapter`` path from
S45 (real MCP stdio, controlled CPTs); local recalculation uses
``ControlledStubAdapter`` (never a provider call).

Covers tasks.md S48c §4 (T1). Defaults: T=100_000_000,
redistribution-v1, review_revision starts 1.
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


def _accept_body(review: dict[str, Any], *, result_id: str | None = None) -> dict[str, Any]:
    """Exact current references from a GET review (baseline or recalculated).

    When the current revision has no successful result yet (recalculating /
    failed), callers pass an explicit ``result_id`` to prove the server
    rejects the attempt rather than the test omitting the field.
    """
    baseline = review["baseline"]
    assert baseline is not None
    if review["current_cpt_revision_id"] is None:
        # Unchanged original: baseline tables are the current CPTs.
        cpt_hash = contracts.canonical_hash(list(baseline["validated_tables"]))
        resolved_result = result_id if result_id is not None else baseline["id"]
    else:
        assert review["cpt_hash"] is not None
        cpt_hash = str(review["cpt_hash"])
        if result_id is not None:
            resolved_result = result_id
        else:
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


# --- S48c §4: no acceptance yet; author accepts the unchanged original ---


def test_no_acceptance_before_any_accept(clean_registry, monkeypatch) -> None:
    """S48c §4: fresh successful review exposes empty acceptance state."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_empty", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    body = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert body["calculation_state"] == "unchanged"
    assert body["is_accepted"] is False
    assert body["acceptance"] is None
    assert body["acceptances"] == []


def test_author_accepts_unchanged_original(clean_registry, monkeypatch) -> None:
    """S48c §4: author accepts the exact unchanged baseline/result/inputs."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_base", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    before = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert before["calculation_state"] == "unchanged"
    assert before["current_cpt_revision_id"] is None

    posted = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(before),
        headers=_auth_headers(csrf),
    )
    assert posted.status_code == 200, posted.text
    payload = posted.json()
    acceptance = payload["acceptance"]
    assert payload["is_accepted"] is True
    # Explicit record: actor/time/ids, baseline result, no revision.
    assert acceptance["question_run_id"] == run_id
    assert acceptance["baseline_id"] == before["baseline"]["id"]
    assert acceptance["cpt_revision_id"] is None
    assert acceptance["result_kind"] == "baseline"
    assert acceptance["result_id"] == before["baseline"]["id"]
    assert acceptance["actor_username"] == "dr_s48c_base"
    assert acceptance["created_at"].endswith("Z")
    assert len(acceptance["cpt_hash"]) == 64
    assert len(acceptance["input_hash"]) == 64
    assert "id" in acceptance and "encounter_id" in acceptance and "batch_id" in acceptance

    after = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after["is_accepted"] is True
    assert after["acceptance"] == acceptance
    assert after["acceptances"] == [acceptance]
    # Independent literal: unchanged originals keep the 80/20 root.
    assert after["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]


# --- S48c §4: wrong-author/admin denied without leak; 404 stays 404 ---


def test_wrong_author_and_admin_denied_without_leak(clean_registry, monkeypatch) -> None:
    """S48c §4: stranger/admin acceptance reads and writes are denied without content."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    admin_csrf = login.json()["csrf_token"]
    for username in ("dr_s48c_own", "dr_s48c_other"):
        created = admin.post(
            "/api/v1/physicians",
            json={"username": username, "password": "pw123"},
            headers=_auth_headers(admin_csrf),
        )
        assert created.status_code == 201, created.text
    own, own_csrf = _login_physician(clean_registry, monkeypatch, "dr_s48c_own")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s48c_other")
    encounter_id, revision = _setup_encounter(own, own_csrf, "0012345678")
    package = s48a_package()
    started = _start_batch(own, own_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    run_id = started.json()["question_runs"][0]["id"]
    outcome = _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))
    assert outcome["status"] == "succeeded", outcome

    owner_review = own.get(f"/api/v1/question-runs/{run_id}/review").json()
    body = _accept_body(owner_review)

    stranger_write = other.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=body,
        headers=_auth_headers(other_csrf),
    )
    assert stranger_write.status_code == 403, stranger_write.text
    assert "percentages" not in stranger_write.text and '"80"' not in stranger_write.text
    admin_write = admin.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=body,
        headers=_auth_headers(admin_csrf),
    )
    assert admin_write.status_code == 403, admin_write.text
    assert "percentages" not in admin_write.text and '"80"' not in admin_write.text

    stranger_read = other.get(f"/api/v1/question-runs/{run_id}/review")
    assert stranger_read.status_code == 403, stranger_read.text
    assert "percentages" not in stranger_read.text
    admin_read = admin.get(f"/api/v1/question-runs/{run_id}/review")
    assert admin_read.status_code in (401, 403), admin_read.text
    assert "percentages" not in admin_read.text

    # 404 stays 404 (missing run, and stranger POST to a missing run).
    assert own.get(f"/api/v1/question-runs/{uuid.uuid4()}/review").status_code == 404
    missing = own.post(
        f"/api/v1/question-runs/{uuid.uuid4()}/acceptance",
        json=body,
        headers=_auth_headers(own_csrf),
    )
    assert missing.status_code == 404, missing.text
    stranger_missing = other.post(
        f"/api/v1/question-runs/{uuid.uuid4()}/acceptance",
        json=body,
        headers=_auth_headers(other_csrf),
    )
    assert stranger_missing.status_code == 404, stranger_missing.text

    # Unauthenticated acceptance is denied.
    naked = TestClient(app)
    assert naked.post(f"/api/v1/question-runs/{run_id}/acceptance", json=body).status_code in (
        401,
        403,
    )

    # No stranger write created an acceptance.
    assert own.get(f"/api/v1/question-runs/{run_id}/review").json()["acceptances"] == []


# --- S48c §4: pending/recalculating, failed, and missing baselines rejected ---


def test_pending_recalculating_current_cannot_be_accepted(clean_registry, monkeypatch) -> None:
    """S48c §4: a queued (recalculating) revision has no successful result yet."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_pending", "0012345678"
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
    rev1 = posted.json()["revision"]

    mid = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert mid["calculation_state"] == "recalculating"
    assert mid["current_cpt_revision_id"] == rev1["id"]
    assert mid["input_freshness"]["stale"] is False

    body = _accept_body(mid, result_id=str(uuid.uuid4()))
    denied = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=body,
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "NO_SUCCESSFUL_RESULT"

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["is_accepted"] is False
    assert reread["acceptance"] is None
    assert reread["acceptances"] == []


def _absent_only_package() -> dict[str, Any]:
    """Variant with a single absent branch to force a public-seam local failure."""
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


def test_failed_current_cannot_be_accepted(clean_registry, monkeypatch) -> None:
    """S48c §4: a failed current revision blocks acceptance until retry/reset."""
    package = _absent_only_package()
    client, csrf, _, _, _, started = _new_physician_batch_with_package(
        clean_registry, monkeypatch, "dr_s48c_failed", "0012345678", package
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
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
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
    assert stub.calls == []

    body = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert body["calculation_state"] == "failed"
    assert body["current_cpt_revision_id"] == rev2["id"]

    attempt = _accept_body(body, result_id=str(uuid.uuid4()))
    denied = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=attempt,
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "NO_SUCCESSFUL_RESULT"

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["is_accepted"] is False
    assert reread["acceptances"] == []


def test_failed_original_has_nothing_to_accept(clean_registry, monkeypatch) -> None:
    """S48c §4: failed originals expose no baseline, so acceptance is rejected."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_nobase", "0012345678"
    )
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

    review = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert review["baseline"] is None
    assert review["adjustable"] is False
    assert review["is_accepted"] is False
    assert review["acceptance"] is None
    assert review["acceptances"] == []

    denied = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json={
            "baseline_id": str(uuid.uuid4()),
            "current_cpt_revision_id": None,
            "cpt_hash": "0" * 64,
            "result_id": str(uuid.uuid4()),
            "input_hash": str(review["input_freshness"]["current_fingerprint"]),
            "expected_review_revision": int(review["review_revision"]),
        },
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "NO_SUCCESSFUL_RESULT"
    assert client.get(f"/api/v1/question-runs/{run_id}/review").json()["acceptances"] == []


# --- S48c §4: stale inputs, mismatched references, stale pointers, idempotency ---


def test_stale_inputs_rejected_and_stay_rejected(clean_registry, monkeypatch) -> None:
    """S48c §4: relevant patient edits stale the review and block acceptance."""
    client, csrf, encounter_id, revision, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_stale", "0012345678"
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
    assert stub.calls == []

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

    denied = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(stale),
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "STALE_INPUTS"

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["is_accepted"] is False
    assert reread["acceptances"] == []


def test_mismatched_references_rejected_without_write(clean_registry, monkeypatch) -> None:
    """S48c §4: each forged reference is rejected without creating acceptance."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_mismatch", "0012345678"
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
    assert stub.calls == []

    live = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert live["calculation_state"] == "successfully_recalculated"
    good = _accept_body(live)

    for field, bad in (
        ("baseline_id", str(uuid.uuid4())),
        ("current_cpt_revision_id", str(uuid.uuid4())),
        ("cpt_hash", "0" * 64),
        ("result_id", str(uuid.uuid4())),
        ("input_hash", "0" * 64),
    ):
        forged = dict(good)
        forged[field] = bad
        denied = client.post(
            f"/api/v1/question-runs/{run_id}/acceptance",
            json=forged,
            headers=_auth_headers(csrf),
        )
        assert denied.status_code == 409, (field, denied.text)
        assert denied.json()["code"] == "REFERENCE_MISMATCH", (field, denied.text)

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["is_accepted"] is False
    assert reread["acceptances"] == []


def test_stale_pointer_and_idempotency(clean_registry, monkeypatch) -> None:
    """S48c §4: stale expected revisions are 412; idempotent replays never duplicate."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_idem", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    live = client.get(f"/api/v1/question-runs/{run_id}/review").json()

    stale_body = dict(_accept_body(live))
    stale_body["expected_review_revision"] = int(live["review_revision"]) + 1
    denied = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=stale_body,
        headers=_auth_headers(csrf),
    )
    assert denied.status_code == 412, denied.text
    assert denied.json()["code"] == "STALE_REVISION"
    assert client.get(f"/api/v1/question-runs/{run_id}/review").json()["acceptances"] == []

    first = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(live),
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48c-idem-0001"},
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["acceptance"]["id"]

    replay = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(live),
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48c-idem-0001"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["acceptance"]["id"] == first_id
    assert replay.json() == first.json()

    # Same key with a different body conflicts; re-accepting without a key
    # returns the same row instead of duplicating history.
    conflict_body = dict(_accept_body(live))
    conflict_body["input_hash"] = "0" * 64
    conflict = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=conflict_body,
        headers={**_auth_headers(csrf), "Idempotency-Key": "s48c-idem-0001"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    naked = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(live),
        headers=_auth_headers(csrf),
    )
    assert naked.status_code == 200, naked.text
    assert naked.json()["acceptance"]["id"] == first_id
    assert len(client.get(f"/api/v1/question-runs/{run_id}/review").json()["acceptances"]) == 1


# --- S48c §4: editing/reset invalidates; history retained; notes preserve ---


def test_edit_and_reset_invalidate_with_history_retained(clean_registry, monkeypatch) -> None:
    """S48c §4: later revisions stop matching; history stays; re-accept works."""
    import pytest as _pytest

    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_life", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    base = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    first_accept = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(base),
        headers=_auth_headers(csrf),
    )
    assert first_accept.status_code == 200, first_accept.text
    acc1 = first_accept.json()["acceptance"]
    assert acc1["result_kind"] == "baseline"

    # A later adjustment moves the pointer: the old acceptance no longer matches.
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
    rev1 = posted.json()["revision"]

    moved = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert moved["is_accepted"] is False
    assert moved["acceptance"] is None
    assert [a["id"] for a in moved["acceptances"]] == [acc1["id"]]
    assert moved["review_revision"] == 2

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []

    solved = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert solved["calculation_state"] == "successfully_recalculated"
    second_accept = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(solved),
        headers=_auth_headers(csrf),
    )
    assert second_accept.status_code == 200, second_accept.text
    acc2 = second_accept.json()["acceptance"]
    assert acc2["cpt_revision_id"] == rev1["id"]
    assert acc2["result_kind"] == "calculation"
    assert acc2["result_id"] == solved["calculation_result"]["id"]
    assert acc2["id"] != acc1["id"]
    # Independent literal: A 60/40 gives P(B=yes)=0.10*0.60+0.70*0.40=0.34.
    by_node = {
        str(entry.get("node_id")): entry for entry in solved["calculation_result"]["posteriors"]
    }
    assert by_node["B"]["probabilities"] == _pytest.approx([0.66, 0.34], abs=1e-6)

    accepted = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert accepted["is_accepted"] is True
    assert accepted["acceptance"] == acc2
    assert [a["id"] for a in accepted["acceptances"]] == [acc1["id"], acc2["id"]]

    # Reset creates a new revision: acceptance lapses again, history retained.
    reset = client.post(
        f"/api/v1/question-runs/{run_id}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    reset_rev = reset.json()["revision"]
    assert reset_rev["kind"] == "reset"

    after_reset = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after_reset["is_accepted"] is False
    assert after_reset["acceptance"] is None
    assert [a["id"] for a in after_reset["acceptances"]] == [acc1["id"], acc2["id"]]
    assert after_reset["review_revision"] == 3

    # The reset revision binds the verified baseline reuse: accepting it works
    # and records the calculation result (reused, not freshly executed).
    reused = after_reset["calculation_result"]
    assert reused is not None
    assert reused["reused_from_baseline_id"] == base["baseline"]["id"]
    third_accept = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(after_reset),
        headers=_auth_headers(csrf),
    )
    assert third_accept.status_code == 200, third_accept.text
    acc3 = third_accept.json()["acceptance"]
    assert acc3["cpt_revision_id"] == reset_rev["id"]
    assert acc3["result_kind"] == "calculation"
    assert acc3["result_id"] == reused["id"]

    final = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert final["is_accepted"] is True
    assert final["acceptance"] == acc3
    assert [a["id"] for a in final["acceptances"]] == [acc1["id"], acc2["id"], acc3["id"]]
    # Adjustment history is never deleted by reset or acceptance.
    assert len(final["revisions"]) == 2


def test_note_only_edit_preserves_acceptance(clean_registry, monkeypatch) -> None:
    """S48c §4 + plan §9.2: note edits change no fingerprint and keep acceptance."""
    client, csrf, encounter_id, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_note", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)

    live = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    accepted = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(live),
        headers=_auth_headers(csrf),
    )
    assert accepted.status_code == 200, accepted.text
    acc_id = accepted.json()["acceptance"]["id"]

    encounter = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": "Review note: probabilities look right."},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(encounter["revision"])},
    )
    assert noted.status_code == 201, noted.text

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["input_freshness"]["stale"] is False
    assert reread["is_accepted"] is True
    assert reread["acceptance"]["id"] == acc_id
    assert [a["id"] for a in reread["acceptances"]] == [acc_id]


# --- S48c §4: malformed bodies are 422; If-Match mismatch is 412 ---


def test_invalid_acceptance_bodies_rejected_422_without_write(clean_registry, monkeypatch) -> None:
    """S48c §4: malformed acceptance bodies are 422 and create no acceptance."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_422", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    live = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    good = _accept_body(live)

    variants: list[dict[str, Any]] = []
    bad_uuid = dict(good)
    bad_uuid["baseline_id"] = "not-a-uuid"
    variants.append(bad_uuid)
    bad_result = dict(good)
    bad_result["result_id"] = "not-a-uuid"
    variants.append(bad_result)
    bad_revision = dict(good)
    bad_revision["current_cpt_revision_id"] = "not-a-uuid"
    variants.append(bad_revision)
    empty_hash = dict(good)
    empty_hash["cpt_hash"] = ""
    variants.append(empty_hash)
    blank_input = dict(good)
    blank_input["input_hash"] = "   "
    variants.append(blank_input)
    extra_field = dict(good)
    extra_field["note"] = "forged"
    variants.append(extra_field)
    missing_field = dict(good)
    del missing_field["result_id"]
    variants.append(missing_field)

    for index, body in enumerate(variants):
        denied = client.post(
            f"/api/v1/question-runs/{run_id}/acceptance",
            json=body,
            headers=_auth_headers(csrf),
        )
        assert denied.status_code == 422, (index, body, denied.text)
        assert denied.json()["code"] in ("VALIDATION_FAILED", "INVALID_IF_MATCH")

    reread = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["is_accepted"] is False
    assert reread["acceptance"] is None
    assert reread["acceptances"] == []


def test_if_match_header_mismatch_is_412_and_matching_header_accepts(
    clean_registry, monkeypatch
) -> None:
    """S48c §4: If-Match/body disagreement is 412; matching header accepts."""
    client, csrf, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s48c_ifmatch", "0012345678"
    )
    _, run_id = _succeed_batch(clean_registry, client, package, started)
    live = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert live["review_revision"] == 1
    good = _accept_body(live)

    # Correct body but wrong header revision: stale write, never merged.
    mismatch = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=good,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert mismatch.status_code == 412, mismatch.text
    assert mismatch.json()["code"] == "STALE_REVISION"

    # Wrong body revision but correct header: still a mismatch, still 412.
    stale_body = dict(good)
    stale_body["expected_review_revision"] = 2
    mismatch2 = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=stale_body,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert mismatch2.status_code == 412, mismatch2.text

    assert client.get(f"/api/v1/question-runs/{run_id}/review").json()["acceptances"] == []

    # Matching header + body succeeds (header wins path, same value).
    accepted = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=good,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["is_accepted"] is True
