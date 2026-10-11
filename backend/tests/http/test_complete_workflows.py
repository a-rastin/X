"""Complete registration + follow-up with CPT review (S59 backend, seams T1/T8).

Through public HTTP + ``run_once()`` with ``BoundedProviderAdapter`` over real
MCP stdio + ``DeterministicProviderEndpoint.captured`` on real PostgreSQL.
No DB-row asserts, no internal mock call-order, no private-helper mirror
tests. Synthetic two-node A->B networks only (independent worked literals:
roots 80/20, P(B=yes)=0.22; adjusted 60/40 gives 0.66/0.34); no clinical
content. Covers tasks.md S59 §§1-4 backend mechanics:

- §1: five-question registration-shaped workflow (four ready + one
  false-gate skip) through assessment/history persistence, catalog
  medications/DDI pin, automatic sequential original generation, all-CPT
  inspection, adjustment/comparison, explicit acceptance and signing.
- §2: unchanged, adjusted, adjusted-then-reset and unchanged-recommendation
  cases; complete original/final signed snapshots and printable report data
  with versions, inputs and attribution.
- §3: relevant patient-variable change regenerates only the affected
  question (captured provider requests prove affected-only); history/new
  original retained; no silent adjustment transfer; note-only edits leave
  acceptance unchanged; follow-up creates a new encounter without modifying
  prior accepted CPTs.
- §4: refresh/restart resumes saved state; pending/failed/stale values
  cannot sign; author-only privacy on drafts/artifacts with shared
  demographics/signed reads.

Approved S39.b packages are NOT executed here: their
``candidate/history/...`` source paths fail closed at projection
(``unknown_source_path``), their candidate history values fail strict S12
history validation (undeclared), and their per-question gates share one
draft-level ``gate`` fact. Those are named content blockers (candidate
history review + bundle release/activation pinning approved hashes), not
silent stubs.
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
from x_insight.assessments import evaluate as evaluate_assessment
from x_insight.assessments.released import get_released_definition
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

REG_KEYS = ("s59_r1", "s59_r2", "s59_r3", "s59_r4", "s59_r5")
REG_FIELDS = {
    "s59_r1": ("h_r1_a", "h_r1_b"),
    "s59_r2": ("h_r2_a", "h_r2_b"),
    "s59_r3": ("h_r3_a", "h_r3_b"),
    "s59_r4": ("h_r4_a", "h_r4_b"),
    "s59_r5": ("h_r5_a", "h_r5_b"),
}
# r5 carries a false gate under draft gate "true": it records
# not_applicable without a provider call, like a skipped question in a
# complete workflow.
REG_GATES = {
    "s59_r1": "true",
    "s59_r2": "true",
    "s59_r3": "true",
    "s59_r4": "true",
    "s59_r5": "gate == 'false'",
}


def _package(
    question_key: str,
    field_a: str,
    field_b: str,
    gate_expression: str = "true",
    workflow: str = "registration",
) -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": question_key,
            "title": f"Synthetic {question_key}",
            "workflow": workflow,
            "version": "s59-test-v1",
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


# S08 validated released assessments (no owner approval needed): qualifying
# diagnosis answers (S08/S09 literal shape) + all-1 PANSS answers derived from
# the released definition's own item inventory. Both evaluate complete here.
_DIAGNOSIS_ANSWERS = {
    "a_delusions": "yes",
    "a_hallucinations": "yes",
    "a_disorganized_speech": "no",
    "a_disorganized_behavior": "no",
    "a_negative_symptoms": "no",
    "b_functional_decline": "yes",
    "c_six_months": "yes",
    "c_active_month": "yes",
    "c_shortened_by_intervention": "no",
    "d_mood_exclusion": "yes",
    "e_substance_medical_exclusion": "yes",
    "f_autism_present": "no",
}


def _panss_all_ones() -> dict[str, int]:
    definition = get_released_definition("panss")
    items = definition.get("items", [])
    assert len(items) == 30, len(items)
    return {str(entry.get("id")): 1 for entry in items}


_PANSS_ANSWERS = _panss_all_ones()


def _assert_assessments_complete() -> None:
    direct = evaluate_assessment(get_released_definition("diagnosis"), _DIAGNOSIS_ANSWERS)
    assert direct["status"] == "complete", direct
    assert direct["findings"]["overall"] == "criteria_satisfied", direct
    scored = evaluate_assessment(get_released_definition("panss"), dict(_PANSS_ANSWERS))
    assert scored["status"] == "complete", scored
    assert scored["scores"] == {"positive": 7, "negative": 7, "general": 16, "total": 30}


def _draft_body(values: dict[str, str]) -> dict[str, Any]:
    return {
        "history": {"values": dict(values)},
        "gate": "true",
        "diagnosis": {"answers": dict(_DIAGNOSIS_ANSWERS)},
        "panss": {"answers": dict(_PANSS_ANSWERS)},
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


# --- DDI limited release (synthetic mechanics; S18 gates real data) ---


def _concept(identifier: str, name: str, catalog: str | None = None) -> dict[str, Any]:
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": "ingredient",
        "catalog_drug_id": catalog,
        "source": "synthetic S59 fixture",
    }


def _vocabulary(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": "test-s59/1", "concepts": concepts, "aliases": []}


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


def _drain_local(engine, times: int = 1) -> None:
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    for _ in range(times):
        outcome = worker_module.run_once(engine, stub)
        assert outcome.get("status") == "succeeded", outcome
    assert stub.calls == []


def _question_key_of(captured_entry: Any) -> str:
    messages = captured_entry.get("messages", [])
    assert len(messages) >= 2
    payload = json.loads(messages[1].get("content", "{}"))
    return str(payload.get("question_key", ""))


def _five_question_setup(clean_registry, monkeypatch, tmp_path, username: str):
    """Real pipeline to a complete 5-question proposal (4 ready + 1 skipped)."""
    _publish_limited(clean_registry, tmp_path)
    admin, _ = _new_physician(clean_registry, monkeypatch, username)
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
    values = {}
    for key in REG_KEYS:
        field_a, field_b = REG_FIELDS[key]
        values[field_a] = "yes"
        values[field_b] = "no"
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": _draft_body(values)},
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
    packages = [_package(key, *REG_FIELDS[key], gate_expression=REG_GATES[key]) for key in REG_KEYS]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch = started.json()["batch"]
    runs = {r["question_key"]: r for r in started.json()["question_runs"]}
    assert {r["status"] for r in runs.values()} == {"ready", "not_applicable"}
    assert runs["s59_r5"]["status"] == "not_applicable"
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 4)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"] * 4, outcomes
        assert [_question_key_of(e) for e in endpoint.captured] == [
            "s59_r1",
            "s59_r2",
            "s59_r3",
            "s59_r4",
        ]
    finally:
        endpoint.stop()
    body = client.get(f"/api/v1/generation-batches/{batch['id']}").json()
    assert body["proposal"] is not None, body
    assert body["workflow"]["complete"] is True
    return admin, client, csrf, encounter_id, patient_id, revision, batch, runs


def _accept_all_ready(client, csrf, runs) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    acc: dict[str, dict[str, Any]] = {}
    revmap: dict[str, int] = {}
    for key in ("s59_r1", "s59_r2", "s59_r3", "s59_r4"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        acc[runs[key]["id"]] = posted.json()["acceptance"]
        revmap[runs[key]["id"]] = int(posted.json()["review_revision"])
    return acc, revmap


def _sign_body(
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


# --- S59 §§1-2: complete five-question workflow to signed snapshot + report ---


def test_registration_complete_workflow_sign_snapshot_and_report(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S59 §§1-2: unchanged/adjusted/reset/skip cases sign; snapshots + report complete."""
    import pytest as _pytest

    admin, client, csrf, encounter_id, patient_id, revision, batch, runs = _five_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s59_reg"
    )
    # Author-only privacy before signing: stranger batch/review reads are 403.
    _new_physician(clean_registry, monkeypatch, "dr_s59_stranger")
    stranger, stranger_csrf = _login_physician(clean_registry, monkeypatch, "dr_s59_stranger")
    denied_batch = stranger.get(f"/api/v1/generation-batches/{batch['id']}")
    assert denied_batch.status_code == 403, denied_batch.text
    assert "percentages" not in denied_batch.text
    denied_review = stranger.get(f"/api/v1/question-runs/{runs['s59_r1']['id']}/review")
    assert denied_review.status_code == 403, denied_review.text
    assert "percentages" not in denied_review.text

    # Q1 unchanged: accept the original baseline as-is.
    review_r1 = client.get(f"/api/v1/question-runs/{runs['s59_r1']['id']}/review").json()
    assert review_r1["baseline"]["validated_tables"][0]["rows"][0]["percentages"] == [
        "80",
        "20",
    ]
    # Five transparency fields are persisted per question before acceptance.
    transparency = review_r1["transparency"]
    for field in (
        "question_key",
        "network_version",
        "saved_patient_inputs",
        "returned_cpt_percentages",
        "deterministic_result",
    ):
        assert field in transparency, field
    assert transparency["saved_patient_inputs"], transparency

    # Q2 adjusted: root A yes 20→40, then local recalculation (no provider call).
    adjusted = client.post(
        f"/api/v1/question-runs/{runs['s59_r2']['id']}/cpt-adjustments",
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
    solved_r2 = client.get(f"/api/v1/question-runs/{runs['s59_r2']['id']}/review").json()
    assert solved_r2["calculation_state"] == "successfully_recalculated"
    # Independent literal: A 60/40 gives P(B=yes)=0.10*0.60+0.70*0.40=0.34.
    by_node = {str(e.get("node_id")): e for e in solved_r2["calculation_result"]["posteriors"]}
    assert by_node["B"]["probabilities"] == _pytest.approx([0.66, 0.34], abs=1e-6)

    # Q3 adjusted then reset: adjustment history retained, final equals original.
    adjusted3 = client.post(
        f"/api/v1/question-runs/{runs['s59_r3']['id']}/cpt-adjustments",
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
    reset = client.post(
        f"/api/v1/question-runs/{runs['s59_r3']['id']}/reset",
        json={"expected_review_revision": 2},
        headers=_auth_headers(csrf),
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["revision"]["kind"] == "reset"
    after_reset = client.get(f"/api/v1/question-runs/{runs['s59_r3']['id']}/review").json()
    assert after_reset["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert after_reset["calculation_result"]["reused_from_baseline_id"] is not None

    # Accept all four ready questions (Q1/Q4 baselines, Q2 calculation, Q3 reset reuse).
    acc: dict[str, dict[str, Any]] = {}
    revmap: dict[str, int] = {}
    for key in ("s59_r1", "s59_r2", "s59_r3", "s59_r4"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        acc[runs[key]["id"]] = posted.json()["acceptance"]
        revmap[runs[key]["id"]] = int(posted.json()["review_revision"])
    assert acc[runs["s59_r1"]["id"]]["result_kind"] == "baseline"
    assert acc[runs["s59_r2"]["id"]]["result_kind"] == "calculation"
    assert acc[runs["s59_r3"]["id"]]["result_kind"] == "calculation"
    assert acc[runs["s59_r3"]["id"]]["result_id"] == after_reset["calculation_result"]["id"]
    assert after_reset["calculation_result"]["reused_from_baseline_id"] is not None
    assert acc[runs["s59_r4"]["id"]]["result_kind"] == "baseline"

    # Restart resume: a fresh client re-login observes identical accepted state.
    fresh_client, _ = _login_physician(clean_registry, monkeypatch, "dr_s59_reg")
    resumed = fresh_client.get(f"/api/v1/question-runs/{runs['s59_r2']['id']}/review").json()
    assert resumed["is_accepted"] is True
    assert resumed["acceptance"]["id"] == acc[runs["s59_r2"]["id"]]["id"]
    assert resumed["current_tables"][0]["rows"][0]["percentages"] == ["60", "40"]

    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Secondary plan with reviewed probabilities.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=_sign_body(revision, 2, batch["id"], acc, revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    assert len(signed.json()["snapshot"]["snapshot_hash"]) == 64
    frozen = signed.json()["snapshot"]["snapshot"]
    by_key = {e["question_key"]: e for e in frozen["questions"]}
    assert set(by_key) == set(REG_KEYS)
    # Versions, inputs and attribution travel into the frozen record.
    for key in ("s59_r1", "s59_r2", "s59_r3", "s59_r4"):
        entry = by_key[key]
        assert entry["original_baseline"]["network_version"]
        assert entry["original_baseline"]["template_version"]
        assert entry["original_baseline"]["prompt_version"]
        assert entry["pinned_versions"]["network_hash"] == NETWORK_HASH
        assert entry["projection"]["variables"], key
        assert entry["acceptance"]["actor_username"] == "dr_s59_reg"
        assert entry["acceptance"]["created_at"], key
    assert frozen["signer"]["signer_username"] == "dr_s59_reg"
    assert frozen["signer"]["signed_at"]
    # Assessment persistence (S08-S12): released answers frozen with the record.
    assert frozen["draft_data"]["diagnosis"]["answers"] == _DIAGNOSIS_ANSWERS
    assert frozen["draft_data"]["panss"]["answers"] == _PANSS_ANSWERS
    assert frozen["fingerprint_payload"]["diagnosis_answers"] == _DIAGNOSIS_ANSWERS
    assert frozen["fingerprint_payload"]["panss_answers"] == _PANSS_ANSWERS
    # Unchanged vs adjusted vs reset vs skipped.
    assert by_key["s59_r1"]["original_baseline"]["validated_tables"][0]["rows"][0][
        "percentages"
    ] == ["80", "20"]
    assert by_key["s59_r1"]["current_tables"][0]["rows"][0]["percentages"] == ["80", "20"]
    assert by_key["s59_r1"]["calculation_state"] == "unchanged"
    assert by_key["s59_r1"]["current_result"] is None
    assert by_key["s59_r2"]["current_tables"][0]["rows"][0]["percentages"] == ["60", "40"]
    assert by_key["s59_r2"]["calculation_state"] == "successfully_recalculated"
    assert by_key["s59_r2"]["current_result"]["section_text"]
    final_posteriors = {
        str(e.get("node_id")): e for e in by_key["s59_r2"]["current_result"]["posteriors"]
    }
    assert final_posteriors["B"]["probabilities"] == _pytest.approx([0.66, 0.34], abs=1e-6)
    assert (
        by_key["s59_r3"]["current_tables"]
        == by_key["s59_r3"]["original_baseline"]["validated_tables"]
    )
    assert by_key["s59_r3"]["review_revision"] == 3
    assert by_key["s59_r3"]["current_cpt_revision_id"] is not None
    assert by_key["s59_r5"]["status"] == "not_applicable"
    assert by_key["s59_r5"]["acceptance"] is None
    assert "gate false" in by_key["s59_r5"]["gate_reason"]

    # Shared signed read stays available to other physicians without draft leaks.
    chart = stranger.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert len(chart["signed_snapshots"]) == 1
    assert (
        chart["signed_snapshots"][0]["snapshot_hash"] == signed.json()["snapshot"]["snapshot_hash"]
    )

    # Printable report data: every question with indicators, versions, attribution.
    report = admin.get(f"/api/v1/patients/{patient_id}/report")
    assert report.status_code == 200, report.text
    html = report.text
    for key in REG_KEYS:
        assert key in html, key
    assert "Unchanged original" in html
    assert "Physician-adjusted" in html
    assert "Adjusted then reset" in html
    assert "Not applicable" in html
    assert "Accepted by" in html
    assert "dr_s59_reg" in html
    assert NETWORK_HASH in html


# --- S59 §3: follow-up creates a new encounter; prior accepted CPTs immutable ---


def test_followup_new_encounter_preserves_prior_and_signs(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S59 §3: follow-up batch signs separately; first snapshot byte-identical."""
    _, client, csrf, encounter_id, patient_id, revision, batch, runs = _five_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s59_fu"
    )
    acc, revmap = _accept_all_ready(client, csrf, runs)
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "First plan.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=_sign_body(revision, 2, batch["id"], acc, revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    first_hash = signed.json()["snapshot"]["snapshot_hash"]
    first_questions = {
        e["question_key"]: e for e in signed.json()["snapshot"]["snapshot"]["questions"]
    }
    first_r1_tables = first_questions["s59_r1"]["original_baseline"]["validated_tables"]

    created = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    fu_encounter_id = created.json()["encounter"]["id"]
    fu_saved = client.patch(
        f"/api/v1/encounters/{fu_encounter_id}",
        json={
            "draft_data": {
                "history": {"values": {"h_f1_a": "yes", "h_f1_b": "no"}},
                "gate": "true",
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert fu_saved.status_code == 200, fu_saved.text
    fu_revision = int(fu_saved.json()["revision"])
    fu_meds = client.post(
        f"/api/v1/encounters/{fu_encounter_id}/medications",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(fu_revision)},
    )
    assert fu_meds.status_code == 200, fu_meds.text
    fu_revision = int(fu_meds.json()["revision"])
    fu_packages = [
        _package("s59_f1", "h_f1_a", "h_f1_b", workflow="followup"),
    ]
    fu_started = client.post(
        f"/api/v1/encounters/{fu_encounter_id}/generation-batches",
        json={"packages": fu_packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(fu_revision)},
    )
    assert fu_started.status_code == 202, fu_started.text
    fu_batch = fu_started.json()["batch"]
    fu_runs = {r["question_key"]: r for r in fu_started.json()["question_runs"]}
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"], outcomes
        assert [_question_key_of(e) for e in endpoint.captured] == ["s59_f1"]
    finally:
        endpoint.stop()
    fu_review = client.get(f"/api/v1/question-runs/{fu_runs['s59_f1']['id']}/review").json()
    fu_accepted = client.post(
        f"/api/v1/question-runs/{fu_runs['s59_f1']['id']}/acceptance",
        json=_accept_body(fu_review),
        headers=_auth_headers(csrf),
    )
    assert fu_accepted.status_code == 200, fu_accepted.text
    fu_acc = {fu_runs["s59_f1"]["id"]: fu_accepted.json()["acceptance"]}
    fu_revmap = {fu_runs["s59_f1"]["id"]: int(fu_accepted.json()["review_revision"])}
    fu_planned = client.patch(
        f"/api/v1/encounters/{fu_encounter_id}/secondary-plan",
        json={"text": "Follow-up plan.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert fu_planned.status_code == 200, fu_planned.text
    fu_signed = client.post(
        f"/api/v1/encounters/{fu_encounter_id}/sign",
        json=_sign_body(fu_revision, 2, fu_batch["id"], fu_acc, fu_revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(fu_revision)},
    )
    assert fu_signed.status_code == 200, fu_signed.text

    # Prior accepted CPTs are untouched; the first snapshot is byte-identical.
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert len(chart["signed_snapshots"]) == 2
    by_encounter = {s["encounter_id"]: s for s in chart["signed_snapshots"]}
    assert by_encounter[encounter_id]["snapshot_hash"] == first_hash
    first_questions = by_encounter[encounter_id]["snapshot"]["questions"]
    assert first_questions[0]["original_baseline"]["validated_tables"] == first_r1_tables
    fu_questions = {
        e["question_key"]: e for e in by_encounter[fu_encounter_id]["snapshot"]["questions"]
    }
    assert set(fu_questions) == {"s59_f1"}


# --- S59 §§3-4: affected-only regeneration, stale fencing, note noninterference ---


def test_complete_workflow_affected_only_regeneration_and_fencing(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S59 §§3-4: relevant edit stales one question; regen costs one call; stale cannot sign."""
    _, client, csrf, encounter_id, _, revision, batch, runs = _five_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_s59_fresh"
    )
    acc, revmap = _accept_all_ready(client, csrf, runs)
    old_r1_baseline_id = client.get(f"/api/v1/question-runs/{runs['s59_r1']['id']}/review").json()[
        "baseline"
    ]["id"]
    old_r2_baseline_id = client.get(f"/api/v1/question-runs/{runs['s59_r2']['id']}/review").json()[
        "baseline"
    ]["id"]

    # Note-only edit preserves every acceptance and freshness.
    encounter = client.get(f"/api/v1/encounters/{encounter_id}").json()
    noted = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "proposal", "text": "Note: reviewed all five questions."},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(encounter["revision"]),
        },
    )
    assert noted.status_code == 201, noted.text
    revision = int(noted.json()["revision"])
    for key in ("s59_r1", "s59_r2", "s59_r3", "s59_r4"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        assert review["is_accepted"] is True, key
        assert review["input_freshness"]["stale"] is False, key

    # Relevant edit to r2's inputs stales only r2 (acceptance invalidated there).
    edited_values = {
        f"h_r{i}_{s}": ("yes" if s == "a" else "no") for i in (1, 3, 4, 5) for s in ("a", "b")
    }
    edited_values["h_r2_a"] = "no"
    edited_values["h_r2_b"] = "no"
    patched = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": _draft_body(edited_values)},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert patched.status_code == 200, patched.text
    revision = int(patched.json()["revision"])
    stale_r2 = client.get(f"/api/v1/question-runs/{runs['s59_r2']['id']}/review").json()
    assert stale_r2["input_freshness"]["stale"] is True
    assert stale_r2["is_accepted"] is False
    for key in ("s59_r1", "s59_r3", "s59_r4"):
        review = client.get(f"/api/v1/question-runs/{runs[key]['id']}/review").json()
        assert review["input_freshness"]["stale"] is False, key
        assert review["is_accepted"] is True, key

    # Stale values cannot sign: the locked transaction rechecks freshness.
    planned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan before regen.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert planned.status_code == 200, planned.text
    refused = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=_sign_body(revision, 2, batch["id"], acc, revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] in ("STALE_INPUTS", "REFERENCE_MISMATCH"), refused.text

    # Regenerate: same pinned packages under current inputs; only r2 re-estimates.
    packages = [_package(key, *REG_FIELDS[key], gate_expression=REG_GATES[key]) for key in REG_KEYS]
    restarted = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"packages": packages},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert restarted.status_code == 202, restarted.text
    new_batch = restarted.json()["batch"]
    assert new_batch["id"] != batch["id"]
    new_runs = {r["question_key"]: r for r in restarted.json()["question_runs"]}
    # Pending values cannot sign: r2 has a queued job but no baseline yet.
    pending_refused = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=_sign_body(revision, 2, new_batch["id"], acc, revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert pending_refused.status_code == 409, pending_refused.text
    assert pending_refused.json()["code"] == "PROPOSAL_INCOMPLETE", pending_refused.text
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o.get("status") for o in outcomes] == ["succeeded"], outcomes
        assert len(endpoint.captured) == 1, len(endpoint.captured)
        assert _question_key_of(endpoint.captured[0]) == "s59_r2"
    finally:
        endpoint.stop()
    # Unaffected baselines carried without new provider calls or new estimates.
    carried_r1 = client.get(f"/api/v1/question-runs/{new_runs['s59_r1']['id']}/review").json()
    assert carried_r1["baseline"] is not None
    assert carried_r1["baseline"]["id"] != old_r1_baseline_id
    assert (
        carried_r1["baseline"]["effective_hash"]
        == (
            client.get(f"/api/v1/question-runs/{runs['s59_r1']['id']}/review").json()["baseline"][
                "effective_hash"
            ]
        )
    )
    # Regenerated r2 is a new original; old rows remain readable history.
    new_r2 = client.get(f"/api/v1/question-runs/{new_runs['s59_r2']['id']}/review").json()
    assert new_r2["baseline"]["id"] != old_r2_baseline_id
    assert new_r2["is_accepted"] is False
    old_batch = client.get(f"/api/v1/generation-batches/{batch['id']}").json()
    assert old_batch["proposal"] is not None

    # Exact review of the regenerated originals restores sign eligibility.
    new_acc: dict[str, dict[str, Any]] = {}
    new_revmap: dict[str, int] = {}
    for key in ("s59_r1", "s59_r2", "s59_r3", "s59_r4"):
        review = client.get(f"/api/v1/question-runs/{new_runs[key]['id']}/review").json()
        posted = client.post(
            f"/api/v1/question-runs/{new_runs[key]['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert posted.status_code == 200, (key, posted.text)
        new_acc[new_runs[key]["id"]] = posted.json()["acceptance"]
        new_revmap[new_runs[key]["id"]] = int(posted.json()["review_revision"])
    replanned = client.patch(
        f"/api/v1/encounters/{encounter_id}/secondary-plan",
        json={"text": "Plan after regen.", "expected_plan_revision": 2},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(2)},
    )
    assert replanned.status_code == 200, replanned.text
    signed = client.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json=_sign_body(revision, 3, new_batch["id"], new_acc, new_revmap),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    assert len(signed.json()["snapshot"]["snapshot"]["questions"]) == 5
