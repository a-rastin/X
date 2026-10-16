"""Cross-user, failure, and security acceptance (S60, seams T1/T6-T10).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio +
``DeterministicProviderEndpoint`` (real PostgreSQL, real auth/draft,
observable behavior only: no DB-row asserts, no internal mocks, no
private-helper mirror tests, no fake clinical screens). Synthetic two-node
A->B networks only (80/20, 90/10, 30/70); no clinical content. No test
shortcut creating a signable flag - every signable batch runs the real
pipeline.

Covers tasks.md S60 (FR-04, FR-16, FR-22, FR-36, FR-41-43, NFR-02/04) as
cross-cutting stitches over the owning suites (cited, not rebuilt):

- S51 ``test_record_races`` (15 T1/T8): archive/deactivation/races,
  wrong-author draft denial, shared demographics/chart.
- S47 ``test_recovery`` (T1/T8): mixed three-attempt exhaustion, stage
  resume, restart/lease fencing, lifecycle cancel.
- S44 ``test_queue`` (T1/T8): admission, lease/heartbeat, deployment
  generation fencing, fairness, restart retention.
- S49 ``test_signing`` (T1): exact acceptance + sign denial matrix.
- S53 ``test_exports`` (T1): escaping + CSV formula neutralization.
- S43 ``test_estimation`` (T7) + S41 ``test_scoped_transport`` (T6):
  payload exclusion, forged tool/grants, MCP isolation.
- S56 ``test_restore_commit`` (T10): restore fencing/rollback drill.

New here (only missing observable scenarios):

- §1: two physicians edit drafts for different patients while one
  provider run fails; saves/sliders stay responsive (200s, no coupling),
  acknowledged (accepted) revisions survive a worker restart; wrong-author
  draft/artifact reads+writes denied (401/403, no leak); shared
  demographics/chart remain available.
- §2: mixed three-attempt exhaustion -> unchanged-stage resume with
  Q2-only provider traffic and unique/preserved baselines; lease
  theft (deployment bump) + crash/restart with a controlled clock (no
  sleeps); mid-flight archive during an outbound delay cancels the late
  commit with no baseline; unarchive resumes; completed artifacts stay
  unique and preserved.
- §3: actual captured provider payloads exclude notes, unrelated
  sentinel fields, identifiers, and secrets; forged tool args and
  provider 401 fail safely with no baseline and no draft mutation;
  physician archive, anonymous access, missing CSRF, stale revisions,
  admin note reads, XXE network import, and non-http(s) provider
  destinations are denied observably (archive-path traversal stays
  covered by ``test_restore_validation``).
- §4: manual/stale/mismatched sign denials, reset invalidation, exact
  re-acceptance, idempotent sign replay, and escaped printable output in
  one encounter flow (formula neutralization stays covered by
  ``test_exports``).

BFRI: Fit 5 + Testability 5 - (Complexity 3 + Data 1 + Ops 2) = 4
(Moderate: new tests, no production change).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.http.test_commit_visibility import start_live_server, stop_live_server
from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service
from x_insight.mcp_server import TOOL_NAME
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


def _package(question_key: str, field_a: str, field_b: str) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s60-test-v1",
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


def _truncate(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE probability_acceptances, calculation_results, cpt_revisions, "
                "question_review_states, reasoning_grants, reasoning_job_attempts, "
                "reasoning_jobs, reasoning_fairness, original_baselines, "
                "proposal_snapshots, question_runs, generation_batches, "
                "encounter_addenda, signed_encounter_snapshots, secondary_plans, "
                "notes, encounters, patients, idempotency_records, sessions, users, "
                "audit_events, ddi_dataset_releases, ddi_source_documents, "
                "ddi_concepts, ddi_interaction_evidence CASCADE"
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


def _new_physician(clean_registry, monkeypatch, username: str):
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
    return admin, login.json()["csrf_token"]


def _login_physician(clean_registry, monkeypatch, username: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def _setup_encounter(
    client: TestClient, csrf: str, identifier: str, values: dict[str, Any]
) -> tuple[str, str, int]:
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
    patient_id = created.json()["patient"]["id"]
    encounter_id = created.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": values}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    return patient_id, encounter_id, int(saved.json()["revision"])


def _post_note(
    client: TestClient, csrf: str, encounter_id: str, page: str, note_text: str, revision: int
):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": page, "text": note_text},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _start_batches(
    client: TestClient, csrf: str, encounter_id: str, revision: int, packages: list[dict[str, Any]]
):
    body = {"packages": packages} if len(packages) != 1 else {"package": packages[0]}
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json=body,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _run_with_script(
    engine: Any,
    script: list[dict[str, Any]],
    at: Any,
    timeout: float = 10.0,
    worker_id: str | None = None,
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
            engine,
            adapter,
            now=at,
            database_url=db_module.get_test_database_url(),
            **({"worker_id": worker_id} if worker_id else {}),
        )
        captured = list(endpoint.captured)
    finally:
        endpoint.stop()
    return outcome, captured


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


# --- S60 §1: two physicians, different patients, one provider run fails ---


def test_two_physicians_isolated_while_one_run_fails(clean_registry, monkeypatch) -> None:
    """S60 §1: A's mixed-failure run never blocks B's saves/sliders/acceptance.

    A's batch exhausts timeout -> invalid CPT -> 429 (exactly three total
    attempts, failed, no baseline). While A stays failed, B saves, adjusts,
    recalculates locally, and accepts; a worker restart keeps B's accepted
    revision/result/baseline identical and A's ledger untouched.
    Wrong-author draft/artifact reads+writes are denied without leaks;
    shared directory/chart stay available to the other physician.
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_a")
    _new_physician(engine, monkeypatch, "dr_s60_b")
    client_a, csrf_a = _login_physician(engine, monkeypatch, "dr_s60_a")
    client_b, csrf_b = _login_physician(engine, monkeypatch, "dr_s60_b")

    patient_a, encounter_a, revision_a = _setup_encounter(
        client_a, csrf_a, "0012345601", {"h_a": "yes", "h_b": "no"}
    )
    noted_a = _post_note(
        client_a, csrf_a, encounter_a, "diagnosis", "SENTINEL-NOTE-A-SECRET", revision_a
    )
    assert noted_a.status_code == 201, noted_a.text
    revision_a = int(noted_a.json()["revision"])
    _patient_b, encounter_b, revision_b = _setup_encounter(
        client_b, csrf_b, "0012345602", {"h_a": "no", "h_b": "yes"}
    )
    noted_b = _post_note(
        client_b, csrf_b, encounter_b, "diagnosis", "SENTINEL-NOTE-B-SECRET", revision_b
    )
    assert noted_b.status_code == 201, noted_b.text
    revision_b = int(noted_b.json()["revision"])

    started_a = _start_batches(
        client_a, csrf_a, encounter_a, revision_a, [_package("s60_a_q", "h_a", "h_b")]
    )
    assert started_a.status_code == 202, started_a.text
    batch_a = started_a.json()["batch"]["id"]
    run_a = started_a.json()["question_runs"][0]["id"]

    # A's run fails with a mixed script: slow-invalid (timeout) -> invalid -> 429.
    t0 = contracts.utcnow()
    _run_with_script(
        engine,
        [{"type": "delay", "seconds": 2.0, "then": {"type": "final", "cpt": _invalid_cpt()}}],
        t0,
        timeout=1.0,
    )
    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=70))
    failed, _ = _run_with_script(
        engine,
        [{"type": "status", "status": 429, "body": {"error": "slow down"}}],
        t0 + timedelta(seconds=140),
    )
    assert failed["status"] == "failed", failed
    view_a = client_a.get(f"/api/v1/generation-batches/{batch_a}").json()
    assert view_a["job"]["status"] == "failed", view_a["job"]
    assert view_a["attempts"] == 3, view_a
    assert view_a["baseline"] is None

    # B's saves stay responsive while A's run sits failed.
    saved_b = client_b.patch(
        f"/api/v1/encounters/{encounter_b}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "yes"}}, "gate": "true"}},
        headers={**_auth_headers(csrf_b), "If-Match": contracts.format_etag(revision_b)},
    )
    assert saved_b.status_code == 200, saved_b.text
    revision_b = int(saved_b.json()["revision"])

    started_b = _start_batches(
        client_b, csrf_b, encounter_b, revision_b, [_package("s60_b_q", "h_a", "h_b")]
    )
    assert started_b.status_code == 202, started_b.text
    run_b = started_b.json()["question_runs"][0]["id"]
    done_b, _ = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=200)
    )
    assert done_b["status"] == "succeeded", done_b

    # B's sliders stay responsive: adjustment -> local recalculation -> exact acceptance.
    posted = client_b.post(
        f"/api/v1/question-runs/{run_b}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_b),
    )
    assert posted.status_code == 200, posted.text
    revision_id = posted.json()["revision"]["id"]
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    local = worker_module.run_once(engine, stub)
    assert local["status"] == "succeeded", local
    assert local.get("job_class") == "local_calculation"
    assert stub.calls == []
    review_b = client_b.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert review_b["calculation_result"]["cpt_revision_id"] == revision_id
    accepted = client_b.post(
        f"/api/v1/question-runs/{run_b}/acceptance",
        json=_accept_body(review_b),
        headers=_auth_headers(csrf_b),
    )
    assert accepted.status_code == 200, accepted.text
    acceptance_id = accepted.json()["acceptance"]["id"]

    # Worker restart: fresh adapter, later clock. B's acknowledged revision
    # survives; A's failed ledger is untouched; nothing duplicates.
    restart = worker_module.run_once(engine, provider_module.ControlledStubAdapter(mode="succeed"))
    assert restart["status"] == "idle", restart
    reread_b = client_b.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert reread_b["acceptance"] is not None, reread_b
    assert reread_b["acceptance"]["id"] == acceptance_id
    assert reread_b["calculation_result"]["id"] == review_b["calculation_result"]["id"]
    assert reread_b["baseline"]["id"] == review_b["baseline"]["id"]
    assert reread_b["input_freshness"]["stale"] is False
    reread_a = client_a.get(f"/api/v1/generation-batches/{batch_a}").json()
    assert reread_a["job"]["status"] == "failed"
    assert reread_a["attempts"] == 3
    assert reread_a["baseline"] is None

    # Wrong-author draft/artifact reads+writes denied without leaks.
    stranger_read = client_b.get(f"/api/v1/encounters/{encounter_a}")
    assert stranger_read.status_code == 403, stranger_read.text
    assert "SENTINEL-NOTE-A-SECRET" not in stranger_read.text
    assert client_a.get(f"/api/v1/encounters/{encounter_b}").status_code == 403
    stranger_review = client_b.get(f"/api/v1/question-runs/{run_a}/review")
    assert stranger_review.status_code == 403, stranger_review.text
    assert "percentages" not in stranger_review.text
    stranger_batch = client_b.get(f"/api/v1/generation-batches/{batch_a}")
    assert stranger_batch.status_code == 403, stranger_batch.text
    stranger_write = client_a.post(
        f"/api/v1/question-runs/{run_b}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "70",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_a),
    )
    assert stranger_write.status_code == 403, stranger_write.text
    assert "percentages" not in stranger_write.text
    anonymous = TestClient(app)
    assert anonymous.get(f"/api/v1/encounters/{encounter_a}").status_code == 401

    # Shared demographics/chart remain available to the other physician.
    directory = client_b.get("/api/v1/patients?query=0012345601&limit=25").json()
    assert "0012345601" in {item["identifier"] for item in directory["items"]}
    assert "SENTINEL-NOTE-A-SECRET" not in directory["items"].__str__()
    chart = client_b.get(f"/api/v1/patients/{patient_a}/chart")
    assert chart.status_code == 200, chart.text
    assert "SENTINEL-NOTE-A-SECRET" not in chart.text
    assert "0012345601" in chart.text


# --- S60 §2a: mixed exhaustion -> unchanged-stage resume, artifacts preserved ---


def test_mixed_exhaustion_then_stage_resume_keeps_completed_artifacts(
    clean_registry, monkeypatch
) -> None:
    """S60 §2: timeout -> invalid -> 429 exhausts Q2; retry is Q2-only.

    Q1's completed baseline survives exhaustion and the retry; the retry
    batch carries Q1 without a new provider request; completed question
    artifacts are unique per question and preserved (old batch intact).
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_exhaust")
    client, csrf = _login_physician(engine, monkeypatch, "dr_s60_exhaust")
    _, encounter_id, revision = _setup_encounter(
        client, csrf, "0012345603", {"h1a": "yes", "h1b": "no", "h2a": "yes", "h2b": "no"}
    )
    packages = [_package("s60_q1", "h1a", "h1b"), _package("s60_q2", "h2a", "h2b")]

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
    _run_with_script(
        engine,
        [{"type": "delay", "seconds": 2.0, "then": {"type": "final", "cpt": _invalid_cpt()}}],
        t0 + timedelta(seconds=5),
        timeout=1.0,
    )
    _run_with_script(engine, [{"type": "final", "cpt": _invalid_cpt()}], t0 + timedelta(seconds=80))
    out_fail, _ = _run_with_script(
        engine,
        [{"type": "status", "status": 429, "body": {"error": "slow down"}}],
        t0 + timedelta(seconds=160),
    )
    assert out_fail["status"] == "failed", out_fail
    mid = client.get(f"/api/v1/generation-batches/{batch1}").json()
    assert mid["baselines"][0]["baseline"] is not None
    assert "s60_q1 absent" in mid["baselines"][0]["baseline"]["section_text"]
    assert mid["baselines"][1]["baseline"] is None
    assert mid["workflow"]["complete"] is False

    retry = _start()
    assert retry.status_code == 202, retry.text
    batch2 = retry.json()["batch"]["id"]
    assert batch2 != batch1
    carried = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert carried["baselines"][0]["baseline"] is not None
    assert carried["baselines"][1]["baseline"] is None
    assert [j["status"] for j in carried["jobs"]] == ["queued"]

    out2, cap2 = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=300)
    )
    assert out2["status"] == "succeeded", out2
    assert len(cap2) == 1, "retry estimates only the failed stage"
    blob = str(cap2[0])
    assert "s60_q2" in blob
    assert "s60_q1" not in blob, "earlier success costs no new request"

    done = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert done["baselines"][0]["baseline"] is not None
    assert done["baselines"][1]["baseline"] is not None
    by_key = {b["question_key"]: b["baseline"] for b in done["baselines"]}
    assert by_key["s60_q1"]["id"] != by_key["s60_q2"]["id"], "artifacts unique per question"
    old = client.get(f"/api/v1/generation-batches/{batch1}").json()
    assert old["baselines"][0]["baseline"] is not None, "prior sections preserved"
    assert old["baselines"][1]["baseline"] is None, "later question stays pending on batch1"
    # New runs establish new originals (S48d §2): the carried Q1 baseline is a
    # new row that links provenance instead of silently transferring the old id.
    assert (
        by_key["s60_q1"]["provenance"].get("retried_from_baseline_id")
        == old["baselines"][0]["baseline"]["id"]
    )
    assert (
        by_key["s60_q1"]["section_text"]
        == old["baselines"][0]["baseline"]["section_text"]
        == mid["baselines"][0]["baseline"]["section_text"]
    )


# --- S60 §2b: lease theft + crash/restart + mid-flight archive fence ---


def test_lease_theft_crash_and_archive_fence_with_controlled_timing(
    clean_registry, monkeypatch
) -> None:
    """S60 §2: old tokens fenced, restarts keep counts, archive cancels late commits.

    A deployment-generation bump between attempts fences the older worker
    without resetting the attempt ledger; a mid-flight archive (signalled
    by the first provider exchange, so the claim is complete and the
    outbound request is in flight - never slept, no startup race) cancels
    the job with no baseline; retry while archived is denied; archive-first
    cancels at claim with no provider exchange; after unarchive the author
    retries to success and a restart keeps the single baseline.
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_fence")
    client, csrf = _login_physician(engine, monkeypatch, "dr_s60_fence")
    patient_id, encounter_id, revision = _setup_encounter(
        client, csrf, "0012345604", {"h_a": "yes", "h_b": "no"}
    )
    package = _package("s60_fence_q", "h_a", "h_b")
    started = _start_batches(client, csrf, encounter_id, revision, [package])
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]
    t0 = contracts.utcnow()

    out_a, _ = _run_with_script(
        engine, [{"type": "final", "cpt": _invalid_cpt()}], t0, worker_id="worker-s60-a"
    )
    assert out_a["status"] == "queued", out_a
    # Lease theft: the deployment moves on; the older worker's token is fenced.
    with engine.begin() as connection:
        connection.execute(text("UPDATE reasoning_deployment SET generation = 2 WHERE id = 1"))
    out_b, _ = _run_with_script(
        engine,
        [{"type": "final", "cpt": _invalid_cpt()}],
        t0 + timedelta(seconds=70),
        worker_id="worker-s60-b",
    )
    assert out_b["status"] == "queued", out_b
    mid = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert mid["attempts"] == 2, "theft/crash must not reset the attempt ledger"
    assert mid["baseline"] is None

    # Mid-flight archive during an externally delayed outbound request.
    admin = TestClient(app)
    admin_login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_csrf = admin_login.json()["csrf_token"]

    holder: dict[str, Any] = {}
    request_in_flight = threading.Event()

    def _run_midflight() -> None:
        # Inline endpoint ownership with the public on_exchange hook: the
        # first provider exchange signals the main thread, so the claim is
        # necessarily complete and the outbound request is in flight
        # (inside the 3s delay). No sleeps, no startup race, no privates.
        endpoint = provider_module.DeterministicProviderEndpoint(
            script=[
                {
                    "type": "delay",
                    "seconds": 3.0,
                    "then": {"type": "final", "cpt": _valid_cpt()},
                }
            ],
            on_exchange=lambda _body: request_in_flight.set(),
        )
        url = endpoint.start()
        try:
            config = provider_module.ProviderConfig(
                endpoint_url=url, model="test-model", capability="schema", timeout_seconds=30.0
            )
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            holder["outcome"] = worker_module.run_once(
                engine,
                adapter,
                now=t0 + timedelta(seconds=140),
                database_url=db_module.get_test_database_url(),
                worker_id="worker-s60-c",
            )
            holder["captured"] = list(endpoint.captured)
        finally:
            endpoint.stop()

    thread = threading.Thread(target=_run_midflight)
    thread.start()
    assert request_in_flight.wait(timeout=30), "provider exchange must start"
    archived = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s60-fence-arch-0001",
        },
    )
    assert archived.status_code == 200, archived.text
    thread.join(timeout=60)
    assert holder.get("outcome") is not None, "worker thread must finish"
    assert holder["outcome"]["status"] == "cancelled", holder["outcome"]
    assert len(holder.get("captured", [])) == 1, "the archived request was mid-flight"
    cancelled = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert cancelled["baseline"] is None, "no late acceptance after archive"
    assert cancelled["job"]["status"] in ("cancelled", "failed"), cancelled["job"]
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["baseline"] is None
    assert review.json()["adjustable"] is False

    # Retry while archived is denied; generation stays blocked until unarchive.
    retry_blocked = _start_batches(client, csrf, encounter_id, revision, [package])
    assert retry_blocked.status_code == 409, retry_blocked.text
    unarchived = admin.post(
        f"/api/v1/patients/{patient_id}/unarchive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(2),
            "Idempotency-Key": "s60-fence-unarch-0001",
        },
    )
    assert unarchived.status_code == 200, unarchived.text

    retry = _start_batches(client, csrf, encounter_id, revision, [package])
    assert retry.status_code == 202, retry.text
    batch2 = retry.json()["batch"]["id"]
    assert batch2 != batch_id

    # Archive-first is the other valid serial outcome: the claim cancels
    # the archived job (idle, no provider exchange, no baseline).
    archived2 = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(3),
            "Idempotency-Key": "s60-fence-arch-0002",
        },
    )
    assert archived2.status_code == 200, archived2.text
    skipped, skipped_captured = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=240)
    )
    assert skipped["status"] == "idle", skipped
    assert skipped_captured == [], "archived jobs cost no provider exchange"
    parked = client.get(f"/api/v1/generation-batches/{batch2}").json()
    assert parked["job"]["status"] == "cancelled", parked["job"]
    assert parked["baseline"] is None
    unarchived2 = admin.post(
        f"/api/v1/patients/{patient_id}/unarchive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(4),
            "Idempotency-Key": "s60-fence-unarch-0002",
        },
    )
    assert unarchived2.status_code == 200, unarchived2.text

    # The cancelled job never resumes: the author retries to a new bounded
    # batch, succeeds, and a restart keeps the single committed artifact.
    retry2 = _start_batches(client, csrf, encounter_id, revision, [package])
    assert retry2.status_code == 202, retry2.text
    batch3 = retry2.json()["batch"]["id"]
    assert batch3 not in (batch_id, batch2)
    out_ok, _ = _run_with_script(
        engine, [{"type": "final", "cpt": _valid_cpt()}], t0 + timedelta(seconds=300)
    )
    assert out_ok["status"] == "succeeded", out_ok
    done = client.get(f"/api/v1/generation-batches/{batch3}").json()
    baseline_id = done["baseline"]["id"]
    assert done["baselines"][0]["baseline"]["id"] == baseline_id
    assert len(done["baselines"]) == 1, "one unique artifact for the retried run"

    idle = worker_module.run_once(engine, provider_module.ControlledStubAdapter(mode="succeed"))
    assert idle["status"] == "idle", idle
    reread = client.get(f"/api/v1/generation-batches/{batch3}").json()
    assert reread["baseline"]["id"] == baseline_id, "restart preserves the committed artifact"
    assert len(reread["baselines"]) == 1


# --- S60 §3: payload inspection + forgery/role/revision/XML/destination denial ---


def test_payloads_exclude_notes_sentinels_and_forgeries_denied_without_mutation(
    clean_registry, monkeypatch
) -> None:
    """S60 §3: actual provider payloads carry only pinned inputs; forgeries fail safely.

    The draft holds a note with markup, an unrelated sentinel history
    field, sentinel names/phone/identifier. The captured external request
    contains none of them. Forged tool args and a provider 401 fail the
    attempt with no baseline and no draft mutation. Role/CSRF/revision,
    admin note reads, XXE import, and non-http(s) destinations are denied
    observably; the draft revision is unchanged by every denied attempt.
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_sec")
    client, csrf = _login_physician(engine, monkeypatch, "dr_s60_sec")
    made = client.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345605",
            "given_name": "SentinelGiven",
            "family_name": "SentinelFamily",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
            "phone": "SENTINEL-PHONE-999",
        },
        headers=_auth_headers(csrf),
    )
    assert made.status_code == 201, made.text
    patient_id = made.json()["patient"]["id"]
    encounter_id = made.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {
                        "h_a": "yes",
                        "h_b": "no",
                        "h_unrelated": "SENTINEL-UNRELATED-FIELD",
                    }
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    noted = _post_note(
        client, csrf, encounter_id, "diagnosis", "SENTINEL-NOTE-SECRET <b>markup</b>", revision
    )
    assert noted.status_code == 201, noted.text
    revision = int(noted.json()["revision"])

    package = _package("s60_sec_q", "h_a", "h_b")
    started = _start_batches(client, csrf, encounter_id, revision, [package])
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]
    t0 = contracts.utcnow()
    done, captured = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert done["status"] == "succeeded", done
    assert len(captured) == 1
    blob = str(captured[0])
    assert "Estimate every CPT" in blob
    assert "get_question_patient_inputs" in blob
    for leaked in (
        "SentinelGiven",
        "SentinelFamily",
        "0012345605",
        "SENTINEL-PHONE-999",
        "SENTINEL-NOTE-SECRET",
        "SENTINEL-UNRELATED-FIELD",
        "markup",
    ):
        assert leaked not in blob, f"provider payload leaked {leaked!r}"

    # Forged tool args fail safely: no baseline, no draft mutation.
    _, encounter_forge, revision_forge = _setup_encounter(
        client, csrf, "0012345606", {"h_a": "yes", "h_b": "no"}
    )
    started_forge = _start_batches(client, csrf, encounter_forge, revision_forge, [package])
    assert started_forge.status_code == 202, started_forge.text
    batch_forge = started_forge.json()["batch"]["id"]
    forged, _ = _run_with_script(
        engine,
        [
            {
                "type": "tool",
                "calls": [
                    {
                        "id": "call_forged",
                        "name": "get_question_patient_inputs",
                        "arguments": {"projection": "spoofed"},
                    }
                ],
            },
            {"type": "final", "cpt": _valid_cpt()},
        ],
        t0 + timedelta(seconds=70),
    )
    assert forged["status"] in ("failed", "queued"), forged
    view_forge = client.get(f"/api/v1/generation-batches/{batch_forge}").json()
    assert view_forge["baseline"] is None, "forged tool args must not produce a section"

    # Valid MCP tool exchange + echoed MCP result carry only the bound
    # projection: both outbound exchanges exclude notes, unrelated
    # sentinels, identifiers, secrets, and any grant key.
    _, encounter_tool, revision_tool = _setup_encounter(
        client, csrf, "0012345608", {"h_a": "yes", "h_b": "no"}
    )
    started_tool = _start_batches(client, csrf, encounter_tool, revision_tool, [package])
    assert started_tool.status_code == 202, started_tool.text
    batch_tool = started_tool.json()["batch"]["id"]
    bridged, bridged_captured = _run_with_script(
        engine,
        [
            {"type": "tool", "calls": [{"id": "call_s60", "name": TOOL_NAME, "arguments": {}}]},
            {"type": "final", "cpt": _valid_cpt()},
        ],
        t0 + timedelta(seconds=105),
    )
    assert bridged["status"] == "succeeded", bridged
    assert len(bridged_captured) == 2, "tool call + final share one attempt"
    for exchange in bridged_captured:
        blob = json.dumps(exchange, sort_keys=True, separators=(",", ":"))
        for leaked in (
            "SentinelGiven",
            "SentinelFamily",
            "0012345605",
            "0012345608",
            "SENTINEL-PHONE-999",
            "SENTINEL-NOTE-SECRET",
            "SENTINEL-UNRELATED-FIELD",
            "grant_token",
        ):
            assert leaked not in blob, f"tool-bridged exchange leaked {leaked!r}"
    assert "get_question_patient_inputs" in str(bridged_captured[0])
    second = bridged_captured[1]
    tool_results = [
        m for m in second.get("messages", []) if isinstance(m, dict) and m.get("role") == "tool"
    ]
    assert len(tool_results) == 1, "echoed MCP result must be present"
    assert tool_results[0].get("tool_call_id") == "call_s60"
    echoed = str(tool_results[0].get("content", ""))
    assert "projection_hash" in echoed, "MCP result stays bound to its projection"
    for leaked in (
        "SENTINEL-NOTE-SECRET",
        "SENTINEL-UNRELATED-FIELD",
        "SENTINEL-PHONE-999",
        "grant_token",
    ):
        assert leaked not in echoed, f"echoed MCP result leaked {leaked!r}"
    # Stored provider responses hold only percentages: no secrets logged.
    stored = client.get(f"/api/v1/generation-batches/{batch_tool}").json()
    raw_blob = json.dumps(stored["baselines"][0]["baseline"]["raw_response"], sort_keys=True)
    for leaked in ("SENTINEL", "grant_token", "0012345605"):
        assert leaked not in raw_blob, f"stored raw response leaked {leaked!r}"

    # Provider 401 fails safely with no baseline.
    _, encounter_auth, revision_auth = _setup_encounter(
        client, csrf, "0012345607", {"h_a": "yes", "h_b": "no"}
    )
    started_auth = _start_batches(client, csrf, encounter_auth, revision_auth, [package])
    assert started_auth.status_code == 202, started_auth.text
    batch_auth = started_auth.json()["batch"]["id"]
    authed, _ = _run_with_script(
        engine,
        [{"type": "status", "status": 401, "body": {"error": "bad key"}}],
        t0 + timedelta(seconds=140),
    )
    assert authed["status"] in ("failed", "queued"), authed
    assert client.get(f"/api/v1/generation-batches/{batch_auth}").json()["baseline"] is None

    # HTTP denial matrix: nothing mutates the draft (revision pinned).
    revision_now = client.get(f"/api/v1/encounters/{encounter_id}").json()["encounter"]["revision"]
    assert (
        client.post(
            f"/api/v1/patients/{patient_id}/archive",
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 403
    )
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}").status_code == 401
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={
                "draft_data": {
                    "history": {"values": {"h_a": "yes", "h_b": "no"}},
                    "gate": "true",
                }
            },
            headers={"If-Match": contracts.format_etag(revision_now)},
        ).status_code
        == 403
    )
    stale = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision_now - 1)},
    )
    assert stale.status_code == 412, stale.text
    stranger_write = client.post(
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
    assert stranger_write.status_code == 200, stranger_write.text  # author: control case
    admin = TestClient(app)
    admin_login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_csrf = admin_login.json()["csrf_token"]
    assert admin.get(
        f"/api/v1/question-runs/{run_id}/review",
        headers=_auth_headers(admin_csrf),
    ).status_code in (401, 403)
    assert (
        admin.get(
            f"/api/v1/encounters/{encounter_id}/notes",
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 403
    )
    xxe = admin.post(
        "/api/v1/networks",
        json={
            "name": "xxe-probe",
            "xml_text": (
                '<?xml version="1.0"?>'
                '<!DOCTYPE BIF [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                "<BIF/>"
            ),
        },
        headers=_auth_headers(admin_csrf),
    )
    assert xxe.status_code == 422, xxe.text
    with pytest.raises(ValueError):
        provider_module.ProviderConfig(endpoint_url="ftp://internal-host/x", model="m")
    with pytest.raises(ValueError):
        provider_module.ProviderConfig(endpoint_url="file:///etc/passwd", model="m")
    after = client.get(f"/api/v1/encounters/{encounter_id}").json()["encounter"]["revision"]
    assert after == revision_now, "denied attempts must not mutate the draft"
    assert client.get(f"/api/v1/generation-batches/{batch_id}").json()["baseline"] is not None


# --- S60 §4: sign denials -> exact acceptance -> escaped outputs ---


def test_sign_denials_then_exact_acceptance_and_safe_outputs(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S60 §4: failed/stale/manual denials, reset invalidation, exact sign, safe report.

    One signed encounter flow: empty (manual) sign denied, stale
    acceptance after a slider edit denied, reset invalidates acceptance so
    the replayed sign is denied, exact re-acceptance signs atomically, an
    idempotent replay preserves the single signature, and the printable
    report escapes physician markup. CSV formula neutralization and export
    permissions stay covered by ``test_exports`` (regression).
    """
    from tests.http.test_signing import (
        _sign_body_after_accept,
        _signable_two_question_setup,
    )

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s60_sign"
    )
    assert client.get(f"/api/v1/generation-batches/{batch['id']}").json()["proposal"] is not None

    # Manual sign (no acceptances) is denied.
    def _sign(payload: dict[str, Any], key: str):
        return client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=payload,
            headers={
                **_auth_headers(csrf),
                "If-Match": contracts.format_etag(revision),
                "Idempotency-Key": key,
            },
        )

    manual = _sign(
        {
            "expected_encounter_revision": int(revision),
            "expected_plan_revision": 1,
            "batch_id": str(batch["id"]),
            "acceptances": [],
        },
        "s60-sign-manual",
    )
    assert manual.status_code == 409, manual.text

    # Exact acceptance of both current results.
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for qkey in ("s49_q1", "s49_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[qkey]['id']}/review").json()
        assert review["input_freshness"]["stale"] is False
        posted = client.post(
            f"/api/v1/question-runs/{runs[qkey]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (qkey, posted.text)
        run_to_acc[runs[qkey]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[qkey]["id"]] = int(posted.json()["review_revision"])

    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={
            "text": "Secondary: continue <b>care</b> & follow up =SUM(A1:A2)",
            "expected_plan_revision": 1,
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    sign_payload = _sign_body_after_accept(revision, 2, batch["id"], run_to_acc, run_to_rev)

    # Stale acceptance: a slider edit moves the revision; the old sign is denied.
    edited = client.post(
        f"/api/v1/question-runs/{runs['s49_q1']['id']}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert edited.status_code == 200, edited.text
    stale_sign = _sign(sign_payload, "s60-sign-stale")
    assert stale_sign.status_code == 412, stale_sign.text
    assert stale_sign.json()["code"] == "STALE_REVISION"
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []

    # Exact re-acceptance of the recalculated current result, then sign.
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    fresh = client.get(f"/api/v1/question-runs/{runs['s49_q1']['id']}/review").json()
    assert fresh["calculation_result"] is not None
    reposted = client.post(
        f"/api/v1/question-runs/{runs['s49_q1']['id']}/acceptance",
        json=_accept_body(fresh),
        headers=_auth_headers(csrf),
    )
    assert reposted.status_code == 200, reposted.text
    run_to_acc[runs["s49_q1"]["id"]] = reposted.json()["acceptance"]
    run_to_rev[runs["s49_q1"]["id"]] = int(reposted.json()["review_revision"])
    sign_payload = _sign_body_after_accept(revision, 2, batch["id"], run_to_acc, run_to_rev)

    # Reset on the other question invalidates its acceptance: sign denied.
    reset = client.post(
        f"/api/v1/question-runs/{runs['s49_q2']['id']}/reset",
        json={"expected_review_revision": run_to_rev[runs["s49_q2"]["id"]]},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    denied_after_reset = _sign(sign_payload, "s60-sign-reset")
    assert denied_after_reset.status_code == 412, denied_after_reset.text
    assert denied_after_reset.json()["code"] == "STALE_REVISION"
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []

    review2 = client.get(f"/api/v1/question-runs/{runs['s49_q2']['id']}/review").json()
    reposted2 = client.post(
        f"/api/v1/question-runs/{runs['s49_q2']['id']}/acceptance",
        json=_accept_body(review2),
        headers=_auth_headers(csrf),
    )
    assert reposted2.status_code == 200, reposted2.text
    run_to_acc[runs["s49_q2"]["id"]] = reposted2.json()["acceptance"]
    run_to_rev[runs["s49_q2"]["id"]] = int(reposted2.json()["review_revision"])
    sign_payload = _sign_body_after_accept(revision, 2, batch["id"], run_to_acc, run_to_rev)

    signed = _sign(sign_payload, "s60-sign-ok")
    assert signed.status_code == 200, signed.text
    snapshot_id = signed.json()["snapshot"]["id"]

    # Idempotent replay preserves the single signature.
    replay = _sign(sign_payload, "s60-sign-ok")
    assert replay.status_code == 200, replay.text
    assert replay.json()["snapshot"]["id"] == snapshot_id

    # Printable report escapes physician markup; identifiers stay exact.
    # The report route is admin-only (S53); physicians read the shared chart.
    chart_after = client.get(f"/api/v1/patients/{patient_id}/chart")
    assert chart_after.status_code == 200, chart_after.text
    assert len(chart_after.json()["signed_snapshots"]) == 1
    admin = TestClient(app)
    admin_login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_csrf = admin_login.json()["csrf_token"]
    report = admin.get(f"/api/v1/patients/{patient_id}/report", headers=_auth_headers(admin_csrf))
    assert report.status_code == 200, report.text
    assert "<b>care</b>" not in report.text
    assert "&lt;b&gt;care&lt;/b&gt;" in report.text
    assert "0012345678" in report.text
    assert "dr_s60_sign" in report.text
    assert "private" in report.headers.get("cache-control", "").lower()
    assert "no-store" in report.headers.get("cache-control", "").lower()

    # Export permissions: physician denied, admin allowed, no secrets.
    assert client.get("/api/v1/exports/patients.csv").status_code == 403
    exported = admin.get("/api/v1/exports/patients.csv")
    assert exported.status_code == 200, exported.text
    assert b"0012345678" in exported.content
    assert b"password" not in exported.content.lower()
    assert b"$2" not in exported.content, "no password hash in CSV"


# --- S60 §1 (review): real overlap while a provider request is held ---


def test_overlapping_provider_hold_with_restart_durable_reread(clean_registry, monkeypatch) -> None:
    """S60 §1: B acknowledges during A's held attempt; restart rereads durably.

    A's provider attempt is HELD open (5s delay, ``on_exchange``-synced so
    the claim is complete and the request outstanding), and WHILE it is
    held B saves, adjusts, recalculates locally, and accepts — every one
    acknowledged before the hold releases. A worker SUBPROCESS (separate
    OS process, frozen clock) then observes the durable state, and the API
    server is restarted twice (new instances, rebuilt engines) with
    byte-identical rereads of B's acceptance and A's ledger. No sleeps:
    the exchange event and joins order everything.
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_ov_a")
    _new_physician(engine, monkeypatch, "dr_s60_ov_b")
    client_a, csrf_a = _login_physician(engine, monkeypatch, "dr_s60_ov_a")
    client_b, csrf_b = _login_physician(engine, monkeypatch, "dr_s60_ov_b")
    _, encounter_a, revision_a = _setup_encounter(
        client_a, csrf_a, "0012345613", {"h_a": "yes", "h_b": "no"}
    )
    _, encounter_b, revision_b = _setup_encounter(
        client_b, csrf_b, "0012345614", {"h_a": "no", "h_b": "yes"}
    )

    started_b = _start_batches(
        client_b, csrf_b, encounter_b, revision_b, [_package("s60_ov_b", "h_a", "h_b")]
    )
    assert started_b.status_code == 202, started_b.text
    batch_b = started_b.json()["batch"]["id"]
    run_b = started_b.json()["question_runs"][0]["id"]
    t0 = contracts.utcnow()
    # B succeeds first while it is the only eligible job; A starts after,
    # so the hold below deterministically pins A's attempt (run_once has
    # no question filter — oldest-eligible would otherwise take A first).
    done_b, _ = _run_with_script(engine, [{"type": "final", "cpt": _valid_cpt()}], t0)
    assert done_b["status"] == "succeeded", done_b
    assert client_b.get(f"/api/v1/generation-batches/{batch_b}").json()["baseline"] is not None
    started_a = _start_batches(
        client_a, csrf_a, encounter_a, revision_a, [_package("s60_ov_a", "h_a", "h_b")]
    )
    assert started_a.status_code == 202, started_a.text
    batch_a = started_a.json()["batch"]["id"]

    # Hold A's attempt open; B acknowledges entirely inside the hold.
    held = threading.Event()
    holder: dict[str, Any] = {}

    def _hold_a() -> None:
        endpoint = provider_module.DeterministicProviderEndpoint(
            script=[
                {
                    "type": "delay",
                    "seconds": 5.0,
                    "then": {"type": "final", "cpt": _invalid_cpt()},
                }
            ],
            on_exchange=lambda _body: held.set(),
        )
        url = endpoint.start()
        try:
            config = provider_module.ProviderConfig(
                endpoint_url=url, model="test-model", capability="schema", timeout_seconds=30.0
            )
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            holder["outcome"] = worker_module.run_once(
                engine,
                adapter,
                now=t0 + timedelta(seconds=10),
                database_url=db_module.get_test_database_url(),
                worker_id="worker-s60-hold",
            )
        finally:
            endpoint.stop()

    thread = threading.Thread(target=_hold_a)
    thread.start()
    assert held.wait(timeout=30), "A's provider exchange must be outstanding"

    saved_b = client_b.patch(
        f"/api/v1/encounters/{encounter_b}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "yes"}}, "gate": "true"}},
        headers={**_auth_headers(csrf_b), "If-Match": contracts.format_etag(revision_b)},
    )
    assert saved_b.status_code == 200, saved_b.text
    posted = client_b.post(
        f"/api/v1/question-runs/{run_b}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_b),
    )
    assert posted.status_code == 200, posted.text
    revision_id = posted.json()["revision"]["id"]
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    local = worker_module.run_once(engine, stub)
    assert local["status"] == "succeeded", local
    assert local.get("job_class") == "local_calculation"
    review_b = client_b.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert review_b["calculation_result"]["cpt_revision_id"] == revision_id
    accepted = client_b.post(
        f"/api/v1/question-runs/{run_b}/acceptance",
        json=_accept_body(review_b),
        headers=_auth_headers(csrf_b),
    )
    assert accepted.status_code == 200, accepted.text
    acceptance_id = accepted.json()["acceptance"]["id"]

    thread.join(timeout=60)
    assert holder.get("outcome") is not None, "held worker must finish"
    assert holder["outcome"]["status"] == "queued", holder["outcome"]
    view_a = client_a.get(f"/api/v1/generation-batches/{batch_a}").json()
    assert view_a["attempts"] == 1, view_a
    assert view_a["baseline"] is None, "held invalid attempt leaves no baseline"
    reread_b = client_b.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert reread_b["acceptance"] is not None
    assert reread_b["acceptance"]["id"] == acceptance_id
    baseline_id = reread_b["baseline"]["id"]

    # A real worker OS process observes the same durable state (frozen
    # clock keeps A's backoff unexpired, so nothing is eligible: idle).
    backend_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": "src",
        "S60_FROZEN_NOW": (t0 + timedelta(seconds=10)).isoformat(),
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os;"
            "from datetime import datetime;"
            "from x_insight import db as d;"
            "from x_insight.reasoning import worker as w;"
            "from x_insight.reasoning import provider as p;"
            "e = d.build_engine(d.get_test_database_url());"
            "at = datetime.fromisoformat(os.environ['S60_FROZEN_NOW']);"
            "o = w.run_once(e, p.ControlledStubAdapter(mode='succeed'), now=at,"
            " database_url=d.get_test_database_url());"
            "print('WORKER-STATUS:' + str(o.get('status')))",
        ],
        cwd=str(backend_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "WORKER-STATUS:idle" in proc.stdout, proc.stdout[-500:]

    # Restart the API server twice (new instances, rebuilt engines) and
    # reread byte-identical state through real HTTP.
    snapshots = []
    for _ in range(2):
        db_module.reset_engine()
        fresh = db_module.build_engine(db_module.get_test_database_url())
        monkeypatch.setattr(db_module, "get_engine", lambda: fresh)
        base_url, server, server_thread = start_live_server()
        try:
            with httpx.Client(base_url=base_url, timeout=10.0) as http_b:
                login_b = http_b.post(
                    "/api/v1/auth/login",
                    json={
                        "username": "dr_s60_ov_b",
                        "password": "pw123",
                        "role": "physician",
                    },
                )
                assert login_b.status_code == 200, login_b.text
                got_b = http_b.get(f"/api/v1/question-runs/{run_b}/review")
                assert got_b.status_code == 200, got_b.text
                body_b = got_b.json()
            with httpx.Client(base_url=base_url, timeout=10.0) as http_a:
                login_a = http_a.post(
                    "/api/v1/auth/login",
                    json={
                        "username": "dr_s60_ov_a",
                        "password": "pw123",
                        "role": "physician",
                    },
                )
                assert login_a.status_code == 200, login_a.text
                got_a = http_a.get(f"/api/v1/generation-batches/{batch_a}")
                assert got_a.status_code == 200, got_a.text
                travail_a = got_a.json()
                snapshots.append(
                    {
                        "acceptance": body_b["acceptance"]["id"],
                        "baseline": body_b["baseline"]["id"],
                        "result": body_b["calculation_result"]["id"],
                        "attempts": travail_a["attempts"],
                        "job": travail_a["job"]["status"],
                    }
                )
        finally:
            stop_live_server(server, server_thread)
            fresh.dispose()
    assert snapshots[0] == snapshots[1], "restart must reread identical state"
    assert snapshots[0]["acceptance"] == acceptance_id
    assert snapshots[0]["baseline"] == baseline_id
    assert snapshots[0]["attempts"] == 1
    assert snapshots[0]["job"] == "queued"


# --- S60 §2 (review): fencing while a provider response is outstanding ---


def test_inflight_generation_bump_fences_stale_commit(clean_registry, monkeypatch) -> None:
    """S60 §2: a generation bump mid-flight fences the stale commit.

    The provider response is HELD open (``on_exchange``-synced, claim
    complete and outstanding) while the deployment generation moves on —
    the same fencing the S56 restore drill relies on (restore bumps
    ``max(live, staged)+1`` so old workers fence identically; see
    ``BT/recovery/test_restore_commit.py``). Releasing the stale
    response then denies the commit (``fencing_failed`` /
    ``DEPLOYMENT_FENCED``) with no baseline and an intact ledger; a
    later worker reclaims and succeeds exactly once with a unique
    artifact. No sleeps: event + joins order everything.
    """
    engine = clean_registry
    _new_physician(engine, monkeypatch, "dr_s60_inflight")
    client, csrf = _login_physician(engine, monkeypatch, "dr_s60_inflight")
    _, encounter_id, revision = _setup_encounter(
        client, csrf, "0012345616", {"h_a": "yes", "h_b": "no"}
    )
    package = _package("s60_inflight_q", "h_a", "h_b")
    started = _start_batches(client, csrf, encounter_id, revision, [package])
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]
    t0 = contracts.utcnow()

    held = threading.Event()
    holder: dict[str, Any] = {}

    def _hold_valid() -> None:
        endpoint = provider_module.DeterministicProviderEndpoint(
            script=[
                {
                    "type": "delay",
                    "seconds": 3.0,
                    "then": {"type": "final", "cpt": _valid_cpt()},
                }
            ],
            on_exchange=lambda _body: held.set(),
        )
        url = endpoint.start()
        try:
            config = provider_module.ProviderConfig(
                endpoint_url=url, model="test-model", capability="schema", timeout_seconds=30.0
            )
            adapter = provider_module.BoundedProviderAdapter(
                config, grant_token="", database_url=db_module.get_test_database_url()
            )
            holder["outcome"] = worker_module.run_once(
                engine,
                adapter,
                now=t0,
                database_url=db_module.get_test_database_url(),
                worker_id="worker-s60-stale",
            )
            holder["captured"] = list(endpoint.captured)
        finally:
            endpoint.stop()

    thread = threading.Thread(target=_hold_valid)
    thread.start()
    assert held.wait(timeout=30), "provider exchange must be outstanding"
    # Fence while outstanding: the deployment moves on (restore-equivalent).
    with engine.begin() as connection:
        connection.execute(text("UPDATE reasoning_deployment SET generation = 2 WHERE id = 1"))
    thread.join(timeout=60)
    assert holder.get("outcome") is not None, "held worker must finish"
    assert holder["outcome"]["status"] == "fencing_failed", holder["outcome"]
    assert holder["outcome"].get("code") == "DEPLOYMENT_FENCED", holder["outcome"]
    assert len(holder.get("captured", [])) == 1, "one exchange, then fenced"
    fenced = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert fenced["attempts"] == 1, fenced
    assert fenced["baseline"] is None, "stale commit must not land"
    assert fenced["job"]["status"] == "leased", fenced["job"]

    # Past lease expiry (120s) the new generation reclaims and succeeds
    # exactly once: unique artifact, no duplicated sections.
    out_ok, cap_ok = _run_with_script(
        engine,
        [{"type": "final", "cpt": _valid_cpt()}],
        t0 + timedelta(seconds=300),
        worker_id="worker-s60-heir",
    )
    assert out_ok["status"] == "succeeded", out_ok
    assert len(cap_ok) == 1, "retry costs exactly one new exchange"
    done = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert done["attempts"] == 2, done
    assert done["baseline"] is not None
    assert len(done["baselines"]) == 1, "one unique artifact for the run"
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["baseline"] is not None
    assert review.json()["adjustable"] is True
