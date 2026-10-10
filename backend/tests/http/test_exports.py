"""Export lists and printable longitudinal patient reports (S53, seam T1 only).

Through public authenticated HTTP on real PostgreSQL + ``run_once()`` with
``BoundedProviderAdapter`` over real MCP stdio + ``DeterministicProviderEndpoint``
for original generation and ``ControlledStubAdapter`` drains for local
recalculation (real PostgreSQL, real routes, no internal mocks, no DB-row
asserts for behavior, no private-helper mirror tests). Synthetic two-node
A->B network only (``80/20``, ``90/10``, ``30/70``); no clinical content. No
test shortcut creating a signable success flag — every signable batch runs
the real pipeline (generation start → worker → MCP → provider → validation
→ inference → rendering → proposal), then real adjustment/reset/acceptance
and atomic signing. Expected literals are worked fixtures, never
implementation output.

Covers tasks.md S53 (FR-03, FR-40; plan.md §§1.3, 2.1, 4.3, 10.1):

- §1: admin-only patient/physician CSV gates (anon 401, physician 403);
  stable English headers, UTF-8, QUOTE_MINIMAL quoting, exact ten-digit
  identifier bytes (``0012345678`` with leading zeros, never a numeric
  column, never a ``="..."`` wrapper); physician CSV carries safe columns
  only and excludes non-physicians; neither CSV carries credentials/hashes.
- §2: formula-like text neutralized with a leading quote (no formula
  wrappers anywhere); quotes/newlines round-trip through ``csv`` parsing.
  The import-as-Text note is operator documentation (Exports page copy);
  over HTTP this suite proves the raw bytes stay exact text.
- §3: admin-only signed-patient HTML (physicians 403 per the provisional
  print policy, plan §§1.3/2.1); malicious text stays inert (escaped);
  per-question complete original + accepted CPTs/results/recommendations,
  pinned versions, adjustment indicators (unchanged / adjusted /
  adjusted-then-reset with retained history), physician/timestamps;
  multi-encounter chronology, secondary plans, frozen notes, addenda;
  open-draft content excluded.
- §4 + Verify/exit: ``private, no-store`` on all three routes; export and
  report audit events with safe details; denied attempts leave no success
  events.

Isolation note (S52 carryover fixed at this touchpoint): the DDI
``_publish_limited`` helper writes dataset releases, so this module's
``_truncate`` also truncates the ``ddi_*`` tables — running ``tests/http``
before ``tests/worker`` no longer flips a worker test. The
``test_only_s02_tables_exist`` drift stays deselected (separate session).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid as uuid_module
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

# Worked malicious-text fixtures (never executed; must render inert).
PLAN_EVIL = "<script>alert('plan-xss')</script> Keep monitoring monthly."
NOTE_EVIL = "<img src=x onerror=alert('note-xss')> Proposal looks complete."
ADDENDUM_EVIL = (
    "Correction <b>bold</b> & <i>italic</i> with "
    "<a href='http://example.invalid'>link</a>."
)

EXPECTED_PATIENT_HEADERS = (
    "patient_id,identifier,given_name,family_name,sex,age,"
    "clinical_status,phone,archived,created_at,updated_at"
)
EXPECTED_PHYSICIAN_HEADERS = "physician_id,username,role,active,theme"

_FORBIDDEN_SECRET_TOKENS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "private_key",
    "csrf",
    "session",
)


def _package(question_key: str, field_a: str, field_b: str) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": "registration",
            "version": "s53-test-v1",
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
        "source": "synthetic S53 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s53/1", "concepts": concepts, "aliases": []}


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


def _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, username: str):
    """Real pipeline to TWO signed encounters on one patient.

    Registration encounter: three questions — ``s53_q1`` unchanged,
    ``s53_q2`` physician-adjusted (80/20 → 60/40), ``s53_q3``
    adjusted-then-reset (history retained, final equals original).
    Follow-up encounter: one unchanged question ``s53_f1``. Malicious
    markup travels in the registration secondary plan + frozen note and in
    the follow-up addendum (second physician). Returns the physician
    client/csrf plus patient/encounter/run ids and the evil strings.
    """
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
                _package("s53_q1", "a1", "b1"),
                _package("s53_q2", "a2", "b2"),
                _package("s53_q3", "a3", "b3"),
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

    # Q2 adjusted 80/20 → 60/40 (independent literal from S48a redistribution).
    adjusted = client.post(
        f"/api/v1/question-runs/{runs['s53_q2']['id']}/cpt-adjustments",
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

    # Q3 adjusted then reset: history retained, final equals original.
    adjusted3 = client.post(
        f"/api/v1/question-runs/{runs['s53_q3']['id']}/cpt-adjustments",
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
        f"/api/v1/question-runs/{runs['s53_q3']['id']}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset3.status_code == 200, reset3.text
    retried3 = client.post(
        f"/api/v1/question-runs/{runs['s53_q3']['id']}/retry-calculation",
        json={"expected_review_revision": 3},
        headers=_auth_headers(csrf),
    )
    assert retried3.status_code == 200, retried3.text
    _drain_local(clean_registry)
    q3_solved = client.get(f"/api/v1/question-runs/{runs['s53_q3']['id']}/review").json()
    assert q3_solved["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]

    run_to_acc: dict[str, dict[str, Any]] = {}
    run_to_rev: dict[str, int] = {}
    for key in ("s53_q1", "s53_q2", "s53_q3"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        run_to_acc[runs[key]["id"]] = posted.json()["acceptance"]
        run_to_rev[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert run_to_acc[runs["s53_q1"]["id"]]["result_kind"] == "baseline"
    assert run_to_acc[runs["s53_q2"]["id"]]["result_kind"] == "calculation"

    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": NOTE_EVIL},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(draft["revision"])},
    )
    assert noted.status_code == 201, noted.text
    revision = int(noted.json()["revision"])
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": PLAN_EVIL, "expected_plan_revision": 1},
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
    signed_revision = int(signed.json()["revision"])

    # Second signed encounter: follow-up without a copied baseline (same
    # public pipeline as registration: history → meds → batch → worker →
    # acceptance → plan → sign).
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
        json={"packages": [_package("s53_f1", "fa", "fb")]},
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
    review2 = client.get(f"/api/v1/question-runs/{runs2['s53_f1']['id']}/review").json()
    accepted2 = client.post(
        f"/api/v1/question-runs/{runs2['s53_f1']['id']}/acceptance",
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
                    "question_run_id": runs2["s53_f1"]["id"],
                    "acceptance_id": accepted2.json()["acceptance"]["id"],
                    "expected_review_revision": int(accepted2.json()["review_revision"]),
                }
            ],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision2)},
    )
    assert signed2.status_code == 200, signed2.text
    signed_revision2 = int(signed2.json()["revision"])

    # Any-physician addendum with malicious markup on the follow-up.
    appended = other.post(
        f"/api/v1/encounters/{followup_id}/addenda",
        json={"text": ADDENDUM_EVIL, "expected_encounter_revision": signed_revision2},
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
        "signed_revision": signed_revision,
        "signed_revision2": signed_revision2,
        "runs": runs,
        "runs2": runs2,
    }


# --- S53 §1: admin-only gates, stable headers, UTF-8, exact identifier ---


def test_exports_require_admin(clean_registry, monkeypatch) -> None:
    """S53 §1: anon 401, physician 403, admin 200 on all three export routes."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_guard")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_guard")
    _register_patient(physician, physician_csrf, "0012345678")

    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anonymous = TestClient(app)
    admin, _ = _admin_client(clean_registry, monkeypatch)

    patient_id = admin.get("/api/v1/patients", params={"limit": 1}).json()["items"][0]["id"]
    routes = [
        "/api/v1/exports/patients.csv",
        "/api/v1/exports/physicians.csv",
        f"/api/v1/patients/{patient_id}/report",
    ]
    for route in routes:
        missing = anonymous.get(route)
        assert missing.status_code == 401, (route, missing.text)
        assert missing.json()["code"] == "UNAUTHENTICATED"
        denied = physician.get(route)
        assert denied.status_code == 403, (route, denied.text)
        assert denied.json()["code"] == "FORBIDDEN"
        allowed = admin.get(route)
        assert allowed.status_code == 200, (route, allowed.text)
        assert allowed.headers["Cache-Control"] == "private, no-store", route
    assert admin.get("/api/v1/exports/patients.csv").headers["content-type"].startswith("text/csv")
    assert admin.get("/api/v1/exports/physicians.csv").headers["content-type"].startswith("text/csv")
    report = admin.get(f"/api/v1/patients/{patient_id}/report")
    assert report.headers["content-type"].startswith("text/html")


def test_patients_csv_stable_headers_utf8_identifier_bytes(
    clean_registry, monkeypatch
) -> None:
    """S53 §1: stable headers, UTF-8 names, exact ``0012345678`` bytes."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_csv")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_csv")
    _register_patient(
        client,
        csrf,
        "0012345678",
        given_name="Émile",
        family_name="Müller",
        phone="+43 699 123456",
    )

    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get("/api/v1/exports/patients.csv")
    assert response.status_code == 200, response.text
    raw = response.content
    text = raw.decode("utf-8")
    lines = text.split("\r\n")
    assert lines[0] == EXPECTED_PATIENT_HEADERS
    # Exact ten-digit text bytes with leading zeros (never numeric, never wrapped).
    assert b",0012345678," in raw
    assert '="0012345678"' not in text
    assert "Émile" in text and "Müller" in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == EXPECTED_PATIENT_HEADERS.split(",")
    by_identifier = {row[1]: row for row in rows[1:]}
    assert by_identifier["0012345678"][2] == "Émile"
    assert by_identifier["0012345678"][3] == "Müller"
    # "+" leads a spreadsheet formula, so the phone carries the "'" prefix.
    assert by_identifier["0012345678"][7] == "'+43 699 123456"
    assert by_identifier["0012345678"][8] == "false"


def test_physicians_csv_safe_fields_excludes_non_physicians(
    clean_registry, monkeypatch
) -> None:
    """S53 §1: safe columns only; admin excluded; active flag round-trips."""
    admin, admin_csrf = _new_physician(clean_registry, monkeypatch, "dr_s53_list_a")
    second = admin.post(
        "/api/v1/physicians",
        json={"username": "dr_s53_list_b", "password": "pw123"},
        headers=_auth_headers(admin_csrf),
    )
    assert second.status_code == 201, second.text
    target_id = second.json()["user"]["id"]
    preview = admin.get(f"/api/v1/physicians/{target_id}/open-drafts")
    assert preview.status_code == 200, preview.text
    deactivated = admin.post(
        f"/api/v1/physicians/{target_id}/deactivate",
        json={
            "draft_action": "retain",
            "draft_set_revision": int(preview.json()["draft_set_revision"]),
        },
        headers=_auth_headers(admin_csrf),
    )
    assert deactivated.status_code == 200, deactivated.text

    response = admin.get("/api/v1/exports/physicians.csv")
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8")
    assert text.split("\r\n")[0] == EXPECTED_PHYSICIAN_HEADERS
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == EXPECTED_PHYSICIAN_HEADERS.split(",")
    by_username = {row[1]: row for row in rows[1:]}
    assert set(by_username) == {"dr_s53_list_a", "dr_s53_list_b"}
    assert "admin" not in by_username
    assert all(row[2] == "physician" for row in by_username.values())
    assert by_username["dr_s53_list_a"][3] == "true"
    assert by_username["dr_s53_list_b"][3] == "false"
    assert all(row[4] in ("light", "dark") for row in by_username.values())


def test_csvs_contain_no_credentials_or_hashes(clean_registry, monkeypatch) -> None:
    """S53 §1: neither CSV carries passwords, hashes, tokens, or secrets."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_noleak")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_noleak")
    _register_patient(client, csrf, "0012345678", phone="=2+2 please")

    admin, _ = _admin_client(clean_registry, monkeypatch)
    for route in ("/api/v1/exports/patients.csv", "/api/v1/exports/physicians.csv"):
        body = admin.get(route)
        assert body.status_code == 200, (route, body.text)
        blob = body.content.decode("utf-8").lower()
        for token in _FORBIDDEN_SECRET_TOKENS:
            assert token not in blob, (route, token)
        assert len(body.content) > 0


# --- S53 §2: formula neutralization without wrappers; quoting round-trip ---


def test_patients_csv_neutralizes_formula_like_text_without_wrappers(
    clean_registry, monkeypatch
) -> None:
    """S53 §2: ``= + - @`` (and tab/newline) leads gain ``'``; no ``="..."``."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_formula")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_formula")
    phones = ["=SUM(A1:A2)", "+cmd|calc", "-2+3 hypothetical", "@mention", "\tindented"]
    identifiers = ["0012345678", "0012345679", "0012345680", "0012345681", "0012345682"]
    for identifier, phone in zip(identifiers, phones, strict=True):
        _register_patient(client, csrf, identifier, phone=phone)

    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get("/api/v1/exports/patients.csv")
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8")
    assert '="' not in text
    rows = list(csv.reader(io.StringIO(text)))
    by_identifier = {row[1]: row for row in rows[1:]}
    for identifier, phone in zip(identifiers, phones, strict=True):
        assert by_identifier[identifier][7] == "'" + phone, (identifier, phone)
    # Identifiers stay exact raw bytes (neutralization never wraps them).
    for identifier in identifiers:
        assert identifier.encode("utf-8") in response.content


def test_patients_csv_quotes_and_newlines_round_trip(clean_registry, monkeypatch) -> None:
    """S53 §2: commas, quotes, and newlines survive standard CSV quoting."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_quote")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_quote")
    tricky = 'Call "nurse", then visit\nsecond line, ok'
    _register_patient(client, csrf, "0012345678", phone=tricky)

    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get("/api/v1/exports/patients.csv")
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
    by_identifier = {row[1]: row for row in rows[1:]}
    assert by_identifier["0012345678"][7] == tricky


# --- S53 §3: escaped signed HTML with complete per-question content ---


def test_report_unknown_is_404_and_unsigned_shows_empty_state(
    clean_registry, monkeypatch
) -> None:
    """S53 §3: unknown patient 404; unsigned patient reports no encounters."""
    _new_physician(clean_registry, monkeypatch, "dr_s53_empty")
    client, csrf = _login_physician(clean_registry, monkeypatch, "dr_s53_empty")
    made = _register_patient(client, csrf, "0012345678")

    admin, _ = _admin_client(clean_registry, monkeypatch)
    missing = admin.get(f"/api/v1/patients/{uuid_module.uuid4()}/report")
    assert missing.status_code == 404, missing.text
    assert missing.json()["code"] == "NOT_FOUND"

    unsigned = admin.get(f"/api/v1/patients/{made['patient']['id']}/report")
    assert unsigned.status_code == 200, unsigned.text
    assert "No signed encounters." in unsigned.text
    assert "Research prototype" in unsigned.text
    assert unsigned.headers["Cache-Control"] == "private, no-store"


def test_report_escapes_malicious_text(clean_registry, monkeypatch, tmp_path) -> None:
    """S53 §3/Verify: plan/note/addendum markup renders inert (escaped)."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_xss")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert response.status_code == 200, response.text
    body = response.text
    assert "&lt;script&gt;" in body
    assert "<script>alert" not in body
    assert "&lt;img" in body
    assert "<img src=x" not in body
    assert "&lt;b&gt;bold&lt;/b&gt;" in body
    assert "<b>bold</b>" not in body
    # Escaped content is still present (nothing dropped, attribution kept).
    assert "Keep monitoring monthly." in body
    assert "Proposal looks complete." in body
    assert "Correction" in body
    assert fixture["other_username"] in body


def test_report_has_original_and_accepted_cpts_results_versions_attribution(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S53 §3: complete original + accepted CPTs/results, versions, actors."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_full")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert response.status_code == 200, response.text
    body = response.text
    for key in ("s53_q1", "s53_q2", "s53_q3", "s53_f1"):
        assert key in body, key
    assert "Original CPTs" in body
    assert "Final accepted CPTs" in body
    assert "Original result" in body
    assert "Final accepted result" in body
    # Worked CPT literals: original 80/20 roots, adjusted 60/40 final.
    assert "80" in body and "20" in body
    assert "60" in body and "40" in body
    assert "90" in body and "70" in body
    # Versions, recommendation prose, and accepting/signing attribution.
    assert "Pinned versions" in body
    assert "Original versions" in body
    assert "outcome." in body
    assert "Accepted by" in body
    assert "dr_s53_full" in body
    assert "Signer" in body
    assert "Snapshot" in body
    # Frozen clinical content from the signed snapshots.
    assert "History values" in body
    assert "catalog_alpha" in body
    assert "Secondary plan" in body
    assert "Signed notes (frozen)" in body
    assert "Addenda" in body


def test_report_adjustment_indicators_cover_all_three_states(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S53 §3: unchanged / adjusted / adjusted-then-reset each labeled."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_adj")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert response.status_code == 200, response.text
    body = response.text
    assert "Unchanged original — no physician adjustment" in body
    assert "Physician-adjusted — final differs from original" in body
    assert "Adjusted then reset — final equals original" in body
    assert "adjustment history retained" in body
    assert "Final equals original: yes" in body
    assert "Final equals original: no" in body


def test_report_multi_encounter_chronology_plans_addenda(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S53 §3/Verify: both signed encounters with plans, notes, addenda."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_multi")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert response.status_code == 200, response.text
    body = response.text
    assert "Signed chronology" in body
    assert f"Signed encounter {fixture['encounter_id']}" in body
    assert f"Signed encounter {fixture['followup_id']}" in body
    assert "Follow-up: continue current care." in body
    assert "Private drafts excluded" in body
    assert "Research prototype" in body


def test_report_excludes_open_draft_content(clean_registry, monkeypatch, tmp_path) -> None:
    """S53 §3: a later open draft (note + history) never leaks into the report."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_priv")
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

    admin, _ = _admin_client(clean_registry, monkeypatch)
    response = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert response.status_code == 200, response.text
    assert marker not in response.text
    assert f"Signed encounter {draft_id}" not in response.text


# --- S53 §4 + Verify/exit: private/no-store, export/report audit, no false success ---


def test_export_and_report_audit_events_with_safe_details(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S53 Verify: each export/report commits a safe attributed audit event."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_audit")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    admin_id = admin.get("/api/v1/me").json()["user"]["id"]
    assert admin.get("/api/v1/exports/patients.csv").status_code == 200
    assert admin.get("/api/v1/exports/physicians.csv").status_code == 200
    report = admin.get(f"/api/v1/patients/{fixture['patient_id']}/report")
    assert report.status_code == 200

    patients_events = admin.get(
        "/api/v1/audit-events", params={"operation": "exports.patients.success"}
    ).json()
    assert patients_events["total"] >= 1
    event = patients_events["items"][0]
    assert event["actor_id"] == admin_id
    assert event["details"]["actor_id"] == admin_id
    assert event["details"]["exported_count"] >= 1

    physicians_events = admin.get(
        "/api/v1/audit-events", params={"operation": "exports.physicians.success"}
    ).json()
    assert physicians_events["total"] >= 1
    assert physicians_events["items"][0]["details"]["exported_count"] >= 1

    report_events = admin.get(
        "/api/v1/audit-events", params={"operation": "reports.patient.success"}
    ).json()
    assert report_events["total"] >= 1
    report_event = next(
        item
        for item in report_events["items"]
        if item["details"].get("patient_id") == fixture["patient_id"]
    )
    assert fixture["encounter_id"] in report_event["details"]["signed_encounter_ids"]
    assert fixture["followup_id"] in report_event["details"]["signed_encounter_ids"]

    blob = json.dumps(
        [patients_events["items"][0], physicians_events["items"][0], report_event]
    ).lower()
    for token in _FORBIDDEN_SECRET_TOKENS:
        assert token not in blob, token


def test_denied_export_attempts_leave_no_success_events(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S53 Verify: physician 403s on exports/report record no success events."""
    fixture = _signed_multi_encounter_setup(clean_registry, monkeypatch, tmp_path, "dr_s53_deny")
    admin, _ = _admin_client(clean_registry, monkeypatch)
    before = {
        operation: admin.get(
            "/api/v1/audit-events", params={"operation": operation}
        ).json()["total"]
        for operation in (
            "exports.patients.success",
            "exports.physicians.success",
            "reports.patient.success",
        )
    }
    physician, _ = _login_physician(clean_registry, monkeypatch, "dr_s53_deny")
    assert physician.get("/api/v1/exports/patients.csv").status_code == 403
    assert physician.get("/api/v1/exports/physicians.csv").status_code == 403
    assert physician.get(f"/api/v1/patients/{fixture['patient_id']}/report").status_code == 403

    after = {
        operation: admin.get(
            "/api/v1/audit-events", params={"operation": operation}
        ).json()["total"]
        for operation in (
            "exports.patients.success",
            "exports.physicians.success",
            "reports.patient.success",
        )
    }
    assert after == before
