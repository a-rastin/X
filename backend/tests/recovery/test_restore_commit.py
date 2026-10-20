"""Commit restore with maintenance, fencing, and rollback (S56, seams T10/T1/T8).

Through public authenticated HTTP on real PostgreSQL + real S54 backup
archives via public HTTP (``POST /api/v1/backups`` + download), real
``POST /api/v1/restores/validate`` staging, real ``POST
/api/v1/restores/{id}/commit`` replacement, and ``worker.run_once`` for the
T8 quiesce probe. No mocks of owned modules, no private-helper mirror
tests, no DB-row asserts for behavior. Synthetic two-node network only; no
clinical content. Expected literals are worked fixtures, never
implementation output.

Covers tasks.md S56 §§1-4 (FR-41–42, NFR-04; plan.md §10.3 steps 4-6):

- §1: only admin confirmation of the exact validated staged digest starts
  replacement (stale/mismatched 409, malformed 422, anon 401 / physician
  403 / bad CSRF 403); quiesce (writes 503 behind maintenance, ``/ready``
  503, ``run_once`` reports maintenance) + generation fencing + pre-restore
  backup readable via ``GET /backups/{id}``.
- §2: phased switch behind maintenance (11-phase history + operator log);
  no mixed old/new traffic (old lease rejected after the generation bump);
  staged-vs-live reads (post-backup live patient removed after commit).
- §3: sessions revoked (old cookies 401, re-login 200), grants revoked,
  pending jobs reconciled (eligible requeued, ineligible cancelled,
  terminal succeeded/cancelled preserved with results readable for local
  retry/reset), ``restores.commit.success`` audit in the restored DB plus
  the external operator log, health (login/read/replay) before reopen
  (``maintenance_active: false``, ``/ready`` 200).
- §4: ``RESTORE_COMMIT_FAIL_AT`` injection (public env seam) at
  ``before_switch`` (sessions preserved, maintenance held, writes 503,
  ``reopen`` 200), ``during_restart`` and ``health_check`` (sessions 401,
  rollback readable, maintenance held, ``reopen`` 200). Rollback restores
  the prior DB/configuration and readable records; maintenance remains
  until verified healthy.

Isolation (disposable destructive drill only — never the owner's live DB):
per-test ``BACKUP_STAGING_DIR`` + ``RESTORE_STAGING_DIR`` +
``RESTORE_MAINTENANCE_FILE`` + ``RESTORE_OPERATOR_LOG`` under ``tmp_path``;
truncate covers ``restore_jobs`` (+ S54 tables) so sequential suites sharing
one test DB stay green. ``RESTORE_COMMIT_FAIL_AT`` is cleared by default
and set per-test via ``monkeypatch``. This is replacement, never a merge:
the switch is explicit phases with durable status + rollback, never one
SQL transaction for DB+process.

Suite-order notes (carryovers, untouched here): ``ddi_*`` releases written
by HTTP-suite helpers flip a worker test when ``tests/http`` runs before
``tests/worker`` (truncate ``ddi_*`` between suites); ``test_only_s02_tables_exist``
drift (now also misses ``backup_jobs``/``restore_jobs``) stays deselected.
"""

from __future__ import annotations

import io
import json
import os
import uuid as uuid_module
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service

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

# The 11 documented commit phases (plan §10.3 steps 4-6): maintenance →
# quiesce → fence → pre_backup → switch → restart → revoke → reconcile →
# audit → verify → reopen.
EXPECTED_PHASES = [
    "maintenance",
    "quiesce",
    "fence",
    "pre_backup",
    "switch",
    "restart",
    "revoke",
    "reconcile",
    "audit",
    "verify",
    "reopen",
]


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
                "ddi_concepts, ddi_interaction_evidence, model_networks, "
                "model_network_versions, model_workflow_pointers, backup_jobs, "
                "restore_jobs CASCADE"
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
def clean_registry(migrated_test_engine, monkeypatch, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    restores = tmp_path / "restores"
    restores.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(backups))
    monkeypatch.setenv("RESTORE_STAGING_DIR", str(restores))
    monkeypatch.setenv("RESTORE_MAINTENANCE_FILE", str(tmp_path / "maintenance.lock"))
    monkeypatch.setenv("RESTORE_OPERATOR_LOG", str(tmp_path / "operator-recovery.log"))
    monkeypatch.delenv("RESTORE_COMMIT_FAIL_AT", raising=False)
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    _reset_deployment(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    try:
        Path(os.environ.get("RESTORE_MAINTENANCE_FILE", "")).unlink(missing_ok=True)
    except Exception:
        pass
    _truncate(migrated_test_engine)
    _reset_deployment(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _admin_client(clean_registry, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    return admin, login.json()["csrf_token"]


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


def _register_patient(client, csrf, identifier: str) -> dict[str, Any]:
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
    return made.json()


def _import_network(admin, admin_csrf, name: str) -> dict[str, Any]:
    response = admin.post(
        "/api/v1/networks",
        json={"name": name, "xml_text": TWO_NODE_XML.decode("utf-8")},
        headers=_auth_headers(admin_csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_backup_blob(admin, admin_csrf, key: str) -> tuple[bytes, str]:
    created = admin.post(
        "/api/v1/backups",
        headers={**_auth_headers(admin_csrf), "Idempotency-Key": key},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "succeeded", created.json()
    backup_id = created.json()["id"]
    downloaded = admin.get(f"/api/v1/backups/{backup_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    return bytes(downloaded.content), backup_id


def _post_validate(admin, admin_csrf, blob: bytes):
    return admin.post(
        "/api/v1/restores/validate",
        content=blob,
        headers={**_auth_headers(admin_csrf), "Content-Type": "application/zip"},
    )


def _post_commit(admin, admin_csrf, restore_id: str, digest: Any):
    return admin.post(
        f"/api/v1/restores/{restore_id}/commit",
        json={"confirmation_digest": digest},
        headers=_auth_headers(admin_csrf),
    )


def _setup_staged(clean_registry, monkeypatch, tag: str) -> dict[str, Any]:
    """Live physician + patient + network, then a staged restore job."""
    _new_physician(clean_registry, monkeypatch, f"dr_s56_{tag}")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, f"dr_s56_{tag}")
    _register_patient(physician, physician_csrf, "0012345678")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _import_network(admin, admin_csrf, f"s56-net-{tag}")
    blob, backup_id = _make_backup_blob(
        admin, admin_csrf, key=f"s56-{tag}-{uuid_module.uuid4().hex[:8]}"
    )
    posted = _post_validate(admin, admin_csrf, blob)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "succeeded", job
    assert job["expired"] is False
    return {
        "admin": admin,
        "admin_csrf": admin_csrf,
        "physician": physician,
        "physician_csrf": physician_csrf,
        "blob": blob,
        "backup_id": backup_id,
        "job": job,
        "digest": job["confirmation_digest"],
    }


def _operator_records() -> list[dict[str, Any]]:
    path = Path(os.environ["RESTORE_OPERATOR_LOG"])
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


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


def _start_batch(client, csrf, encounter_id: str, revision: int) -> dict[str, Any]:
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": _synthetic_package()},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    return started.json()


def _open_draft(client, csrf, identifier: str, values: dict[str, str]):
    created = _register_patient(client, csrf, identifier)
    encounter_id = created["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    return encounter_id, int(saved.json()["revision"])


# --- S56 §1: exact-digest commit succeeds with fencing + pre-restore backup ---


def test_commit_exact_digest_succeeds_with_fencing_and_prebackup(
    clean_registry, monkeypatch
) -> None:
    """S56 §1: exact digest commits; generation fenced; pre-backup readable."""
    fixture = _setup_staged(clean_registry, monkeypatch, "success")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    job, digest = fixture["job"], fixture["digest"]

    committed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert committed.status_code == 200, committed.text
    assert committed.headers["Cache-Control"] == "private, no-store"
    body = committed.json()
    assert body["commit_status"] == "succeeded", body
    assert body["commit_phase"] == "reopen"
    assert body["maintenance_active"] is False
    assert [h["phase"] for h in body["commit_history"]] == EXPECTED_PHASES
    assert all(h["ok"] is True for h in body["commit_history"])
    # Deployment starts at the worked fixture generation 1. The pre-switch
    # fence bump is replaced by the staged value during the switch, then
    # revoke re-fences to max(live, staged)+1 = 2.
    assert body["generation_before"] == 1
    assert body["generation_after"] == 2

    pre_id = body["pre_restore_backup_id"]
    assert pre_id
    # Sessions are revoked by the switch: old cookies 401, so re-login to
    # read the pre-restore backup through public HTTP.
    assert admin.get("/api/v1/me").status_code == 401
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh = TestClient(app)
    relogin = fresh.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert relogin.status_code == 200, relogin.text
    readable = fresh.get(f"/api/v1/backups/{pre_id}")
    assert readable.status_code == 200, readable.text
    assert readable.json()["id"] == pre_id

    # External operator log (outside the replaced DB) records the switch.
    records = _operator_records()
    assert records, "operator log must exist after commit"
    success = [
        r
        for r in records
        if r.get("operation") == "restores.commit.success" and r.get("restore_id") == job["id"]
    ]
    assert len(success) == 1
    assert success[0]["pre_restore_backup_id"] == pre_id
    assert any(
        r.get("operation") == "restores.commit.switch" and r.get("restore_id") == job["id"]
        for r in records
    )

    # Health before reopen is already verified: ready 200, reads work.
    assert fresh.get("/api/v1/ready").status_code == 200
    patients = fresh.get("/api/v1/patients")
    assert patients.status_code == 200, patients.text


# --- S56 §1: stale/mismatched digests fail without mutation ---


def test_commit_rejects_stale_and_mismatched_digest(clean_registry, monkeypatch) -> None:
    """S56 §1: stale (superseded) and mismatched digests fail 409, live intact."""
    fixture = _setup_staged(clean_registry, monkeypatch, "stale")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    job, digest = fixture["job"], fixture["digest"]
    before_total = fixture["physician"].get("/api/v1/patients").json()["total"]

    # Mismatched: same shape, one hex nibble off the staged digest.
    wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
    denied = _post_commit(admin, admin_csrf, job["id"], wrong)
    assert denied.status_code == 409, denied.text

    # Stale: a replacing upload expires the prior confirmation.
    blob2, _ = _make_backup_blob(admin, admin_csrf, key="s56-stale-second")
    second = _post_validate(admin, admin_csrf, blob2)
    assert second.status_code == 201, second.text
    assert second.json()["id"] != job["id"]
    reread = admin.get(f"/api/v1/restores/{job['id']}").json()
    assert reread["expired"] is True
    stale = _post_commit(admin, admin_csrf, job["id"], digest)
    assert stale.status_code == 409, stale.text

    # Neither rejection mutated live reads or minted maintenance.
    assert fixture["physician"].get("/api/v1/patients").json()["total"] == before_total
    assert admin.get("/api/v1/ready").status_code == 200
    assert Path(os.environ["RESTORE_MAINTENANCE_FILE"]).exists() is False

    # Already-committed confirmations are stale too: commit the live job,
    # then replay its digest (re-login first: the commit revoked sessions).
    live_digest = second.json()["confirmation_digest"]
    done = _post_commit(admin, admin_csrf, second.json()["id"], live_digest)
    assert done.status_code == 200, done.text
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anew, anew_csrf = _admin_client(clean_registry, monkeypatch)
    replay = _post_commit(anew, anew_csrf, second.json()["id"], live_digest)
    assert replay.status_code == 409, replay.text


def test_commit_malformed_digest_is_422(clean_registry, monkeypatch) -> None:
    """S56 §1: missing/short/non-hex digests fail 422 with no mutation."""
    fixture = _setup_staged(clean_registry, monkeypatch, "malformed")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    restore_id = fixture["job"]["id"]

    missing = admin.post(
        f"/api/v1/restores/{restore_id}/commit",
        json={},
        headers=_auth_headers(admin_csrf),
    )
    assert missing.status_code == 422, missing.text
    short = _post_commit(admin, admin_csrf, restore_id, "abc123")
    assert short.status_code == 422, short.text
    nonhex = _post_commit(admin, admin_csrf, restore_id, "z" * 64)
    assert nonhex.status_code == 422, nonhex.text
    empty = _post_commit(admin, admin_csrf, restore_id, "   ")
    assert empty.status_code == 422, empty.text
    unknown = _post_commit(admin, admin_csrf, str(uuid_module.uuid4()), "a" * 64)
    assert unknown.status_code == 404, unknown.text


def test_commit_gates_admin_only(clean_registry, monkeypatch) -> None:
    """S56 §1: anon 401 / physician 403 / bad CSRF 403 on commit + reopen."""
    _new_physician(clean_registry, monkeypatch, "dr_s56_gate")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s56_gate")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anonymous = TestClient(app)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _register_patient(physician, physician_csrf, "0012345678")
    _import_network(admin, admin_csrf, "s56-gate-net")
    blob, _ = _make_backup_blob(admin, admin_csrf, key="s56-gate-1")
    staged = _post_validate(admin, admin_csrf, blob).json()
    restore_id, digest = staged["id"], staged["confirmation_digest"]

    assert (
        anonymous.post(
            f"/api/v1/restores/{restore_id}/commit",
            json={"confirmation_digest": digest},
        ).status_code
        == 401
    )
    denied = physician.post(
        f"/api/v1/restores/{restore_id}/commit",
        json={"confirmation_digest": digest},
        headers=_auth_headers(physician_csrf),
    )
    assert denied.status_code == 403, denied.text
    forged = admin.post(
        f"/api/v1/restores/{restore_id}/commit",
        json={"confirmation_digest": digest},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert forged.status_code == 403, forged.text
    assert anonymous.post(f"/api/v1/restores/{restore_id}/reopen").status_code == 401
    assert (
        physician.post(
            f"/api/v1/restores/{restore_id}/reopen", headers=_auth_headers(physician_csrf)
        ).status_code
        == 403
    )


# --- S56 §1 quiesce: writes fenced behind maintenance, worker idles ---


def test_commit_quiesce_blocks_writes_and_worker(clean_registry, monkeypatch) -> None:
    """S56 §1: held maintenance 503s writes/ready and idles the worker (T8)."""
    from x_insight.reasoning import worker as worker_module

    fixture = _setup_staged(clean_registry, monkeypatch, "quiesce")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    job, digest = fixture["job"], fixture["digest"]
    physician, physician_csrf = fixture["physician"], fixture["physician_csrf"]

    monkeypatch.setenv("RESTORE_COMMIT_FAIL_AT", "before_switch")
    held = _post_commit(admin, admin_csrf, job["id"], digest)
    assert held.status_code == 500, held.text
    assert held.json()["maintenance_active"] is True

    # Non-restore writes fail safe 503 MAINTENANCE; reads stay usable.
    blocked = physician.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345000",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(physician_csrf),
    )
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["code"] == "MAINTENANCE"
    assert physician.get("/api/v1/patients").status_code == 200
    ready = admin.get("/api/v1/ready")
    assert ready.status_code == 503, ready.text
    assert ready.json()["code"] == "MAINTENANCE"

    # T8: the worker claims nothing behind maintenance (no mixed traffic).
    outcome = worker_module.run_once(clean_registry)
    assert outcome["status"] == "maintenance", outcome

    # A second commit behind maintenance fails 409 without further mutation.
    again = _post_commit(admin, admin_csrf, job["id"], digest)
    assert again.status_code == 409, again.text

    # Operator reopens only after verified health: clear the injection first.
    monkeypatch.delenv("RESTORE_COMMIT_FAIL_AT", raising=False)
    reopened = admin.post(f"/api/v1/restores/{job['id']}/reopen", headers=_auth_headers(admin_csrf))
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["maintenance_active"] is False
    assert admin.get("/api/v1/ready").status_code == 200
    working = physician.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345000",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(physician_csrf),
    )
    assert working.status_code == 201, working.text


# --- S56 §2: phased switch, no mixed traffic, staged-vs-live reads ---


def test_phased_switch_replaces_live_without_mixed_traffic(clean_registry, monkeypatch) -> None:
    """S56 §2: post-backup live patient removed; old lease rejected (fenced)."""
    from x_insight.reasoning import queue as queue_module

    _new_physician(clean_registry, monkeypatch, "dr_s56_switch")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s56_switch")
    encounter_id, revision = _open_draft(
        physician, physician_csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    started = _start_batch(physician, physician_csrf, encounter_id, revision)
    batch_id = started["batch"]["id"]
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-switch")
    assert claimed is not None and "job" in claimed, claimed
    old_job_id = claimed["job"]["id"]
    old_token = str(claimed["lease_token"])

    # Historical-only fixture: verify retained legacy notes survive replacement.
    from x_insight.cases import tables as cases_tables

    legacy_id = uuid_module.uuid4()
    with clean_registry.begin() as connection:
        owner = connection.execute(
            text("SELECT author_id FROM encounters WHERE id = :id"), {"id": encounter_id}
        ).scalar_one()
        connection.execute(
            cases_tables.notes.insert().values(
                id=legacy_id,
                encounter_id=uuid_module.UUID(encounter_id),
                page="history",
                author_id=owner,
                author_display="historical physician",
                created_at=contracts.utcnow(),
                text="LEGACY-NOTE-RESTORE-SENTINEL",
            )
        )

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _import_network(admin, admin_csrf, "s56-switch-net")
    blob, _ = _make_backup_blob(admin, admin_csrf, key="s56-switch-1")
    staged = _post_validate(admin, admin_csrf, blob).json()
    assert staged["status"] == "succeeded"

    # Live diverges after the backup: a post-backup patient only the switch
    # can remove.
    _register_patient(physician, physician_csrf, "0099999999")
    assert physician.get("/api/v1/patients").json()["total"] == 2

    committed = _post_commit(admin, admin_csrf, staged["id"], staged["confirmation_digest"])
    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert [h["phase"] for h in body["commit_history"]] == EXPECTED_PHASES

    # Staged-vs-live: the post-backup patient is gone; staged content reads.
    # Batch reads are author-only, and the switch revoked sessions, so the
    # draft author re-logs in to read both.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anew, _anew_csrf = _admin_client(clean_registry, monkeypatch)
    identifiers = sorted(p["identifier"] for p in anew.get("/api/v1/patients").json()["items"])
    assert identifiers == ["0012345678"]
    author, _ = _login_physician(clean_registry, monkeypatch, "dr_s56_switch")
    batches = author.get(f"/api/v1/generation-batches/{batch_id}")
    assert batches.status_code == 200, batches.text
    # Observe restored historical storage through the portable backup interface.
    anew, anew_csrf = _admin_client(clean_registry, monkeypatch)
    restored_blob, _ = _make_backup_blob(anew, anew_csrf, key="s56-restored-legacy-notes")
    with zipfile.ZipFile(io.BytesIO(restored_blob)) as archive:
        stored = json.loads(archive.read("database.json"))
    legacy = next(note for note in stored["tables"]["notes"] if note["id"] == str(legacy_id))
    assert legacy["text"] == "LEGACY-NOTE-RESTORE-SENTINEL"
    assert legacy["page"] == "history"
    assert author.get(f"/api/v1/encounters/{encounter_id}/notes").status_code == 404

    # No mixed old/new traffic: the pre-switch lease is rejected after the
    # generation fence (heartbeat refuses; a late commit cannot land).
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, old_job_id, old_token) is False
    with db_module.session_scope(clean_registry) as session:
        try:
            queue_module.commit_job_result(
                session,
                old_job_id,
                old_token,
                provider_ok=True,
                provider_payload={"stub": "late"},
            )
            landed = True
        except contracts.ContractError as exc:
            landed = False
            assert exc.code in (
                "DEPLOYMENT_FENCED",
                "FENCING_TOKEN_MISMATCH",
                "LEASE_EXPIRED",
            ), exc.code
    assert landed is False


# --- S56 §3: sessions revoked, grants revoked, jobs reconciled ---


def test_commit_revokes_sessions_requiring_relogin(clean_registry, monkeypatch) -> None:
    """S56 §3: old admin/physician cookies 401 after commit; re-login 200."""
    fixture = _setup_staged(clean_registry, monkeypatch, "sessions")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    physician = fixture["physician"]
    job, digest = fixture["job"], fixture["digest"]

    committed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert committed.status_code == 200, committed.text
    assert committed.json()["report"]["sessions_revoked"] >= 1

    assert admin.get("/api/v1/me").status_code == 401
    assert physician.get("/api/v1/patients").status_code == 401

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh_admin = TestClient(app)
    admin_relogin = fresh_admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert admin_relogin.status_code == 200, admin_relogin.text
    fresh_physician = TestClient(app)
    physician_relogin = fresh_physician.post(
        "/api/v1/auth/login",
        json={"username": "dr_s56_sessions", "password": "pw123", "role": "physician"},
    )
    assert physician_relogin.status_code == 200, physician_relogin.text
    assert fresh_physician.get("/api/v1/patients").status_code == 200


def test_commit_reconciles_pending_jobs_and_preserves_terminal(clean_registry, monkeypatch) -> None:
    """S56 §3: eligible requeued, stale cancelled, terminal kept with results."""
    from x_insight.reasoning import provider as provider_module
    from x_insight.reasoning import queue as queue_module
    from x_insight.reasoning import worker as worker_module

    _new_physician(clean_registry, monkeypatch, "dr_s56_recon")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s56_recon")

    # Terminal first (oldest): run the stub so the job succeeds with results.
    first_id, first_rev = _open_draft(
        physician, physician_csrf, "0012345678", {"h_a": "yes", "h_b": "no"}
    )
    first = _start_batch(physician, physician_csrf, first_id, first_rev)
    first_batch, first_run = first["batch"]["id"], first["question_runs"][0]["id"]
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert (
        physician.get(f"/api/v1/generation-batches/{first_batch}").json()["job"]["status"]
        == "succeeded"
    )

    # Eligible active: queued then claimed (leased + grant) before the backup.
    second_id, second_rev = _open_draft(
        physician, physician_csrf, "0012345679", {"h_a": "yes", "h_b": "no"}
    )
    second = _start_batch(physician, physician_csrf, second_id, second_rev)
    second_batch = second["batch"]["id"]
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, "worker-recon")
    assert claimed is not None and "job" in claimed, claimed
    assert str(claimed["job"]["batch_id"]) == second_batch
    old_token = str(claimed["lease_token"])

    # Ineligible active: a stale fingerprint (draft edited, no new batch) stays
    # queued at backup time but can never resume.
    third_id, third_rev = _open_draft(
        physician, physician_csrf, "0012345680", {"h_a": "yes", "h_b": "no"}
    )
    third = _start_batch(physician, physician_csrf, third_id, third_rev)
    third_batch = third["batch"]["id"]
    restaled = physician.patch(
        f"/api/v1/encounters/{third_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(physician_csrf), "If-Match": contracts.format_etag(third_rev)},
    )
    assert restaled.status_code == 200, restaled.text
    assert (
        physician.get(f"/api/v1/generation-batches/{third_batch}").json()["job"]["status"]
        == "queued"
    )

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _import_network(admin, admin_csrf, "s56-recon-net")
    blob, _ = _make_backup_blob(admin, admin_csrf, key="s56-recon-1")
    staged = _post_validate(admin, admin_csrf, blob).json()
    assert staged["status"] == "succeeded"

    committed = _post_commit(admin, admin_csrf, staged["id"], staged["confirmation_digest"])
    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["report"]["grants_revoked"] >= 1
    summary = body["report"]["reconciled"]
    assert summary["requeued"] >= 1
    assert summary["cancelled"] >= 1
    assert summary["preserved_terminal"] >= 1

    # Old lease cannot commit after the fence + lease clear.
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.heartbeat_job(session, claimed["job"]["id"], old_token) is False

    # Re-login (sessions revoked) and read every lifecycle through T8 HTTP.
    fresh, fresh_csrf = _login_physician(clean_registry, monkeypatch, "dr_s56_recon")
    eligible = fresh.get(f"/api/v1/generation-batches/{second_batch}").json()
    assert eligible["job"]["status"] == "queued", eligible
    ineligible = fresh.get(f"/api/v1/generation-batches/{third_batch}").json()
    assert ineligible["job"]["status"] == "cancelled", ineligible
    terminal = fresh.get(f"/api/v1/generation-batches/{first_batch}").json()
    assert terminal["job"]["status"] == "succeeded", terminal
    assert terminal["job"]["result"] is not None
    # Saved-CPT replay surface stays readable for local retry/reset work.
    review = fresh.get(f"/api/v1/question-runs/{first_run}/review")
    assert review.status_code == 200, review.text
    assert review.json()["question_run"]["id"] == first_run
    assert review.json()["job"]["status"] == "succeeded"
    assert review.json()["attempts"] >= 1


def test_commit_audits_success_and_health_before_reopen(clean_registry, monkeypatch) -> None:
    """S56 §3: commit.success audit in restored DB + operator log; ready 200."""
    fixture = _setup_staged(clean_registry, monkeypatch, "audithealth")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    job, digest = fixture["job"], fixture["digest"]

    before = admin.get(
        "/api/v1/audit-events", params={"operation": "restores.commit.success"}
    ).json()["total"]
    committed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert committed.status_code == 200, committed.text
    pre_id = committed.json()["pre_restore_backup_id"]

    # Sessions revoked: re-login, then the restored DB carries the audit.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh = TestClient(app)
    relogin = fresh.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert relogin.status_code == 200, relogin.text
    events = fresh.get(
        "/api/v1/audit-events", params={"operation": "restores.commit.success"}
    ).json()
    assert events["total"] == before + 1
    event = next(i for i in events["items"] if i["details"].get("restore_id") == job["id"])
    assert event["details"]["pre_restore_backup_id"] == pre_id
    assert event["details"]["confirmation_digest"] == digest
    lowered = json.dumps(event).lower()
    for token in ("password", "passwd", "secret", "private_key", "csrf_token"):
        assert token not in lowered, token

    # External operator record mirrors the in-DB audit (readable post-switch).
    records = _operator_records()
    assert any(
        r.get("operation") == "restores.commit.success" and r.get("restore_id") == job["id"]
        for r in records
    )

    # Health already verified before reopen: login + reads + ready 200.
    assert fresh.get("/api/v1/me").status_code == 200
    assert fresh.get("/api/v1/patients").status_code == 200
    assert fresh.get(f"/api/v1/backups/{pre_id}").status_code == 200
    assert fresh.get("/api/v1/ready").status_code == 200
    assert fresh.get(f"/api/v1/restores/{job['id']}").json()["maintenance_active"] is False


# --- S56 §4: failure injection with rollback, maintenance held ---


def test_fail_before_switch_preserves_live_and_reopens(clean_registry, monkeypatch) -> None:
    """S56 §4: before_switch fails 500; live/sessions intact; reopen 200."""
    fixture = _setup_staged(clean_registry, monkeypatch, "failbefore")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    physician = fixture["physician"]
    job, digest = fixture["job"], fixture["digest"]
    live_before = physician.get("/api/v1/patients").json()

    monkeypatch.setenv("RESTORE_COMMIT_FAIL_AT", "before_switch")
    failed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert failed.status_code == 500, failed.text
    body = failed.json()
    assert body["commit_status"] == "failed", body
    assert body["maintenance_active"] is True
    assert "rollback" in (body["report"] or {})

    # Live was never mutated: sessions preserved, records byte-identical.
    assert physician.get("/api/v1/patients").json() == live_before
    assert admin.get("/api/v1/me").status_code == 200
    assert admin.get("/api/v1/ready").status_code == 503
    records = _operator_records()
    assert any(
        r.get("operation") == "restores.commit.failed" and r.get("restore_id") == job["id"]
        for r in records
    )

    # Maintenance remains until verified healthy: reopen clears it.
    monkeypatch.delenv("RESTORE_COMMIT_FAIL_AT", raising=False)
    reopened = admin.post(f"/api/v1/restores/{job['id']}/reopen", headers=_auth_headers(admin_csrf))
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["maintenance_active"] is False
    assert admin.get("/api/v1/ready").status_code == 200
    assert physician.get("/api/v1/patients").json() == live_before


def test_fail_during_restart_rolls_back_and_holds(clean_registry, monkeypatch) -> None:
    """S56 §4: during_restart fails 500; prior DB restored; reopen 200."""
    fixture = _setup_staged(clean_registry, monkeypatch, "failrestart")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    physician, physician_csrf = fixture["physician"], fixture["physician_csrf"]
    job, digest = fixture["job"], fixture["digest"]

    # Post-backup live state the rollback must restore.
    _register_patient(physician, physician_csrf, "0099999999")
    assert physician.get("/api/v1/patients").json()["total"] == 2

    monkeypatch.setenv("RESTORE_COMMIT_FAIL_AT", "during_restart")
    failed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert failed.status_code == 500, failed.text
    body = failed.json()
    assert body["commit_status"] == "rolled_back", body
    assert body["maintenance_active"] is True

    # The switch revoked sessions: old cookies 401 even though live rolled back.
    assert physician.get("/api/v1/patients").status_code == 401
    assert admin.get("/api/v1/me").status_code == 401

    # Rollback readable with the re-login: prior records (both patients) back.
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh_admin, fresh_csrf = _admin_client(clean_registry, monkeypatch)
    identifiers = sorted(
        p["identifier"] for p in fresh_admin.get("/api/v1/patients").json()["items"]
    )
    assert identifiers == ["0012345678", "0099999999"]
    # Maintenance held: writes and readiness still fenced.
    assert fresh_admin.get("/api/v1/ready").status_code == 503

    monkeypatch.delenv("RESTORE_COMMIT_FAIL_AT", raising=False)
    reopened = fresh_admin.post(
        f"/api/v1/restores/{job['id']}/reopen", headers=_auth_headers(fresh_csrf)
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["maintenance_active"] is False
    assert fresh_admin.get("/api/v1/ready").status_code == 200


def test_fail_health_check_rolls_back_and_holds(clean_registry, monkeypatch) -> None:
    """S56 §4: health_check fails 500; rollback readable; reopen 200."""
    fixture = _setup_staged(clean_registry, monkeypatch, "failhealth")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]
    physician, physician_csrf = fixture["physician"], fixture["physician_csrf"]
    job, digest = fixture["job"], fixture["digest"]

    _register_patient(physician, physician_csrf, "0099999999")

    monkeypatch.setenv("RESTORE_COMMIT_FAIL_AT", "health_check")
    failed = _post_commit(admin, admin_csrf, job["id"], digest)
    assert failed.status_code == 500, failed.text
    body = failed.json()
    assert body["commit_status"] == "rolled_back", body
    assert body["commit_phase"] == "rollback"
    assert body["maintenance_active"] is True
    assert [h["phase"] for h in body["commit_history"]][-1] == "rollback"

    # Sessions revoked by the switch; rollback restored prior records.
    assert physician.get("/api/v1/patients").status_code == 401
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    fresh_admin, _ = _admin_client(clean_registry, monkeypatch)
    identifiers = sorted(
        p["identifier"] for p in fresh_admin.get("/api/v1/patients").json()["items"]
    )
    assert identifiers == ["0012345678", "0099999999"]
    assert fresh_admin.get("/api/v1/ready").status_code == 503
    records = _operator_records()
    failed_ops = [
        r
        for r in records
        if r.get("operation") == "restores.commit.failed" and r.get("restore_id") == job["id"]
    ]
    assert len(failed_ops) == 1
    assert failed_ops[0]["rolled_back"] is True

    monkeypatch.delenv("RESTORE_COMMIT_FAIL_AT", raising=False)
    relogin = TestClient(app)
    login = relogin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    reopened = relogin.post(
        f"/api/v1/restores/{job['id']}/reopen",
        headers=_auth_headers(login.json()["csrf_token"]),
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["maintenance_active"] is False
    assert relogin.get("/api/v1/ready").status_code == 200
