"""Complete append-only audit and administration views (S52, seam T1 only).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio + ``DeterministicProviderEndpoint``
(real PostgreSQL, real routes, no internal mocks, no DB-row asserts for
behavior, no private-helper mirror tests). Synthetic two-node A->B network
only (``80/20``, ``90/10``, ``30/70``); no clinical content. Fixture setup
may truncate/migrate; behavior assertions use HTTP only, except the
append-only grant check which is a migration/operations privilege probe
(``has_table_privilege``), not a behavioral assertion.

Covers tasks.md S52 (FR-03, FR-42):

- §1: successful mutation + event commit together; failed commands produce
  no false success event.
- §2: stable attribution survives rename/deactivation (snapshot display +
  immutable actor id); original runs, slider edits/redistribution, resets,
  calculation outcomes, acceptance and sign events carry
  actor/time/patient/encounter/question/run/revision plus before-after CPT
  values; keys and unrelated clinical text excluded.
- §3: physician cannot inspect audit (403); admin filters by
  actor/operation/time/target with stable (occurred_at, id) pagination
  (default 25, max 100); no update/delete interface exists.
- §4 (backend slice): backup/restore operation names are reserved hooks
  (no backup/restore implementation here).

DB-owner/restore can replace history: this suite does NOT claim
tamper-proof storage (see ``test_audit_grants_append_only``).
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
            "version": "s52-test-v1",
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


def _audit_list(client, params=None):
    response = client.get("/api/v1/audit-events", params=params or {})
    return response


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S52 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s52/1", "concepts": concepts, "aliases": []}


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


def _single_question_setup(clean_registry, monkeypatch, tmp_path, username: str):
    """Real pipeline to one ready question: DDI + history + meds + batch."""
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
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
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
        json={"packages": [_package("s52_q1", "h_a", "h_b")]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch = started.json()["batch"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}
    return client, csrf, encounter_id, patient_id, revision, batch, runs


def _run_generation_once(engine, cpt_payload: dict[str, Any]):
    from datetime import timedelta as _timedelta

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
            engine, adapter, now=contracts.utcnow(), database_url=db_module.get_test_database_url()
        )
        return endpoint, outcome
    except Exception:
        endpoint.stop()
        raise


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


# --- S52 §3: admin-only inspection, no write interface ---


def test_audit_requires_admin(clean_registry, monkeypatch) -> None:
    """S52 §3: anonymous 401, physician 403, admin 200 through HTTP."""
    _new_physician(clean_registry, monkeypatch, "dr_s52_guard")
    physician_client, _ = _login_physician(clean_registry, monkeypatch, "dr_s52_guard")
    denied = physician_client.get("/api/v1/audit-events")
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anonymous = TestClient(app)
    missing = anonymous.get("/api/v1/audit-events")
    assert missing.status_code == 401, missing.text

    admin, _ = _admin_client(clean_registry, monkeypatch)
    listed = admin.get("/api/v1/audit-events")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"items", "total", "limit", "offset"} <= set(body)
    assert body["limit"] == 25 and body["offset"] == 0


def test_audit_has_no_write_interface(clean_registry, monkeypatch) -> None:
    """S52 §3: audit is read-only; no update/delete route exists."""
    admin, csrf = _admin_client(clean_registry, monkeypatch)
    headers = _auth_headers(csrf)
    for method in ("post", "put", "patch"):
        response = getattr(admin, method)("/api/v1/audit-events", json={}, headers=headers)
        assert response.status_code == 405, (method, response.text)
    response = admin.delete("/api/v1/audit-events", headers=headers)
    assert response.status_code == 405, ("delete", response.text)


def test_audit_pagination_bounded_and_stable(clean_registry, monkeypatch) -> None:
    """S52 §3: default 25 / max 100 bounds with stable (occurred_at, id) order."""
    admin, _ = _admin_client(clean_registry, monkeypatch)
    bad = admin.get("/api/v1/audit-events", params={"limit": 101})
    assert bad.status_code == 422, bad.text
    bad_zero = admin.get("/api/v1/audit-events", params={"limit": 0})
    assert bad_zero.status_code == 422, bad_zero.text

    first = admin.get("/api/v1/audit-events", params={"limit": 2, "offset": 0}).json()
    assert first["limit"] == 2 and first["offset"] == 0
    assert len(first["items"]) <= 2
    keys = [(item["occurred_at"], item["id"]) for item in first["items"]]
    assert keys == sorted(keys)

    second = admin.get("/api/v1/audit-events", params={"limit": 2, "offset": 2}).json()
    assert second["offset"] == 2
    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert not (first_ids & second_ids)


def test_audit_filters_by_actor_operation_time_target(clean_registry, monkeypatch) -> None:
    """S52 §3: admin filters by actor/operation/time/target through HTTP."""
    _new_physician(clean_registry, monkeypatch, "dr_s52_filter")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s52_filter")
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

    admin, _ = _admin_client(clean_registry, monkeypatch)
    by_operation = admin.get(
        "/api/v1/audit-events", params={"operation": "patients.create.success"}
    ).json()
    assert by_operation["total"] >= 1
    assert all(item["operation"] == "patients.create.success" for item in by_operation["items"])

    by_target = admin.get(
        "/api/v1/audit-events", params={"patient_id": patient_id}
    ).json()
    assert by_target["total"] >= 1
    for item in by_target["items"]:
        assert item["details"].get("patient_id") == patient_id

    import uuid as _uuid

    empty_target = admin.get(
        "/api/v1/audit-events", params={"patient_id": str(_uuid.uuid4())}
    ).json()
    assert empty_target["total"] == 0 and empty_target["items"] == []

    by_actor = admin.get(
        "/api/v1/audit-events", params={"actor": "dr_s52_filter"}
    ).json()
    assert by_actor["total"] >= 1

    future = admin.get(
        "/api/v1/audit-events", params={"since": "2999-01-01T00:00:00Z"}
    ).json()
    assert future["total"] == 0
    past = admin.get(
        "/api/v1/audit-events", params={"until": "2000-01-01T00:00:00Z"}
    ).json()
    assert past["total"] == 0

    bad_time = admin.get("/api/v1/audit-events", params={"since": "not-a-time"})
    assert bad_time.status_code == 422, bad_time.text
    bad_uuid = admin.get("/api/v1/audit-events", params={"patient_id": "nope"})
    assert bad_uuid.status_code == 422, bad_uuid.text


def test_audit_grants_append_only(clean_registry, monkeypatch) -> None:
    """S52 §3: migration/operations check — app role appends/reads only.

    This is a privilege probe (``has_table_privilege``), not a behavioral
    assertion. It does NOT claim tamper-proof storage: the database owner
    or a restore can still replace history.
    """
    with clean_registry.connect() as connection:
        roles = sorted(
            row[0]
            for row in connection.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'x_insight%'")
            )
        )
    assert roles == ["x_insight_app", "x_insight_migrate", "x_insight_readonly"]

    def _can(role: str, privilege: str) -> bool:
        with clean_registry.connect() as connection:
            return bool(
                connection.execute(
                    text(f"SELECT has_table_privilege('{role}', 'audit_events', '{privilege}')")
                ).scalar()
            )

    assert _can("x_insight_app", "SELECT")
    assert _can("x_insight_app", "INSERT")
    assert not _can("x_insight_app", "UPDATE")
    assert not _can("x_insight_app", "DELETE")


def test_failed_command_creates_no_false_success(clean_registry, monkeypatch) -> None:
    """S52 §1: a failed (stale/invalid) command leaves no false success event."""
    _new_physician(clean_registry, monkeypatch, "dr_s52_atomic")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s52_atomic")
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

    admin, _ = _admin_client(clean_registry, monkeypatch)
    before = admin.get(
        "/api/v1/audit-events", params={"operation": "encounters.patch.success"}
    ).json()["total"]

    stale = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(999)},
    )
    assert stale.status_code == 412, stale.text
    invalid = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": "not-an-object"},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert invalid.status_code == 422, invalid.text

    after = admin.get(
        "/api/v1/audit-events", params={"operation": "encounters.patch.success"}
    ).json()["total"]
    assert after == before


# --- S52 §2: stable attribution + required event coverage ---


def test_attribution_survives_rename_and_deactivation(clean_registry, monkeypatch) -> None:
    """S52 §2: audit keeps the snapshot display name + immutable actor id."""
    admin, admin_csrf = _new_physician(clean_registry, monkeypatch, "dr_s52_rename")
    created = admin.get("/api/v1/physicians").json()
    target = next(item for item in created["items"] if item["username"] == "dr_s52_rename")
    physician_id = target["id"]

    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s52_rename")
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

    renamed = admin.patch(
        f"/api/v1/physicians/{physician_id}",
        json={"username": "dr_s52_renamed"},
        headers={**_auth_headers(admin_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert renamed.status_code == 200, renamed.text

    preview = admin.get(f"/api/v1/physicians/{physician_id}/open-drafts")
    assert preview.status_code == 200, preview.text
    draft_revision = int(preview.json()["draft_set_revision"])
    deactivated = admin.post(
        f"/api/v1/physicians/{physician_id}/deactivate",
        json={"draft_action": "retain", "draft_set_revision": draft_revision},
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text

    events = admin.get(
        "/api/v1/audit-events", params={"operation": "patients.create.success"}
    ).json()
    assert events["total"] >= 1
    event = next(
        item
        for item in events["items"]
        if item["details"].get("identifier") == "0012345678"
    )
    # Snapshot display name survives the rename; immutable id still matches.
    assert event["actor"] == "dr_s52_rename"
    assert event["actor_display"] == "dr_s52_rename"
    assert event["actor_id"] == physician_id


def test_adjustment_reset_acceptance_sign_events_through_http(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S52 §2: slider/redistribution, reset, acceptance and sign via audit HTTP."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _single_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s52_cover"
    )
    run_id = runs["s52_q1"]["id"]
    admin, _ = _admin_client(clean_registry, monkeypatch)

    endpoint, outcome = _run_generation_once(clean_registry, _valid_cpt())
    try:
        assert outcome.get("status") == "succeeded", outcome
    finally:
        endpoint.stop()

    started_events = admin.get(
        "/api/v1/audit-events", params={"operation": "generation.start.success"}
    ).json()
    assert started_events["total"] >= 1
    run_success = admin.get(
        "/api/v1/audit-events", params={"operation": "generation.run.success"}
    ).json()
    assert run_success["total"] >= 1
    success_event = next(
        item for item in run_success["items"] if item["details"].get("question_run_id") == run_id
    )
    assert success_event["details"]["patient_id"] == patient_id
    assert success_event["details"]["encounter_id"] == encounter_id
    assert success_event["details"]["batch_id"] == batch["id"]
    assert success_event["details"]["question_key"] == "s52_q1"
    assert "baseline_id" in success_event["details"]
    assert isinstance(success_event["details"].get("tool_calls_made"), int)

    adjusted = client.post(
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
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()["revision"]["after_row"]["percentages"] == ["60", "40"]

    from x_insight.reasoning import provider as _provider
    from x_insight.reasoning import worker as _worker

    stub = _provider.ControlledStubAdapter(mode="succeed")
    assert _worker.run_once(clean_registry, stub)["status"] == "succeeded"

    reset = client.post(
        f"/api/v1/question-runs/{run_id}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text

    retried = client.post(
        f"/api/v1/question-runs/{run_id}/retry-calculation",
        json={"expected_review_revision": 3},
        headers=_auth_headers(csrf),
    )
    assert retried.status_code == 200, retried.text

    retry_events = admin.get(
        "/api/v1/audit-events", params={"operation": "cpt_retry.requested"}
    ).json()
    retry_event = next(
        item for item in retry_events["items"] if item["details"].get("question_run_id") == run_id
    )
    assert retry_event["details"]["patient_id"] == patient_id
    assert retry_event["details"]["cpt_revision_id"] == reset.json()["revision"]["id"]

    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"

    review = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    accepted = client.post(
        f"/api/v1/question-runs/{run_id}/acceptance",
        json=_accept_body(review),
        headers=_auth_headers(csrf),
    )
    assert accepted.status_code == 200, accepted.text
    acceptance = accepted.json()["acceptance"]

    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary with reset probabilities.", "expected_plan_revision": 1},
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
                    "acceptance_id": acceptance["id"],
                    "expected_review_revision": int(accepted.json()["review_revision"]),
                }
            ],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    encounter_revision = int(signed.json()["encounter"]["revision"])
    addendum = client.post(
        f"/api/v1/encounters/{encounter_id}/addenda",
        json={"text": "Correction text.", "expected_encounter_revision": encounter_revision},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(encounter_revision)},
    )
    assert addendum.status_code == 201, addendum.text

    adjustment_events = admin.get(
        "/api/v1/audit-events", params={"operation": "cpt_adjustment.success"}
    ).json()
    adjustment = next(
        item for item in adjustment_events["items"] if item["details"].get("question_run_id") == run_id
    )
    assert adjustment["details"]["patient_id"] == patient_id
    assert adjustment["details"]["encounter_id"] == encounter_id
    assert adjustment["details"]["question_key"] == "s52_q1"
    assert adjustment["details"]["before_row"]["percentages"] == ["80", "20"]
    assert adjustment["details"]["after_row"]["percentages"] == ["60", "40"]
    assert adjustment["actor_id"] is not None

    reset_events = admin.get(
        "/api/v1/audit-events", params={"operation": "cpt_reset.success"}
    ).json()
    reset_event = next(
        item for item in reset_events["items"] if item["details"].get("question_run_id") == run_id
    )
    assert reset_event["details"]["patient_id"] == patient_id
    assert reset_event["details"]["kind"] == "reset"

    calc_events = admin.get(
        "/api/v1/audit-events", params={"operation": "calculation.success"}
    ).json()
    assert calc_events["total"] >= 1
    calc = next(
        item for item in calc_events["items"] if item["details"].get("question_run_id") == run_id
    )
    assert calc["details"]["patient_id"] == patient_id

    acceptance_events = admin.get(
        "/api/v1/audit-events", params={"operation": "prob_acceptance.success"}
    ).json()
    acceptance_event = next(
        item
        for item in acceptance_events["items"]
        if item["details"].get("question_run_id") == run_id
    )
    assert acceptance_event["details"]["patient_id"] == patient_id
    assert acceptance_event["details"]["acceptance_id"] == acceptance["id"]

    sign_events = admin.get(
        "/api/v1/audit-events", params={"operation": "encounters.sign.success"}
    ).json()
    sign_event = next(
        item for item in sign_events["items"] if item["details"].get("encounter_id") == encounter_id
    )
    assert sign_event["details"]["patient_id"] == patient_id

    addendum_events = admin.get(
        "/api/v1/audit-events", params={"operation": "encounters.addendum.success"}
    ).json()
    addendum_event = next(
        item
        for item in addendum_events["items"]
        if item["details"].get("encounter_id") == encounter_id
    )
    assert addendum_event["details"]["patient_id"] == patient_id

    # Keys and unrelated clinical text never land in audit details.
    forbidden = ("password", "passwd", "secret", "api_key", "private_key", "csrf", "session")
    for operation in (
        "generation.start.success",
        "generation.run.success",
        "cpt_adjustment.success",
        "cpt_reset.success",
        "calculation.success",
        "prob_acceptance.success",
        "encounters.sign.success",
        "encounters.addendum.success",
    ):
        payload = admin.get("/api/v1/audit-events", params={"operation": operation}).json()
        blob = json.dumps(payload["items"]).lower()
        for token in forbidden:
            assert token not in blob, (operation, token)


def test_original_run_failure_audited_without_false_success(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S52 §§1-2: a failed original run audits failure metadata, never success."""
    client, csrf, encounter_id, patient_id, revision, batch, runs = _single_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s52_fail"
    )
    run_id = runs["s52_q1"]["id"]
    admin, _ = _admin_client(clean_registry, monkeypatch)

    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[
            {"type": "status", "status": 401, "body": {"error": "bad credentials"}},
            {"type": "status", "status": 401, "body": {"error": "bad credentials"}},
            {"type": "status", "status": 401, "body": {"error": "bad credentials"}},
        ]
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
            clean_registry, adapter, now=contracts.utcnow(),
            database_url=db_module.get_test_database_url(),
        )
    finally:
        endpoint.stop()
    assert outcome.get("status") == "failed", outcome

    failures = admin.get(
        "/api/v1/audit-events", params={"operation": "generation.run.failed"}
    ).json()
    assert failures["total"] >= 1
    failure = next(
        item for item in failures["items"] if item["details"].get("question_run_id") == run_id
    )
    assert failure["details"]["patient_id"] == patient_id
    assert failure["details"]["encounter_id"] == encounter_id
    assert failure["details"]["question_key"] == "s52_q1"
    assert failure["details"].get("error_code")

    successes = admin.get(
        "/api/v1/audit-events", params={"operation": "generation.run.success"}
    ).json()
    assert all(
        item["details"].get("question_run_id") != run_id for item in successes["items"]
    )

    review = client.get(f"/api/v1/question-runs/{run_id}/review").json()
    assert review["baseline"] is None


def test_backup_restore_operation_hooks_reserved(clean_registry, monkeypatch) -> None:
    """S52 §4 (backend slice): backup/restore/export hooks exist as names only."""
    from x_insight.operations import audit as audit_module

    for name in (
        "BACKUP_CREATE_SUCCESS_OPERATION",
        "BACKUP_CREATE_FAILED_OPERATION",
        "RESTORE_VALIDATE_SUCCESS_OPERATION",
        "RESTORE_VALIDATE_FAILED_OPERATION",
        "RESTORE_COMMIT_SUCCESS_OPERATION",
        "RESTORE_COMMIT_FAILED_OPERATION",
        "EXPORT_PATIENTS_SUCCESS_OPERATION",
        "EXPORT_PHYSICIANS_SUCCESS_OPERATION",
    ):
        assert isinstance(getattr(audit_module, name), str)
        assert getattr(audit_module, name)
