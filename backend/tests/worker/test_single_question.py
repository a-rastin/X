"""Run one full synthetic clinical question end to end (S45, seams T1/T8 exercising T5-T7).

Through public generation start → ``run_once()`` with a per-job
``BoundedProviderAdapter`` → real MCP stdio → controlled provider →
all-CPT validation → effective XML → empty-evidence inference → template
section, observable through ``GET /generation-batches/{id}`` and question
review. Synthetic A→B network from plan.md §7.4 yields empty-evidence
``P(A=yes)=0.20``, ``P(B=yes)=0.22``.

Covers tasks.md S45 §§1-4 (FR-32-35):

- §1: end-to-end success with exact 0.20/0.22 + transparency 5 fields.
- §2: provenance persistence + immutable OriginalBaseline atomic only
  after success (failed exposes null + ``adjustable`` false).
- §3: structure mutation / missing root → unsuccessful, base bytes
  unchanged, no default enters inference.
- §4: template only from reviewed mapping + stored result (LLM prose
  never used), five transparency fields from persisted data.

Real PostgreSQL + real MCP stdio + ``DeterministicProviderEndpoint``;
no internal mocks, no DB-row asserts (every behavior is asserted via GET
batch + review), independent expected literals.
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


def s45_package() -> dict[str, Any]:
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
                "TRUNCATE reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, original_baselines, question_runs, generation_batches, "
                "notes, encounters, patients, idempotency_records, sessions, users, "
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


def _run_bounded_until_terminal(
    engine, cpt_payload: dict[str, Any], now: Any = None
) -> dict[str, Any]:
    """Exhaust the S47 shared budget (initial + two retries, three total).

    Retryable failures return the job to queued with persisted
    ``next_eligible_at``; each subsequent call past eligibility consumes
    the next attempt. Returns the terminal outcome.
    """
    from datetime import timedelta as _timedelta

    moment = now or contracts.utcnow()
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
    package = s45_package()
    started = _start_batch(client, csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    return client, csrf, encounter_id, revision, package, started.json()


# --- S45 §1: end-to-end success with exact 0.20/0.22 + persisted transparency ---


def test_end_to_end_success_exact_posteriors(clean_registry, monkeypatch) -> None:
    """S45 §1: public start → worker → real MCP → provider → effective XML → inference."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s45_e2e", "0012345678"
    )
    batch_id = started["batch"]["id"]
    run_id = started["question_runs"][0]["id"]
    network_hash = package["manifest"]["network_hash"]

    outcome = _run_bounded_once(clean_registry, valid_cpt(network_hash))
    assert outcome["status"] == "succeeded", outcome

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "succeeded", body["job"]
    baseline = body["baseline"]
    assert baseline is not None, "baseline must persist after success"
    # Independent mathematical literals from plan.md §7.4 (never computed by code).
    by_node = {entry["node_id"]: entry for entry in baseline["posteriors"]}
    assert by_node["A"]["probabilities"] == pytest.approx([0.80, 0.20], abs=1e-6)
    assert by_node["B"]["probabilities"] == pytest.approx([0.78, 0.22], abs=1e-6)
    # Effective artifact holds the accepted tables; registered hash is bound.
    assert baseline["source_hash"] == network_hash
    assert len(baseline["effective_hash"]) == 64
    assert "0.8" in baseline["effective_xml"] and "0.2" in baseline["effective_xml"]
    assert baseline["query_nodes"] == ["A", "B"]
    # Section comes from the reviewed template's absent branch, not provider prose.
    assert "absent" in baseline["section_text"]
    assert "no 78.00%" in baseline["section_text"]
    # Five transparency fields come from persisted data.
    transparency = body["transparency"]
    assert set(transparency.keys()) == {
        "question_key",
        "network_version",
        "saved_patient_inputs",
        "returned_cpt_percentages",
        "deterministic_result",
    }, transparency.keys()
    assert transparency["question_key"] == "synthetic_single"
    assert transparency["deterministic_result"]["section_text"] == baseline["section_text"]
    # Same persisted view via question review; baseline is adjustable.
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["adjustable"] is True
    assert review.json()["transparency"] == transparency
    assert "lease_token" not in str(body) and "grant_token" not in str(body)


# --- S45 §2/§3: structure failure leaves no baseline; base bytes unchanged ---


def test_missing_root_table_fails_without_baseline(clean_registry, monkeypatch) -> None:
    """S45 §3: missing root table → failed; §2: no baseline row, not adjustable."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s45_mut", "0012345678"
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

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "failed", body["job"]
    assert body["attempts"] == 3, "shared budget exhausts three total attempts"
    assert body["baseline"] is None, "failed originals persist no baseline"
    assert body["transparency"] is None
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["baseline"] is None
    assert review.json()["adjustable"] is False
    # Registered bundle stays pinned via the public seam (SHA-256 of exact
    # bytes); failed run exposes no baseline to hide behind.
    assert body["batch"]["pinned_bundle"]["network_hash"] == network_hash
    assert review.json()["batch"]["pinned_bundle"]["network_hash"] == network_hash


def test_swapped_parent_structure_fails(clean_registry, monkeypatch) -> None:
    """S45 §3: swapped parents → failed with no default entering inference."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s45_swap", "0012345679"
    )
    batch_id = started["batch"]["id"]
    network_hash = package["manifest"]["network_hash"]
    mutated = {
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
                "parent_ids": [],
                "states": ["no", "yes"],
                "rows": [{"parent_states": [], "percentages": ["50", "50"]}],
            },
        ],
    }

    outcome = _run_bounded_until_terminal(clean_registry, mutated)
    assert outcome["status"] == "failed", outcome

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "failed", body["job"]
    assert body["attempts"] == 3, "shared budget exhausts three total attempts"
    assert body["baseline"] is None
    assert body["transparency"] is None


# --- S45 §4: template only from reviewed mapping + stored result ---


def test_template_renders_only_from_reviewed_mapping(clean_registry, monkeypatch) -> None:
    """S45 §4: section equals the reviewed branch with escaped slots; prose never used."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s45_tpl", "0012345678"
    )
    batch_id = started["batch"]["id"]
    run_id = started["question_runs"][0]["id"]
    network_hash = package["manifest"]["network_hash"]
    assert network_hash == hashlib.sha256(TWO_NODE_XML).hexdigest()

    outcome = _run_bounded_once(clean_registry, valid_cpt(network_hash))
    assert outcome["status"] == "succeeded", outcome

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    baseline = body["baseline"]
    assert baseline is not None
    # Raw and validated percentages persist exactly as returned.
    assert baseline["raw_response"]["tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert baseline["validated_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert baseline["validated_tables"][1]["rows"][1]["percentages"] == ["30", "70"]
    # P(B=yes)=0.22 → argmax "no" → absent branch with escaped "{B}" slot.
    assert "Result for" in baseline["section_text"]
    assert "absent" in baseline["section_text"]
    assert "no 78.00%" in baseline["section_text"]
    assert "Take drug" not in baseline["section_text"]
    # Provenance persists who/what produced the baseline.
    provenance = baseline["provenance"]
    for key in (
        "question_key",
        "projection_hash",
        "prompt_version",
        "template_version",
        "network_version",
        "provider_model",
        "attempt_index",
        "worker_id",
    ):
        assert key in provenance, f"provenance missing {key}"
    assert provenance["provider_model"] == "test-model"
    assert provenance["question_key"] == "synthetic_single"
    # Review exposes the same persisted transparency with an adjustable baseline.
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["adjustable"] is True
    assert review.json()["baseline"]["section_text"] == baseline["section_text"]
    assert review.json()["transparency"] == body["transparency"]


def test_provider_prose_extra_field_never_becomes_recommendation(
    clean_registry, monkeypatch
) -> None:
    """S45 §4: strict validator rejects extra LLM prose → failed, never rendered."""
    client, _, _, _, package, started = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s45_prose", "0012345679"
    )
    batch_id = started["batch"]["id"]
    network_hash = package["manifest"]["network_hash"]
    payload = valid_cpt(network_hash)
    payload["prose"] = "Take drug X now!"
    payload["recommendation"] = "LLM says yes"

    outcome = _run_bounded_until_terminal(clean_registry, payload)
    assert outcome["status"] == "failed", outcome

    body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert body["job"]["status"] == "failed"
    assert body["attempts"] == 3, "shared budget exhausts three total attempts"
    assert body["baseline"] is None
    assert "Take drug" not in str(body)


# --- S45 §2 (review): permissions, immutability, terminal never reuses ---


def test_review_permissions_immutability_and_fresh_batch(clean_registry, monkeypatch) -> None:
    """S45 §2/§4: owner review ok, stranger denied, edits stale but baseline immutable."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200
    for username in ("dr_s45_own", "dr_s45_other"):
        created = admin.post(
            "/api/v1/physicians",
            json={"username": username, "password": "pw123"},
            headers=_auth_headers(login.json()["csrf_token"]),
        )
        assert created.status_code == 201, created.text
    own, own_csrf = _login_physician(clean_registry, monkeypatch, "dr_s45_own")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s45_other")
    encounter_id, revision = _setup_encounter(own, own_csrf, "0012345678")
    package = s45_package()
    started = _start_batch(own, own_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]

    outcome = _run_bounded_once(clean_registry, valid_cpt(package["manifest"]["network_hash"]))
    assert outcome["status"] == "succeeded", outcome

    review = own.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["adjustable"] is True
    assert review.json()["transparency"]["question_key"] == "synthetic_single"
    # Stranger reads are denied without content.
    denied = other.get(f"/api/v1/question-runs/{run_id}/review")
    assert denied.status_code == 403, denied.text
    assert "78.00" not in denied.text and "h_a" not in denied.text
    assert other.get(f"/api/v1/generation-batches/{batch_id}").status_code == 403
    # Missing entities are 404.
    assert own.get(f"/api/v1/question-runs/{uuid.uuid4()}/review").status_code == 404
    assert own.get(f"/api/v1/generation-batches/{uuid.uuid4()}").status_code == 404
    # Relevant edits stale the freshness view while the persisted baseline stays put.
    edited = own.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(own_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text
    reread = own.get(f"/api/v1/generation-batches/{batch_id}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["freshness"]["stale"] is True
    assert reread.json()["baseline"]["posteriors"][1]["probabilities"] == pytest.approx(
        [0.78, 0.22], abs=1e-6
    )
    # Terminal batches admit a fresh batch; the old one stays readable history.
    second = _start_batch(own, own_csrf, encounter_id, edited.json()["revision"], package)
    assert second.status_code == 202, second.text
    assert second.json()["batch"]["id"] != batch_id
    old = own.get(f"/api/v1/generation-batches/{batch_id}")
    assert old.status_code == 200 and old.json()["baseline"] is not None
