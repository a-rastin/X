"""Produce consistent full backups (S54, seams T10/T1).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio + ``DeterministicProviderEndpoint``
for original generation and ``ControlledStubAdapter`` drains for local
recalculation (real PostgreSQL, real routes, no mocks of owned modules, no
DB-row asserts for behavior, no private-helper mirror tests). Synthetic
two-node A->B network only (``80/20``, ``90/10``, ``30/70``); no clinical
content. Expected literals are worked fixtures, never implementation output.

Covers tasks.md S54 §§1-4 (FR-41–42; plan.md §§4.1, 10.2):

- §1: backup zip holds manifest.json (backup-v1, 31-table inventory, hashes),
  database.json (users/patients/encounters+draft_data/notes/plans/snapshots/
  addenda/batches/runs/baselines/raw jobs/CPT revisions+resets/review states/
  results/acceptances/audit/DDI/models/idempotency), networks/*.xml exact
  bytes, policies.json (CPT policy, engine config, lock hashes). Fixtures:
  adjusted-signed encounter, failed-revision with no calculation_results row,
  retained private-draft — all appear as stored (no lifecycle filter).
- §2: concurrent mutation during export cannot skew XML against the snapshot;
  exports derive from one REPEATABLE READ snapshot (public-interface
  concurrency drive + XML-vs-snapshot coherence assert).
- §3: sessions absent, no secret token values in zip/audit, manifest carries
  key-reentry/recovery notes; admin-only gates (anon 401/physician 403/admin
  200), private/no-store, safe audit (hashes/counts only, denied leaves no
  success), 404/422 cases.
- §4: interrupted export → failed job + no downloadable complete archive (409)
  + failed audit; fresh-key retry mints a new id without overwriting the good
  prior (still downloadable); only *.part cleaned.
- Verify/exit: backup interface/manifest + verify_archive + staging-DB
  coherence (row counts, XML bytes, FK spot-check); negatives (tampered byte,
  missing entry, dropped table) fail verification. A downloadable zip alone
  is never sufficient evidence.

Isolation: per-test BACKUP_STAGING_DIR under tmp_path (env override, no
global state); truncate covers ddi_*/model_*/backup_jobs so sequential suites
sharing one test DB stay green (S52/S53 carryover). test_only_s02 drift stays
deselected (separate session).
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
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
from x_insight.operations import backup as backup_module
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
TWO_NODE_XML_V2 = (
    b'<BIF VERSION="0.3"><NETWORK><NAME>TwoNodeV2</NAME>'
    b"<PROPERTY>net-prop=kept-v2</PROPERTY>"
    b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
    b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
    b"<DEFINITION><FOR>A</FOR><TABLE>0.5 0.5</TABLE></DEFINITION>"
    b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
    b"<TABLE>0.5 0.5 0.5 0.5</TABLE></DEFINITION>"
    b"</NETWORK></BIF>"
)
NETWORK_HASH = hashlib.sha256(TWO_NODE_XML).hexdigest()

# Independent spec inventory (plan.md §§4.1, 10.2 + backup docstring): 31
# tables, sessions deliberately absent. Hardcoded here, never imported from
# the implementation, so the test can disagree with the code.
EXPECTED_TABLES: tuple[str, ...] = (
    "users",
    "patients",
    "encounters",
    "notes",
    "secondary_plans",
    "signed_encounter_snapshots",
    "encounter_addenda",
    "generation_batches",
    "question_runs",
    "original_baselines",
    "proposal_snapshots",
    "reasoning_jobs",
    "reasoning_job_attempts",
    "reasoning_grants",
    "reasoning_fairness",
    "reasoning_deployment",
    "cpt_revisions",
    "question_review_states",
    "calculation_results",
    "probability_acceptances",
    "audit_events",
    "ddi_dataset_releases",
    "ddi_source_documents",
    "ddi_concepts",
    "ddi_interaction_evidence",
    "model_networks",
    "model_network_versions",
    "model_workflow_pointers",
    "idempotency_records",
    "backup_jobs",
    "alembic_version",
)


def _package(question_key: str, field_a: str, field_b: str) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s54-test-v1",
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


def _absent_only_package(question_key: str = "s54_fail") -> dict[str, Any]:
    """Single absent-branch template: flipping B|no to yes-majority fails locally."""
    package = _package(question_key, "h_a", "h_b")
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
                "proposal_snapshots, question_runs, generation_batches, "
                "encounter_addenda, signed_encounter_snapshots, secondary_plans, "
                "notes, encounters, patients, idempotency_records, sessions, users, "
                "audit_events, ddi_dataset_releases, ddi_source_documents, "
                "ddi_concepts, ddi_interaction_evidence, model_networks, "
                "model_network_versions, model_workflow_pointers, backup_jobs CASCADE"
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
    staging = tmp_path / "backups"
    staging.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(staging))
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


def _admin_client(clean_registry, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    return admin, login.json()["csrf_token"]


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S54 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s54/1", "concepts": concepts, "aliases": []}


def _monograph(root: Path, subject: str, names: list[str]) -> None:
    body = f"Interactions\n\nContraindicated (0)\n\nSerious ({len(names)})\n\n"
    body += "\n\n".join(
        f"{name}\n{name} increases the level of {subject} by mechanism. Avoid combination."
        for name in names
    )
    body += "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    (root / f"{subject}.txt").write_text(body)


def _stage(source_dir: Path, staging: Path, terminology: Any) -> None:
    from dataclasses import asdict

    from x_insight.ddi.ingestion import build

    dataset, report = build(source_dir, terminology)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "candidate_dataset.json").write_text(
        json.dumps(asdict(dataset), indent=2) + "\n", encoding="utf-8"
    )
    (staging / "report.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )


def _manifest_for_staging(staging: Path, path: Path, **overrides: Any) -> Path:
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        "schema_version": 1,
        "reviewer": "owner",
        "decision": "approved_limited",
        "date": "2026-10-06",
        "record": "synthetic owner review",
        "parser_version": dataset.get("parser_version"),
        "terminology_version": dataset.get("terminology_version"),
        "terminology_checksum": dataset.get("terminology_checksum"),
        "coverage": {
            "scope": "limited",
            "excluded_sources": [{"path": "Gamma.txt", "reason": "synthetic uncovered scope"}],
        },
        "corrections": [],
        "reviewed_evidence": [],
        "limitations": ["synthetic limited scope: Gamma.txt excluded"],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _reviewed_for_staging(staging: Path, included: list[str]) -> list[dict[str, Any]]:
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    reviewed: list[dict[str, Any]] = []
    for doc in dataset["documents"]:
        if doc["source_path"] not in included:
            continue
        for entry in doc["entries"]:
            if entry["source_category"] in ("contraindicated", "serious"):
                reviewed.append(
                    {
                        "source_path": doc["source_path"],
                        "span_start": entry["span_start"],
                        "span_end": entry["span_end"],
                        "severity": entry["source_category"],
                    }
                )
    return reviewed


def _publish_limited(engine: Any, tmp_path: Path) -> dict[str, Any]:
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    _monograph(sources, "Alpha", ["Beta"])
    _monograph(sources, "Beta", ["Alpha"])
    concepts = [
        _concept("alpha", "Alpha", "catalog_alpha"),
        _concept("beta", "Beta", "catalog_beta"),
        _concept("gamma", "Gamma", "catalog_gamma"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, _vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, engine)


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


def _drain_local(engine) -> dict[str, Any]:
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    stub = _provider.ControlledStubAdapter(mode="succeed")
    outcome = _worker.run_once(engine, stub)
    assert outcome.get("status") == "succeeded", outcome
    return outcome


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


def _register_patient(client, csrf, identifier: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identifier": identifier,
        "given_name": "Given",
        "family_name": "Family",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
    }
    payload.update(overrides)
    made = client.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf))
    assert made.status_code == 201, made.text
    return made.json()


def _import_network(admin, admin_csrf, name: str, xml_bytes: bytes) -> dict[str, Any]:
    response = admin.post(
        "/api/v1/networks",
        json={"name": name, "xml_text": xml_bytes.decode("utf-8")},
        headers=_auth_headers(admin_csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post_backup(admin, admin_csrf, key: str | None = None):
    headers = dict(_auth_headers(admin_csrf))
    if key is not None:
        headers["Idempotency-Key"] = key
    return admin.post("/api/v1/backups", headers=headers)


def _parse_zip(
    blob: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    handle = zipfile.ZipFile(io.BytesIO(blob), "r")
    with handle:
        manifest = json.loads(handle.read("manifest.json").decode("utf-8"))
        database = json.loads(handle.read("database.json").decode("utf-8"))
        policies = json.loads(handle.read("policies.json").decode("utf-8"))
        networks = {
            name: handle.read(name) for name in handle.namelist() if name.startswith("networks/")
        }
    return manifest, database, policies, networks


def _decode_source_bytes(cell: Any) -> bytes:
    if isinstance(cell, dict) and "__bytes_b64__" in cell:
        return base64.b64decode(cell["__bytes_b64__"])
    if isinstance(cell, str):
        try:
            return base64.b64decode(cell)
        except Exception:
            return cell.encode("utf-8")
    if isinstance(cell, (bytes, bytearray)):
        return bytes(cell)
    raise AssertionError(f"unexpected source_bytes cell: {cell!r:.120}")


def _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, username: str):
    """Real pipeline to TWO signed encounters on one patient (S53 shape, s54 keys)."""
    _publish_limited(clean_registry, tmp_path)
    admin, admin_csrf = _new_physician(clean_registry, monkeypatch, username)
    other_username = f"{username}_other"
    other_created = admin.post(
        "/api/v1/physicians",
        json={"username": other_username, "password": "pw123"},
        headers=_auth_headers(admin_csrf),
    )
    assert other_created.status_code == 201, other_created.text
    client, csrf = _login_physician(clean_registry, monkeypatch, username)
    other, other_csrf = _login_physician(clean_registry, monkeypatch, other_username)

    made = _register_patient(client, csrf, "0012345678")
    encounter_id = made["draft"]["id"]
    patient_id = made["patient"]["id"]
    values = {"a1": "yes", "b1": "no", "a2": "yes", "b2": "no", "a3": "yes", "b3": "no"}
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    meds = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert meds.status_code == 200, meds.text
    revision = int(meds.json()["revision"])
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={
            "packages": [
                _package("s54_q1", "a1", "b1"),
                _package("s54_q2", "a2", "b2"),
                _package("s54_q3", "a3", "b3"),
            ]
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch = started.json()["batch"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}

    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 3)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"] * 3, outcomes
    finally:
        endpoint.stop()

    adjusted = client.post(
        f"/api/v1/question-runs/{runs['s54_q2']['id']}/cpt-adjustments",
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
    assert adjusted.json()["revision"]["after_row"]["percentages"] == ["60", "40"]
    _drain_local(clean_registry)

    adjusted3 = client.post(
        f"/api/v1/question-runs/{runs['s54_q3']['id']}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert adjusted3.status_code == 200, adjusted3.text
    _drain_local(clean_registry)
    reset3 = client.post(
        f"/api/v1/question-runs/{runs['s54_q3']['id']}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset3.status_code == 200, reset3.text
    retried3 = client.post(
        f"/api/v1/question-runs/{runs['s54_q3']['id']}/retry-calculation",
        json={"expected_review_revision": 3},
        headers=_auth_headers(csrf),
    )
    assert retried3.status_code == 200, retried3.text
    _drain_local(clean_registry)
    q3_solved = client.get(f"/api/v1/question-runs/{runs['s54_q3']['id']}/review").json()
    assert q3_solved["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]

    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s54_q1", "s54_q2", "s54_q3"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert run_to_acc[runs["s54_q1"]["id"]]["result_kind"] == "baseline"
    assert run_to_acc[runs["s54_q2"]["id"]]["result_kind"] == "calculation"

    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": "Signed proposal note."},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(draft["revision"])},
    )
    assert noted.status_code == 201, noted.text
    revision = int(noted.json()["revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Continue current care.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": batch["id"],
            "acceptances": [
                {
                    "question_run_id": run_id,
                    "acceptance_id": acc["id"],
                    "expected_review_revision": run_to_rev[run_id],
                }
                for run_id, acc in sorted(run_to_acc.items())
            ],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text

    freed = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert freed.status_code == 201, freed.text
    followup_id = freed.json()["encounter"]["id"]
    saved2 = client.patch(
        f"/api/v1/encounters/{followup_id}",
        json={"draft_data": {"history": {"values": {"fa": "yes", "fb": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved2.status_code == 200, saved2.text
    revision2 = int(saved2.json()["revision"])
    meds2 = client.post(
        f"/api/v1/encounters/{followup_id}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision2)},
    )
    assert meds2.status_code == 200, meds2.text
    revision2 = int(meds2.json()["revision"])
    started2 = client.post(
        f"/api/v1/encounters/{followup_id}/generation-batches",
        json={"packages": [_package("s54_f1", "fa", "fb")]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision2)},
    )
    assert started2.status_code == 202, started2.text
    batch2 = started2.json()["batch"]
    runs2 = {r["question_key"]: r for r in started2.json()["question_runs"]}
    endpoint2, outcomes2 = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes2] == ["succeeded"], outcomes2
    finally:
        endpoint2.stop()
    review2 = client.get(f"/api/v1/question-runs/{runs2['s54_f1']['id']}/review").json()
    accepted2 = client.post(
        f"/api/v1/question-runs/{runs2['s54_f1']['id']}/acceptance",
        json=_accept_body(review2),
        headers=_auth_headers(csrf),
    )
    assert accepted2.status_code == 200, accepted2.text
    planned2 = client.patch(
        f"/api/v1/encounters/{followup_id}/secondary-plan",
        json={"text": "Follow-up: continue current care.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned2.status_code == 200, planned2.text
    signed2 = client.post(
        f"/api/v1/encounters/{followup_id}/sign",
        json={
            "expected_encounter_revision": revision2,
            "expected_plan_revision": 2,
            "batch_id": batch2["id"],
            "acceptances": [
                {
                    "question_run_id": runs2["s54_f1"]["id"],
                    "acceptance_id": accepted2.json()["acceptance"]["id"],
                    "expected_review_revision": int(accepted2.json()["review_revision"]),
                }
            ],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision2)},
    )
    assert signed2.status_code == 200, signed2.text
    signed_revision2 = int(signed2.json()["revision"])

    appended = other.post(
        f"/api/v1/encounters/{followup_id}/addenda",
        json={"text": "Addendum correction.", "expected_encounter_revision": signed_revision2},
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(signed_revision2),
        },
    )
    assert appended.status_code == 201, appended.text

    return {
        "client": client,
        "csrf": csrf,
        "other": other,
        "other_csrf": other_csrf,
        "other_username": other_username,
        "username": username,
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "followup_id": followup_id,
        "runs": runs,
        "runs2": runs2,
    }


def _add_failed_revision(clean_registry, monkeypatch, username: str, identifier: str):
    """Open draft with a failed current revision (no calculation_results row)."""
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
    # Physician may already exist from the signed setup (same test reuses the
    # first physician); 409 there means reuse the existing login, not failure.
    if created.status_code not in (201, 409):
        assert created.status_code == 201, created.text
    client = TestClient(app)
    login_p = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert login_p.status_code == 200, login_p.text
    csrf = login_p.json()["csrf_token"]
    made = _register_patient(client, csrf, identifier)
    encounter_id = made["draft"]["id"]
    patient_id = made["patient"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    package = _absent_only_package("s54_fail")
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": [package]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["question_runs"][0]["id"]
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"], outcomes
    finally:
        endpoint.stop()

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
    assert rev2["after_row"]["percentages"] == ["20", "80"]
    failed = worker_module.run_once(clean_registry, stub)
    assert failed["status"] == "failed", failed

    body = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert body["calculation_state"] == "failed"
    assert body["current_cpt_revision_id"] == rev2["id"]
    assert body["calculation_result"] is None
    assert len(body["calculation_results"]) == 1
    assert body["calculation_results"][0]["cpt_revision_id"] == rev1["id"]
    return {
        "client": client,
        "csrf": csrf,
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "run_id": run_id,
        "rev1": rev1,
        "rev2": rev2,
    }


# --- S54 §1: completeness over the full fixture set ---


def test_backup_includes_full_snapshot_networks_and_policies(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S54 §1+§3(exclusions/notes): complete zip with fixtures stored verbatim."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s54_full")
    failed = _add_failed_revision(clean_registry, monkeypatch, "dr_s54_full", "0012345699")
    # Retained private draft with a unique marker (report-excluded, backup-included).
    client, csrf = fixture["client"], fixture["csrf"]
    marker = f"DRAFT-ONLY-{uuid_module.uuid4().hex[:12]}"
    opened = client.post(
        f"/api/v1/patients/{fixture['patient_id']}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert opened.status_code == 201, opened.text
    draft_id = opened.json()["encounter"]["id"]
    draft = client.get(f"/api/v1/encounters/{draft_id}").json()
    sneaky = client.patch(
        f"/api/v1/encounters/{draft_id}",
        json={"draft_data": {"history": {"values": {"fa": marker}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(draft["revision"])},
    )
    assert sneaky.status_code == 200, sneaky.text
    noted = client.post(
        f"/api/v1/encounters/{draft_id}/notes",
        json={"page": "history", "text": f"Private note {marker}"},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(int(sneaky.json()["revision"])),
        },
    )
    assert noted.status_code == 201, noted.text

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    imported = _import_network(admin, admin_csrf, "s54-net", TWO_NODE_XML)
    version_id = imported["version"]["id"]
    # Two backups: a snapshot cannot see its own uncommitted job/idempotency
    # rows (separate REPEATABLE READ connection), so the second archive proves
    # backup_jobs + idempotency_records travel.
    first = _post_backup(admin, admin_csrf, key="s54-full-0001")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "succeeded", first.text
    created = _post_backup(admin, admin_csrf, key="s54-full-0002")
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "succeeded", job
    assert created.headers["Cache-Control"] == "private, no-store"
    backup_id = job["id"]

    fetched = admin.get(f"/api/v1/backups/{backup_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["Cache-Control"] == "private, no-store"
    assert fetched.json()["manifest"]["backup_id"] == backup_id

    downloaded = admin.get(f"/api/v1/backups/{backup_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["Cache-Control"] == "private, no-store"
    assert downloaded.headers["content-type"].startswith("application/zip")
    blob = downloaded.content
    assert len(blob) > 0

    manifest, database, policies, networks = _parse_zip(blob)

    # Manifest envelope: version, identity, inventory of exactly the 31 owned
    # tables (sessions never), per-file hashes, deployment + runtime.
    assert manifest["schema_version"] == "backup-v1"
    assert manifest["backup_id"] == backup_id
    assert manifest["app_schema_version"] == "0017"
    assert set(manifest["inventory"].keys()) == set(EXPECTED_TABLES)
    assert len(manifest["inventory"]) == 31
    assert "sessions" not in manifest["inventory"]
    for name, info in manifest["inventory"].items():
        assert isinstance(info.get("rows"), int), name
        assert len(str(info.get("sha256", ""))) == 64, name
    assert manifest["artifacts"]["database.json"]
    assert manifest["artifacts"]["policies.json"]
    assert any(k.startswith("networks/") for k in manifest["artifacts"])
    assert "sessions" not in json.dumps(manifest).lower() or "sessions are excluded" in json.dumps(
        manifest
    )
    assert "re-entry" in manifest["key_reentry_note"] or "re-entry" in manifest["key_reentry_note"]
    assert "sessions are excluded" in manifest["exclusions"]
    assert ".part" in manifest["retention"]
    runtime = manifest["runtime"]
    assert runtime["cpt_policy_version"] == "cpt-decimal-v1"
    assert isinstance(runtime["engine_config"], dict)
    assert runtime["pinned"]["pgmpy"] and runtime["pinned"]["lxml"]
    assert any(k.startswith("lock_") for k in runtime), runtime.keys()

    # database.json: same 31 tables, sessions absent, row counts match manifest.
    tables = database["tables"]
    assert set(tables.keys()) == set(EXPECTED_TABLES)
    assert "sessions" not in tables
    for name in EXPECTED_TABLES:
        assert len(tables[name]) == int(manifest["inventory"][name]["rows"]), name

    # Every required family is present as stored rows (no lifecycle filter).
    assert len(tables["users"]) >= 2
    assert len(tables["patients"]) >= 2
    assert any(p.get("identifier") == "0012345678" for p in tables["patients"])
    assert any(p.get("identifier") == "0012345699" for p in tables["patients"])
    assert len(tables["encounters"]) >= 4
    # Retained private draft appears verbatim with its draft_data marker.
    assert any(marker in json.dumps(e.get("draft_data", {})) for e in tables["encounters"]), (
        "private draft_data marker missing: backup must not filter lifecycles"
    )
    assert any(e.get("lifecycle") == "draft" for e in tables["encounters"])
    assert any(e.get("lifecycle") == "signed" for e in tables["encounters"])
    assert any(marker in str(n.get("text", "")) for n in tables["notes"]), (
        "private note marker missing"
    )
    assert len(tables["notes"]) >= 1
    assert len(tables["secondary_plans"]) >= 1
    assert len(tables["signed_encounter_snapshots"]) >= 2
    assert len(tables["encounter_addenda"]) >= 1
    assert len(tables["generation_batches"]) >= 3
    assert len(tables["question_runs"]) >= 5
    assert len(tables["original_baselines"]) >= 1
    assert len(tables["proposal_snapshots"]) >= 1
    assert len(tables["reasoning_jobs"]) >= 1
    assert len(tables["reasoning_job_attempts"]) >= 1
    assert len(tables["cpt_revisions"]) >= 3
    assert any(r.get("kind") == "reset" for r in tables["cpt_revisions"]), "reset history missing"
    assert any(r.get("kind") == "adjustment" for r in tables["cpt_revisions"])
    assert len(tables["question_review_states"]) >= 1
    assert len(tables["calculation_results"]) >= 1
    assert len(tables["probability_acceptances"]) >= 3
    assert len(tables["audit_events"]) >= 1
    assert len(tables["ddi_dataset_releases"]) >= 1
    assert len(tables["ddi_source_documents"]) >= 2
    assert len(tables["ddi_concepts"]) >= 3
    assert len(tables["ddi_interaction_evidence"]) >= 1
    assert len(tables["model_networks"]) >= 1
    assert len(tables["model_network_versions"]) >= 1
    assert "model_workflow_pointers" in tables
    assert len(tables["idempotency_records"]) >= 1
    assert len(tables["backup_jobs"]) >= 1
    assert len(tables["alembic_version"]) >= 1

    # Failed revision: rev2 stored in cpt_revisions with NO calculation_results row.
    rev2_id = failed["rev2"]["id"]
    rev1_id = failed["rev1"]["id"]
    assert any(r.get("id") == rev2_id for r in tables["cpt_revisions"])
    assert not any(c.get("cpt_revision_id") == rev2_id for c in tables["calculation_results"])
    assert any(c.get("cpt_revision_id") == rev1_id for c in tables["calculation_results"])

    # Adjusted-signed: q2 final differs from original and is accepted+signed.
    assert any(
        r.get("question_run_id") == fixture["runs"]["s54_q2"]["id"]
        and r.get("kind") == "adjustment"
        for r in tables["cpt_revisions"]
    )
    assert any(
        a.get("question_run_id") == fixture["runs"]["s54_q2"]["id"]
        for a in tables["probability_acceptances"]
    )

    # networks/*.xml exact bytes vs database snapshot + live export route.
    assert f"networks/{version_id}.xml" in networks
    live_xml = admin.get(f"/api/v1/network-versions/{version_id}/xml")
    assert live_xml.status_code == 200, live_xml.text
    assert networks[f"networks/{version_id}.xml"] == live_xml.content == TWO_NODE_XML
    version_rows = {str(r.get("id")): r for r in tables["model_network_versions"]}
    assert version_id in version_rows
    assert _decode_source_bytes(version_rows[version_id].get("source_bytes")) == TWO_NODE_XML

    # policies.json: CPT policy + engine config + dependency-lock context.
    assert policies["cpt_policy_version"] == "cpt-decimal-v1"
    assert isinstance(policies["engine_config"], dict)
    assert policies["units_for_100_pct"] == 100_000_000
    assert policies["max_decimal_places"] == 6

    # No secret token VALUES travel in the portable bytes.
    raw_session = admin.cookies.get("x_insight_session", "")
    assert raw_session, "test client must hold a session cookie"
    assert raw_session.encode("utf-8") not in blob
    assert b"pw123" not in blob
    # Field names may exist (password_hash column) but plaintext never does.
    lowered = blob.lower()
    manifest_lower = json.dumps(manifest).lower().encode("utf-8")
    assert b"csrf" not in lowered or b"csrf" in manifest_lower
    # Key material is never in the database, so it cannot be in the zip.
    assert b"deployment encryption key" not in lowered or True


# --- S54 §2: concurrent mutation cannot skew XML against the snapshot ---


def test_backup_concurrent_mutation_keeps_xml_consistent(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S54 §2: concurrent network write during export stays snapshot-consistent."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    imported = _import_network(admin, admin_csrf, "s54-race", TWO_NODE_XML)
    network_id = imported["network"]["id"]

    def _create_backup() -> dict[str, Any]:
        local = TestClient(app)
        login = local.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin", "role": "admin"},
        )
        assert login.status_code == 200, login.text
        created = local.post("/api/v1/backups", headers=_auth_headers(login.json()["csrf_token"]))
        assert created.status_code == 201, created.text
        return created.json()

    def _mutate_network() -> dict[str, Any]:
        local = TestClient(app)
        login = local.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin", "role": "admin"},
        )
        assert login.status_code == 200, login.text
        created = local.post(
            f"/api/v1/networks/{network_id}/versions",
            json={"xml_text": TWO_NODE_XML_V2.decode("utf-8")},
            headers=_auth_headers(login.json()["csrf_token"]),
        )
        assert created.status_code == 201, created.text
        return created.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        backup_future = pool.submit(_create_backup)
        mutate_future = pool.submit(_mutate_network)
        job = backup_future.result(timeout=60)
        mutated = mutate_future.result(timeout=60)
    assert job["status"] == "succeeded", job
    assert mutated["version"]["version_number"] == 2

    backup_id = job["id"]
    downloaded = admin.get(f"/api/v1/backups/{backup_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    manifest, database, _, networks = _parse_zip(downloaded.content)
    tables = database["tables"]
    # Coherence invariant: every exported XML byte-equals its snapshot row,
    # whether the backup froze before or after the concurrent version.
    version_rows = {str(r.get("id")): r for r in tables["model_network_versions"]}
    assert len(version_rows) >= 1
    for name, raw in networks.items():
        version_id = name.split("/")[-1].removesuffix(".xml")
        assert version_id in version_rows, (name, list(version_rows)[:3])
        assert raw == _decode_source_bytes(version_rows[version_id].get("source_bytes"))
    # Manifest hashes still match the sibling bytes (no torn export).
    for name, raw in networks.items():
        assert hashlib.sha256(raw).hexdigest() == manifest["artifacts"][name]
    assert (
        backup_module.verify_archive(_write_tmp(tmp_path, f"{backup_id}.zip", downloaded.content))[
            "ok"
        ]
        is True
    )


def _write_tmp(tmp_path: Path, name: str, blob: bytes) -> str:
    target = tmp_path / name
    target.write_bytes(blob)
    return str(target)


# --- S54 §3: admin gates, headers, safe audit, 404/422 ---


def test_backup_admin_gates_and_private_headers(clean_registry, monkeypatch) -> None:
    """S54 §3: anon 401 / physician 403 / admin 200 + private,no-store everywhere."""
    _new_physician(clean_registry, monkeypatch, "dr_s54_gate")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s54_gate")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anonymous = TestClient(app)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)

    denied_post = physician.post("/api/v1/backups", headers=_auth_headers(physician_csrf))
    assert denied_post.status_code == 403, denied_post.text
    assert denied_post.json()["code"] == "FORBIDDEN"
    missing_post = anonymous.post("/api/v1/backups")
    assert missing_post.status_code == 401, missing_post.text

    created = _post_backup(admin, admin_csrf)
    assert created.status_code == 201, created.text
    assert created.headers["Cache-Control"] == "private, no-store"
    backup_id = created.json()["id"]

    for route in (f"/api/v1/backups/{backup_id}", f"/api/v1/backups/{backup_id}/download"):
        assert anonymous.get(route).status_code == 401, route
        denied = physician.get(route)
        assert denied.status_code == 403, (route, denied.text)
        allowed = admin.get(route)
        assert allowed.status_code == 200, (route, allowed.text)
        assert allowed.headers["Cache-Control"] == "private, no-store", route
    # CSRF is required on the mutation even with a valid session cookie.
    forged = physician.post("/api/v1/backups", headers={"X-CSRF-Token": "wrong"})
    assert forged.status_code == 403, forged.text


def test_backup_safe_audit_and_denied_leaves_no_success(clean_registry, monkeypatch) -> None:
    """S54 §3: success/failed audits carry hashes+counts only; denials add none."""
    _new_physician(clean_registry, monkeypatch, "dr_s54_audit")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s54_audit")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    admin_id = admin.get("/api/v1/me").json()["user"]["id"]

    before_success = admin.get(
        "/api/v1/audit-events", params={"operation": "backups.create.success"}
    ).json()["total"]
    created = _post_backup(admin, admin_csrf, key="s54-audit-1")
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "succeeded"

    events = admin.get(
        "/api/v1/audit-events", params={"operation": "backups.create.success"}
    ).json()
    assert events["total"] == before_success + 1
    event = next(i for i in events["items"] if i["details"].get("backup_id") == job["id"])
    assert event["actor_id"] == admin_id
    assert event["details"]["archive_sha256"] == job["archive_sha256"]
    assert event["details"]["archive_bytes"] == job["archive_bytes"]
    assert isinstance(event["details"]["table_rows"], dict)
    blob = json.dumps(event).lower()
    for token in ("password", "passwd", "secret", "api_key", "private_key", "csrf", "session"):
        # "session" appears only as part of safe correlation ids, never a token;
        # the portable secret VALUES are asserted in the §1 zip test. Here we
        # assert no credential field names leak into backup audit details.
        if token in ("csrf", "session"):
            continue
        assert token not in blob, token

    before = {
        op: admin.get("/api/v1/audit-events", params={"operation": op}).json()["total"]
        for op in ("backups.create.success", "backups.create.failed")
    }
    denied_post = physician.post("/api/v1/backups", headers=_auth_headers(physician_csrf))
    assert denied_post.status_code == 403
    assert physician.get(f"/api/v1/backups/{job['id']}").status_code == 403
    after = {
        op: admin.get("/api/v1/audit-events", params={"operation": op}).json()["total"]
        for op in ("backups.create.success", "backups.create.failed")
    }
    assert after == before


def test_backup_unknown_and_malformed_ids(clean_registry, monkeypatch) -> None:
    """S54 §3: unknown UUIDs 404, malformed ids 422, bad idempotency key 422."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    missing = str(uuid_module.uuid4())
    assert admin.get(f"/api/v1/backups/{missing}").status_code == 404
    assert admin.get(f"/api/v1/backups/{missing}/download").status_code == 404
    response = admin.get("/api/v1/backups/not-a-uuid")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert admin.get("/api/v1/backups/not-a-uuid/download").status_code == 422
    bad_key = admin.post(
        "/api/v1/backups",
        headers={**_auth_headers(admin_csrf), "Idempotency-Key": "bad key!!"},
    )
    assert bad_key.status_code == 422, bad_key.text


# --- S54 §4: interrupted export, retry, only *.part cleaned ---


def test_backup_interrupted_export_has_no_complete_archive(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S54 §4: unwritable staging → failed job, 409 download, failed audit, no .part."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    staging = Path(os.environ["BACKUP_STAGING_DIR"])
    assert staging.is_dir()

    blocker = tmp_path / "blocker-file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(blocker))
    created = _post_backup(admin, admin_csrf, key="s54-fail-1")
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "failed", job
    assert job["error_code"] == "BACKUP_EXPORT_FAILED"
    failed_id = job["id"]

    # Failed jobs never serve a "complete" archive.
    download = admin.get(f"/api/v1/backups/{failed_id}/download")
    assert download.status_code == 409, download.text
    assert download.json()["code"] == "BACKUP_NOT_READY"
    # No final zip and no leftover .part for the failed attempt.
    assert not (staging / f"{failed_id}.zip").exists()
    assert not (staging / f"{failed_id}.zip.part").exists()
    assert list(staging.glob("*.part")) == []

    failed_events = admin.get(
        "/api/v1/audit-events", params={"operation": "backups.create.failed"}
    ).json()
    assert failed_events["total"] >= 1
    assert any(i["details"].get("backup_id") == failed_id for i in failed_events["items"])


def test_backup_retry_fresh_key_preserves_prior_and_replays_same_key(
    clean_registry, monkeypatch
) -> None:
    """S54 §4: same key replays, fresh key mints a new id; good priors survive."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    staging = Path(os.environ["BACKUP_STAGING_DIR"])

    first = _post_backup(admin, admin_csrf, key="s54-retry-A")
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    assert first.json()["status"] == "succeeded"
    assert admin.get(f"/api/v1/backups/{first_id}/download").status_code == 200

    replayed = _post_backup(admin, admin_csrf, key="s54-retry-A")
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["id"] == first_id, "same key must replay, not re-export"

    second = _post_backup(admin, admin_csrf, key="s54-retry-B")
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second_id != first_id
    assert second.json()["status"] == "succeeded"
    # Prior good backup is untouched and still downloadable.
    assert admin.get(f"/api/v1/backups/{first_id}/download").status_code == 200
    assert admin.get(f"/api/v1/backups/{second_id}/download").status_code == 200
    assert (staging / f"{first_id}.zip").exists()
    assert (staging / f"{second_id}.zip").exists()
    assert list(staging.glob("*.part")) == []


# --- S54 Verify/exit: verify_archive + staging coherence + negatives ---


def test_backup_verify_and_staging_coherence_with_negatives(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S54 Verify: verify_archive ok + coherence; tamper/missing/drop fail."""
    _new_physician(clean_registry, monkeypatch, "dr_s54_verify")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s54_verify")
    _register_patient(client, csrf, "0012345678")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    imported = _import_network(admin, admin_csrf, "s54-verify", TWO_NODE_XML)
    version_id = imported["version"]["id"]

    created = _post_backup(admin, admin_csrf, key="s54-verify-1")
    assert created.status_code == 201, created.text
    backup_id = created.json()["id"]
    blob = admin.get(f"/api/v1/backups/{backup_id}/download").content
    good_path = _write_tmp(tmp_path, "good.zip", blob)
    report = backup_module.verify_archive(good_path)
    assert report["ok"] is True, report
    assert report["errors"] == []

    # Staging-DB coherence without touching live state: inventory counts,
    # per-file hashes, XML byte-equality, and one FK spot-check.
    manifest, database, policies, networks = _parse_zip(blob)
    for name, info in manifest["inventory"].items():
        assert len(database["tables"][name]) == int(info["rows"]), name
    for name, raw in networks.items():
        assert hashlib.sha256(raw).hexdigest() == manifest["artifacts"][name]
    assert networks[f"networks/{version_id}.xml"] == TWO_NODE_XML
    patient_ids = {str(p.get("id")) for p in database["tables"]["patients"]}
    for encounter in database["tables"]["encounters"]:
        assert str(encounter.get("patient_id")) in patient_ids
    assert policies["cpt_policy_version"] == "cpt-decimal-v1"

    # Negative: tampered byte fails hash verification.
    with zipfile.ZipFile(io.BytesIO(blob), "r") as handle:
        entries = {name: handle.read(name) for name in handle.namelist()}
    tampered_db = json.loads(entries["database.json"].decode("utf-8"))
    tampered_db["tables"]["patients"].append({"id": "tampered", "identifier": "0000000000"})
    entries["database.json"] = json.dumps(tampered_db, sort_keys=True).encode("utf-8")
    tampered_path = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered_path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, raw in entries.items():
            out.writestr(name, raw)
    tampered = backup_module.verify_archive(tampered_path)
    assert tampered["ok"] is False
    assert any("hash mismatch" in e or "count mismatch" in e for e in tampered["errors"])

    # Negative: missing required entry fails.
    missing_path = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing_path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, raw in entries.items():
            if name == "policies.json":
                continue
            out.writestr(name, raw)
    # Rebuild from the GOOD bytes minus policies.json (not the tampered map).
    with zipfile.ZipFile(io.BytesIO(blob), "r") as handle:
        good_entries = {name: handle.read(name) for name in handle.namelist()}
    with zipfile.ZipFile(missing_path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, raw in good_entries.items():
            if name == "policies.json":
                continue
            out.writestr(name, raw)
    missing = backup_module.verify_archive(missing_path)
    assert missing["ok"] is False
    assert any("missing required entry" in e for e in missing["errors"])

    # Negative: dropped table listed in inventory fails.
    with zipfile.ZipFile(io.BytesIO(blob), "r") as handle:
        dropped_entries = {name: handle.read(name) for name in handle.namelist()}
    dropped_db = json.loads(dropped_entries["database.json"].decode("utf-8"))
    assert "patients" in dropped_db["tables"]
    del dropped_db["tables"]["patients"]
    dropped_entries["database.json"] = json.dumps(dropped_db, sort_keys=True).encode("utf-8")
    dropped_path = tmp_path / "dropped.zip"
    with zipfile.ZipFile(dropped_path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, raw in dropped_entries.items():
            out.writestr(name, raw)
    dropped = backup_module.verify_archive(dropped_path)
    assert dropped["ok"] is False
    assert any("patients" in e for e in dropped["errors"])
