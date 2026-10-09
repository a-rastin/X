"""Execute ordered workflows and assemble a complete proposal (S46, seams T1/T8).

Through public HTTP + ``run_once()`` with ``BoundedProviderAdapter`` over real
MCP stdio + ``DeterministicProviderEndpoint.captured`` on real PostgreSQL.
No DB-row asserts, no internal mock call-order; order is observed via public
run status + captured external provider requests.

Covers tasks.md S46 §§1-4 (FR-15, FR-30-35):

- §1: pinned order + no intra-run parallelism (5-question sequential captures;
  second request absent until prior commit; cross-encounter independence).
- §2: gates/isolation (false → not_applicable no call, successor proceeds;
  required-unknown → needs_clarification stops; per-question isolation).
- §3: proposal assembly + DDI (valid limited succeeds with skipped + warnings;
  partial readable incomplete; unavailable → null with ddi_status unavailable;
  no LLM proposal writing, single tool only).
- §4: freshness/pinning + LAI (new fingerprint/batch/DDI, old immutable;
  content version does not mutate pinned; 6-question lai_discussion only
  retained branch).

Synthetic A→B 0.20/0.22 networks with distinct question_keys, pinned order,
per-question projections; independent expected literals, never produced by
the implementation under test.
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
from x_insight.mcp_server import TOOL_INPUT_SCHEMA, TOOL_NAME
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


def _package(
    question_key: str,
    field_a: str,
    field_b: str,
    *,
    gate_expression: str = "true",
    required_fields: list[str] | None = None,
    workflow: str = "registration",
    version: str = "s46-test-v1",
    template_prefix: str | None = None,
) -> dict[str, Any]:
    prefix = template_prefix if template_prefix is not None else question_key
    req = (
        list(required_fields)
        if required_fields is not None
        else [f"synthetic/history/{field_a}", f"synthetic/history/{field_b}"]
    )
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": workflow,
            "version": version,
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
                "expression": gate_expression,
                "required_fields": req,
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
                    "text": f"{prefix} present: {{B}} outcome.",
                },
                {
                    "when": {"node": "B", "state": "no", "operator": "=="},
                    "text": f"{prefix} absent: {{B}} outcome.",
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


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S46 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s46/1", "concepts": concepts, "aliases": []}


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
        "decision": "approved_complete",
        "date": "2026-10-06",
        "record": "synthetic owner review",
        "parser_version": dataset.get("parser_version"),
        "terminology_version": dataset.get("terminology_version"),
        "terminology_checksum": dataset.get("terminology_checksum"),
        "coverage": {"scope": "complete", "excluded_sources": []},
        "corrections": [],
        "reviewed_evidence": [],
        "limitations": [],
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


def _publish_complete(engine: Any, tmp_path: Path) -> dict[str, Any]:
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


def _publish_limited(engine: Any, tmp_path: Path) -> dict[str, Any]:
    from x_insight.ddi import publish

    sources = tmp_path / "sources_lim"
    sources.mkdir(parents=True, exist_ok=True)
    _monograph(sources, "Alpha", ["Beta"])
    _monograph(sources, "Beta", ["Alpha"])
    concepts = [
        _concept("alpha", "Alpha", "catalog_alpha"),
        _concept("beta", "Beta", "catalog_beta"),
        _concept("gamma", "Gamma", "catalog_gamma"),
    ]
    staging = tmp_path / "staging_lim"
    _stage(sources, staging, _vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest_lim.json",
        decision="approved_limited",
        coverage={
            "scope": "limited",
            "excluded_sources": [{"path": "Gamma.txt", "reason": "synthetic uncovered scope"}],
        },
        limitations=["synthetic limited scope: Gamma.txt excluded"],
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, engine)


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


def _truncate(engine: Any) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE reasoning_grants, reasoning_job_attempts, reasoning_jobs, "
                "reasoning_fairness, original_baselines, proposal_snapshots, "
                "question_runs, generation_batches, notes, encounters, patients, "
                "idempotency_records, sessions, users, audit_events, "
                "ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases CASCADE"
            )
        )


def _reset_deployment(engine: Any) -> None:
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


def _make_physician(admin_client: TestClient, admin_csrf: str, username: str) -> None:
    created = admin_client.post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers=_auth_headers(admin_csrf),
    )
    assert created.status_code == 201, created.text


def _login_physician(engine: Any, monkeypatch: Any, username: str) -> tuple[TestClient, str]:
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def _new_physician(
    engine: Any, monkeypatch: Any, username: str
) -> tuple[TestClient, str, TestClient, str]:
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    _make_physician(admin, login.json()["csrf_token"], username)
    client, csrf = _login_physician(engine, monkeypatch, username)
    return admin, login.json()["csrf_token"], client, csrf


def _create_patient(client: TestClient, csrf: str, identifier: str) -> str:
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
    return created.json()["draft"]["id"]


def _patch_history(
    client: TestClient, csrf: str, encounter_id: str, values: dict[str, Any], revision: int
) -> int:
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": dict(values)}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert saved.status_code == 200, saved.text
    return int(saved.json()["revision"])


def _save_meds(
    client: TestClient, csrf: str, encounter_id: str, catalog_ids: list[str], revision: int
) -> int:
    meds = [{"catalog_drug_id": catalog_id} for catalog_id in catalog_ids]
    saved = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": meds},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert saved.status_code == 200, saved.text
    return int(saved.json()["revision"])


def _start_workflow(
    client: TestClient, csrf: str, encounter_id: str, revision: int, packages: list[dict]
) -> Any:
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _read_batch(client: TestClient, batch_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/generation-batches/{batch_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _question_key_of(captured_entry: Any) -> str:
    messages = captured_entry.get("messages", [])
    assert len(messages) >= 2
    user_content = messages[1].get("content", "")
    payload = json.loads(user_content)
    return str(payload.get("question_key", ""))


def _projection_of(captured_entry: Any) -> dict[str, Any]:
    messages = captured_entry.get("messages", [])
    payload = json.loads(messages[1].get("content", "{}"))
    projection = payload.get("projection", {})
    assert isinstance(projection, dict)
    return projection


def _assert_no_proposal_writing(captured: list[Any]) -> None:
    for entry in captured:
        blob = json.dumps(entry).lower()
        assert "proposal" not in blob, "no LLM proposal-writing request may exist"
        assert "write a plan" not in blob, "no LLM plan-writing request may exist"
        tools = entry.get("tools", [])
        assert len(tools) == 1, f"single tool only, got {tools}"
        function = tools[0].get("function", {})
        assert function.get("name") == TOOL_NAME, function
        assert function.get("parameters") == dict(TOOL_INPUT_SCHEMA), function


def _assert_posteriors_exact(baseline: dict[str, Any]) -> None:
    by_node = {entry["node_id"]: entry for entry in baseline["posteriors"]}
    assert by_node["A"]["probabilities"] == pytest.approx([0.80, 0.20], abs=1e-6)
    assert by_node["B"]["probabilities"] == pytest.approx([0.78, 0.22], abs=1e-6)


# --- S46 §1: pinned order + no intra-run parallelism ---


def test_single_package_backward_compat(clean_registry, monkeypatch) -> None:
    """S46 compat: single {"package": ...} still starts, exposes compat keys."""
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_compat")
    encounter_id = _create_patient(client, csrf, "0012345678")
    revision = _patch_history(client, csrf, encounter_id, {"h_a": "yes", "h_b": "no"}, 1)
    package = _package("s46_compat", "h_a", "h_b")
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    run_id = started.json()["question_runs"][0]["id"]
    assert [run["question_key"] for run in started.json()["question_runs"]] == ["s46_compat"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()}]
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
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert outcome["status"] == "succeeded", outcome
    finally:
        endpoint.stop()
    assert len(endpoint.captured) == 1
    assert _question_key_of(endpoint.captured[0]) == "s46_compat"
    _assert_no_proposal_writing(list(endpoint.captured))

    body = _read_batch(client, batch_id)
    assert body["job"]["status"] == "succeeded"
    assert body["baseline"] is not None
    assert body["transparency"] is not None
    assert len(body["baselines"]) == 1
    assert body["baselines"][0]["question_key"] == "s46_compat"
    assert body["baselines"][0]["baseline"] is not None
    _assert_posteriors_exact(body["baseline"])
    review = client.get(f"/api/v1/question-runs/{run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["adjustable"] is True


def test_pinned_order_sequential_no_parallelism(clean_registry, monkeypatch) -> None:
    """S46 §1: 5-question pinned order; next absent until prior commit."""
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_order")
    encounter_id = _create_patient(client, csrf, "0012345678")
    keys = ["s46_q1", "s46_q2", "s46_q3", "s46_q4", "s46_q5"]
    values: dict[str, Any] = {}
    packages: list[dict[str, Any]] = []
    for key in keys:
        field_a = f"h_{key}_a"
        field_b = f"h_{key}_b"
        values[field_a] = "yes"
        values[field_b] = "no"
        packages.append(_package(key, field_a, field_b))
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    body0 = started.json()
    assert [run["question_key"] for run in body0["question_runs"]] == keys
    batch_id = body0["batch"]["id"]

    fresh = _read_batch(client, batch_id)
    assert fresh["job"]["status"] == "queued"
    assert len(fresh["jobs"]) == 1
    assert [entry["question_key"] for entry in fresh["question_runs"]] == keys
    assert all(entry["baseline"] is None for entry in fresh["baselines"])
    assert fresh["proposal"] is None
    assert fresh["workflow"]["status"] == "incomplete"

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in keys]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        for index, key in enumerate(keys):
            assert len(endpoint.captured) == index, "next request absent until prior commit"
            outcome = worker_module.run_once(
                clean_registry, adapter, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
            assert len(endpoint.captured) == index + 1
            assert _question_key_of(endpoint.captured[index]) == key
            polled = _read_batch(client, batch_id)
            assert polled["baselines"][index]["baseline"] is not None
            assert polled["baselines"][index]["question_key"] == key
            _assert_posteriors_exact(polled["baselines"][index]["baseline"])
            for later in range(index + 1, len(keys)):
                assert polled["baselines"][later]["baseline"] is None
            expected_jobs = index + 2 if index + 1 < len(keys) else len(keys)
            assert len(polled["jobs"]) == expected_jobs
    finally:
        endpoint.stop()

    assert [_question_key_of(entry) for entry in endpoint.captured] == keys
    _assert_no_proposal_writing(list(endpoint.captured))
    final = _read_batch(client, batch_id)
    assert all(entry["baseline"] is not None for entry in final["baselines"])
    assert [entry["question_key"] for entry in final["baselines"]] == keys
    for run_id in [run["id"] for run in final["question_runs"]]:
        review = client.get(f"/api/v1/question-runs/{run_id}/review")
        assert review.status_code == 200, review.text
        assert review.json()["baseline"] is not None
    for entry in endpoint.captured:
        projection = _projection_of(entry)
        variables = projection.get("variables", [])
        assert len(variables) == 2
        for variable in variables:
            assert "posterior" not in str(variable).lower()


def test_cross_encounter_independent_progress(clean_registry, monkeypatch) -> None:
    """S46 §1: two batches progress independently; never two of one run concurrently."""
    from x_insight.reasoning import queue as queue_module

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    admin_csrf = login.json()["csrf_token"]
    for username in ("dr_s46_xa", "dr_s46_xb"):
        _make_physician(admin, admin_csrf, username)
    client_a, csrf_a = _login_physician(clean_registry, monkeypatch, "dr_s46_xa")
    client_b, csrf_b = _login_physician(clean_registry, monkeypatch, "dr_s46_xb")

    def _two_question_batch(
        client: TestClient, csrf: str, identifier: str, suffix: str
    ) -> tuple[str, list[str]]:
        encounter_id = _create_patient(client, csrf, identifier)
        keys = [f"s46_{suffix}_one", f"s46_{suffix}_two"]
        values = {}
        packages = []
        for key in keys:
            field_a = f"h_{key}_a"
            field_b = f"h_{key}_b"
            values[field_a] = "yes"
            values[field_b] = "no"
            packages.append(_package(key, field_a, field_b))
        revision = _patch_history(client, csrf, encounter_id, values, 1)
        started = _start_workflow(client, csrf, encounter_id, revision, packages)
        assert started.status_code == 202, started.text
        return started.json()["batch"]["id"], keys

    batch_a, keys_a = _two_question_batch(client_a, csrf_a, "0012345678", "xa")
    batch_b, keys_b = _two_question_batch(client_b, csrf_b, "0012345679", "xb")

    with db_module.session_scope(clean_registry) as session:
        first = queue_module.claim_next_job(session, "worker-a")
    assert first is not None and "job" in first, first
    with db_module.session_scope(clean_registry) as session:
        second = queue_module.claim_next_job(session, "worker-b")
    assert second is not None and "job" in second, second
    assert str(first["job"]["batch_id"]) != str(second["job"]["batch_id"])
    assert str(first["batch"]["id"]) in (batch_a, batch_b)
    assert str(second["batch"]["id"]) in (batch_a, batch_b)
    with db_module.session_scope(clean_registry) as session:
        third = queue_module.claim_next_job(session, "worker-c")
    assert third is not None and third.get("busy") is True, f"slots full must be busy: {third}"

    # Release the held leases by expiry reclaim for the run_once progress below.
    # The two leased jobs block successors; reclaim them deterministically.
    with db_module.session_scope(clean_registry) as session:
        reclaimed = queue_module.reclaim_expired_leases(session, now=contracts.utcnow())
    _ = reclaimed
    # Force expiry by moving deadlines into the past via the public reclaim path
    # is time-based; instead finish by running the queued work after resetting
    # leases through deployment-safe reclaim with a far-future clock.
    from datetime import timedelta

    future = contracts.utcnow() + timedelta(seconds=1000)
    with db_module.session_scope(clean_registry) as session:
        assert queue_module.reclaim_expired_leases(session, now=future) >= 1

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(4)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        seen: list[str] = []
        for _ in range(4):
            outcome = worker_module.run_once(
                clean_registry, adapter, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
            seen.append(outcome["batch_id"])
    finally:
        endpoint.stop()
    assert sorted(seen) == sorted([batch_a, batch_a, batch_b, batch_b])
    order = [_question_key_of(entry) for entry in endpoint.captured]
    assert order.index(keys_a[0]) < order.index(keys_a[1])
    assert order.index(keys_b[0]) < order.index(keys_b[1])
    for body_client, batch_id, keys in (
        (client_a, batch_a, keys_a),
        (client_b, batch_b, keys_b),
    ):
        polled = body_client.get(f"/api/v1/generation-batches/{batch_id}").json()
        assert [entry["question_key"] for entry in polled["baselines"]] == keys
        assert all(entry["baseline"] is not None for entry in polled["baselines"])


# --- S46 §2: gates/isolation ---


def test_false_gate_skips_without_provider_call(clean_registry, monkeypatch) -> None:
    """S46 §2: false gate → not_applicable, no call, successor proceeds + isolation."""
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_gate")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {
        "h_s46_g1_a": "yes",
        "h_s46_g1_b": "no",
        "h_s46_g2_a": "yes",
        "h_s46_g2_b": "no",
        "h_s46_g3_a": "yes",
        "h_s46_g3_b": "no",
    }
    packages = [
        _package("s46_g1", "h_s46_g1_a", "h_s46_g1_b"),
        _package(
            "s46_g2",
            "h_s46_g2_a",
            "h_s46_g2_b",
            gate_expression="gate == 'false'",
        ),
        _package("s46_g3", "h_s46_g3_a", "h_s46_g3_b"),
    ]
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    runs = started.json()["question_runs"]
    assert [run["question_key"] for run in runs] == ["s46_g1", "s46_g2", "s46_g3"]
    assert runs[0]["status"] == "ready"
    assert runs[1]["status"] == "not_applicable"
    assert "gate false" in runs[1]["gate_reason"]
    assert runs[2]["status"] == "ready"
    batch_id = started.json()["batch"]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        first = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert first["status"] == "succeeded", first
        assert len(endpoint.captured) == 1
        assert _question_key_of(endpoint.captured[0]) == "s46_g1"
        mid = _read_batch(client, batch_id)
        assert mid["baselines"][0]["baseline"] is not None
        assert mid["baselines"][1]["baseline"] is None
        assert mid["baselines"][1]["status"] == "not_applicable"
        assert len(mid["jobs"]) == 2
        assert {job["question_run_id"] for job in mid["jobs"]} == {
            runs[0]["id"],
            runs[2]["id"],
        }, "skipped question must never get a job"

        second = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert second["status"] == "succeeded", second
        assert len(endpoint.captured) == 2
        assert _question_key_of(endpoint.captured[1]) == "s46_g3"
    finally:
        endpoint.stop()

    assert "s46_g2" not in [_question_key_of(entry) for entry in endpoint.captured]
    _assert_no_proposal_writing(list(endpoint.captured))
    # Isolation: each payload holds only its own represented variables.
    first_projection = _projection_of(endpoint.captured[0])
    second_projection = _projection_of(endpoint.captured[1])
    first_paths = {var["source_path"] for var in first_projection.get("variables", [])}
    second_paths = {var["source_path"] for var in second_projection.get("variables", [])}
    assert first_paths == {"synthetic/history/h_s46_g1_a", "synthetic/history/h_s46_g1_b"}
    assert second_paths == {"synthetic/history/h_s46_g3_a", "synthetic/history/h_s46_g3_b"}
    assert first_paths.isdisjoint(second_paths)
    for entry in endpoint.captured:
        blob = json.dumps(entry).lower()
        assert "posterior" not in blob
        assert "sentinel" not in blob
        assert "0012345678" not in blob
    final = _read_batch(client, batch_id)
    assert final["baselines"][0]["baseline"] is not None
    assert final["baselines"][2]["baseline"] is not None
    _assert_posteriors_exact(final["baselines"][0]["baseline"])
    _assert_posteriors_exact(final["baselines"][2]["baseline"])


def test_required_unknown_stops_progression(clean_registry, monkeypatch) -> None:
    """S46 §2: required-unknown → needs_clarification, later stays pending, no captures."""
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_clar")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {
        "h_s46_c1_a": "yes",
        "h_s46_c1_b": "no",
        "h_s46_c2_a": "yes",
        "h_s46_c3_a": "yes",
        "h_s46_c3_b": "no",
    }
    packages = [
        _package("s46_c1", "h_s46_c1_a", "h_s46_c1_b"),
        _package(
            "s46_c2",
            "h_s46_c2_a",
            "h_s46_c2_b",
            required_fields=["synthetic/history/h_s46_c2_a", "synthetic/history/h_missing_never"],
        ),
        _package("s46_c3", "h_s46_c3_a", "h_s46_c3_b"),
    ]
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    runs = started.json()["question_runs"]
    assert [run["status"] for run in runs] == ["ready", "needs_clarification", "ready"]
    assert "h_missing_never" in runs[1]["gate_reason"]
    batch_id = started.json()["batch"]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(3)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        first = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert first["status"] == "succeeded", first
        assert len(endpoint.captured) == 1
        assert _question_key_of(endpoint.captured[0]) == "s46_c1"
        stalled = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert stalled["status"] == "idle", stalled
        assert len(endpoint.captured) == 1, "clarification must stop further captures"
    finally:
        endpoint.stop()

    polled = _read_batch(client, batch_id)
    assert polled["baselines"][0]["baseline"] is not None
    assert polled["baselines"][1]["baseline"] is None
    assert polled["baselines"][1]["status"] == "needs_clarification"
    assert polled["baselines"][2]["baseline"] is None
    assert polled["proposal"] is None
    assert polled["workflow"]["status"] == "incomplete"
    assert "s46_c2" in polled["workflow"]["needs_clarification"]
    assert "s46_c3" in polled["workflow"]["pending_question_keys"]
    _assert_no_proposal_writing(list(endpoint.captured))


# --- S46 §3: proposal assembly + DDI ---


def test_proposal_limited_coverage_with_skipped_and_warnings(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S46 §3: valid limited DDI succeeds with skipped reasons + coverage warnings."""
    _publish_limited(clean_registry, tmp_path)
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_prop")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {
        "h_s46_p1_a": "yes",
        "h_s46_p1_b": "no",
        "h_s46_p2_a": "yes",
        "h_s46_p2_b": "no",
        "h_s46_p3_a": "yes",
        "h_s46_p3_b": "no",
    }
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    revision = _save_meds(client, csrf, encounter_id, ["catalog_alpha", "catalog_gamma"], revision)
    packages = [
        _package("s46_p1", "h_s46_p1_a", "h_s46_p1_b"),
        _package(
            "s46_p2",
            "h_s46_p2_a",
            "h_s46_p2_b",
            gate_expression="gate == 'false'",
        ),
        _package("s46_p3", "h_s46_p3_a", "h_s46_p3_b"),
    ]
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    assert [run["status"] for run in started.json()["question_runs"]] == [
        "ready",
        "not_applicable",
        "ready",
    ]
    batch_id = started.json()["batch"]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        first = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert first["status"] == "succeeded", first
        partial = _read_batch(client, batch_id)
        assert partial["baselines"][0]["baseline"] is not None
        assert partial["baselines"][2]["baseline"] is None
        assert partial["proposal"] is None, "partial stays readable but incomplete"
        assert partial["workflow"]["status"] == "incomplete"
        assert partial["workflow"]["skipped"][0]["question_key"] == "s46_p2"

        second = worker_module.run_once(
            clean_registry, adapter, database_url=db_module.get_test_database_url()
        )
        assert second["status"] == "succeeded", second
        assert len(endpoint.captured) == 2
    finally:
        endpoint.stop()

    _assert_no_proposal_writing(list(endpoint.captured))
    final = _read_batch(client, batch_id)
    assert final["proposal"] is not None, "valid limited DDI must assemble"
    assert final["workflow"]["complete"] is True
    assert final["workflow"]["status"] == "complete"
    proposal = final["proposal"]
    assert [section["question_key"] for section in proposal["sections"]] == ["s46_p1", "s46_p3"]
    assert len(proposal["sections"]) == 2
    assert proposal["skipped"][0]["question_key"] == "s46_p2"
    assert "gate false" in proposal["skipped"][0]["reason"]
    assert len(proposal["coverage_warnings"]) >= 1
    assert any("coverage unavailable" in str(w).lower() for w in proposal["coverage_warnings"])
    assert any("gamma" in str(w).lower() for w in proposal["coverage_warnings"])
    assert final["workflow"]["coverage_warnings"] == proposal["coverage_warnings"]
    assert final["workflow"]["ddi_status"] == "valid"
    ddi_report = proposal["ddi_report"]
    assert ddi_report["dataset_version"].startswith("ddi-")
    assert len(ddi_report["coverage_unavailable_medications"]) >= 1
    for section in proposal["sections"]:
        assert section["section_text"]
        assert section["posteriors"]
    for entry in final["baselines"]:
        if entry["status"] == "ready":
            assert entry["baseline"] is not None
            _assert_posteriors_exact(entry["baseline"])


def test_missing_dataset_proposal_null_unavailable(clean_registry, monkeypatch) -> None:
    """S46 §3: missing dataset → jobs succeed but proposal null, ddi unavailable."""
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_noddi")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {"h_s46_n1_a": "yes", "h_s46_n1_b": "no", "h_s46_n2_a": "yes", "h_s46_n2_b": "no"}
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    packages = [
        _package("s46_n1", "h_s46_n1_a", "h_s46_n1_b"),
        _package("s46_n2", "h_s46_n2_a", "h_s46_n2_b"),
    ]
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    assert started.json()["batch"]["pinned_bundle"]["ddi_status"] == "unavailable"

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        for _ in range(2):
            outcome = worker_module.run_once(
                clean_registry, adapter, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
    finally:
        endpoint.stop()

    assert len(endpoint.captured) == 2
    _assert_no_proposal_writing(list(endpoint.captured))
    final = _read_batch(client, batch_id)
    assert all(entry["baseline"] is not None for entry in final["baselines"])
    assert final["proposal"] is None, "unavailable DDI keeps proposal incomplete"
    assert final["workflow"]["status"] == "incomplete"
    assert final["workflow"]["complete"] is False
    assert final["workflow"]["ddi_status"] == "unavailable"
    assert final["workflow"]["pending_question_keys"] == []


# --- S46 §4: freshness/pinning + LAI ---


def test_freshness_new_batch_old_proposal_immutable(clean_registry, monkeypatch, tmp_path) -> None:
    """S46 §4: changed meds → new fingerprint/batch/DDI, old proposal byte-identical."""
    _publish_complete(clean_registry, tmp_path)
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_fresh")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {"h_s46_f1_a": "yes", "h_s46_f1_b": "no", "h_s46_f2_a": "yes", "h_s46_f2_b": "no"}
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    revision = _save_meds(client, csrf, encounter_id, ["catalog_alpha", "catalog_beta"], revision)
    packages = [
        _package("s46_f1", "h_s46_f1_a", "h_s46_f1_b"),
        _package("s46_f2", "h_s46_f2_a", "h_s46_f2_b"),
    ]
    started = _start_workflow(client, csrf, encounter_id, revision, packages)
    assert started.status_code == 202, started.text
    old_batch_id = started.json()["batch"]["id"]
    old_fingerprint = started.json()["batch"]["fingerprint"]

    endpoint_old = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url_old = endpoint_old.start()
    try:
        config_old = provider_module.ProviderConfig(
            endpoint_url=url_old, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter_old = provider_module.BoundedProviderAdapter(
            config_old, grant_token="", database_url=db_module.get_test_database_url()
        )
        for _ in range(2):
            outcome = worker_module.run_once(
                clean_registry, adapter_old, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
    finally:
        endpoint_old.stop()
    assert len(endpoint_old.captured) == 2
    old_view = _read_batch(client, old_batch_id)
    assert old_view["proposal"] is not None
    old_proposal_bytes = contracts.canonical_json(old_view["proposal"])
    old_ddi_fp = old_view["proposal"]["ddi_report"]["medication_fingerprint"]
    old_sections = [section["section_text"] for section in old_view["proposal"]["sections"]]

    # Change medications: new fingerprint/batch/DDI expected.
    changed_revision = _save_meds(client, csrf, encounter_id, ["catalog_alpha"], revision)
    live = client.get(f"/api/v1/encounters/{encounter_id}").json()
    new_revision = int(live["revision"])
    assert changed_revision == new_revision
    assert new_revision != revision
    restarted = _start_workflow(client, csrf, encounter_id, new_revision, packages)
    assert restarted.status_code == 202, restarted.text
    new_batch_id = restarted.json()["batch"]["id"]
    assert new_batch_id != old_batch_id
    assert restarted.json()["batch"]["fingerprint"] != old_fingerprint

    endpoint_new = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url_new = endpoint_new.start()
    try:
        config_new = provider_module.ProviderConfig(
            endpoint_url=url_new, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter_new = provider_module.BoundedProviderAdapter(
            config_new, grant_token="", database_url=db_module.get_test_database_url()
        )
        for _ in range(2):
            outcome = worker_module.run_once(
                clean_registry, adapter_new, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
    finally:
        endpoint_new.stop()
    assert len(endpoint_new.captured) == 2
    _assert_no_proposal_writing(list(endpoint_old.captured) + list(endpoint_new.captured))

    new_view = _read_batch(client, new_batch_id)
    assert new_view["proposal"] is not None
    assert new_view["proposal"]["ddi_report"]["medication_fingerprint"] != old_ddi_fp
    assert new_view["batch"]["fingerprint"] != old_fingerprint

    reread_old = _read_batch(client, old_batch_id)
    assert contracts.canonical_json(reread_old["proposal"]) == old_proposal_bytes
    assert [
        section["section_text"] for section in reread_old["proposal"]["sections"]
    ] == old_sections
    assert reread_old["batch"]["fingerprint"] == old_fingerprint
    # Old pinned content version stays v1.
    assert reread_old["batch"]["pinned_bundle"]["per_question"]["s46_f1"]["package_version"] == (
        "s46-test-v1"
    )


def test_content_version_does_not_mutate_pinned(clean_registry, monkeypatch, tmp_path) -> None:
    """S46 §4: new content version leaves old pinned_bundle per_question at v1."""
    _publish_complete(clean_registry, tmp_path)
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_pin")
    encounter_id = _create_patient(client, csrf, "0012345678")
    values = {"h_s46_v1_a": "yes", "h_s46_v1_b": "no", "h_s46_v2_a": "yes", "h_s46_v2_b": "no"}
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    revision = _save_meds(client, csrf, encounter_id, ["catalog_alpha"], revision)
    packages_v1 = [
        _package("s46_v1", "h_s46_v1_a", "h_s46_v1_b", version="s46-test-v1"),
        _package("s46_v2", "h_s46_v2_a", "h_s46_v2_b", version="s46-test-v1"),
    ]
    started_v1 = _start_workflow(client, csrf, encounter_id, revision, packages_v1)
    assert started_v1.status_code == 202, started_v1.text
    batch_v1 = started_v1.json()["batch"]["id"]

    endpoint_v1 = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in range(2)]
    )
    url_v1 = endpoint_v1.start()
    try:
        config_v1 = provider_module.ProviderConfig(
            endpoint_url=url_v1, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter_v1 = provider_module.BoundedProviderAdapter(
            config_v1, grant_token="", database_url=db_module.get_test_database_url()
        )
        for _ in range(2):
            outcome = worker_module.run_once(
                clean_registry, adapter_v1, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
    finally:
        endpoint_v1.stop()
    view_v1 = _read_batch(client, batch_v1)
    assert view_v1["proposal"] is not None
    v1_bytes = contracts.canonical_json(view_v1["proposal"])
    v1_content = contracts.canonical_json(view_v1["batch"]["pinned_bundle"])

    packages_v2 = [
        _package("s46_v1", "h_s46_v1_a", "h_s46_v1_b", version="s46-test-v2"),
        _package("s46_v2", "h_s46_v2_a", "h_s46_v2_b", version="s46-test-v2"),
    ]
    live = client.get(f"/api/v1/encounters/{encounter_id}").json()
    current_revision = int(live["revision"])
    started_v2 = _start_workflow(client, csrf, encounter_id, current_revision, packages_v2)
    assert started_v2.status_code == 202, started_v2.text
    batch_v2 = started_v2.json()["batch"]["id"]
    assert batch_v2 != batch_v1
    assert started_v2.json()["batch"]["fingerprint"] != view_v1["batch"]["fingerprint"]

    reread_v1 = _read_batch(client, batch_v1)
    assert contracts.canonical_json(reread_v1["proposal"]) == v1_bytes
    assert contracts.canonical_json(reread_v1["batch"]["pinned_bundle"]) == v1_content
    per_question = reread_v1["batch"]["pinned_bundle"]["per_question"]
    assert per_question["s46_v1"]["package_version"] == "s46-test-v1"
    assert per_question["s46_v2"]["package_version"] == "s46-test-v1"
    assert (
        started_v2.json()["batch"]["pinned_bundle"]["per_question"]["s46_v1"]["package_version"]
        == "s46-test-v2"
    )
    # Old pinned run content stays v1 per question via question review as well.
    for run in reread_v1["question_runs"]:
        review = client.get(f"/api/v1/question-runs/{run['id']}/review")
        assert review.status_code == 200, review.text
        assert review.json()["baseline"] is not None


def test_lai_discussion_only_retained_branch(clean_registry, monkeypatch, tmp_path) -> None:
    """S46 §4: 6-question followup with lai_discussion renders only retained review."""
    _publish_complete(clean_registry, tmp_path)
    _, _, client, csrf = _new_physician(clean_registry, monkeypatch, "dr_s46_lai")
    encounter_id = _create_patient(client, csrf, "0012345678")
    keys = ["s46_f1", "s46_f2", "s46_f3", "s46_f4", "s46_f5", "lai_discussion"]
    values: dict[str, Any] = {}
    packages: list[dict[str, Any]] = []
    for key in keys:
        field_a = f"h_{key}_a"
        field_b = f"h_{key}_b"
        values[field_a] = "yes"
        values[field_b] = "no"
        if key == "lai_discussion":
            packages.append(
                _package(
                    key,
                    field_a,
                    field_b,
                    workflow="followup",
                    template_prefix="LAI discussion retained review",
                )
            )
        else:
            packages.append(_package(key, field_a, field_b, workflow="followup"))
    revision = _patch_history(client, csrf, encounter_id, values, 1)
    revision = _save_meds(client, csrf, encounter_id, ["catalog_alpha", "catalog_beta"], revision)
    live = client.get(f"/api/v1/encounters/{encounter_id}").json()
    current_revision = int(live["revision"])
    started = _start_workflow(client, csrf, encounter_id, current_revision, packages)
    assert started.status_code == 202, started.text
    assert [run["question_key"] for run in started.json()["question_runs"]] == keys
    batch_id = started.json()["batch"]["id"]

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt()} for _ in keys]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="test-model", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=db_module.get_test_database_url()
        )
        for index, key in enumerate(keys):
            assert len(endpoint.captured) == index
            outcome = worker_module.run_once(
                clean_registry, adapter, database_url=db_module.get_test_database_url()
            )
            assert outcome["status"] == "succeeded", outcome
            assert _question_key_of(endpoint.captured[index]) == key
    finally:
        endpoint.stop()

    assert [_question_key_of(entry) for entry in endpoint.captured] == keys
    _assert_no_proposal_writing(list(endpoint.captured))
    final = _read_batch(client, batch_id)
    assert final["proposal"] is not None
    assert final["workflow"]["complete"] is True
    assert len(final["proposal"]["sections"]) == 6
    assert [section["question_key"] for section in final["proposal"]["sections"]] == keys
    lai_section = next(
        section
        for section in final["proposal"]["sections"]
        if section["question_key"] == "lai_discussion"
    )
    assert "discussion" in lai_section["section_text"].lower()
    assert "retained" in lai_section["section_text"].lower()
    for forbidden in (
        "product-choice",
        "product choice",
        "choose product",
        "dose",
        "aripiprazole",
        "risperidone",
    ):
        assert forbidden not in lai_section["section_text"].lower(), forbidden
    # Every section comes from its reviewed template branch, never LLM prose.
    for section in final["proposal"]["sections"]:
        assert section["section_text"]
        assert "Take drug" not in section["section_text"]
    lai_run_id = next(
        run["id"] for run in final["question_runs"] if run["question_key"] == "lai_discussion"
    )
    review = client.get(f"/api/v1/question-runs/{lai_run_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["baseline"]["section_text"] == lai_section["section_text"]
