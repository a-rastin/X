"""Real private MCP transport and context grants (S41, seam T6; plan.md §8.2).

Through real PostgreSQL + real stdio subprocesses (pinned ``mcp==2.3.0``
low-level ``Server`` + ``stdio_client``/``ClientSession``). No direct
in-process shortcut satisfies §1. Synthetic single-question scope
(``synthetic_snapshot``) over S40 frozen projections and S44 leased jobs.

Case map to tasks.md S41 §§1-4:

- §1 (discovery + bound projection): ``test_discovery_*``,
  ``test_call_empty_args_returns_bound_projection``.
- §2 (negatives + env-only context): ``test_extra_args_*``,
  ``test_unknown_tools_*``, ``test_forged_missing_expired_grants_*``,
  ``test_stale_snapshot_*``, ``test_inactive_actor_*``,
  ``test_grant_via_protected_env_only``.
- §3 (A/B + successive isolation, stderr/stdout): ``test_concurrent_ab_*``,
  ``test_revoke_before_reuse_and_stderr_*``.
- §4 (oversized + DB/transport + cleanup + fencing):
  ``test_oversized_*``, ``test_db_transport_failure_*``,
  ``test_cleanup_revokes_and_deployment_fencing_*``.

No public MCP listener, patient search/write, inference, shell, or file
tool exists: every discovery test asserts exactly one tool.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service
from x_insight.mcp_server import TOOL_INPUT_SCHEMA, TOOL_NAME


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
                {"when": {"node": "B", "state": "yes", "operator": "=="}, "text": "Result {B}."},
            ],
        },
        "examples": {
            "numerical": [{"inputs": {}, "expected": {"A": {"no": 0.8, "yes": 0.2}}}],
            "clinical": [{"inputs": {}, "expected_for_review": {"B": "yes"}, "note": "review"}],
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


def _setup_ready_batch(
    admin_client,
    clean_registry,
    monkeypatch,
    username: str,
    identifier: str,
    h_a: str = "yes",
    h_b: str = "no",
    note: str | None = None,
):
    """Create physician + patient + frozen ready batch; return client/csrf/encounter/rev/batch."""
    _make_physician(admin_client, username)
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, username)
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
    encounter_id = created.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": h_a, "h_b": h_b}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    if note is not None:
        noted = client.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "history", "text": note},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )
        assert noted.status_code == 201, noted.text
        revision = noted.json()["revision"]
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": _synthetic_package()},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    batch_id = started.json()["batch"]["id"]
    return client, csrf, encounter_id, revision, batch_id


def _claim_job(engine, worker_id: str = "worker-s41"):
    from x_insight.reasoning import queue as queue_module

    with db_module.session_scope(engine) as session:
        claimed = queue_module.claim_next_job(session, worker_id, now=contracts.utcnow())
    assert claimed is not None and "job" in claimed, claimed
    return claimed


def _assert_bounded_no_leak(message: str, *secrets: str) -> None:
    assert len(message) <= 200, f"error must be bounded, got {len(message)} chars"
    lowered = message.lower()
    assert "traceback" not in lowered
    for secret in secrets:
        if secret:
            assert secret not in message, f"error leaks secret {secret!r}"


# --- S41 §1: real stdio discovery + bound projection ---


def test_discovery_exposes_exactly_one_allowed_tool(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §1: real SDK client starts stdio, discovers exactly the allowed tool."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_disc", "0012345678")
    claimed = _claim_job(clean_registry, "worker-disc")
    grant_token = str(claimed["grant_token"])
    db_url = db_module.get_test_database_url()

    listed = host_module.list_tools_raw(grant_token, database_url=db_url)
    tools = list(listed.tools)
    assert len(tools) == 1, f"exactly one tool, got {[t.name for t in tools]}"
    assert tools[0].name == TOOL_NAME
    assert tools[0].name == "get_question_patient_inputs"
    schema = getattr(tools[0], "inputSchema", getattr(tools[0], "input_schema", None))
    assert schema == {"type": "object", "properties": {}, "additionalProperties": False}
    assert schema == dict(TOOL_INPUT_SCHEMA)

    # No public listener/search/write/inference/shell/file tool exists:
    # exactly one Tool definition in the server implementation.
    backend_root = Path(__file__).resolve().parents[2]
    server_src = (backend_root / "src/x_insight/mcp_server/server.py").read_text()
    assert server_src.count("types.Tool(") == 1
    for forbidden in ("search_patients", "write", "inference", "shell", "read_file"):
        assert forbidden not in [t.name for t in tools]


def test_call_empty_args_returns_bound_stored_projection(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §1: call with {} returns the bound S40 frozen projection via real stdio."""
    from x_insight.reasoning import mcp_host as host_module

    client, _, _, _, batch_id = _setup_ready_batch(
        admin_client, clean_registry, monkeypatch, "dr_s41_call", "0012345678"
    )
    claimed = _claim_job(clean_registry, "worker-call")
    grant_token = str(claimed["grant_token"])
    expected_hash = str(claimed["run"]["projection_hash"])
    db_url = db_module.get_test_database_url()

    output = host_module.read_projection_blocking(grant_token, database_url=db_url)
    assert set(output.keys()) == {
        "question_key",
        "network_version",
        "projection_hash",
        "variables",
    }
    assert output["question_key"] == "synthetic_snapshot"
    assert output["network_version"] == "s40-test-v1"
    assert output["projection_hash"] == expected_hash
    assert isinstance(output["variables"], list) and len(output["variables"]) == 2
    by_node = {entry["node_id"]: entry for entry in output["variables"]}
    assert by_node["A"]["value"] == "yes"
    assert by_node["B"]["value"] == "no"

    # Variables match the S40 frozen projection readable via public GET.
    read = client.get(f"/api/v1/generation-batches/{batch_id}")
    assert read.status_code == 200, read.text
    frozen = read.json()["question_runs"][0]
    assert frozen["projection_hash"] == expected_hash
    assert frozen["projection"]["variables"] == output["variables"]

    # Production path crosses real stdio; no direct in-process shortcut.
    src = inspect.getsource(host_module.read_projection_via_stdio)
    assert "stdio_client" in src and "call_tool" in src
    assert "resolve_projection" not in src


# --- S41 §2: negatives fail safely; context from protected env only ---


def test_extra_args_rejected_safely(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §2: extra model arguments fail with bounded generic error, no values."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_args", "0012345678")
    claimed = _claim_job(clean_registry, "worker-args")
    grant_token = str(claimed["grant_token"])
    db_url = db_module.get_test_database_url()

    async def _raw(args: Any):
        return await host_module.call_tool_raw(
            grant_token, "get_question_patient_inputs", args, database_url=db_url
        )

    for bad_args in ({"extra": "x"}, {"question_key": "synthetic_snapshot"}, {"a": 1}):
        result = asyncio.run(_raw(bad_args))
        assert bool(getattr(result, "isError", getattr(result, "is_error", False))) is True
        err_text = str(getattr(result.content[0], "text", ""))
        _assert_bounded_no_leak(err_text, "yes", "SENTINEL")
        assert "yes" not in err_text


def test_unknown_tools_rejected_safely(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §2: unknown/search/write/inference/shell/file tools fail, exactly one exists."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_unknown", "0012345678")
    claimed = _claim_job(clean_registry, "worker-unknown")
    grant_token = str(claimed["grant_token"])
    db_url = db_module.get_test_database_url()

    for name in (
        "search_patients",
        "write_note",
        "run_inference",
        "exec_shell",
        "read_file",
        "list_tools_extra",
    ):
        result = asyncio.run(host_module.call_tool_raw(grant_token, name, {}, database_url=db_url))
        assert bool(getattr(result, "isError", getattr(result, "is_error", False))) is True
        err_text = str(getattr(result.content[0], "text", ""))
        _assert_bounded_no_leak(err_text, name)


def test_forged_missing_expired_grants_fail_safely(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §2: forged/missing/expired grants fail bounded without secrets/existence."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_grant", "0012345678")
    claimed = _claim_job(clean_registry, "worker-grant")
    grant_token = str(claimed["grant_token"])
    job_id = claimed["job"]["id"]
    db_url = db_module.get_test_database_url()

    try:
        host_module.read_projection_blocking("f" * 64, database_url=db_url)
        forged_ok = True
    except host_module.McpHostError as exc:
        forged_ok = False
        _assert_bounded_no_leak(str(exc), "f" * 8)
    assert forged_ok is False

    try:
        host_module.read_projection_blocking("", database_url=db_url)
        missing_ok = True
    except host_module.McpHostError as exc:
        missing_ok = False
        _assert_bounded_no_leak(str(exc))
    assert missing_ok is False

    past = contracts.utcnow() - timedelta(seconds=1)
    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE reasoning_grants SET expires_at = :past WHERE job_id = :jid"),
            {"past": past, "jid": str(job_id)},
        )
    try:
        host_module.read_projection_blocking(grant_token, database_url=db_url)
        expired_ok = True
    except host_module.McpHostError as exc:
        expired_ok = False
        assert "invalid or expired" in str(exc).lower()
        _assert_bounded_no_leak(str(exc), grant_token[:8])
    assert expired_ok is False


def test_stale_snapshot_fails_safely(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §2: mismatched snapshot (relevant edit) fails without leaking new values."""
    from x_insight.reasoning import mcp_host as host_module

    client, csrf, encounter_id, revision, _ = _setup_ready_batch(
        admin_client, clean_registry, monkeypatch, "dr_s41_stale", "0012345678"
    )
    claimed = _claim_job(clean_registry, "worker-stale")
    grant_token = str(claimed["grant_token"])
    db_url = db_module.get_test_database_url()

    edited = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "no", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert edited.status_code == 200, edited.text
    try:
        host_module.read_projection_blocking(grant_token, database_url=db_url)
        stale_ok = True
    except host_module.McpHostError as exc:
        stale_ok = False
        assert "eligible" in str(exc).lower()
        _assert_bounded_no_leak(str(exc), grant_token[:8])
    assert stale_ok is False


def test_inactive_actor_fails_safely(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §2: inactive actor fails safely with bounded generic error."""
    from x_insight.identity import tables as identity_tables
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_inactive", "0012345680")
    claimed = _claim_job(clean_registry, "worker-inactive")
    grant_token = str(claimed["grant_token"])
    db_url = db_module.get_test_database_url()

    with db_module.session_scope(clean_registry) as session:
        session.execute(
            update(identity_tables.users)
            .where(identity_tables.users.c.username == "dr_s41_inactive")
            .values(active=False)
        )
    try:
        host_module.read_projection_blocking(grant_token, database_url=db_url)
        inactive_ok = True
    except host_module.McpHostError as exc:
        inactive_ok = False
        _assert_bounded_no_leak(str(exc), "h_a", grant_token[:8])
        assert "eligible" in str(exc).lower() or "grant" in str(exc).lower()
    assert inactive_ok is False


def test_grant_via_protected_env_only() -> None:
    """S41 §2: context comes from X_INSIGHT_MCP_GRANT env, never model/CLI args."""
    from x_insight.mcp_server import GRANT_ENV_VAR
    from x_insight.reasoning import mcp_host as host_module

    assert GRANT_ENV_VAR == "X_INSIGHT_MCP_GRANT"
    host_src = inspect.getsource(host_module)
    assert "GRANT_ENV_VAR" in host_src or "X_INSIGHT_MCP_GRANT" in host_src
    assert "MAX_MCP_OUTPUT_BYTES" in host_src
    assert 'args=["-m", "x_insight.mcp_server"]' in host_src
    # Tool takes no arguments, so the grant cannot travel as a model argument.
    assert TOOL_INPUT_SCHEMA == {"type": "object", "properties": {}, "additionalProperties": False}
    backend_root = Path(__file__).resolve().parents[2]
    server_src = (backend_root / "src/x_insight/mcp_server/server.py").read_text()
    assert "GRANT_ENV_VAR" in server_src or "X_INSIGHT_MCP_GRANT" in server_src
    assert "sys.argv" not in server_src


# --- S41 §3: A/B + successive isolation; stderr/stdout separation ---


def test_concurrent_ab_processes_isolate_sentinels_and_notes(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §3: simultaneous A/B stdio processes never cross sentinels/notes."""
    from x_insight.reasoning import mcp_host as host_module

    _make_physician(admin_client, "dr_s41_iso_a")
    _make_physician(admin_client, "dr_s41_iso_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_s41_iso_a")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_s41_iso_b")

    def _make_batch(client, csrf, identifier: str, h_a: str, h_b: str, note: str) -> None:
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
        encounter_id = created.json()["draft"]["id"]
        saved = client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {"history": {"values": {"h_a": h_a, "h_b": h_b}}, "gate": "true"}},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        noted = client.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "history", "text": note},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )
        assert noted.status_code == 201, noted.text
        revision = noted.json()["revision"]
        started = client.post(
            f"/api/v1/encounters/{encounter_id}/generation-batches",
            json={"package": _synthetic_package()},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )
        assert started.status_code == 202, started.text

    _make_batch(client_a, csrf_a, "0012345678", "SENTINEL-A-HA", "no", "NOTE-A-SECRET")
    _make_batch(client_b, csrf_b, "0012345679", "yes", "SENTINEL-B-HB", "NOTE-B-SECRET")

    first = _claim_job(clean_registry, "worker-iso-1")
    second = _claim_job(clean_registry, "worker-iso-2")
    assert str(first["job"]["id"]) != str(second["job"]["id"])
    assert str(first["grant_token"]) != str(second["grant_token"])
    db_url = db_module.get_test_database_url()

    async def _both() -> tuple[dict, dict]:
        return await asyncio.gather(
            host_module.read_projection_via_stdio(str(first["grant_token"]), database_url=db_url),
            host_module.read_projection_via_stdio(str(second["grant_token"]), database_url=db_url),
        )

    out_first, out_second = asyncio.run(_both())
    texts = [str(out_first), str(out_second)]
    assert ("SENTINEL-A-HA" in texts[0]) != ("SENTINEL-A-HA" in texts[1])
    assert ("SENTINEL-B-HB" in texts[0]) != ("SENTINEL-B-HB" in texts[1])
    for payload_text in texts:
        assert "NOTE-A-SECRET" not in payload_text
        assert "NOTE-B-SECRET" not in payload_text


def test_revoke_before_reuse_and_stderr_separation(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §3: stop/revoke before reuse; stderr diagnostics never corrupt stdout."""
    from x_insight.reasoning import mcp_host as host_module

    _make_physician(admin_client, "dr_s41_rev_a")
    _make_physician(admin_client, "dr_s41_rev_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_s41_rev_a")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_s41_rev_b")

    def _make_batch(client, csrf, identifier: str) -> None:
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
        encounter_id = created.json()["draft"]["id"]
        saved = client.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={
                "draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}
            },
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
        )
        assert saved.status_code == 200, saved.text
        started = client.post(
            f"/api/v1/encounters/{encounter_id}/generation-batches",
            json={"package": _synthetic_package()},
            headers={
                **_auth_headers(csrf),
                "If-Match": contracts.format_etag(saved.json()["revision"]),
            },
        )
        assert started.status_code == 202, started.text

    _make_batch(client_a, csrf_a, "0012345678")
    _make_batch(client_b, csrf_b, "0012345679")
    first = _claim_job(clean_registry, "worker-rev-1")
    second = _claim_job(clean_registry, "worker-rev-2")
    db_url = db_module.get_test_database_url()

    revoked = host_module.revoke_scoped_grant(clean_registry, first["job"]["id"])
    assert revoked >= 1
    try:
        host_module.read_projection_blocking(str(first["grant_token"]), database_url=db_url)
        old_ok = True
    except host_module.McpHostError:
        old_ok = False
    assert old_ok is False, "revoked context must not read after stop/revoke"
    still = host_module.read_projection_blocking(str(second["grant_token"]), database_url=db_url)
    assert still["projection_hash"]

    backend_root = Path(__file__).resolve().parents[2]
    main_src = (backend_root / "src/x_insight/mcp_server/__main__.py").read_text()
    assert "stderr" in main_src
    assert "\nprint(" not in main_src
    server_src = (backend_root / "src/x_insight/mcp_server/server.py").read_text()
    assert "\nprint(" not in server_src


# --- S41 §4: oversized + DB/transport + cleanup + fencing ---


def test_oversized_projection_fails_bounded(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §4: oversized projection returns bounded error without leaking payload."""
    from x_insight.reasoning import mcp_host as host_module

    _make_physician(admin_client, "dr_s41_big")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_s41_big")
    created = client.post(
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
    assert created.status_code == 201, created.text
    encounter_id = created.json()["draft"]["id"]
    big_value = "BIG-" + "X" * 70000
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {"history": {"values": {"h_a": big_value, "h_b": "no"}}, "gate": "true"}
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": _synthetic_package()},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(saved.json()["revision"]),
        },
    )
    assert started.status_code == 202, started.text
    claimed = _claim_job(clean_registry, "worker-big")
    db_url = db_module.get_test_database_url()
    try:
        host_module.read_projection_blocking(str(claimed["grant_token"]), database_url=db_url)
        oversized_ok = True
    except host_module.McpHostError as exc:
        oversized_ok = False
        _assert_bounded_no_leak(str(exc), "BIG-", "XXXX")
    assert oversized_ok is False, "oversized projection must fail bounded"


def test_db_transport_failure_fails_bounded(admin_client, clean_registry, monkeypatch) -> None:
    """S41 §4: database/transport failure returns bounded error without secrets."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_dbfail", "0012345678")
    claimed = _claim_job(clean_registry, "worker-dbfail")
    grant_token = str(claimed["grant_token"])
    bad_url = "postgresql+psycopg://xinsight:wrong@localhost:5499/x_insight_test"
    try:
        host_module.read_projection_blocking(grant_token, database_url=bad_url, timeout_seconds=8.0)
        dbfail_ok = True
    except host_module.McpHostError as exc:
        dbfail_ok = False
        _assert_bounded_no_leak(str(exc), "wrong", "5499")
    assert dbfail_ok is False


def test_cleanup_revokes_and_deployment_fencing_respected(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S41 §4: process cleanup revokes; S44 lease/deployment fencing is respected."""
    from x_insight.reasoning import mcp_host as host_module

    _setup_ready_batch(admin_client, clean_registry, monkeypatch, "dr_s41_fence", "0012345679")
    claimed = _claim_job(clean_registry, "worker-fence")
    grant_token = str(claimed["grant_token"])
    job_id = claimed["job"]["id"]
    db_url = db_module.get_test_database_url()

    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE reasoning_deployment SET generation = generation + 1 WHERE id = 1")
        )
    try:
        try:
            host_module.read_projection_blocking(grant_token, database_url=db_url)
            fenced_ok = True
        except host_module.McpHostError as exc:
            fenced_ok = False
            _assert_bounded_no_leak(str(exc), grant_token[:8])
        assert fenced_ok is False, "deployment bump must fence old grant"
    finally:
        with clean_registry.begin() as connection:
            connection.execute(text("UPDATE reasoning_deployment SET generation = 1 WHERE id = 1"))

    count = host_module.revoke_scoped_grant(clean_registry, job_id)
    assert count >= 0
    try:
        host_module.read_projection_blocking(grant_token, database_url=db_url)
        after_ok = True
    except host_module.McpHostError:
        after_ok = False
    assert after_ok is False

    backend_root = Path(__file__).resolve().parents[2]
    queue_src = (backend_root / "src/x_insight/reasoning/queue.py").read_text()
    assert "FENCING_TOKEN_MISMATCH" in queue_src and "DEPLOYMENT_FENCED" in queue_src
