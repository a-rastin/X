"""Bounded provider CPT estimation and tool bridging (S43, seam T7 + real T6).

Through real PostgreSQL + real MCP stdio subprocesses (protected-env
grants) + the committed ``DeterministicProviderEndpoint`` loopback double.
No live paid model. Assertions read ``endpoint.captured`` (the decoded
external request), never internal mocks.

Case map to tasks.md S43 §§1-4 (S42 persistence deferred by design: the
adapter takes an injected ``ProviderConfig``; no api-settings routes here):

- §1 pinned payload: ``test_pinned_payload_excludes_secrets``.
- §2 tool bridging: ``test_no_resolve_projection_in_production_path``,
  ``test_tool_call_bridges_real_mcp``,
  ``test_disallowed_tool_name_and_args_rejected``,
  ``test_forged_hash_and_missing_grant_rejected``.
- §3 shared strict validator: ``test_capability_modes_share_strict_validator``,
  ``test_reuses_s23_validator_no_second_parser``,
  ``test_strict_response_rejections``, ``test_tiny_response_budget_rejected``.
- §4 errors/budgets: ``test_error_status_mapping``,
  ``test_tool_and_request_budgets``.

Templates never pass through the provider in either direction.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service
from x_insight.mcp_server import GRANT_ENV_VAR, TOOL_NAME
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import queue as queue_module


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
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "client": client,
        "csrf": body["csrf_token"],
        "user": body["user"],
        "engine": clean_registry,
    }


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str) -> None:
    created = admin_client["client"].post(
        "/api/v1/physicians",
        json={"username": username, "password": "pw123"},
        headers=_auth_headers(admin_client["csrf"]),
    )
    assert created.status_code == 201, created.text


def _physician_client(clean_registry, monkeypatch, username: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return client, body["csrf_token"]


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
        "prompt": {
            "version": "v1",
            "text": (
                "Estimate every CPT in percentages from the supplied inputs. Return strict schema."
            ),
        },
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


def _contract() -> dict[str, Any]:
    return {
        "nodes": [
            {"node_id": "A", "parent_ids": [], "states": ["no", "yes"]},
            {"node_id": "B", "parent_ids": ["A"], "states": ["no", "yes"]},
        ]
    }


def _valid_tables(network_hash: str) -> list[dict[str, Any]]:
    _ = network_hash
    return [
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
    ]


def _valid_cpt(network_hash: str) -> dict[str, Any]:
    return {"network_hash": network_hash, "tables": _valid_tables(network_hash)}


def _setup_projection(
    admin_client,
    clean_registry,
    monkeypatch,
    *,
    username: str,
    identifier: str,
    given_name: str = "Given",
    family_name: str = "Family",
    phone: str | None = None,
    note_text: str | None = None,
) -> dict[str, Any]:
    """Create physician + patient + ready batch; claim the job for its grant."""
    _make_physician(admin_client, username)
    client, csrf = _physician_client(clean_registry, monkeypatch, username)
    patient_body: dict[str, Any] = {
        "identifier": identifier,
        "given_name": given_name,
        "family_name": family_name,
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
    }
    if phone is not None:
        patient_body["phone"] = phone
    created = client.post("/api/v1/patients", json=patient_body, headers=_auth_headers(csrf))
    assert created.status_code == 201, created.text
    encounter_id = created.json()["draft"]["id"]
    saved = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(1)},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    if note_text is not None:
        noted = client.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "history", "text": note_text},
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
        )
        assert noted.status_code == 201, noted.text
        revision = noted.json()["revision"]
    package = _synthetic_package()
    started = client.post(
        f"/api/v1/encounters/{encounter_id}/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert started.status_code == 202, started.text
    body = started.json()
    run = body["question_runs"][0]
    with db_module.session_scope(clean_registry) as session:
        claimed = queue_module.claim_next_job(session, f"worker-{username}")
    assert claimed is not None and "grant_token" in claimed, claimed
    return {
        "client": client,
        "csrf": csrf,
        "engine": clean_registry,
        "test_url": db_module.get_test_database_url(),
        "package": package,
        "run": run,
        "batch_id": body["batch"]["id"],
        "grant": str(claimed["grant_token"]),
    }


def _provider_request(
    setup: dict[str, Any], *, network_hash: str
) -> provider_module.ProviderRequest:
    run = setup["run"]
    package = setup["package"]
    return provider_module.ProviderRequest(
        question_key=str(run["question_key"]),
        projection=dict(run["projection"]),
        projection_hash=str(run["projection_hash"]),
        prompt_version="v1",
        question_run_id=str(run["id"]),
        batch_id=str(setup["batch_id"]),
        prompt_text=str(package["prompt"]["text"]),
        network_hash=network_hash,
        network_version="s40-test-v1",
        cpt_contract=_contract(),
    )


def _estimate_with_script(
    setup: dict[str, Any],
    script: list[dict[str, Any]],
    *,
    network_hash: str,
    capability: str = "schema",
    timeout_seconds: float = 10.0,
    max_request_bytes: int = 1_048_576,
    max_response_bytes: int = 1_048_576,
    grant: str | None = None,
) -> tuple[provider_module.ProviderResult, list[Any]]:
    """One estimate via the committed loopback double; stops it before return."""
    endpoint = provider_module.DeterministicProviderEndpoint(script=script)
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url,
            model="test-model",
            capability=capability,
            timeout_seconds=timeout_seconds,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )
        adapter = provider_module.BoundedProviderAdapter(
            config,
            grant_token=grant if grant is not None else setup["grant"],
            database_url=setup["test_url"],
        )
        result = adapter.estimate(_provider_request(setup, network_hash=network_hash))
        return result, list(endpoint.captured)
    finally:
        endpoint.stop()


def _estimate_raw_content(
    setup: dict[str, Any],
    content: str,
    *,
    network_hash: str,
    capability: str = "schema",
    max_response_bytes: int = 1_048_576,
) -> tuple[provider_module.ProviderResult, list[Any]]:
    """One estimate against a loopback returning raw (possibly non-JSON) bytes."""
    captured: list[Any] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                captured.append(json.loads(raw.decode("utf-8") or "{}"))
            except Exception:
                captured.append({"_raw_unparseable": raw.decode("utf-8", "replace")[:500]})
            body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except BrokenPipeError:
                pass

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/chat/completions"
        config = provider_module.ProviderConfig(
            endpoint_url=url,
            model="test-model",
            capability=capability,
            timeout_seconds=10.0,
            max_response_bytes=max_response_bytes,
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token=setup["grant"], database_url=setup["test_url"]
        )
        result = adapter.estimate(_provider_request(setup, network_hash=network_hash))
        return result, captured
    finally:
        server.shutdown()
        server.server_close()


# --- S43 §1: pinned outbound payload ---


def test_pinned_payload_excludes_secrets(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §1: valid final → ok; captured request holds only pinned inputs."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_s1",
        identifier="0012345678",
        given_name="SentinelGiven",
        family_name="SentinelFamily",
        phone="SENTINEL-PHONE-999",
        note_text="SENTINEL-NOTE-SECRET",
    )
    network_hash = "ab" * 32
    result, captured = _estimate_with_script(
        setup,
        [{"type": "final", "cpt": _valid_cpt(network_hash)}],
        network_hash=network_hash,
    )
    assert result.ok, f"expected ok, got {result.error_code}"
    assert result.payload["tables"], "candidate tables missing"
    assert result.payload["tool_calls_made"] == 0
    assert len(captured) == 1, f"one HTTP exchange, got {len(captured)}"
    blob = str(captured[0])
    for present in (
        "Estimate every CPT",
        setup["run"]["projection_hash"],
        "variables",
        "network_hash",
        TOOL_NAME,
        "test-model",
        "json_schema",
    ):
        assert present in blob, f"captured request missing {present!r}"
    for sentinel in (
        "SentinelGiven",
        "SentinelFamily",
        "0012345678",
        "SENTINEL-PHONE-999",
        "SENTINEL-NOTE-SECRET",
        "Result for",
        "template",
    ):
        assert sentinel not in blob, f"captured request leaked {sentinel!r}"
    assert setup["grant"] not in blob, "grant leaked into provider body"


# --- S43 §2: tool bridging over the real MCP transport ---


def test_no_resolve_projection_in_production_path() -> None:
    """S43 §2: every patient read crosses real stdio; no direct-call bypass."""
    text_body = Path(provider_module.__file__).read_text()
    assert "resolve_projection" not in text_body, "production bypass forbidden"
    assert "read_projection_blocking" in text_body or "read_projection_via_stdio" in text_body, (
        "patient reads must cross the real MCP transport"
    )


def test_tool_call_bridges_real_mcp(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §2: tool[{id:call_abc123, {}}] → final crosses real MCP with correlation."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_t1",
        identifier="0012345678",
    )
    network_hash = "cd" * 32
    result, captured = _estimate_with_script(
        setup,
        [
            {
                "type": "tool",
                "calls": [{"id": "call_abc123", "name": TOOL_NAME, "arguments": {}}],
            },
            {"type": "final", "cpt": _valid_cpt(network_hash)},
        ],
        network_hash=network_hash,
    )
    assert result.ok, f"tool bridging failed: {result.error_code}"
    assert result.payload["tool_calls_made"] == 1
    assert len(captured) == 2, f"expected 2 exchanges, got {len(captured)}"
    second = str(captured[1])
    assert "call_abc123" in second, "tool_call_id correlation missing"
    assert str(setup["run"]["projection_hash"]) in second, "bounded MCP result missing"


def test_disallowed_tool_name_and_args_rejected(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §2: bad tool name / non-empty args → TOOL_REJECTED, 1 exchange."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_t2",
        identifier="0012345679",
    )
    network_hash = "ef" * 32
    for bad in (
        {"id": "call_bad", "name": "search_patients", "arguments": {}},
        {"id": "call_bad2", "name": TOOL_NAME, "arguments": {"extra": 1}},
    ):
        result, captured = _estimate_with_script(
            setup,
            [
                {"type": "tool", "calls": [bad]},
                {"type": "final", "cpt": _valid_cpt(network_hash)},
            ],
            network_hash=network_hash,
        )
        assert not result.ok, f"disallowed {bad} must fail"
        assert result.error_code == "PROVIDER_TOOL_REJECTED", result.error_code
        assert result.retryable is False
        assert len(captured) == 1, "no follow-up exchange after rejection"


def test_forged_hash_and_missing_grant_rejected(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §2: forged hash → CONTEXT/AUTH, missing grant → AUTH, 0 exchanges."""
    monkeypatch.delenv(GRANT_ENV_VAR, raising=False)
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_t3",
        identifier="0012345680",
    )
    network_hash = "aa" * 32
    forged = provider_module.ProviderRequest(
        question_key=str(setup["run"]["question_key"]),
        projection=dict(setup["run"]["projection"]),
        projection_hash="0" * 64,
        prompt_version="v1",
        question_run_id=str(setup["run"]["id"]),
        batch_id=str(setup["batch_id"]),
        prompt_text=str(setup["package"]["prompt"]["text"]),
        network_hash=network_hash,
        network_version="s40-test-v1",
        cpt_contract=_contract(),
    )
    endpoint = provider_module.DeterministicProviderEndpoint(
        script=[{"type": "final", "cpt": _valid_cpt(network_hash)}]
    )
    url = endpoint.start()
    try:
        config = provider_module.ProviderConfig(
            endpoint_url=url, model="m", capability="schema", timeout_seconds=10.0
        )
        adapter = provider_module.BoundedProviderAdapter(
            config, grant_token=setup["grant"], database_url=setup["test_url"]
        )
        result = adapter.estimate(forged)
        assert not result.ok
        assert result.error_code in ("PROVIDER_CONTEXT", "PROVIDER_AUTH"), result.error_code
        assert len(endpoint.captured) == 0, "no HTTP on forged context"
        adapter_no_grant = provider_module.BoundedProviderAdapter(
            config, grant_token="", database_url=setup["test_url"]
        )
        result_no_grant = adapter_no_grant.estimate(
            _provider_request(setup, network_hash=network_hash)
        )
        assert not result_no_grant.ok and result_no_grant.error_code == "PROVIDER_AUTH"
        assert len(endpoint.captured) == 0, "no HTTP without a grant"
    finally:
        endpoint.stop()


# --- S43 §3: capability modes share one strict validator ---


def test_reuses_s23_validator_no_second_parser() -> None:
    """S43 §3: numeric/table checks reuse inference.validate_cpts."""
    text_body = Path(provider_module.__file__).read_text()
    assert "validate_cpts" in text_body, "must reuse the S23 validator"
    assert "def _parse_percentage" not in text_body, "no second percentage parser"


def test_capability_modes_share_strict_validator(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §3: schema/json both accept the same valid tables (own response_format)."""
    network_hash = "bb" * 32
    valid = _valid_cpt(network_hash)
    setup_schema = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_c1",
        identifier="0012345678",
    )
    result_schema, captured_schema = _estimate_with_script(
        setup_schema,
        [{"type": "final", "cpt": valid}],
        network_hash=network_hash,
        capability="schema",
    )
    assert result_schema.ok, f"schema mode must accept valid: {result_schema.error_code}"
    assert "json_schema" in str(captured_schema[0])
    setup_json = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_c2",
        identifier="0012345679",
    )
    result_json, captured_json = _estimate_with_script(
        setup_json,
        [{"type": "final", "cpt": valid}],
        network_hash=network_hash,
        capability="json",
    )
    assert result_json.ok, f"json mode must accept valid: {result_json.error_code}"
    assert "json_object" in str(captured_json[0])
    assert result_schema.payload["tables"] == result_json.payload["tables"]


def test_strict_response_rejections(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §3: prose/ambiguous/malformed/S23 violations → INVALID_RESPONSE retryable."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_v",
        identifier="0012345681",
    )
    network_hash = "bb" * 32

    def _tables_with(mutate) -> list[dict[str, Any]]:
        tables = _valid_tables(network_hash)
        mutate(tables)
        return tables

    def _drop_row(tables: list[dict[str, Any]]) -> None:
        tables[1]["rows"] = tables[1]["rows"][:1]

    def _out_of_range(tables: list[dict[str, Any]]) -> None:
        tables[0]["rows"][0]["percentages"] = ["120", "-20"]

    def _nonfinite(tables: list[dict[str, Any]]) -> None:
        tables[0]["rows"][0]["percentages"] = ["NaN", "nan"]

    def _excess_precision(tables: list[dict[str, Any]]) -> None:
        tables[0]["rows"][0]["percentages"] = ["80.1234567", "19.8765433"]

    def _inexact_total(tables: list[dict[str, Any]]) -> None:
        tables[0]["rows"][0]["percentages"] = ["80", "30"]

    valid_blob = json.dumps(_valid_cpt(network_hash), separators=(",", ":"))
    structured: list[tuple[str, Any]] = [
        ("ambiguous_list", [_valid_cpt(network_hash), _valid_cpt(network_hash)]),
        (
            "missing_table",
            {"network_hash": network_hash, "tables": _valid_tables(network_hash)[:1]},
        ),
        (
            "missing_row",
            {"network_hash": network_hash, "tables": _tables_with(_drop_row)},
        ),
        (
            "out_of_range",
            {"network_hash": network_hash, "tables": _tables_with(_out_of_range)},
        ),
        (
            "nonfinite",
            {"network_hash": network_hash, "tables": _tables_with(_nonfinite)},
        ),
        (
            "excess_precision",
            {"network_hash": network_hash, "tables": _tables_with(_excess_precision)},
        ),
        (
            "inexact_total",
            {"network_hash": network_hash, "tables": _tables_with(_inexact_total)},
        ),
        (
            "template_field",
            {**_valid_cpt(network_hash), "template": {"text": "Result for {B}"}},
        ),
    ]
    for name, cpt in structured:
        result, _ = _estimate_with_script(
            setup, [{"type": "final", "cpt": cpt}], network_hash=network_hash
        )
        assert not result.ok, f"{name} must be rejected"
        assert result.error_code == "PROVIDER_INVALID_RESPONSE", f"{name}: {result.error_code}"
        assert result.retryable is True, f"{name} must be retryable"
    raw_cases = [
        ("extra_prose", f"Here you go: {valid_blob} done"),
        ("malformed", "{not json"),
        ("truncated", '{"network_hash": "bb"'),
    ]
    for name, content in raw_cases:
        result, _ = _estimate_raw_content(setup, content, network_hash=network_hash)
        assert not result.ok, f"{name} must be rejected"
        assert result.error_code == "PROVIDER_INVALID_RESPONSE", f"{name}: {result.error_code}"
        assert result.retryable is True, f"{name} must be retryable"


def test_tiny_response_budget_rejected(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §3: tiny max_response_bytes → BUDGET_EXCEEDED (non-retryable)."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_o",
        identifier="0012345688",
    )
    network_hash = "bb" * 32
    valid = _valid_cpt(network_hash)
    assert len(json.dumps(valid).encode("utf-8")) > 100
    result, _ = _estimate_with_script(
        setup,
        [{"type": "final", "cpt": valid}],
        network_hash=network_hash,
        max_response_bytes=100,
    )
    assert not result.ok
    assert result.error_code == "PROVIDER_BUDGET_EXCEEDED"
    assert result.retryable is False


# --- S43 §4: error/budget mapping, no hidden retries ---


def test_error_status_mapping(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §4: status → explicit code/retryability with exactly one exchange."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_e",
        identifier="0012345690",
    )
    network_hash = "dd" * 32
    matrix = [
        (
            [{"type": "status", "status": 401, "body": {"error": "unauthorized"}}],
            "PROVIDER_AUTH",
            False,
        ),
        ([{"type": "status", "status": 403, "body": {}}], "PROVIDER_AUTH", False),
        ([{"type": "status", "status": 404, "body": {}}], "PROVIDER_MODEL", False),
        ([{"type": "status", "status": 422, "body": {}}], "PROVIDER_MODEL", False),
        (
            [{"type": "status", "status": 400, "body": {"error": "unsupported"}}],
            "PROVIDER_CAPABILITY",
            False,
        ),
        ([{"type": "status", "status": 429, "body": {}}], "PROVIDER_RATE_LIMIT", True),
        ([{"type": "status", "status": 500, "body": {}}], "PROVIDER_TRANSIENT", True),
        ([{"type": "status", "status": 503, "body": {}}], "PROVIDER_TRANSIENT", True),
    ]
    for script, code, retryable in matrix:
        result, captured = _estimate_with_script(setup, script, network_hash=network_hash)
        assert not result.ok, f"{code} must fail"
        assert result.error_code == code, f"got {result.error_code}, want {code}"
        assert result.retryable is retryable, f"{code} retryable={result.retryable}"
        assert len(captured) == 1, f"{code} must be a single exchange"
    delayed = [
        {
            "type": "delay",
            "seconds": 2.0,
            "then": {"type": "final", "cpt": _valid_cpt(network_hash)},
        }
    ]
    result, _ = _estimate_with_script(
        setup, delayed, network_hash=network_hash, timeout_seconds=0.5
    )
    assert not result.ok and result.error_code == "PROVIDER_TIMEOUT", result
    assert result.retryable is True


def test_tool_and_request_budgets(admin_client, clean_registry, monkeypatch) -> None:
    """S43 §4: 11 tools → BUDGET_EXCEEDED/11 exchanges; tiny request → 0; guards."""
    setup = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_b",
        identifier="0012345691",
    )
    network_hash = "dd" * 32
    tool_script = [
        {"type": "tool", "calls": [{"id": f"call_{i}", "name": TOOL_NAME, "arguments": {}}]}
        for i in range(11)
    ]
    tool_script.append({"type": "final", "cpt": _valid_cpt(network_hash)})
    result, captured = _estimate_with_script(setup, tool_script, network_hash=network_hash)
    assert not result.ok and result.error_code == "PROVIDER_BUDGET_EXCEEDED", result
    assert result.retryable is False
    assert len(captured) == 11, f"10 tools + initial = 11 exchanges, got {len(captured)}"
    setup_small = _setup_projection(
        admin_client,
        clean_registry,
        monkeypatch,
        username="dr_s43_b2",
        identifier="0012345692",
    )
    result_small, captured_small = _estimate_with_script(
        setup_small,
        [{"type": "final", "cpt": _valid_cpt(network_hash)}],
        network_hash=network_hash,
        max_request_bytes=100,
    )
    assert not result_small.ok and result_small.error_code == "PROVIDER_BUDGET_EXCEEDED"
    assert len(captured_small) == 0, "oversized request must not POST"
    with pytest.raises(ValueError):
        provider_module.ProviderConfig(
            endpoint_url="http://127.0.0.1:9/x", model="m", timeout_seconds=61.0
        )
    with pytest.raises(ValueError):
        provider_module.ProviderConfig(
            endpoint_url="http://127.0.0.1:9/x", model="m", max_tool_calls=11
        )
    assert (
        provider_module.ProviderConfig(
            endpoint_url="http://127.0.0.1:9/x", model="m"
        ).timeout_seconds
        == 60.0
    )
    assert (
        provider_module.ProviderConfig(
            endpoint_url="http://127.0.0.1:9/x", model="m"
        ).max_tool_calls
        == 10
    )
