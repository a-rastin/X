"""Atomic plan signing + immutable snapshots (S49, seam T1 only).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio + ``DeterministicProviderEndpoint``
(real PostgreSQL, real routes, no internal mocks, no DB-row asserts, no
private-helper mirror tests). Synthetic two-node A→B networks only
(``80/20``, ``90/10``, ``30/70``); no clinical content. No test shortcut
creating a signable success flag — every signable batch runs the real
pipeline (generation start → worker → MCP → provider → validation →
inference → rendering → proposal).

Covers tasks.md S49 §§1-4 (FR-15, FR-22, FR-42, FR-57, NFR-04):

- §1: separate revisioned secondary plan (own If-Match fence) + sign only
  with complete current proposal, acknowledged saves, exact per-question
  acceptance/result references including unchanged originals.
- §2: reject absent/failed/partial/stale originals, pending/failed/stale/
  mismatched currents, forged manual plans, wrong author/admin, outdated
  revisions. Freshness rechecked INSIDE the locked transaction (S48d
  per-question hash, not batch hash). Server recomputes; client never grants.
- §3: atomic freeze + slot release + audit; idempotent repeats preserve one
  signature; failures leave no partial state.
- §4: signed immutability vs live demographics; any-physician addenda;
  shared chart read without draft leaks.

Defaults: T=100_000_000, review_revision starts 1, plan revision starts 1.
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
            "version": "s49-test-v1",
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


# --- S49 §1: separate revisioned secondary plan (own fence, author-only) ---


def test_secondary_plan_revisioned_own_fence_author_only(clean_registry, monkeypatch) -> None:
    """S49 §1: secondary plan has its own revision; strangers/admin denied; bad input 422/412."""
    _new_physician(clean_registry, monkeypatch, "dr_s49_plan")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_plan")
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

    # Fresh plan reads revision 1 empty (no row yet, no encounter bump).
    first = client.get(f"/api/v1/encounters/{encounter_id}/secondary-plan")
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert first.json()["text"] == ""
    assert first.headers["ETag"] == contracts.format_etag(1)
    draft_before = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert int(draft_before["revision"]) == 1

    # Author saves with its own fence (If-Match plan revision + body revision).
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary: monitor and follow up.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert saved.json()["text"] == "Secondary: monitor and follow up."
    assert saved.headers["ETag"] == contracts.format_etag(2)
    # Separate fences: encounter revision untouched by plan edits.
    draft_after = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert int(draft_after["revision"]) == 1

    # Stale plan revision is 412 and changes nothing.
    stale = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Stale overwrite.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    reread = client.get(f"/api/v1/encounters/{encounter_id}/secondary-plan").json()
    assert reread["revision"] == 2
    assert reread["text"] == "Secondary: monitor and follow up."

    # Header/body disagreement is 412; absent If-Match is 422.
    mismatch = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Mismatch.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert mismatch.status_code == 412, mismatch.text
    missing_header = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "No fence.", "expected_plan_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert missing_header.status_code == 422, missing_header.text

    # Oversized text is 422 without a write.
    big = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "x" * 10_001, "expected_plan_revision": 2},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert big.status_code == 422, big.text
    assert client.get(f"/api/v1/encounters/{encounter_id}/secondary-plan").json()["revision"] == 2

    # Stranger + admin denied without content; missing is 404.
    _new_physician(clean_registry, monkeypatch, "dr_s49_stranger")
    stranger, stranger_csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_stranger")
    denied_read = stranger.get(f"/api/v1/encounters/{encounter_id}/secondary-plan")
    assert denied_read.status_code == 403, denied_read.text
    assert "monitor" not in denied_read.text
    denied_write = stranger.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Stranger edit.", "expected_plan_revision": 2},
        headers={**_auth_headers(stranger_csrf), "If-Match": contracts.format_etag(2)},
    )
    assert denied_write.status_code == 403, denied_write.text
    import uuid as _uuid

    assert client.get(f"/api/v1/encounters/{_uuid.uuid4()}/secondary-plan").status_code == 404


def test_secondary_plan_idempotent_replay_and_conflict(clean_registry, monkeypatch) -> None:
    """S49 §1/§3: same Idempotency-Key + body replays; same key + new body is 409."""
    _new_physician(clean_registry, monkeypatch, "dr_s49_idem")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_idem")
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
    body = {"text": "Plan v1.", "expected_plan_revision": 1}
    first = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json=body,
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s49-plan-0001",
        },
    )
    assert first.status_code == 200, first.text
    replay = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json=body,
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s49-plan-0001",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert client.get(f"/api/v1/encounters/{encounter_id}/secondary-plan").json()["revision"] == 2
    conflict = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Different body.", "expected_plan_revision": 1},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(1),
            "Idempotency-Key": "s49-plan-0001",
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


# --- DDI + pipeline helpers (real completed synthetic results, no shortcut) ---


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S49 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s49/1", "concepts": concepts, "aliases": []}


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
        _package("s49_q1", "h_q1_a", "h_q1_b"),
        _package("s49_q2", "h_q2_a", "h_q2_b"),
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


def _sign_body_after_accept(
    encounter_revision: int,
    plan_revision: int,
    batch_id: str,
    run_to_acceptance: dict[str, dict[str, Any]],
    run_to_review_rev: dict[str, int],
) -> dict[str, Any]:
    return {
        "expected_encounter_revision": int(encounter_revision),
        "expected_plan_revision": int(plan_revision),
        "batch_id": str(batch_id),
        "acceptances": [
            {
                "question_run_id": str(run_id),
                "acceptance_id": str(acc["id"]),
                "expected_review_revision": int(run_to_review_rev[run_id]),
            }
            for run_id, acc in sorted(run_to_acceptance.items())
        ],
    }


# --- S49 §2: absent/failed/partial originals rejected (real pipeline, no shortcut) ---


def test_sign_rejects_absent_failed_partial_proposals(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §2: no proposal / failed original / partial run cannot sign."""
    import uuid as _uuid

    _publish_limited(clean_registry, tmp_path)
    _new_physician(clean_registry, monkeypatch, "dr_s49_incomplete")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_incomplete")
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
    values = {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    revision = int(saved.json()["revision"])
    meds = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    revision = int(meds.json()["revision"])
    packages = [_package("s49_q1", "h_q1_a", "h_q1_b"), _package("s49_q2", "h_q2_a", "h_q2_b")]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text

    # Absent: unknown batch is 404, no snapshot created.
    absent = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": str(_uuid.uuid4()),
            "acceptances": [],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert absent.status_code == 404, absent.text

    # No baselines yet (partial/pending): proposal null → 409, no partial sign.
    empty_body = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert empty_body["proposal"] is None
    pending = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": batch_id,
            "acceptances": [],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert pending.status_code == 409, pending.text
    assert pending.json()["code"] in ("PROPOSAL_INCOMPLETE", "REFERENCE_MISMATCH")

    # Partial: exactly one baseline (run_once once) still incomplete → 409.
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert outcomes[0].get("status") == "succeeded", outcomes
    finally:
        endpoint.stop()
    partial_view = client.get(f"/api/v1/generation-batches/{batch_id}").json()
    assert partial_view["proposal"] is None
    assert partial_view["baselines"][0]["baseline"] is not None
    assert partial_view["baselines"][1]["baseline"] is None
    partial = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": batch_id,
            "acceptances": [],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert partial.status_code == 409, partial.text
    # No partial snapshot leaked via chart; encounter still draft.
    patient_id = made.json()["patient"]["id"]
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 200


def test_sign_rejects_failed_original_without_shortcut(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §2: structurally invalid provider CPTs fail closed; no signable flag."""
    _publish_limited(clean_registry, tmp_path)
    _new_physician(clean_registry, monkeypatch, "dr_s49_failed")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_failed")
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
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    revision = int(saved.json()["revision"])

    single = _package("s49_single", "h_a", "h_b")
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": single},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    network_hash = single["manifest"]["network_hash"]
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
    from datetime import timedelta as _timedelta

    moment = contracts.utcnow()
    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": mutated}]
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
            clean_registry, adapter, now=moment, database_url=db_module.get_test_database_url()
        )
        for _ in range(2):
            if outcome.get("status") in ("failed", "succeeded"):
                break
            moment = moment + _timedelta(seconds=70)
            outcome = worker_module.run_once(
                clean_registry, adapter, now=moment, database_url=db_module.get_test_database_url()
            )
        assert outcome["status"] == "failed", outcome
    finally:
        endpoint.stop()
    run_id = started.json()["question_runs"][0]["id"]
    review = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert review["baseline"] is None
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    denied = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": batch_id,
            "acceptances": [],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "PROPOSAL_INCOMPLETE"


# --- S49 §§1+3: atomic sign freezes, releases slot, audits, idempotent ---


def test_sign_freezes_releases_slot_idempotent(clean_registry, monkeypatch, tmp_path) -> None:
    """S49 §§1+3: exact acceptances (incl. unchanged) + plan → one atomic signature."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s49_sign"
    )
    batch_id = batch["id"]
    # Exact per-question acceptance, including unchanged originals.
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        assert review["input_freshness"]["stale"] is False
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
        # Independent literal: unchanged originals keep 80/20 roots.
        assert review["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]

    # Separate secondary-plan save (own fence, encounter untouched).
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary: continue current care, follow up.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    assert int(planned.json()["revision"]) == 2

    sign_payload = _sign_body_after_accept(revision, 2, batch_id, run_to_acc, run_to_rev)
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(revision),
            "Idempotency-Key": "s49-sign-0001",
        },
    )
    assert signed.status_code == 200, signed.text
    body = signed.json()
    snapshot = body["snapshot"]
    assert body["revision"] == revision + 1
    assert body["encounter"]["lifecycle"] == "signed"
    assert body["encounter"]["id"] == encounter_id
    assert signed.headers["ETag"] == contracts.format_etag(revision + 1)
    # Frozen record: original + final, plan, inputs/versions, attribution, hash.
    assert snapshot["encounter_id"] == encounter_id
    assert snapshot["batch_id"] == batch_id
    assert snapshot["secondary_plan_revision"] == 2
    assert snapshot["secondary_plan_text"] == "Secondary: continue current care, follow up."
    assert len(snapshot["snapshot_hash"]) == 64
    frozen = snapshot["snapshot"]
    assert frozen["schema_version"] == "signed-encounter-v1"
    assert frozen["patient"]["identifier"] == "0012345678"
    assert frozen["proposal"]["id"] is not None
    assert len(frozen["questions"]) == 2
    for entry in frozen["questions"]:
        assert entry["original_baseline"] is not None
        assert entry["acceptance"] is not None
        assert entry["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
        assert entry["calculation_state"] == "unchanged"
    assert frozen["secondary_plan"]["text"] == "Secondary: continue current care, follow up."
    assert frozen["signer"]["signer_username"] == "dr_s49_sign"
    assert frozen["signer"]["signed_at"].endswith("Z")
    assert len(frozen["notes"]) == 0

    # Slot released: new draft for the same patient succeeds; old draft reads 404.
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["open_draft"] == {
        "exists": False
    }
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 404
    second = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert second.status_code == 201, second.text

    # Idempotent repeat: same key + body returns the original (one signature).
    replay = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=sign_payload,
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(revision),
            "Idempotency-Key": "s49-sign-0001",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == body
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert len(chart["signed_snapshots"]) == 1
    assert chart["signed_snapshots"][0]["id"] == snapshot["id"]

    # Same key + different body is 409 without a second signature.
    forged = dict(sign_payload)
    forged["expected_plan_revision"] = 1
    conflict = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=forged,
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(revision),
            "Idempotency-Key": "s49-sign-0001",
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"]) == 1


# --- S49 §2: stale/pending/mismatched currents rejected; freshness inside tx ---


def test_sign_rejects_stale_pending_mismatched_with_server_recheck(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §2: stale inputs, recalculating currents, forged refs rejected; server rechecks."""
    import uuid as _uuid

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s49_deny"
    )
    batch_id = batch["id"]
    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, posted.text
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    good_payload = _sign_body_after_accept(revision, 2, batch_id, run_to_acc, run_to_rev)

    # Stale: relevant edit moves Q1 inputs; old exact acceptances now stale.
    # Server recomputes inside the locked tx — client IDs alone never grant.
    staled = client.patch(
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
    assert staled.status_code == 200, staled.text
    new_revision = int(staled.json()["revision"])
    stale_payload = dict(good_payload)
    stale_payload["expected_encounter_revision"] = new_revision
    denied_stale = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=stale_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(new_revision)},
    )
    assert denied_stale.status_code == 409, denied_stale.text
    assert denied_stale.json()["code"] == "STALE_INPUTS"
    # Q2 stays fresh/accepted but Q1 blocks the whole sign (no partial).
    q1_after = client.get(f"/api/v1/question-runs/{runs['s49_q1']['id']}/review").json()
    q2_after = client.get(f"/api/v1/question-runs/{runs['s49_q2']['id']}/review").json()
    assert q1_after["input_freshness"]["stale"] is True
    assert q2_after["input_freshness"]["stale"] is False
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []

    # Pending: fresh setup, adjust Q1 without draining → recalculating blocks sign.
    # (Use a second encounter to keep the stale case isolated.)
    _new_physician(clean_registry, monkeypatch, "dr_s49_pending2")
    # Reuse same physician? New patient for isolation via same client would collide
    # slot (one draft per patient only, new patient is fine).
    made2 = client.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345679",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(csrf),
    )
    assert made2.status_code == 201, made2.text
    enc2 = made2.json()["draft"]["id"]
    saved2 = client.patch(
        f"/api/v1/encounters/{enc2}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    rev2 = int(saved2.json()["revision"])
    meds2 = client.post(
        f"/api/v1/encounters/{enc2}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(rev2)},
    )
    rev2 = int(meds2.json()["revision"])
    pkgs = [_package("s49_q1", "h_q1_a", "h_q1_b"), _package("s49_q2", "h_q2_a", "h_q2_b")]
    started2 = client.post(
        f"/api/v1/encounters/{enc2}/generation-batches",
        json={"packages": pkgs},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(rev2)},
    )
    assert started2.status_code == 202, started2.text
    batch2 = started2.json()["batch"]["id"]
    runs2 = {r["question_key"]: r for r in started2.json()["question_runs"]}
    endpoint2, outcomes2 = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes2] == ["succeeded", "succeeded"]
    finally:
        endpoint2.stop()
    acc2: dict[str, dict[str, Any]] = {}
    revmap2: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        revw = client.get(f"/api/v1/question-runs/{runs2[key]['id']}/review").json()
        p = client.post(
            f"/api/v1/question-runs/{runs2[key]['id']}/acceptance",
            json=_accept_body(revw),
            headers=_auth_headers(csrf),
        )
        assert p.status_code == 200, p.text
        acc2[runs2[key]["id"]] = p.json()["acceptance"]
        revmap2[runs2[key]["id"]] = int(p.json()["review_revision"])
    plan2 = client.patch(
        f"/api/v1/encounters/{enc2}/secondary-plan",
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert plan2.status_code == 200, plan2.text
    # Mismatched on the fresh signable state (before adjustment): swapped IDs
    # and forged UUIDs are 409 REFERENCE_MISMATCH without a write.
    swapped = _sign_body_after_accept(rev2, 2, batch2, acc2, revmap2)
    swapped["acceptances"] = [
        {
            "question_run_id": runs2["s49_q1"]["id"],
            "acceptance_id": acc2[runs2["s49_q2"]["id"]]["id"],
            "expected_review_revision": revmap2[runs2["s49_q1"]["id"]],
        },
        {
            "question_run_id": runs2["s49_q2"]["id"],
            "acceptance_id": acc2[runs2["s49_q1"]["id"]]["id"],
            "expected_review_revision": revmap2[runs2["s49_q2"]["id"]],
        },
    ]
    denied_swap = client.post(
        f"/api/v1/encounters/{enc2}/sign",
        json=swapped,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(rev2)},
    )
    assert denied_swap.status_code == 409, denied_swap.text
    assert denied_swap.json()["code"] == "REFERENCE_MISMATCH"
    forged_ids = _sign_body_after_accept(rev2, 2, batch2, acc2, revmap2)
    forged_ids["acceptances"][0]["acceptance_id"] = str(_uuid.uuid4())
    denied_forged = client.post(
        f"/api/v1/encounters/{enc2}/sign",
        json=forged_ids,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(rev2)},
    )
    assert denied_forged.status_code == 409, denied_forged.text
    assert denied_forged.json()["code"] == "REFERENCE_MISMATCH"

    adjusted = client.post(
        f"/api/v1/question-runs/{runs2['s49_q1']['id']}/cpt-adjustments",
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
    mid = client.get(f"/api/v1/question-runs/{runs2['s49_q1']['id']}/review").json()
    assert mid["calculation_state"] == "recalculating"
    pending_payload = {
        "expected_encounter_revision": rev2,
        "expected_plan_revision": 2,
        "batch_id": batch2,
        "acceptances": [
            {
                "question_run_id": runs2["s49_q1"]["id"],
                "acceptance_id": acc2[runs2["s49_q1"]["id"]]["id"],
                "expected_review_revision": 2,
            },
            {
                "question_run_id": runs2["s49_q2"]["id"],
                "acceptance_id": acc2[runs2["s49_q2"]["id"]]["id"],
                "expected_review_revision": revmap2[runs2["s49_q2"]["id"]],
            },
        ],
    }
    denied_pending = client.post(
        f"/api/v1/encounters/{enc2}/sign",
        json=pending_payload,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(rev2)},
    )
    assert denied_pending.status_code == 409, denied_pending.text
    assert denied_pending.json()["code"] == "NO_SUCCESSFUL_RESULT"


# --- S49 §2: wrong author/admin, outdated revisions, forged manual plans ---


def test_sign_rejects_auth_stale_forged_then_succeeds(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §2: 403/412/422 denials leave no partial; corrected sign still succeeds."""
    import uuid as _uuid

    admin, admin_csrf = _new_physician(clean_registry, monkeypatch, "dr_s49_auth")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_auth")
    # Second physician for stranger checks.
    _new_physician(clean_registry, monkeypatch, "dr_s49_auth_other")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_auth_other")

    _publish_limited(clean_registry, tmp_path)
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
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    revision = int(saved.json()["revision"])
    meds = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    revision = int(meds.json()["revision"])
    pkgs = [_package("s49_q1", "h_q1_a", "h_q1_b"), _package("s49_q2", "h_q2_a", "h_q2_b")]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": pkgs},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    batch_id = started.json()["batch"]["id"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"]
    finally:
        endpoint.stop()
    acc: dict[str, dict[str, Any]] = {}
    revmap: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        revw = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        p = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(revw),
            headers=_auth_headers(csrf),
        )
        acc[runs[key]["id"]] = p.json()["acceptance"]
        revmap[runs[key]["id"]] = int(p.json()["review_revision"])
    plan = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert plan.status_code == 200, plan.text
    good = _sign_body_after_accept(revision, 2, batch_id, acc, revmap)

    # Wrong author (stranger physician) is generic 403 without content.
    denied_stranger = other.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=good,
        headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_stranger.status_code == 403, denied_stranger.text
    # No-leak tokens are structural ("percentages" is the CPT-row key): a bare
    # "80" also matches random request_id UUID hex and flakes (~1 run in 9).
    assert "percentages" not in denied_stranger.text
    assert "Plan ready" not in denied_stranger.text
    assert "h_q1_a" not in denied_stranger.text
    # Admin cannot sign (physician-only mutation).
    denied_admin = admin.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=good,
        headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_admin.status_code == 403, denied_admin.text
    # Unauthenticated is 401.
    naked = TestClient(app)
    assert (
        naked.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=good,
            headers={"If-Match": contracts.format_etag(revision)},
        ).status_code
        == 401
    )

    # Outdated encounter revision is 412 (header + body old).
    old_enc = dict(good)
    old_enc["expected_encounter_revision"] = revision - 1
    denied_old_enc = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=old_enc,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision - 1)},
    )
    assert denied_old_enc.status_code == 412, denied_old_enc.text
    # Outdated plan revision is 412.
    old_plan = dict(good)
    old_plan["expected_plan_revision"] = 1
    denied_old_plan = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=old_plan,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_old_plan.status_code == 412, denied_old_plan.text
    # Outdated review revision is 412.
    old_rev = _sign_body_after_accept(revision, 2, batch_id, acc, revmap)
    old_rev["acceptances"][0]["expected_review_revision"] = 99
    denied_old_rev = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=old_rev,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_old_rev.status_code == 412, denied_old_rev.text
    # Missing If-Match is 422.
    assert (
        client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=good,
            headers=_auth_headers(csrf),
        ).status_code
        == 422
    )

    # Forged manual plan: extra plan text is 422, never signed.
    forged_extra = dict(good)
    forged_extra["plan_text"] = "Forged proposal."
    denied_extra = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=forged_extra,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied_extra.status_code == 422, denied_extra.text
    # Forged batch from another encounter is 409.
    made2 = other.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345679",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(other_csrf),
    )
    enc2 = made2.json()["draft"]["id"]
    forged_batch = dict(good)
    forged_batch["batch_id"] = str(_uuid.uuid4())
    denied_batch = client.post(
        f"/api/v1/encounters/{enc2}/sign",
        json=forged_batch,
        headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert denied_batch.status_code in (403, 404, 409), denied_batch.text

    # No partial snapshot from any denial; corrected sign still succeeds.
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 200
    ok = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=good,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["snapshot"]["snapshot_hash"] is not None


# --- S49 §4: signed immutability, live demographics, addenda, shared chart ---


def test_signed_immutable_live_demographics_addenda_chart(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §4: post-sign mutations blocked; live demographics move; addenda append; chart shares."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s49_s4"
    )
    admin, admin_csrf = _new_physician(clean_registry, monkeypatch, "dr_s49_s4_other")
    other, other_csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_s4_other")
    batch_id = batch["id"]
    acc: dict[str, dict[str, Any]] = {}
    revmap: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        revw = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        p = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(revw),
            headers=_auth_headers(csrf),
        )
        acc[runs[key]["id"]] = p.json()["acceptance"]
        revmap[runs[key]["id"]] = int(p.json()["review_revision"])
    # One attributed note before sign freezes into the snapshot.
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": "Pre-sign note."},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(draft["revision"])},
    )
    assert noted.status_code == 201, noted.text
    revision = int(noted.json()["revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary: signed plan.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    good = _sign_body_after_accept(revision, 2, batch_id, acc, revmap)
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=good,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    signed_body = signed.json()
    snapshot_id = signed_body["snapshot"]["id"]
    snapshot_hash = signed_body["snapshot"]["snapshot_hash"]
    signed_revision = int(signed_body["revision"])
    assert signed_revision == revision + 1
    frozen = signed_body["snapshot"]["snapshot"]
    assert frozen["notes"][0]["text"] == "Pre-sign note."
    assert frozen["secondary_plan"]["text"] == "Secondary: signed plan."

    # Post-sign clinical/plan/note/probability mutations are all blocked.
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {}},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(signed_revision)},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "proposal", "text": "Late note."},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(signed_revision)},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/encounters/{encounter_id}/secondary-plan",
            json={"text": "Late plan.", "expected_plan_revision": 2},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/encounters/{encounter_id}/secondary-plan").status_code == 404
    for run_key in ("s49_q1", "s49_q2"):
        run_id = runs[run_key]["id"]
        assert (
            client.post(
                f"/api/v1/question-runs/{run_id}/cpt-adjustments",
                json={
                    "node_id": "A",
                    "parent_states": [],
                    "state": "yes",
                    "target_percentage": "40",
                    "expected_review_revision": 1,
                },
                headers=_auth_headers(csrf),
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/question-runs/{run_id}/acceptance",
                json=_accept_body(client.get(f"/api/v1/question-runs/{run_id}/review").json()),
                headers=_auth_headers(csrf),
            ).status_code
            == 409
        )
    # Second sign without idempotency is 409 ALREADY_SIGNED (one signature).
    assert (
        client.post(
            f"/api/v1/encounters/{encounter_id}/sign",
            json=good,
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        ).status_code
        == 409
    )

    # Live demographics can change (setup) without mutating the snapshot.
    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE patients SET given_name = 'Changed', phone = '555-0100' WHERE id = :pid"),
            {"pid": patient_id},
        )
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["patient"]["given_name"] == "Changed"
    assert chart["patient"]["phone"] == "555-0100"
    assert len(chart["signed_snapshots"]) == 1
    assert chart["signed_snapshots"][0]["id"] == snapshot_id
    assert chart["signed_snapshots"][0]["snapshot_hash"] == snapshot_hash
    frozen_after = chart["signed_snapshots"][0]["snapshot"]
    assert frozen_after["patient"]["given_name"] == "Given"
    assert frozen_after["patient"]["phone"] is None
    assert frozen_after["questions"][0]["current_tables"][0]["rows"][0]["percentages"] == [
        "80",
        "20",
    ]
    assert chart["open_draft"] == {"exists": False}

    # Any active physician appends own attributed dated addendum (server-derived).
    appended = other.post(
        f"/api/v1/encounters/{encounter_id}/addenda",
        json={
            "text": "Correction by second physician.",
            "expected_encounter_revision": signed_revision,
        },
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(signed_revision),
            "Idempotency-Key": "s49-add-0001",
        },
    )
    assert appended.status_code == 201, appended.text
    addendum = appended.json()["addendum"]
    assert addendum["encounter_id"] == encounter_id
    assert addendum["author_display"] == "dr_s49_s4_other"
    assert addendum["text"] == "Correction by second physician."
    assert addendum["created_at"].endswith("Z")
    assert appended.json()["revision"] == signed_revision
    # Original signer/content intact; addendum visible in shared chart.
    reread = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert reread["signed_snapshots"][0]["snapshot_hash"] == snapshot_hash
    assert reread["signed_snapshots"][0]["snapshot"]["signer"]["signer_username"] == "dr_s49_s4"
    assert len(reread["addenda"]) == 1
    assert reread["addenda"][0]["id"] == addendum["id"]
    # Idempotent repeat returns the original; same key new body is 409.
    replay = other.post(
        f"/api/v1/encounters/{encounter_id}/addenda",
        json={
            "text": "Correction by second physician.",
            "expected_encounter_revision": signed_revision,
        },
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(signed_revision),
            "Idempotency-Key": "s49-add-0001",
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json() == appended.json()
    assert len(client.get(f"/api/v1/patients/{patient_id}/chart").json()["addenda"]) == 1
    conflict = other.post(
        f"/api/v1/encounters/{encounter_id}/addenda",
        json={"text": "Different text.", "expected_encounter_revision": signed_revision},
        headers={
            **_auth_headers(other_csrf),
            "If-Match": contracts.format_etag(signed_revision),
            "Idempotency-Key": "s49-add-0001",
        },
    )
    assert conflict.status_code == 409, conflict.text
    # Empty/oversized/stale/missing-fence are 422/412; admin cannot append.
    assert (
        other.post(
            f"/api/v1/encounters/{encounter_id}/addenda",
            json={"text": "", "expected_encounter_revision": signed_revision},
            headers={
                **_auth_headers(other_csrf),
                "If-Match": contracts.format_etag(signed_revision),
            },
        ).status_code
        == 422
    )
    assert (
        other.post(
            f"/api/v1/encounters/{encounter_id}/addenda",
            json={"text": "x", "expected_encounter_revision": signed_revision},
            headers=_auth_headers(other_csrf),
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/v1/encounters/{encounter_id}/addenda",
            json={"text": "Admin correction.", "expected_encounter_revision": signed_revision},
            headers={
                **_auth_headers(admin_csrf),
                "If-Match": contracts.format_etag(signed_revision),
            },
        ).status_code
        == 403
    )
    # Draft encounters reject addenda (409, signed-only).
    made_draft = other.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345680",
            "given_name": "Given",
            "family_name": "Family",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(other_csrf),
    )
    draft_enc = made_draft.json()["draft"]["id"]
    assert (
        other.post(
            f"/api/v1/encounters/{draft_enc}/addenda",
            json={"text": "Draft addendum.", "expected_encounter_revision": 1},
            headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(1)},
        ).status_code
        == 409
    )

    # Shared chart read shares signed content without leaking other drafts.
    sentinel = other.patch(
        f"/api/v1/encounters/{draft_enc}",
        json={
            "draft_data": {
                "history": {"values": {"h_secret": "SENTINEL-SECRET-123"}},
                "gate": "true",
            }
        },
        headers={**_auth_headers(other_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert sentinel.status_code == 200, sentinel.text
    stranger_chart = other.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert stranger_chart["signed_snapshots"][0]["id"] == snapshot_id
    assert "80" in str(stranger_chart["signed_snapshots"][0]["snapshot"]["questions"])
    assert "Secondary: signed plan." in str(stranger_chart)
    assert "SENTINEL-SECRET-123" not in str(stranger_chart)
    assert "SENTINEL-SECRET-123" not in str(chart)
    # Stranger cannot read signed-off draft bodies via ordinary routes.
    assert other.get(f"/api/v1/encounters/{encounter_id}").status_code in (403, 404)
    denied_review = other.get(f"/api/v1/question-runs/{runs['s49_q1']['id']}/review")
    assert denied_review.status_code == 403, denied_review.text
    assert "percentages" not in denied_review.text


# --- S49 §§1+3: adjusted recalculation freezes original + final separately ---


def test_sign_with_adjusted_recalculation_freezes_final(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S49 §§1+3: one adjusted question signs with original vs final CPTs/results."""
    import pytest as _pytest

    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s49_adjusted"
    )
    batch_id = batch["id"]
    # Adjust Q1 A yes 20→40 ([20,30,50]→[40,22.5,37.5] shape proven in S48a;
    # here two-state root 80/20→60/40) then drain the local job.
    adjusted = client.post(
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
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()["revision"]["after_row"]["percentages"] == ["60", "40"]
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    stub = _provider.ControlledStubAdapter(mode="succeed")
    assert _worker.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []
    q1_solved = client.get(f"/api/v1/question-runs/{runs['s49_q1']['id']}/review").json()
    assert q1_solved["calculation_state"] == "successfully_recalculated"
    assert q1_solved["current_tables"][0]["rows"][0]["percentages"] == ["60", "40"]
    # Independent literal: A 60/40 gives P(B=yes)=0.10*0.60+0.70*0.40=0.34.
    by_node = {str(e.get("node_id")): e for e in q1_solved["calculation_result"]["posteriors"]}
    assert by_node["B"]["probabilities"] == _pytest.approx([0.66, 0.34], abs=1e-6)

    # Accept adjusted Q1 (calculation) + unchanged Q2 (baseline).
    acc: dict[str, dict[str, Any]] = {}
    revmap: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
        revw = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        p = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(revw),
            headers=_auth_headers(csrf),
        )
        assert p.status_code == 200, (key, p.text)
        acc[runs[key]["id"]] = p.json()["acceptance"]
        revmap[runs[key]["id"]] = int(p.json()["review_revision"])
    assert acc[runs["s49_q1"]["id"]]["result_kind"] == "calculation"
    assert acc[runs["s49_q2"]["id"]]["result_kind"] == "baseline"

    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary with adjusted probabilities.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    good = _sign_body_after_accept(revision, 2, batch_id, acc, revmap)
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=good,
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    frozen = signed.json()["snapshot"]["snapshot"]
    by_key = {e["question_key"]: e for e in frozen["questions"]}
    # Original preserved; final reflects the accepted adjustment.
    assert by_key["s49_q1"]["original_baseline"]["validated_tables"][0]["rows"][0][
        "percentages"
    ] == ["80", "20"]
    assert by_key["s49_q1"]["current_tables"][0]["rows"][0]["percentages"] == ["60", "40"]
    assert by_key["s49_q1"]["calculation_state"] == "successfully_recalculated"
    assert by_key["s49_q1"]["acceptance"]["result_kind"] == "calculation"
    assert by_key["s49_q2"]["calculation_state"] == "unchanged"
    assert by_key["s49_q2"]["acceptance"]["result_kind"] == "baseline"
    # Adjusted recommendation text retained (section from the solved result).
    assert by_key["s49_q1"]["current_result"] is not None
    assert by_key["s49_q1"]["current_result"]["section_text"]


def test_sign_rejects_failed_current_calculation(clean_registry, monkeypatch, tmp_path) -> None:
    """S49 §2: failed recalculated current blocks sign; no partial snapshot."""
    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    _publish_limited(clean_registry, tmp_path)
    _new_physician(clean_registry, monkeypatch, "dr_s49_failcur")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s49_failcur")
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
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "history": {
                    "values": {"h_q1_a": "yes", "h_q1_b": "no", "h_q2_a": "yes", "h_q2_b": "no"}
                },
                "gate": "true",
            }
        },
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
    q1 = _package("s49_q1", "h_q1_a", "h_q1_b")
    # Single absent branch only: baseline argmax no (P(B=yes)=0.22) renders,
    # but flipping B|no to a yes-majority leaves argmax yes with no matching
    # branch (TEMPLATE_NO_MATCH), failing the local recalculation.
    q1["template"] = {
        "version": "v1",
        "branches": [
            {
                "when": {"node": "B", "state": "no", "operator": "=="},
                "text": "s49_q1 absent: {B} outcome.",
            },
        ],
    }
    packages = [q1, _package("s49_q2", "h_q2_a", "h_q2_b")]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 2)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded", "succeeded"], outcomes
    finally:
        endpoint.stop()
    assert client.get(f"/api/v1/generation-batches/{batch_id}").json()["proposal"] is not None

    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s49_q1", "s49_q2"):
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
        json={"text": "Plan ready.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text

    # Fail the current: B|no [90,10]→[20,80] flips argmax to yes (no branch).
    adjusted = client.post(
        f"/api/v1/question-runs/{runs['s49_q1']['id']}/cpt-adjustments",
        json={
            "node_id": "B",
            "parent_states": ["no"],
            "state": "yes",
            "target_percentage": "80",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()["revision"]["after_row"]["percentages"] == ["20", "80"]
    stub = _provider.ControlledStubAdapter(mode="succeed")
    failed = _worker.run_once(clean_registry, stub)
    assert failed["status"] == "failed", failed
    assert stub.calls == []
    assert (
        client.get(f"/api/v1/question-runs/{runs['s49_q1']['id']}/review").json()[
            "calculation_state"
        ]
        == "failed"
    )

    denied = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={
            "expected_encounter_revision": revision,
            "expected_plan_revision": 2,
            "batch_id": batch_id,
            "acceptances": [
                {
                    "question_run_id": runs["s49_q1"]["id"],
                    "acceptance_id": run_to_acc[runs["s49_q1"]["id"]]["id"],
                    "expected_review_revision": 2,
                },
                {
                    "question_run_id": runs["s49_q2"]["id"],
                    "acceptance_id": run_to_acc[runs["s49_q2"]["id"]]["id"],
                    "expected_review_revision": run_to_rev[runs["s49_q2"]["id"]],
                },
            ],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "NO_SUCCESSFUL_RESULT"
    # No partial snapshot; draft still readable.
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"] == []
    assert client.get(f"/api/v1/encounters/{encounter_id}").status_code == 200
