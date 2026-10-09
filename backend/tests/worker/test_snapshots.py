"""Freeze analysis snapshots and project one question's inputs (S40, seams T1/T5).

Through public HTTP + real PostgreSQL (synthetic question package fixture,
no real approved bundles, no queue/provider/MCP):

1. Start from a saved private author-owned revision freezes allowed
   facts/version references; notes/names/ID/phone are absent from the
   model-facing projection. Wrong-author reads and invalid/stale starts
   are denied without partial creation.
2. Each question receives exactly its represented variables with typed
   missing/conflict state and source references; note/unrelated mappings
   are rejected regardless of prompt wording.
3. Note-only edits leave fingerprints unchanged; relevant edits invalidate
   affected results. Old snapshots remain author-readable history.
4. Gates true/false/required-unknown produce ready/not-applicable/
   clarification with explicit reasons; undeclared cross-question inputs
   are forbidden.
"""

from __future__ import annotations

import copy
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


@pytest.fixture()
def clean_registry(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate(migrated_test_engine)
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
    csrf = response.json()["csrf_token"]
    user = response.json()["user"]
    return {"client": client, "csrf": csrf, "user": user, "engine": clean_registry}


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    client = admin_client["client"]
    created = client.post(
        "/api/v1/physicians",
        json={"username": username, "password": password},
        headers=_auth_headers(admin_client["csrf"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["user"]


def _physician_client(clean_registry, monkeypatch, username: str, password: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "role": "physician"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return client, body["csrf_token"], body["user"]


def _synthetic_package(**overrides: Any) -> dict[str, Any]:
    package: dict[str, Any] = {
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
                "required_fields": [
                    "synthetic/history/h_a",
                    "synthetic/history/h_b",
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
                {
                    "inputs": {},
                    "expected_for_review": {"B": "yes"},
                    "note": "review candidate, never self-validating",
                }
            ],
        },
        "review": {
            "reviewer": "owner",
            "decision": "draft",
            "date": "2026-10-04",
            "source_hashes": {"network.xml": "0" * 64},
            "assumptions": ["synthetic only, no clinical use"],
            "source_comparison": "synthetic source comparison",
            "explicit_graph": "A -> B",
            "reference_table_provenance": "synthetic placeholders, not patient estimates",
            "estimation_instructions": "estimate every CPT including roots",
            "result_mapping": "B yes/no maps to explicit branches",
            "numerical_examples": "two-node 80/20 fixture",
            "clinical_examples": "synthetic clinical review candidate",
            "admission_measurements": "synthetic only",
            "open_assumptions": "synthetic only",
        },
    }
    if overrides:
        package = copy.deepcopy(package)
        for key, value in overrides.items():
            package[key] = value
    return package


def _create_patient(client, csrf, identifier="0012345678", **overrides):
    payload = {
        "identifier": identifier,
        "given_name": "SentinelGiven",
        "family_name": "SentinelFamily",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "SENTINEL-PHONE-999",
    }
    payload.update(overrides)
    response = client.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf))
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(client, csrf, encounter_id, draft_data, revision):
    return client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": draft_data},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )


def _start_batch(client, csrf, encounter_id, revision, package, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers=headers,
    )


# --- Slice 1: freeze + sentinel exclusion + ownership + stale/invalid denial ---


def test_start_freezes_facts_and_hides_sentinels(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_s40_owner")
    _make_physician(admin_client, "dr_s40_other")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_s40_owner", "pw123")
    other, _, _ = _physician_client(clean_registry, monkeypatch, "dr_s40_other", "pw123")

    created = _create_patient(owner, owner_csrf, identifier="0012345678")
    encounter_id = created["draft"]["id"]
    assert created["draft"]["revision"] == 1

    draft_data = {
        "history": {"values": {"h_a": "yes", "h_b": "no"}},
        "gate": "true",
    }
    saved = _patch_draft(owner, owner_csrf, encounter_id, draft_data, 1)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]

    noted = owner.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "history", "text": "SENTINEL-NOTE-SECRET"},
        headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert noted.status_code == 201, noted.text
    revision = noted.json()["revision"]

    package = _synthetic_package()
    started = _start_batch(owner, owner_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    body = started.json()
    batch_id = body["batch"]["id"]
    assert body["batch"]["source_revision"] == revision
    assert len(body["question_runs"]) == 1
    run = body["question_runs"][0]
    assert run["question_key"] == "synthetic_snapshot"
    assert run["status"] == "ready"

    projection_text = str(run["projection"])
    for sentinel in (
        "SentinelGiven",
        "SentinelFamily",
        "0012345678",
        "SENTINEL-PHONE-999",
        "SENTINEL-NOTE-SECRET",
    ):
        assert sentinel not in projection_text

    # Author can read the frozen snapshot.
    read = owner.get(f"/api/v1/generation-batches/{batch_id}")
    assert read.status_code == 200, read.text
    assert read.json()["batch"]["fingerprint"] == body["batch"]["fingerprint"]

    # Wrong author cannot read projections/artifacts.
    denied = other.get(f"/api/v1/generation-batches/{batch_id}")
    assert denied.status_code == 403, denied.text
    assert "SENTINEL" not in denied.text
    assert "h_a" not in denied.text

    # Stale start is denied without partial creation.
    stale = _start_batch(owner, owner_csrf, encounter_id, revision - 1, package)
    assert stale.status_code == 412, stale.text

    # Invalid package start is denied without partial creation.
    bad = copy.deepcopy(package)
    bad["manifest"]["patient_mappings"][0]["allowed_source_paths"] = ["notes/page"]
    invalid = _start_batch(owner, owner_csrf, encounter_id, revision, bad)
    assert invalid.status_code == 422, invalid.text

    # The valid snapshot is still the only readable history.
    reread = owner.get(f"/api/v1/generation-batches/{batch_id}")
    assert reread.status_code == 200, reread.text


def _setup_owner_with_draft(admin_client, clean_registry, monkeypatch, username):
    _make_physician(admin_client, username)
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_patient(client, csrf, identifier="0099999999")
    encounter_id = created["draft"]["id"]
    return client, csrf, encounter_id


# --- Slice 2: exact typed projection + note/unrelated rejection ---


def test_projection_holds_exact_typed_variables_with_sources(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_proj"
    )
    draft_data = {"history": {"values": {"h_a": "yes"}}, "gate": "true"}
    saved = _patch_draft(owner, owner_csrf, encounter_id, draft_data, 1)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]

    package = _synthetic_package()
    started = _start_batch(owner, owner_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    run = started.json()["question_runs"][0]
    assert run["status"] == "needs_clarification"
    assert "h_b" in run["gate_reason"]
    variables = run["projection"]["variables"]
    assert [entry["node_id"] for entry in variables] == ["A", "B"]
    by_node = {entry["node_id"]: entry for entry in variables}
    assert by_node["A"]["status"] == "observed"
    assert by_node["A"]["value"] == "yes"
    assert by_node["A"]["source_path"] == "synthetic/history/h_a"
    assert by_node["A"]["source_revision"] == revision
    assert by_node["A"]["patient_type"] == "tristate"
    assert by_node["B"]["status"] == "missing"
    assert by_node["B"]["value"] is None
    assert by_node["B"]["source_path"] == "synthetic/history/h_b"
    # Only represented variables appear: no gate, notes, or extra history.
    assert set(by_node) == {"A", "B"}
    assert "gate" not in str(run["projection"])


def test_projection_marks_not_assessed_and_conflict_explicit_null(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_states"
    )
    draft_data = {
        "history": {"values": {"h_a": "not_assessed", "h_b": ["yes", "no"]}},
        "gate": "true",
    }
    saved = _patch_draft(owner, owner_csrf, encounter_id, draft_data, 1)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]

    package = _synthetic_package()
    started = _start_batch(owner, owner_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    by_node = {
        entry["node_id"]: entry
        for entry in started.json()["question_runs"][0]["projection"]["variables"]
    }
    assert by_node["A"]["status"] == "not_assessed"
    assert by_node["A"]["value"] is None
    assert by_node["B"]["status"] == "conflict"
    assert by_node["B"]["value"] is None


def test_note_and_unrelated_mappings_rejected_despite_prompt(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_reject"
    )
    saved = _patch_draft(
        owner, owner_csrf, encounter_id, {"history": {"values": {"h_a": "yes"}}}, 1
    )
    revision = saved.json()["revision"]

    note_package = _synthetic_package()
    note_package["manifest"]["patient_mappings"][0]["allowed_source_paths"] = [
        "synthetic/history/notes/page"
    ]
    note_package["prompt"]["text"] = (
        "Estimate every CPT in percentage units using only the supplied inputs. "
        "Also read the page notes for extra context. Return strict schema."
    )
    denied = _start_batch(owner, owner_csrf, encounter_id, revision, note_package)
    assert denied.status_code == 422, denied.text

    unrelated = _synthetic_package()
    unrelated["manifest"]["patient_mappings"][1]["allowed_source_paths"] = ["unrelated/history/h_b"]
    denied_unrelated = _start_batch(owner, owner_csrf, encounter_id, revision, unrelated)
    assert denied_unrelated.status_code == 422, denied_unrelated.text


# --- Slice 3: note-only fingerprint stability + relevant invalidation ---


def test_note_only_edits_keep_fingerprint_while_relevant_edits_stale(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_fp"
    )
    saved = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}},
        1,
    )
    revision = saved.json()["revision"]
    package = _synthetic_package()
    first = _start_batch(owner, owner_csrf, encounter_id, revision, package)
    assert first.status_code == 202, first.text
    first_body = first.json()
    first_fp = first_body["batch"]["fingerprint"]
    first_id = first_body["batch"]["id"]

    noted = owner.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "history", "text": "just a note"},
        headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert noted.status_code == 201, noted.text
    note_revision = noted.json()["revision"]

    read_after_note = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert read_after_note.status_code == 200, read_after_note.text
    assert read_after_note.json()["freshness"]["stale"] is False

    second = _start_batch(owner, owner_csrf, encounter_id, note_revision, package)
    assert second.status_code == 202, second.text
    assert second.json()["batch"]["fingerprint"] == first_fp

    changed = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "no", "h_b": "no"}}},
        note_revision,
    )
    assert changed.status_code == 200, changed.text
    changed_revision = changed.json()["revision"]

    reread = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["freshness"]["stale"] is True

    third = _start_batch(owner, owner_csrf, encounter_id, changed_revision, package)
    assert third.status_code == 202, third.text
    assert third.json()["batch"]["fingerprint"] != first_fp

    # Immutable old snapshots remain author-readable history.
    old = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert old.status_code == 200, old.text
    assert old.json()["batch"]["fingerprint"] == first_fp


# --- Slice 4: gates + cross-question forbiddance ---


def test_gates_true_false_unknown_and_cross_question_forbidden(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_gate"
    )
    saved = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
        1,
    )
    revision = saved.json()["revision"]

    gated = _synthetic_package()
    gated["manifest"]["applicability"] = {
        "expression": "gate == 'true'",
        "required_fields": ["synthetic/history/h_a", "synthetic/history/h_b"],
        "unknown_policy": "needs_clarification",
    }
    ready = _start_batch(owner, owner_csrf, encounter_id, revision, gated)
    assert ready.status_code == 202, ready.text
    assert ready.json()["question_runs"][0]["status"] == "ready"
    assert "gate true" in ready.json()["question_runs"][0]["gate_reason"]

    flipped = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "false"},
        revision,
    )
    assert flipped.status_code == 200, flipped.text
    false_revision = flipped.json()["revision"]
    not_applicable = _start_batch(owner, owner_csrf, encounter_id, false_revision, gated)
    assert not_applicable.status_code == 202, not_applicable.text
    assert not_applicable.json()["question_runs"][0]["status"] == "not_applicable"
    assert "gate false" in not_applicable.json()["question_runs"][0]["gate_reason"]

    unknowned = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "unknown"},
        false_revision,
    )
    assert unknowned.status_code == 200, unknowned.text
    unknown_revision = unknowned.json()["revision"]
    needs = _start_batch(owner, owner_csrf, encounter_id, unknown_revision, gated)
    assert needs.status_code == 202, needs.text
    assert needs.json()["question_runs"][0]["status"] == "needs_clarification"

    chained = _synthetic_package()
    chained["manifest"]["patient_mappings"][0]["allowed_source_paths"] = [
        "synthetic/history/posterior_B"
    ]
    denied_chain = _start_batch(owner, owner_csrf, encounter_id, unknown_revision, chained)
    assert denied_chain.status_code == 422, denied_chain.text

    chained_required = _synthetic_package()
    chained_required["manifest"]["applicability"] = {
        "expression": "true",
        "required_fields": ["questions/other/result"],
        "unknown_policy": "needs_clarification",
    }
    denied_required = _start_batch(
        owner, owner_csrf, encounter_id, unknown_revision, chained_required
    )
    assert denied_required.status_code == 422, denied_required.text


def _batch_counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        batches = connection.execute(text("SELECT count(*) FROM generation_batches")).scalar_one()
        runs = connection.execute(text("SELECT count(*) FROM question_runs")).scalar_one()
    return int(batches), int(runs)


# --- Slice 5: ownership/404/idempotency/If-Match + no partial rows + no extra endpoints ---


def test_start_ownership_not_found_idempotency_and_no_partial_rows(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_s40_own")
    _make_physician(admin_client, "dr_s40_stranger")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_s40_own", "pw123")
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_s40_stranger", "pw123"
    )

    created = _create_patient(owner, owner_csrf, identifier="0012345678")
    encounter_id = created["draft"]["id"]
    saved = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
        1,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    noted = owner.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "history", "text": "SENTINEL-NOTE-SECRET"},
        headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert noted.status_code == 201, noted.text
    revision = noted.json()["revision"]

    package = _synthetic_package()
    first = _start_batch(owner, owner_csrf, encounter_id, revision, package, key="s40-idem-001")
    assert first.status_code == 202, first.text
    first_body = first.json()
    batch_id = first_body["batch"]["id"]
    fingerprint = first_body["batch"]["fingerprint"]
    assert first_body["batch"]["source_revision"] == revision
    assert set(first_body.keys()) == {"batch", "question_runs"}
    assert set(first_body["batch"].keys()) == {
        "id",
        "encounter_id",
        "author_id",
        "source_revision",
        "fingerprint",
        "status",
        "pinned_bundle",
        "created_at",
    }
    assert set(first_body["question_runs"][0].keys()) == {
        "id",
        "batch_id",
        "question_key",
        "status",
        "gate_reason",
        "projection",
        "projection_hash",
        "fingerprint",
        "created_at",
    }
    full_text = str(first_body)
    for sentinel in (
        "SentinelGiven",
        "SentinelFamily",
        "0012345678",
        "SENTINEL-PHONE-999",
        "SENTINEL-NOTE-SECRET",
    ):
        assert sentinel not in full_text
    for marker in ("provider", "mcp", "notes"):
        assert marker not in full_text.lower()

    # Same key + same body replays the original batch without a duplicate row.
    replay = _start_batch(owner, owner_csrf, encounter_id, revision, package, key="s40-idem-001")
    assert replay.status_code == 202, replay.text
    assert replay.json()["batch"]["id"] == batch_id
    assert replay.json()["batch"]["fingerprint"] == fingerprint
    assert _batch_counts(clean_registry) == (1, 1)

    # Same key + changed body is 409 and creates nothing.
    altered = _synthetic_package()
    altered["prompt"]["text"] = altered["prompt"]["text"] + " Extra sentence."
    conflict = _start_batch(owner, owner_csrf, encounter_id, revision, altered, key="s40-idem-001")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _batch_counts(clean_registry) == (1, 1)

    # If-Match is required: absent and "*" are 422, never 412.
    missing_match = owner.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers=_auth_headers(owner_csrf),
    )
    assert missing_match.status_code == 422, missing_match.text
    star_match = owner.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(owner_csrf), "If-Match": "*"},
    )
    assert star_match.status_code == 422, star_match.text
    assert _batch_counts(clean_registry) == (1, 1)

    # Unknown encounter/batch are 404 without leaking content.
    unknown_encounter = str(uuid.uuid4())
    not_found_start = owner.post(
        f"/api/v1/encounters/{unknown_encounter}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(1)},
    )
    assert not_found_start.status_code == 404, not_found_start.text
    assert "SENTINEL" not in not_found_start.text
    not_found_read = owner.get(f"/api/v1/generation-batches/{uuid.uuid4()}")
    assert not_found_read.status_code == 404, not_found_read.text
    assert _batch_counts(clean_registry) == (1, 1)

    # Stranger start and admin start are 403 without content.
    stranger_start = stranger.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={
            **_auth_headers(stranger_csrf),
            "If-Match": contracts.format_etag(revision),
        },
    )
    assert stranger_start.status_code == 403, stranger_start.text
    assert "SENTINEL" not in stranger_start.text
    assert "h_a" not in stranger_start.text
    admin_start = admin_client["client"].post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={
            **_auth_headers(admin_client["csrf"]),
            "If-Match": contracts.format_etag(revision),
        },
    )
    assert admin_start.status_code == 403, admin_start.text
    assert _batch_counts(clean_registry) == (1, 1)

    # Stale and invalid starts also create nothing.
    stale = _start_batch(owner, owner_csrf, encounter_id, revision - 1, package)
    assert stale.status_code == 412, stale.text
    bad = copy.deepcopy(package)
    bad["manifest"]["patient_mappings"][0]["allowed_source_paths"] = ["notes/page"]
    invalid = _start_batch(owner, owner_csrf, encounter_id, revision, bad)
    assert invalid.status_code == 422, invalid.text
    assert _batch_counts(clean_registry) == (1, 1)

    # S44 reuse: same fingerprint with a fresh key reuses the run (no duplicate).
    retry = _start_batch(owner, owner_csrf, encounter_id, revision, package, key="s40-idem-002")
    assert retry.status_code == 202, retry.text
    assert retry.json()["batch"]["fingerprint"] == fingerprint
    assert retry.json()["batch"]["id"] == batch_id
    assert _batch_counts(clean_registry) == (1, 1)

    # No general snapshot collection or provider path exists.
    assert owner.get("/api/v1/generation-batches").status_code in (404, 405)
    assert owner.get(f"/api/v1/encounters/{encounter_id}/generation-batches").status_code in (
        404,
        405,
    )
    assert (
        owner.post(
            f"/api/v1/encounters/{encounter_id}/provider",
            json={"package": package},
            headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(revision)},
        ).status_code
        == 404
    )


# --- Slice 6: identifying/candidate rejection + unknown/conflict typing ---


def test_identifying_and_candidate_sources_rejected_and_unknown_maps_to_missing(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_ident"
    )
    saved = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "unknown", "h_b": "conflict"}}, "gate": "true"},
        1,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]

    package = _synthetic_package()
    started = _start_batch(owner, owner_csrf, encounter_id, revision, package)
    assert started.status_code == 202, started.text
    run = started.json()["question_runs"][0]
    assert run["status"] == "needs_clarification"
    assert "h_a" in run["gate_reason"]
    by_node = {entry["node_id"]: entry for entry in run["projection"]["variables"]}
    assert by_node["A"]["status"] == "missing"
    assert by_node["A"]["value"] is None
    assert by_node["B"]["status"] == "conflict"
    assert by_node["B"]["value"] is None

    identifying = _synthetic_package()
    identifying["manifest"]["patient_mappings"][0]["allowed_source_paths"] = [
        "synthetic/demographics/phone"
    ]
    denied_ident = _start_batch(owner, owner_csrf, encounter_id, revision, identifying)
    assert denied_ident.status_code == 422, denied_ident.text

    candidate = _synthetic_package()
    candidate["manifest"]["patient_mappings"][1]["allowed_source_paths"] = ["candidate/history/h_b"]
    denied_candidate = _start_batch(owner, owner_csrf, encounter_id, revision, candidate)
    assert denied_candidate.status_code == 422, denied_candidate.text


# --- Slice 7: secondary-plan/UI stability vs gate invalidation + immutability ---


def test_secondary_plan_edits_keep_fingerprint_while_gate_edits_stale(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, encounter_id = _setup_owner_with_draft(
        admin_client, clean_registry, monkeypatch, "dr_s40_plan"
    )
    saved = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"},
        1,
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]

    gated = _synthetic_package()
    gated["manifest"]["applicability"] = {
        "expression": "gate == 'true'",
        "required_fields": ["synthetic/history/h_a", "synthetic/history/h_b"],
        "unknown_policy": "needs_clarification",
    }
    first = _start_batch(owner, owner_csrf, encounter_id, revision, gated)
    assert first.status_code == 202, first.text
    first_fp = first.json()["batch"]["fingerprint"]
    first_id = first.json()["batch"]["id"]
    assert first.json()["question_runs"][0]["status"] == "ready"

    plan_edit = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {
            "history": {"values": {"h_a": "yes", "h_b": "no"}},
            "gate": "true",
            "secondary_plan": {"text": "SENTINEL-PLAN-SECRET"},
            "ui_state": {"tab": "history"},
        },
        revision,
    )
    assert plan_edit.status_code == 200, plan_edit.text
    plan_revision = plan_edit.json()["revision"]

    second = _start_batch(owner, owner_csrf, encounter_id, plan_revision, gated)
    assert second.status_code == 202, second.text
    assert second.json()["batch"]["fingerprint"] == first_fp
    assert "SENTINEL-PLAN-SECRET" not in str(second.json())
    fresh = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["freshness"]["stale"] is False

    gate_edit = _patch_draft(
        owner,
        owner_csrf,
        encounter_id,
        {
            "history": {"values": {"h_a": "yes", "h_b": "no"}},
            "gate": "false",
            "secondary_plan": {"text": "SENTINEL-PLAN-SECRET"},
            "ui_state": {"tab": "history"},
        },
        plan_revision,
    )
    assert gate_edit.status_code == 200, gate_edit.text
    gate_revision = gate_edit.json()["revision"]

    stale_read = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert stale_read.status_code == 200, stale_read.text
    assert stale_read.json()["freshness"]["stale"] is True

    third = _start_batch(owner, owner_csrf, encounter_id, gate_revision, gated)
    assert third.status_code == 202, third.text
    assert third.json()["batch"]["fingerprint"] != first_fp
    assert third.json()["question_runs"][0]["status"] == "not_applicable"

    old = owner.get(f"/api/v1/generation-batches/{first_id}")
    assert old.status_code == 200, old.text
    assert old.json()["batch"]["fingerprint"] == first_fp
    assert old.json()["batch"]["source_revision"] == revision
    old_vars = {
        entry["node_id"]: entry
        for entry in old.json()["question_runs"][0]["projection"]["variables"]
    }
    assert old_vars["A"]["value"] == "yes"
    assert old_vars["B"]["value"] == "no"
