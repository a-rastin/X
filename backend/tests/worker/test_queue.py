"""Durable leased jobs and global admission (S44, seam T8; plan.md §§8.4-8.5).

Through public HTTP + ``run_once()`` on real PostgreSQL (synthetic
single-question scope, no MCP/provider network, no full CPT work):

- S44 §1: run creation + first job atomic; same-fingerprint reuse;
  simultaneous triggers yield one active generation.
- S44 §2: two workers share two global slots; one run never executes
  twice; short claim transactions; lease/heartbeat; fencing token +
  deployment generation.
- S44 §3: fair rotation across physicians then FIFO; saturation shows
  busy without deleting drafts/jobs.
- S44 §4: expired leases reclaimed; old tokens fenced; restarts retain
  attempts + terminal artifacts; one ``run_once()`` for tests/polling.

Seams: ``POST /api/v1/encounters/{id}/generation-batches``,
``GET /api/v1/generation-batches/{id}``, and ``run_once()``. Behavior
assertions read public run status (job/attempts/queue); fixture setup
may truncate/migrate. Queue collaborators are never mocked; the only
faked external is ``ControlledStubAdapter`` with a controlled clock.
"""

from __future__ import annotations

import os
import threading
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
                "reasoning_fairness, question_runs, generation_batches, notes, encounters, "
                "patients, idempotency_records, sessions, users, audit_events CASCADE"
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


@pytest.fixture()
def admin_client(clean_registry, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "client": client,
        "csrf": body["csrf_token"],
        "user": body["user"],
        "engine": clean_registry,
    }


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str) -> dict[str, Any]:
    created = admin_client["client"].post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers=_auth_headers(admin_client["csrf"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["user"]


def _physician_client(clean_registry, monkeypatch, username: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return client, body["csrf_token"], body["user"]


def _synthetic_package() -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": "synthetic_snapshot",
            "title": "Synthetic snapshot question",
            "workflow": "registration",
            "version": "s40-test-v1",
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
                {"inputs": {}, "expected_for_review": {"B": "yes"}, "note": "review candidate"}
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


def _create_patient(client, csrf, identifier: str) -> dict[str, Any]:
    response = client.post(
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
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(client, csrf, encounter_id: str, draft_data: dict[str, Any], revision: int):
    return client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": draft_data},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _start_batch(client, csrf, encounter_id: str, revision: int, package: dict[str, Any]):
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _read_batch(client, batch_id: str):
    return client.get(f"/api/v1/generation-batches/{batch_id}")


def _setup_batch(admin_client, clean_registry, monkeypatch, username: str, identifier: str):
    """Create one physician + patient + ready batch; return client/csrf/batch/encounter."""
    _make_physician(admin_client, username)
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, username)
    created = _create_patient(client, csrf, identifier)
    encounter_id = created["draft"]["id"]
    saved = _patch_draft(
        client,
        csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
        1,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    started = _start_batch(client, csrf, encounter_id, revision, _synthetic_package())
    assert started.status_code == 202, started.text
    return client, csrf, started.json()["batch"]["id"], encounter_id, revision


def _assert_no_tokens(body: dict[str, Any]) -> None:
    text_blob = str(body)
    assert "lease_token" not in text_blob
    assert "grant_token" not in text_blob


# --- S44 §1: atomic creation + first job; reuse; single active ---


def test_atomic_creation_enqueues_first_eligible_job(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §1: run creation and its first eligible job are atomic (T8 GET shows queued)."""
    client, csrf, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_atomic", "0012345678"
    )
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["job"] is not None
    assert body["job"]["status"] == "queued"
    assert body["attempts"] == 0
    assert body["queue"]["queue_position"] == 1
    assert body["job"]["result"] is None
    _assert_no_tokens(body)


def test_same_fingerprint_reuses_single_run(admin_client, clean_registry, monkeypatch) -> None:
    """S44 §1: repeated same-fingerprint triggers reuse the run (same batch id)."""
    client, csrf, batch_id, encounter_id, revision = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_reuse", "0012345678"
    )
    repeated = _start_batch(client, csrf, encounter_id, revision, _synthetic_package())
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["batch"]["id"] == batch_id
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "queued"
    assert read.json()["attempts"] == 0


def test_simultaneous_triggers_yield_single_active_generation(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §1: simultaneous same-fingerprint POSTs reuse one active generation."""
    _make_physician(admin_client, "dr_q_race")
    clients: list[tuple[Any, str]] = []
    for _ in range(4):
        client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_q_race")
        clients.append((client, csrf))
    owner, csrf = clients[0]
    created = _create_patient(owner, csrf, "0012345678")
    encounter_id = created["draft"]["id"]
    saved = _patch_draft(
        owner,
        csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
        1,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    package = _synthetic_package()
    results: list[Any] = [None] * 4

    def _trigger(index: int) -> None:
        client, token = clients[index]
        try:
            results[index] = _start_batch(client, token, encounter_id, revision, package)
        except Exception as exc:  # pragma: no cover - thread transport failure
            results[index] = exc

    threads = [threading.Thread(target=_trigger, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(hasattr(result, "status_code") for result in results), results
    assert all(result.status_code == 202 for result in results), [
        (result.status_code, result.text) for result in results
    ]
    ids = [result.json()["batch"]["id"] for result in results]
    assert len(set(ids)) == 1, f"simultaneous triggers must reuse one run, got {ids}"
    read = _read_batch(owner, ids[0])
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "queued"


def test_relevant_edit_supersedes_to_single_active_generation(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §1: different fingerprints supersede; old history stays readable, one active."""
    client, csrf, first_id, encounter_id, revision = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_supersede", "0012345678"
    )
    changed = _patch_draft(
        client,
        csrf,
        encounter_id,
        {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"},
        revision,
    )
    assert changed.status_code == 200, changed.text
    new_revision = changed.json()["revision"]
    second = _start_batch(client, csrf, encounter_id, new_revision, _synthetic_package())
    assert second.status_code == 202, second.text
    second_id = second.json()["batch"]["id"]
    assert second_id != first_id
    assert (
        second.json()["batch"]["fingerprint"]
        != _read_batch(client, first_id).json()["batch"]["fingerprint"]
    )
    old = _read_batch(client, first_id)
    assert old.status_code == 200, old.text
    assert old.json()["job"]["status"] == "cancelled"
    new = _read_batch(client, second_id)
    assert new.status_code == 200, new.text
    assert new.json()["job"]["status"] == "queued"


# --- S44 §2: slots, per-run exclusivity, short tx, lease/heartbeat, fencing ---


def test_run_once_executes_stub_outside_claim_transaction(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §2/§4: one run_once claims, calls the stub outside any tx, commits terminal."""
    first_client, _, first_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_exec_a", "0012345678"
    )
    second_client, _, second_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_exec_b", "0012345679"
    )
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert len(stub.calls) == 1
    assert stub.calls[0].projection_hash
    assert stub.calls[0].projection["variables"]
    succeeded = [first_id, second_id]
    statuses = {}
    for client, batch_id in ((first_client, first_id), (second_client, second_id)):
        read = _read_batch(client, batch_id)
        assert read.status_code == 200, read.text
        statuses[batch_id] = read.json()["job"]["status"]
    assert sorted(statuses.values()).count("succeeded") == 1
    assert sorted(statuses.values()).count("queued") == 1
    terminal_id = next(bid for bid in succeeded if statuses[bid] == "succeeded")
    terminal_client = first_client if statuses[first_id] == "succeeded" else second_client
    terminal = _read_batch(terminal_client, terminal_id)
    assert terminal.json()["job"]["result"]["projection_hash"]
    assert terminal.json()["attempts"] == 1
    _assert_no_tokens(terminal.json())


def test_two_workers_hold_distinct_slots_third_sees_busy(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §2: two workers hold different jobs; per-run exclusivity; third claim is busy."""
    from x_insight.reasoning import queue as queue_module

    _setup_batch(admin_client, clean_registry, monkeypatch, "dr_q_slot_a", "0012345678")
    _setup_batch(admin_client, clean_registry, monkeypatch, "dr_q_slot_b", "0012345679")
    _setup_batch(admin_client, clean_registry, monkeypatch, "dr_q_slot_c", "0012345680")
    with db_module.session_scope(clean_registry) as session:
        first = queue_module.claim_next_job(session, "worker-a")
    assert first is not None and "job" in first, first
    with db_module.session_scope(clean_registry) as session:
        second = queue_module.claim_next_job(session, "worker-b")
    assert second is not None and "job" in second, second
    assert first["job"]["id"] != second["job"]["id"]
    assert first["job"]["batch_id"] != second["job"]["batch_id"]
    with db_module.session_scope(clean_registry) as session:
        third = queue_module.claim_next_job(session, "worker-c")
    assert third is not None and third.get("busy") is True, f"slots full must be busy: {third}"


def test_single_run_never_executes_twice(admin_client, clean_registry, monkeypatch) -> None:
    """S44 §2: one leased job per batch; a second worker cannot claim the same run."""
    from x_insight.reasoning import queue as queue_module

    client, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_once", "0012345678"
    )
    with db_module.session_scope(clean_registry) as session:
        first = queue_module.claim_next_job(session, "worker-a")
    assert first is not None and "job" in first, first
    assert str(first["job"]["batch_id"]) == batch_id
    with db_module.session_scope(clean_registry) as session:
        second = queue_module.claim_next_job(session, "worker-b")
    assert second is None, f"same run must not execute twice, got {second}"
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "leased"


def test_heartbeat_extends_live_lease_and_rejects_stale(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §2: heartbeat extends a live lease; wrong token and expiry do not."""
    from x_insight.reasoning import queue as queue_module

    client, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_beat", "0012345678"
    )
    moment = contracts.utcnow()
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-beat", now=moment)
    assert claimed is not None and "job" in claimed, claimed
    job_id = claimed["job"]["id"]
    token = str(claimed["lease_token"])
    mid = moment + timedelta(seconds=60)
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, job_id, token, now=mid) is True
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, job_id, "wrong-token", now=mid) is False
    expired = moment + timedelta(seconds=60 + 120 + 1)
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, job_id, token, now=expired) is False
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "leased"


def test_deployment_generation_fences_stale_commit(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §2: deployment bump fences the old token; public status stays leased."""
    from x_insight.reasoning import queue as queue_module

    client, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_fence", "0012345678"
    )
    moment = contracts.utcnow()
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-fence", now=moment)
    assert claimed is not None and "job" in claimed, claimed
    job_id = claimed["job"]["id"]
    token = str(claimed["lease_token"])
    try:
        with clean_registry.begin() as connection:
            connection.execute(text("UPDATE reasoning_deployment SET generation = 2 WHERE id = 1"))
        with db_module.session_scope(clean_registry) as session:
            try:
                queue_module.commit_job_result(
                    session,
                    job_id,
                    token,
                    provider_ok=True,
                    provider_payload={"stub": "late"},
                    now=moment,
                )
                fenced = False
            except contracts.ContractError as exc:
                fenced = exc.code == "DEPLOYMENT_FENCED"
        assert fenced, "deployment change must fence the old token"
        read = _read_batch(client, batch_id)
        assert read.status_code == 200, read.text
        assert read.json()["job"]["status"] == "leased"
    finally:
        _reset_deployment(clean_registry)


# --- S44 §3: fair rotation then FIFO; busy without deletion ---


def test_fair_rotation_across_physicians_then_fifo(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §3: A1, A2 + B1 serve as A1, B1, A2 (round-robin, then FIFO)."""
    _make_physician(admin_client, "dr_q_fair_a")
    _make_physician(admin_client, "dr_q_fair_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_q_fair_a")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_q_fair_b")

    def _make_batch(client, csrf, identifier: str) -> str:
        created = _create_patient(client, csrf, identifier)
        encounter_id = created["draft"]["id"]
        saved = _patch_draft(
            client,
            csrf,
            encounter_id,
            {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
            1,
        )
        assert saved.status_code == 200, saved.text
        started = _start_batch(
            client, csrf, encounter_id, saved.json()["revision"], _synthetic_package()
        )
        assert started.status_code == 202, started.text
        return started.json()["batch"]["id"]

    batch_a1 = _make_batch(client_a, csrf_a, "0012345678")
    batch_a2 = _make_batch(client_a, csrf_a, "0012345679")
    batch_b1 = _make_batch(client_b, csrf_b, "0012345680")
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    order: list[str] = []
    for _ in range(3):
        outcome = worker_module.run_once(clean_registry, stub)
        assert outcome["status"] == "succeeded", outcome
        order.append(outcome["batch_id"])
    assert order[0] == batch_a1, f"oldest overall first, got {order}"
    assert order[1] == batch_b1, f"round-robin across physicians, got {order}"
    assert order[2] == batch_a2, f"FIFO within physician, got {order}"
    for client, batch_id in ((client_a, batch_a1), (client_a, batch_a2), (client_b, batch_b1)):
        read = _read_batch(client, batch_id)
        assert read.status_code == 200, read.text
        assert read.json()["job"]["status"] == "succeeded", read.json()


def test_saturation_reports_busy_without_deleting(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §3: saturated slots show busy via T8; drafts and jobs stay intact."""
    from x_insight.reasoning import queue as queue_module

    clients: dict[str, Any] = {}
    batch_ids: list[str] = []
    encounters: dict[str, str] = {}
    for username, identifier in (
        ("dr_q_busy_a", "0012345678"),
        ("dr_q_busy_b", "0012345679"),
        ("dr_q_busy_c", "0012345680"),
    ):
        client, _, batch_id, encounter_id, _ = _setup_batch(
            admin_client, clean_registry, monkeypatch, username, identifier
        )
        clients[batch_id] = client
        batch_ids.append(batch_id)
        encounters[batch_id] = encounter_id
    with db_module.session_scope(clean_registry) as session:
        first = queue_module.claim_next_job(session, "worker-a")
    assert first is not None and "job" in first, first
    with db_module.session_scope(clean_registry) as session:
        second = queue_module.claim_next_job(session, "worker-b")
    assert second is not None and "job" in second, second
    leased_batches = {str(first["job"]["batch_id"]), str(second["job"]["batch_id"])}
    remaining = next(batch_id for batch_id in batch_ids if batch_id not in leased_batches)
    read = _read_batch(clients[remaining], remaining)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["job"]["status"] == "queued", body["job"]
    assert body["queue"]["busy"] is True, body["queue"]
    assert body["queue"]["leased_count"] == 2, body["queue"]
    assert body["queue"]["queue_position"] is not None
    _assert_no_tokens(body)
    encounter = clients[remaining].get(f"/api/v1/encounters/{encounters[remaining]}")
    assert encounter.status_code == 200, encounter.text


# --- S44 §4: expiry, fencing, restart retention, run_once ---


def test_expired_lease_reclaimed_and_old_token_fenced(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §4: expired leases return to queued; the old token cannot commit."""
    from x_insight.reasoning import queue as queue_module

    client, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_expire", "0012345678"
    )
    started_at = contracts.utcnow()
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-old", now=started_at)
    assert claimed is not None and "job" in claimed, claimed
    old_token = str(claimed["lease_token"])
    job_id = claimed["job"]["id"]
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, job_id, old_token, now=started_at) is True
    expired_at = started_at + timedelta(seconds=181)
    with db_module.session_scope(clean_registry) as session:
        reclaimed = queue_module.reclaim_expired_leases(session, now=expired_at)
    assert reclaimed == 1
    with db_module.session_scope(clean_registry) as session:
        try:
            queue_module.commit_job_result(
                session,
                job_id,
                old_token,
                provider_ok=True,
                provider_payload={"stub": "late"},
                now=expired_at,
            )
            fenced = False
        except contracts.ContractError as exc:
            fenced = exc.code in ("FENCING_TOKEN_MISMATCH", "LEASE_EXPIRED", "DEPLOYMENT_FENCED")
    assert fenced, "old token must not commit after reclaim"
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "queued", read.json()["job"]
    assert read.json()["attempts"] == 1


def test_restart_retains_attempts_and_terminal_artifacts(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §4: a restarted worker reclaims, succeeds, and keeps both attempts."""
    from x_insight.reasoning import queue as queue_module

    client, _, batch_id, _, _ = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_restart", "0012345678"
    )
    started_at = contracts.utcnow()
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-old", now=started_at)
    assert claimed is not None and "job" in claimed, claimed
    job_id = claimed["job"]["id"]
    expired_at = started_at + timedelta(seconds=181)
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.reclaim_expired_leases(session, now=expired_at) == 1
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub, now=expired_at, worker_id="worker-new")
    assert outcome["status"] == "succeeded", outcome
    assert outcome["job_id"] == str(job_id)
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["job"]["status"] == "succeeded", body["job"]
    assert body["attempts"] == 2, f"both attempts retained, got {body}"
    assert body["job"]["result"]["projection_hash"]
    again = worker_module.run_once(clean_registry, stub, worker_id="worker-restart")
    assert again["status"] == "idle", again
    reread = _read_batch(client, batch_id)
    assert reread.status_code == 200, reread.text
    assert reread.json()["job"]["status"] == "succeeded"
    assert reread.json()["attempts"] == 2


def test_stale_fingerprint_job_cancelled_without_provider_call(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S44 §4: real gate/fingerprint checks cancel stale work; no stub call is made."""
    client, csrf, batch_id, encounter_id, revision = _setup_batch(
        admin_client, clean_registry, monkeypatch, "dr_q_stale", "0012345678"
    )
    edited = _patch_draft(
        client,
        csrf,
        encounter_id,
        {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"},
        revision,
    )
    assert edited.status_code == 200, edited.text
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] in ("cancelled", "idle"), outcome
    assert stub.calls == [], "stale work must not reach the provider"
    read = _read_batch(client, batch_id)
    assert read.status_code == 200, read.text
    assert read.json()["job"]["status"] == "cancelled", read.json()["job"]
