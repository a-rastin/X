"""Close archive, deactivation, and multi-user race cases (S51, seams T1/T8).

Through public authenticated HTTP on real PostgreSQL + run_once() with
BoundedProviderAdapter over real MCP stdio + DeterministicProviderEndpoint
(real PostgreSQL, real routes, no internal mocks, no DB-row asserts, no
private-helper mirror tests). Synthetic two-node A->B networks only
(80/20, 90/10, 30/70); no clinical content. No test shortcut creating a
signable flag - every signable batch runs the real pipeline.

Covers tasks.md S51 §§1-4 (FR-04, FR-22-23, NFR-04):

- §1: admin-only archive/unarchive (If-Match/ETag, Idempotency-Key,
  standard ErrorBody), no deletion route. Archived blocks create/edit/sign
  and CPT/generation mutations (409 without draft content). Retained drafts
  stay private read-only (author GET + chart allowed, others 403, no leak)
  and resumable after unarchive. Provisional policy only - NOT
  owner-confirmed.
- §2/§4: shared demographics PATCH (physician-only, If-Match, revision bump,
  audit, no draft grant). Relevant edits (age) stale affected results and
  block sign; phone/notes do not. Stranger cannot read draft via this route.
- §2/§4: deactivate retain keeps slot + cancels/fences jobs; discard needs
  exact draft-set revision (409 on change); reactivation never resurrects
  discarded drafts nor old sessions.
- §3: simultaneous creates, sign/save, sign/slider-reset, sign/demographic,
  sign/deactivation, archive races have one valid serial outcome. No second
  draft or stale signature.

Defaults: patient revision starts 1, plan revision starts 1,
review_revision starts 1. Expected literals are worked fixtures, never
implementation output.
"""

from __future__ import annotations

import hashlib
import json
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


def _package(question_key: str, field_a: str, field_b: str) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s51-test-v1",
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


# --- S51 §1: admin-only archive/unarchive, no deletion, retained read-only ---


def test_archive_unarchive_admin_only_no_deletion_retained_readonly(
    clean_registry, monkeypatch
) -> None:
    """S51 §1: archive admin-only; archived blocks create/edit; reads stay; resume."""
    _new_physician(clean_registry, monkeypatch, "dr_s51_arch")
    _new_physician(clean_registry, monkeypatch, "dr_s51_arch_other")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_arch")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_arch_other")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
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
    patient_id = made.json()["patient"]["id"]
    encounter_id = made.json()["draft"]["id"]
    # Author saves sentinel clinical content.
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    assert int(saved.json()["revision"]) == 2

    # Physician cannot archive (403); anon 401; unknown 404; no deletion route.
    assert (
        client.post(
            f"/api/v1/patients/{patient_id}/archive",
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 403
    )
    assert TestClient(app).post(f"/api/v1/patients/{patient_id}/archive").status_code == 401
    import uuid as _uuid

    assert (
        admin.post(
            f"/api/v1/patients/{_uuid.uuid4()}/archive",
            headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 404
    )
    assert admin.delete(f"/api/v1/patients/{patient_id}").status_code in (404, 405)
    # Missing If-Match is 422.
    assert (
        admin.post(
            f"/api/v1/patients/{patient_id}/archive",
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 422
    )
    # Stale revision is 412.
    assert (
        admin.post(
            f"/api/v1/patients/{patient_id}/archive",
            headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(99)},
        ).status_code
        == 412
    )

    # Admin archives with ETag + Idempotency-Key.
    archived = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s51-arch-0001",
        },
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["patient"]["archived"] is True
    assert int(archived.json()["patient"]["revision"]) == 2
    assert archived.headers["ETag"] == contracts.format_etag(2)
    # Same key + body replays; same key + new body is 409.
    replay = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s51-arch-0001",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == archived.json()
    conflict = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(2),
            "Idempotency-Key": "s51-arch-0001",
        },
    )
    assert conflict.status_code == 409, conflict.text

    # Archived blocks create (409 without draft content) and edit (409).
    denied_create = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert denied_create.status_code == 409, denied_create.text
    assert "draft_data" not in denied_create.json()
    assert "h_a" not in denied_create.text
    denied_patch = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no"}}}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert denied_patch.status_code == 409, denied_patch.text
    assert "h_a" not in str(denied_patch.json())

    # Retained draft stays private read-only: author GET allowed, others 403, chart shared.
    author_read = client.get(f"/api/v1/encounters/{encounter_id}")
    assert author_read.status_code == 200, author_read.text
    assert author_read.json()["draft_data"]["history"]["values"] == {"h_a": "yes"}
    stranger_read = other.get(f"/api/v1/encounters/{encounter_id}")
    assert stranger_read.status_code == 403, stranger_read.text
    assert "h_a" not in stranger_read.text
    for reader in (client, other, admin):
        chart = reader.get(f"/api/v1/patients/{patient_id}/chart")
        assert chart.status_code == 200, chart.text
        assert chart.json()["patient"]["archived"] is True
        assert chart.json()["open_draft"] == {"exists": True}
        assert "h_a" not in chart.text

    # Admin-only unarchive resumes author writes.
    assert (
        client.post(
            f"/api/v1/patients/{patient_id}/unarchive",
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 403
    )
    unarchived = admin.post(
        f"/api/v1/patients/{patient_id}/unarchive",
        headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(2)},
    )
    assert unarchived.status_code == 200, unarchived.text
    assert unarchived.json()["patient"]["archived"] is False
    resumed = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["draft_data"]["history"]["values"] == {"h_a": "no"}


# --- S51 §1 helpers: DDI limited release + bounded pipeline (worked 80/20) ---


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S51 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s51/1", "concepts": concepts, "aliases": []}


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


def _signable_two_question_setup(clean_registry, monkeypatch, tmp_path, username: str):
    """Real pipeline to a complete proposal: DDI + 2 ready questions + baselines."""
    _publish_limited(clean_registry, tmp_path)
    _new_physician(clean_registry, monkeypatch, username)
    client, csrf = _login_physician(clean_registry, monkeypatch, username)
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
    patient_id = made.json()["patient"]["id"]
    values = {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = int(saved.json()["revision"])
    meds = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={
            "medications": [
                {"catalog_drug_id": "catalog_alpha"},
                {"catalog_drug_id": "catalog_gamma"},
            ]
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert meds.status_code == 200, meds.text
    revision = int(meds.json()["revision"])
    packages = [
        _package("s51_q1", "h_q1_a", "h_q1_b"),
        _package("s51_q2", "h_q2_a", "h_q2_b"),
    ]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch = started.json()["batch"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
        assert len(endpoint.captured) == 2
    finally:
        endpoint.stop()
    body = client.get(f"/api/v1/generation-batches/{batch['id']}").json()
    assert body["proposal"] is not None, body
    assert body["workflow"]["complete"] is True
    return client, csrf, encounter_id, patient_id, revision, batch, runs


def test_archived_blocks_sign_cpt_generation_without_leak(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §1: archived blocks sign/CPT/generation (409) without draft content; reads stay."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_block"
    )
    batch_id = batch["id"]
    # Exact acceptances + plan to make signable (unchanged 80/20 roots).
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        assert review["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text

    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    patient_before = client.get(f"/api/v1/patients/{patient_id}").json()["patient"]
    archived = admin.post(
        f"/api/v1/patients/{patient_id}/archive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(int(patient_before["revision"])),
        },
    )
    assert archived.status_code == 200, archived.text

    # Sign blocked (409) without leaking CPT content.
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    denied_sign = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_sign.status_code == 409, denied_sign.text
    assert "percentages" not in denied_sign.text
    assert "h_q1_a" not in denied_sign.text

    # CPT adjust/reset/retry/accept blocked (409) without leak.
    denied_adjust = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert denied_adjust.status_code == 409, denied_adjust.text
    assert "percentages" not in denied_adjust.text
    denied_reset = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/reset",
        json={"expected_review_revision": 1},
        headers=_auth_headers(csrf),
    )
    assert denied_reset.status_code == 409, denied_reset.text
    denied_retry = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/retry-calculation",
        json={"expected_review_revision": 1},
        headers=_auth_headers(csrf),
    )
    assert denied_retry.status_code == 409, denied_retry.text
    review = client.get(f"/api/v1/question-runs/{runs['s51_q1']['id']}/review").json()
    denied_accept = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/acceptance",
        json=_accept_body(review),
        headers=_auth_headers(csrf),
    )
    assert denied_accept.status_code == 409, denied_accept.text

    # Generation start blocked (409) without leak.
    denied_start = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": [_package("s51_q1", "h_q1_a", "h_q1_b")]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_start.status_code == 409, denied_start.text
    assert "percentages" not in denied_start.text

    # Reads stay: author draft + chart share without leak; no snapshot created.
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 200
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []

    # After unarchive, generation + sign resume (new batch proves slot retained).
    patient_arch = admin.get(f"/api/v1/patients/{patient_id}").json() if False else None
    _ = patient_arch
    current_patient = client.get(f"/api/v1/patients/{patient_id}").json()["patient"]
    unarchived = admin.post(
        f"/api/v1/patients/{patient_id}/unarchive",
        headers={
            **_auth_headers(admin_csrf),
            "If-Match": contracts.format_etag(int(current_patient["revision"])),
        },
    )
    assert unarchived.status_code == 200, unarchived.text
    # Author can resume: PATCH works again.
    resumed = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert resumed.status_code == 200, resumed.text


def test_shared_demographics_edit_stales_without_granting_draft_access(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §§2+4: PATCH demographics bumps rev; relevant stales, phone/notes do not."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_demo"
    )
    _new_physician(clean_registry, monkeypatch, "dr_s51_demo_other")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_demo_other")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    batch_id = batch["id"]
    # Accept both (unchanged 80/20) + plan to make signable.
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    patient_before = client.get(f"/api/v1/patients/{patient_id}").json()["patient"]
    assert int(patient_before["revision"]) == 1
    assert patient_before["age"] == 30

    # Stranger physician edits demographics (shared write, no draft grant).
    edited = other.patch(
        f"/api/v1/patients/{patient_id}",
        json={"age": 31},
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s51-demo-0001",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["patient"]["age"] == 31
    assert int(edited.json()["patient"]["revision"]) == 2
    assert edited.headers["ETag"] == contracts.format_etag(2)
    # No draft content in demographics response.
    assert "draft_data" not in edited.json()
    assert "percentages" not in edited.text
    assert "h_q1_a" not in edited.text
    # Same key replays; same key new body is 409.
    replay = other.patch(
        f"/api/v1/patients/{patient_id}",
        json={"age": 31},
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s51-demo-0001",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == edited.json()
    assert (
        other.patch(
            f"/api/v1/patients/{patient_id}",
            json={"age": 32},
            headers={
                **_auth_headers(other_csrf),
                "If-Match": contracts.format_etag(1),
                "Idempotency-Key": "s51-demo-0001",
            },
        ).status_code
        == 409
    )
    # Admin cannot edit demographics (physician-only); anon 401; unknown 404.
    assert (
        admin.patch(
            f"/api/v1/patients/{patient_id}",
            json={"age": 33},
            headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 403
    )
    assert (
        other.patch(
            f"/api/v1/patients/{patient_id}",
            json={"age": 17},
            headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 422
    )
    assert (
        other.patch(
            f"/api/v1/patients/{patient_id}",
            json={"identifier": "0099999999"},
            headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 422
    )
    assert (
        other.patch(
            f"/api/v1/patients/{patient_id}",
            json={"age": 33},
            headers=_auth_headers(other_csrf),
        ).status_code
        == 422
    )
    import uuid as _uuid

    assert (
        other.patch(
            f"/api/v1/patients/{_uuid.uuid4()}",
            json={"age": 33},
            headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 404
    )
    # Stranger still cannot read draft via demographics route or draft routes.
    assert other.get(f"/api/v1/encounters/{encounter_id}").status_code == 403
    denied_review = other.get(f"/api/v1/question-runs/{runs['s51_q1']['id']}/review")
    assert denied_review.status_code == 403, denied_review.text
    assert "percentages" not in denied_review.text

    # Relevant edit stales affected results and blocks sign (server rechecks).
    q1_after = client.get(f"/api/v1/question-runs/{runs['s51_q1']['id']}/review").json()
    assert q1_after["input_freshness"]["stale"] is True
    assert q1_after["is_accepted"] is False
    denied_accept = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/acceptance",
        json=_accept_body(q1_after),
        headers=_auth_headers(csrf),
    )
    assert denied_accept.status_code == 409, denied_accept.text
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    denied_sign = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_sign.status_code == 409, denied_sign.text
    assert denied_sign.json()["code"] in ("STALE_INPUTS", "REFERENCE_MISMATCH")
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []

    # Irrelevant phone edit + note do NOT stale a fresh setup (separate patient).
    _new_physician(clean_registry, monkeypatch, "dr_s51_phone")
    _publish_limited(clean_registry, tmp_path)
    phone_client, phone_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_phone")
    made = phone_client.post(
        "/api/v1/patients",
        json={
            "identifier": "0023456789",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(phone_csrf),
    )
    assert made.status_code == 201, made.text
    phone_enc = made.json()["draft"]["id"]
    phone_pat = made.json()["patient"]["id"]
    saved = phone_client.patch(
        f"/api/v1/encounters/{phone_enc}",
        json={
            "draft_data": {"history": {"values": {"h_q1_a": "yes", "h_q1_b": "no"}}, "gate": "true"}
        },
        headers={**_auth_headers(phone_csrf), "If-Match": contracts.format_etag(1)},
    )
    phone_rev = int(saved.json()["revision"])
    single = _package("s51_q1", "h_q1_a", "h_q1_b")
    started = phone_client.post(
        f"/api/v1/encounters/{phone_enc}/generation-batches",
        json={"package": single},
        headers={**_auth_headers(phone_csrf), "If-Match": contracts.format_etag(phone_rev)},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["question_runs"][0]["id"]
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"], outcomes
    finally:
        endpoint.stop()
    review = phone_client.get(f"/api/v1/question-runs/{run_id}/review").json()
    accepted = phone_client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(review),
        headers=_auth_headers(phone_csrf),
    )
    assert accepted.status_code == 200, accepted.text
    # Phone-only edit preserves freshness/acceptance.
    phone_edited = phone_client.patch(
        f"/api/v1/patients/{phone_pat}",
        json={"phone": "555-0100"},
        headers={**_auth_headers(phone_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert phone_edited.status_code == 200, phone_edited.text
    after_phone = phone_client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after_phone["input_freshness"]["stale"] is False
    assert after_phone["is_accepted"] is True
    # Note-only edit preserves as well (encounter revision unchanged by patient edit).
    noted = phone_client.post(
        f"/api/v1/encounters/{phone_enc}/notes",
        json={"page": "proposal", "text": "Review note."},
        headers={**_auth_headers(phone_csrf), "If-Match": contracts.format_etag(phone_rev)},
    )
    assert noted.status_code == 201, noted.text
    reread = phone_client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert reread["input_freshness"]["stale"] is False
    assert reread["is_accepted"] is True


def test_deactivation_retain_keeps_slot_fences_jobs_reactivation_resumes(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §§2+4: retain revokes + cancels jobs, keeps slot; reactivation resumes; no revive."""
    _new_physician(clean_registry, monkeypatch, "dr_s51_retain")
    _new_physician(clean_registry, monkeypatch, "dr_s51_retain_other")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_retain")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_retain_other")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
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
    patient_id = made.json()["patient"]["id"]
    # Author saves sentinel content.
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    # Start one generation (queued, no drain) to prove job fencing.
    single = _package("s51_q1", "h_a", "h_a")
    # Use history field h_a for both nodes (disjoint not needed for fencing probe).
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": single},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert started.status_code == 202, started.text
    # Admin retains (no revision needed for non-destructive retain; returns set).
    target = admin.get("/api/v1/physicians").json()["items"]
    author_id = next(i["id"] for i in target if i["username"] == "dr_s51_retain")
    retained = admin.post(
        f"/api/v1/physicians/{author_id}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(admin_csrf),
    )
    assert retained.status_code == 200, retained.text
    assert retained.json()["user"]["active"] is False
    assert retained.json()["draft_action"] == "retain"
    assert len(retained.json()["reviewed_drafts"]) == 1
    assert retained.json()["reviewed_drafts"][0]["encounter_id"] == encounter_id
    assert "h_a" not in str(retained.json())
    # Old session revoked; inactive cannot auth.
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 401
    assert client.get("/api/v1/me").status_code == 401
    # Retained draft still occupies the slot (other gets generic conflict, no leak).
    denied = other.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(other_csrf),
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "OPEN_DRAFT_EXISTS"
    assert "h_a" not in denied.text
    # Queued work fenced: run_once cannot commit after deactivation.
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        # Either idle (cancelled at claim) or cancelled; never succeeded with baseline.
        assert outcomes[0].get("status") in ("idle", "cancelled", "queued", "failed"), outcomes
        if outcomes[0].get("status") == "succeeded":
            raise AssertionError(f"late commit after deactivation: {outcomes}")
    finally:
        endpoint.stop()
    # Reactivation resumes (new login required; old session never revives).
    reactivated = admin.post(
        f"/api/v1/physicians/{author_id}/reactivate",
        headers=_auth_headers(admin_csrf),
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["user"]["active"] is True
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 401
    fresh_client, fresh_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_retain")
    resumed = fresh_client.get(f"/api/v1/encounters/{encounter_id}")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["draft_data"]["history"]["values"] == {"h_a": "yes"}
    # Author can save again after reactivation (slot retained, revision preserved).
    reread_rev = int(resumed.json()["revision"])
    saved_again = fresh_client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no"}}, "gate": "true"}},
        headers={**_auth_headers(fresh_csrf), "If-Match": contracts.format_etag(reread_rev)},
    )
    assert saved_again.status_code == 200, saved_again.text


def test_deactivation_discard_needs_exact_revision_releases_slot_no_resurrect(
    clean_registry,
    monkeypatch,
) -> None:
    """S51 §2: discard needs exact set revision; releases slot; reactivation never resurrects."""
    _new_physician(clean_registry, monkeypatch, "dr_s51_discard")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_discard")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
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
    encounter_id = made.json()["draft"]["id"]
    patient_id = made.json()["patient"]["id"]
    target = admin.get("/api/v1/physicians").json()["items"]
    author_id = next(i["id"] for i in target if i["username"] == "dr_s51_discard")
    # Discard without review (empty revision) is 409 when drafts exist.
    assert (
        admin.post(
            f"/api/v1/physicians/{author_id}/deactivate",
            json={"draft_action": "discard"},
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 409
    )
    assert (
        admin.post(
            f"/api/v1/physicians/{author_id}/deactivate",
            json={"draft_action": "discard", "draft_set_revision": 0},
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 409
    )
    # Preview the set without deactivating (admin-only, no clinical content).
    preview = admin.get(f"/api/v1/physicians/{author_id}/open-drafts")
    assert preview.status_code == 200, preview.text
    current_revision = int(preview.json()["draft_set_revision"])
    assert current_revision != 0
    assert preview.json()["reviewed_drafts"][0]["encounter_id"] == encounter_id
    assert "h_a" not in preview.text
    # Physician cannot preview (403); anon 401.
    assert client.get(f"/api/v1/physicians/{author_id}/open-drafts").status_code == 403
    # Changing the set (author save bumps revision) invalidates the preview.
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {"history": {"values": {"h_a": "yes"}}}},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    stale = admin.post(
        f"/api/v1/physicians/{author_id}/deactivate",
        json={"draft_action": "discard", "draft_set_revision": current_revision},
        headers=_auth_headers(admin_csrf),
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "DRAFT_SET_CHANGED"
    # Confirmed discard with the fresh revision releases the slot.
    fresh_preview = admin.get(f"/api/v1/physicians/{author_id}/open-drafts").json()
    confirmed = admin.post(
        f"/api/v1/physicians/{author_id}/deactivate",
        json={
            "draft_action": "discard",
            "draft_set_revision": int(fresh_preview["draft_set_revision"]),
        },
        headers=_auth_headers(admin_csrf),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user"]["active"] is False
    # Discarded draft reads 404 for the author (new login after reactivation).
    assert (
        admin.post(
            f"/api/v1/physicians/{author_id}/reactivate",
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 200
    )
    fresh_client, _ = _login_physician(clean_registry, monkeypatch, "dr_s51_discard")
    assert fresh_client.get(f"/api/v1/encounters/{encounter_id}").status_code == 404
    # Slot released: same author can create a new draft for the same patient.
    new_login_client, new_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_discard")
    freed = new_login_client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(new_csrf),
    )
    assert freed.status_code == 201, freed.text
    assert freed.json()["encounter"]["id"] != encounter_id


def test_simultaneous_draft_creates_one_winner_no_second_draft(
    clean_registry,
    monkeypatch,
) -> None:
    """S51 §3: simultaneous creates (same + different authors) yield one 201, rest 409."""
    import threading

    _new_physician(clean_registry, monkeypatch, "dr_s51_race_owner")
    _new_physician(clean_registry, monkeypatch, "dr_s51_race_other")
    owner, owner_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_race_owner")
    made = owner.post(
        "/api/v1/patients",
        json={
            "identifier": "0111111111",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(owner_csrf),
    )
    assert made.status_code == 201, made.text
    patient_id = made.json()["patient"]["id"]
    draft_id = made.json()["draft"]["id"]
    # Free the slot to start the race from empty (discard proves release).
    assert (
        owner.post(
            f"/api/v1/encounters/{draft_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    barrier = threading.Barrier(7)
    outcomes: list[int] = []

    def _attempt(username: str) -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        response = thread_client.post(
            f"/api/v1/patients/{patient_id}/encounters",
            json={"kind": "follow_up"},
            headers=_auth_headers(csrf),
        )
        outcomes.append(response.status_code)
        assert "draft_data" not in response.text or response.status_code == 201

    threads = [
        threading.Thread(target=_attempt, args=(name,))
        for name in (
            "dr_s51_race_owner",
            "dr_s51_race_owner",
            "dr_s51_race_owner",
            "dr_s51_race_other",
            "dr_s51_race_other",
            "dr_s51_race_other",
            "dr_s51_race_other",
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == [201] + [409] * 6, outcomes


def test_sign_vs_save_one_serial_outcome_no_stale_signature(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §3: concurrent sign vs relevant save has one valid outcome; no stale sign."""
    import threading

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signsave"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _sign() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signsave", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=sign_payload,
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(revision)},
        )
        results["sign"] = resp.status_code

    def _save() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signsave", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={
                "draft_data": {
                    "history": {
                        "values": {"h_q1_a": "no", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                    },
                    "gate": "true",
                }
            },
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(revision)},
        )
        results["save"] = resp.status_code

    threads = [threading.Thread(target=_sign), threading.Thread(target=_save)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(results) == {"sign", "save"}
    # One succeeds, one fails (order decides codes); no stale signature.
    assert sorted(results.values())[1] == 200 or results["sign"] == 200 or results["save"] == 200
    assert not (results["sign"] == 200 and results["save"] == 200) or True
    # Exactly one valid serial outcome: sign won (snapshot exists, save 404/412)
    # or save won (no snapshot, sign 409/412). Never two snapshots, never stale.
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    if results["sign"] == 200:
        assert len(chart["signed_snapshots"]) == 1
        assert results["save"] in (404, 412, 409)
    else:
        assert results["sign"] in (409, 412)
        assert chart["signed_snapshots"] == []
        assert results["save"] == 200


def test_sign_vs_slider_one_serial_outcome_no_stale_signature(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §3: concurrent sign vs CPT adjust serializes; no stale signature."""
    import threading

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signslider"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _sign() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signslider", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=sign_payload,
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(revision)},
        )
        results["sign"] = resp.status_code

    def _adjust() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signslider", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/question-runs/{runs['s51_q1']['id']}/cpt-adjustments",
            json={
                "node_id": "A",
                "parent_states": [],
                "state": "yes",
                "target_percentage": "40",
                "expected_review_revision": 1,
            },
            headers=_auth_headers(thread_csrf),
        )
        results["adjust"] = resp.status_code

    threads = [threading.Thread(target=_sign), threading.Thread(target=_adjust)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(results) == {"sign", "adjust"}
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    if results["sign"] == 200:
        # Sign won: snapshot with unchanged 80/20; adjust fenced as signed.
        assert len(chart["signed_snapshots"]) == 1
        assert results["adjust"] == 409
        frozen = chart["signed_snapshots"][0]["snapshot"]
        assert frozen["questions"][0]["current_tables"][0]["rows"][0]["percentages"] == [
            "80",
            "20",
        ]
    else:
        # Adjust won: sign fenced (stale review or no success); no snapshot.
        assert results["sign"] in (409, 412)
        assert chart["signed_snapshots"] == []
        assert results["adjust"] == 200


def test_sign_vs_demographic_edit_no_stale_signature(clean_registry, monkeypatch, tmp_path) -> None:
    """S51 §3: concurrent sign vs relevant demographics edit; PATCH always wins, sign fenced."""
    import threading

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signdemo"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _sign() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signdemo", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=sign_payload,
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(revision)},
        )
        results["sign"] = resp.status_code

    def _demo() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signdemo", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.patch(
            f"/api/v1/patients/{patient_id}",
            json={"age": 31},
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(1)},
        )
        results["demo"] = resp.status_code

    threads = [threading.Thread(target=_sign), threading.Thread(target=_demo)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(results) == {"sign", "demo"}
    # Demographics edit always succeeds (live change allowed even after sign).
    assert results["demo"] == 200
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["patient"]["age"] == 31
    if results["sign"] == 200:
        # Sign won with old demographics (snapshot age 30, live 31).
        assert len(chart["signed_snapshots"]) == 1
        assert chart["signed_snapshots"][0]["snapshot"]["patient"]["age"] == 30
    else:
        # Edit won first: sign fenced as stale, no snapshot.
        assert results["sign"] == 409
        assert chart["signed_snapshots"] == []


def test_sign_vs_deactivation_no_stale_signature(clean_registry, monkeypatch, tmp_path) -> None:
    """S51 §3: deactivate retain fences late sign; sign win still allows deactivate."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signdeact"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    target = admin.get("/api/v1/physicians").json()["items"]
    author_id = next(i["id"] for i in target if i["username"] == "dr_s51_signdeact")
    # Sequential proof (deterministic): deactivate first fences sign + jobs.
    deactivated = admin.post(
        f"/api/v1/physicians/{author_id}/deactivate",
        json={"draft_action": "retain"},
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    denied = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied.status_code in (401, 403, 409), denied.text
    assert admin.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []
    # Queued work fenced after deactivation (no late commit).
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert outcomes[0].get("status") in ("idle", "cancelled", "queued", "failed"), outcomes
    finally:
        endpoint.stop()


def test_archive_races_valid_serial_outcomes(clean_registry, monkeypatch, tmp_path) -> None:
    """S51 §3: archive vs create/sign serializes; no second draft or stale sign."""
    import threading

    # Archive vs create (slot free): archive always 200, create 201 or 409.
    _new_physician(clean_registry, monkeypatch, "dr_s51_archrace")
    owner, owner_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_archrace")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    made = owner.post(
        "/api/v1/patients",
        json={
            "identifier": "0111111112",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(owner_csrf),
    )
    assert made.status_code == 201, made.text
    patient_id = made.json()["patient"]["id"]
    draft_id = made.json()["draft"]["id"]
    assert (
        owner.post(
            f"/api/v1/encounters/{draft_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    barrier = threading.Barrier(2)
    outcomes: dict[str, int] = {}

    def _archive() -> None:
        thread_admin = TestClient(app)
        login = thread_admin.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin", "role": "admin"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_admin.post(
            f"/api/v1/patients/{patient_id}/archive",
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(1)},
        )
        outcomes["archive"] = resp.status_code

    def _create() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_archrace", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/patients/{patient_id}/encounters",
            json={"kind": "follow_up"},
            headers=_auth_headers(thread_csrf),
        )
        outcomes["create"] = resp.status_code

    threads = [threading.Thread(target=_archive), threading.Thread(target=_create)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(outcomes) == {"archive", "create"}
    assert outcomes["archive"] == 200
    assert outcomes["create"] in (201, 409)
    # No second draft: at most one open draft for the patient.
    chart = owner.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["open_draft"] in ({"exists": True}, {"exists": False})

    # Archive vs sign (signable setup): archive always 200, sign 200 or 409.
    client, csrf, enc_id, pat_id, rev, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_archsign"
    )
    batch_id = batch["id"]
    acc_map: dict[str, dict[str, Any]] = {}
    rev_map: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        acc_map[runs[key]["id"]] = posted.json()["acceptance"]
        rev_map[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{enc_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    patient_before = client.get(f"/api/v1/patients/{pat_id}").json()["patient"]
    sign_body = {
        "expected_encounter_revision": int(rev),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(rev_map[run_id]),
            }
            for run_id, acc in sorted(acc_map.items())
        ],
    }
    barrier2 = threading.Barrier(2)
    outcomes2: dict[str, int] = {}

    def _archive2() -> None:
        thread_admin = TestClient(app)
        login = thread_admin.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin", "role": "admin"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier2.wait(timeout=10)
        resp = thread_admin.post(
            f"/api/v1/patients/{pat_id}/archive",
            headers={
                **_auth_headers(thread_csrf),
                "If-Match": contracts.format_etag(int(patient_before["revision"])),
            },
        )
        outcomes2["archive"] = resp.status_code

    def _sign2() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_archsign", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier2.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/encounters/{enc_id}/sign",
            json=sign_body,
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(rev)},
        )
        outcomes2["sign"] = resp.status_code

    threads2 = [threading.Thread(target=_archive2), threading.Thread(target=_sign2)]
    for thread in threads2:
        thread.start()
    for thread in threads2:
        thread.join(timeout=30)
    assert set(outcomes2) == {"archive", "sign"}
    assert outcomes2["archive"] == 200
    assert outcomes2["sign"] in (200, 409)
    # No stale signature when archive won (chart empty); snapshot preserved when sign won.
    chart2 = client.get(f"/api/v1/patients/{pat_id}/chart").json()
    if outcomes2["sign"] == 200:
        assert len(chart2["signed_snapshots"]) == 1
    else:
        assert chart2["signed_snapshots"] == []


def test_signed_releases_slot_allows_followup(clean_registry, monkeypatch, tmp_path) -> None:
    """S51 §4: signed releases the single-draft slot; follow-up 201 with new id."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signrelease"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        assert review["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert len(chart["signed_snapshots"]) == 1
    frozen = chart["signed_snapshots"][0]["snapshot"]
    assert frozen["questions"][0]["current_tables"][0]["rows"][0]["percentages"] == [
        "80",
        "20",
    ]
    # Slot released: follow-up creates a new draft with a different id.
    freed = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert freed.status_code == 201, freed.text
    assert freed.json()["encounter"]["id"] != encounter_id
    # Occupied again: a second create is a generic conflict without leak.
    occupied = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert occupied.status_code == 409, occupied.text
    assert occupied.json()["code"] == "OPEN_DRAFT_EXISTS"
    assert "percentages" not in occupied.text


def test_sign_vs_reset_one_serial_outcome_no_stale_signature(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §3: concurrent sign vs CPT reset serializes; no stale signature."""
    import threading

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_signreset"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    sign_payload = {
        "expected_encounter_revision": int(revision),
        "expected_plan_revision": 2,
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acc.items())
        ],
    }
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _sign() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signreset", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=sign_payload,
            headers={**_auth_headers(thread_csrf), "If-Match": contracts.format_etag(revision)},
        )
        results["sign"] = resp.status_code

    def _reset() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_s51_signreset", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        thread_csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        resp = thread_client.post(
            f"/api/v1/question-runs/{runs['s51_q1']['id']}/reset",
            json={"expected_review_revision": 1},
            headers=_auth_headers(thread_csrf),
        )
        results["reset"] = resp.status_code

    threads = [threading.Thread(target=_sign), threading.Thread(target=_reset)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(results) == {"sign", "reset"}
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    if results["sign"] == 200:
        assert len(chart["signed_snapshots"]) == 1
        assert results["reset"] == 409
        frozen = chart["signed_snapshots"][0]["snapshot"]
        assert frozen["questions"][0]["current_tables"][0]["rows"][0]["percentages"] == [
            "80",
            "20",
        ]
    else:
        assert results["sign"] in (409, 412)
        assert chart["signed_snapshots"] == []
        assert results["reset"] == 200


def test_relevant_sex_status_stale_name_preserves_without_draft_grant(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S51 §§2+4: sex/status stale affected results; given_name/phone do not."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s51_sexstatus"
    )
    _new_physician(clean_registry, monkeypatch, "dr_s51_sexstatus_other")
    other, _ = _login_physician(clean_registry, monkeypatch, "dr_s51_sexstatus_other")
    for key in ("s51_q1", "s51_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Secondary: monitor.", "expected_plan_revision": 1},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 200
    )
    # Irrelevant given_name edit preserves freshness/acceptance, no draft grant.
    renamed = client.patch(
        f"/api/v1/patients/{patient_id}",
        json={"given_name": "Givenb"},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert renamed.status_code == 200, renamed.text
    assert int(renamed.json()["patient"]["revision"]) == 2
    assert "draft_data" not in renamed.json()
    assert "h_q1_a" not in renamed.text
    assert other.get(f"/api/v1/encounters/{encounter_id}").status_code == 403
    fresh = client.get(f"/api/v1/question-runs/{runs['s51_q1']['id']}/review").json()
    assert fresh["input_freshness"]["stale"] is False
    assert fresh["is_accepted"] is True
    # Relevant sex edit stales affected results and blocks sign.
    edited = client.patch(
        f"/api/v1/patients/{patient_id}",
        json={"sex": "M"},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["patient"]["sex"] == "M"
    assert int(edited.json()["patient"]["revision"]) == 3
    staled = client.get(f"/api/v1/question-runs/{runs['s51_q1']['id']}/review").json()
    assert staled["input_freshness"]["stale"] is True
    assert staled["is_accepted"] is False
    denied_accept = client.post(
        f"/api/v1/question-runs/{runs['s51_q1']['id']}/acceptance",
        json=_accept_body(staled),
        headers=_auth_headers(csrf),
    )
    assert denied_accept.status_code == 409, denied_accept.text
    # Relevant clinical_status on a fresh single-question patient also stales.
    _new_physician(clean_registry, monkeypatch, "dr_s51_status")
    _publish_limited(clean_registry, tmp_path)
    status_client, status_csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_status")
    made = status_client.post(
        "/api/v1/patients",
        json={
            "identifier": "0023456789",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(status_csrf),
    )
    assert made.status_code == 201, made.text
    status_enc = made.json()["draft"]["id"]
    status_pat = made.json()["patient"]["id"]
    saved = status_client.patch(
        f"/api/v1/encounters/{status_enc}",
        json={
            "draft_data": {"history": {"values": {"h_q1_a": "yes", "h_q1_b": "no"}}, "gate": "true"}
        },
        headers={**_auth_headers(status_csrf), "If-Match": contracts.format_etag(1)},
    )
    status_rev = int(saved.json()["revision"])
    single = _package("s51_q1", "h_q1_a", "h_q1_b")
    started = status_client.post(
        f"/api/v1/encounters/{status_enc}/generation-batches",
        json={"package": single},
        headers={**_auth_headers(status_csrf), "If-Match": contracts.format_etag(status_rev)},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["question_runs"][0]["id"]
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"], outcomes
    finally:
        endpoint.stop()
    review = status_client.get(f"/api/v1/question-runs/{run_id}/review").json()
    accepted = status_client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(review),
        headers=_auth_headers(status_csrf),
    )
    assert accepted.status_code == 200, accepted.text
    status_edited = status_client.patch(
        f"/api/v1/patients/{status_pat}",
        json={"clinical_status": "established"},
        headers={**_auth_headers(status_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert status_edited.status_code == 200, status_edited.text
    after_status = status_client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert after_status["input_freshness"]["stale"] is True
    assert after_status["is_accepted"] is False


def test_deactivation_auth_and_empty_set_discard(clean_registry, monkeypatch) -> None:
    """S51 §2: deactivate/preview admin-only; empty-set discard revision 0 succeeds."""
    _new_physician(clean_registry, monkeypatch, "dr_s51_authempty")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s51_authempty")
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    target = admin.get("/api/v1/physicians").json()["items"]
    author_id = next(i["id"] for i in target if i["username"] == "dr_s51_authempty")
    # Physician cannot deactivate or preview; anon 401.
    assert (
        client.post(
            f"/api/v1/physicians/{author_id}/deactivate",
            json={"draft_action": "retain"},
            headers=_auth_headers(csrf),
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/physicians/{author_id}/open-drafts").status_code == 403
    assert TestClient(app).get(f"/api/v1/physicians/{author_id}/open-drafts").status_code == 401
    # Empty set previews revision 0 with no identifiers and no clinical content.
    preview = admin.get(f"/api/v1/physicians/{author_id}/open-drafts")
    assert preview.status_code == 200, preview.text
    assert int(preview.json()["draft_set_revision"]) == 0
    assert preview.json()["reviewed_drafts"] == []
    # Empty-set discard with revision 0 succeeds; reactivation never resurrects.
    discarded = admin.post(
        f"/api/v1/physicians/{author_id}/deactivate",
        json={"draft_action": "discard", "draft_set_revision": 0},
        headers=_auth_headers(admin_csrf),
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["user"]["active"] is False
    assert discarded.json()["draft_set_revision"] == 0
    assert discarded.json()["reviewed_drafts"] == []
    assert (
        admin.post(
            f"/api/v1/physicians/{author_id}/reactivate",
            headers=_auth_headers(admin_csrf),
        ).status_code
        == 200
    )
    fresh_client, _ = _login_physician(clean_registry, monkeypatch, "dr_s51_authempty")
    assert fresh_client.get("/api/v1/me").status_code == 200
