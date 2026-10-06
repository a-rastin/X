"""Request/persistence contracts (S02, seam T1).

Covers plan.md §4.2–§4.3 through the public HTTP interface (health/readiness,
correlation, standard error body, size limits) backed by real PostgreSQL, plus
the shared revision/idempotency/canonical-hash/UTC conventions at their actual
public uses. No test-only production endpoint: validation surfaces below are
the real S02 edge conventions (size limit, idempotency-key format).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from x_insight import contracts
from x_insight.app import app


def _body(response) -> dict:
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


# --- Slice A: canonical JSON / hash + UTC helpers (plan.md §4.2) ---


def test_canonical_json_sorts_keys_and_uses_compact_utf8() -> None:
    raw = contracts.canonical_json({"b": 1, "a": [1, 2], "c": "ä"})
    assert raw == '{"a":[1,2],"b":1,"c":"ä"}'.encode()


def test_canonical_hash_stable_under_key_reorder_but_order_sensitive_arrays() -> None:
    left = contracts.canonical_hash({"b": 1, "a": [1, 2]})
    right = contracts.canonical_hash({"a": [1, 2], "b": 1})
    assert left == right
    assert len(left) == 64
    assert contracts.canonical_hash({"a": [2, 1]}) != left


def test_canonical_json_rejects_non_finite_and_non_json() -> None:
    with pytest.raises(TypeError):
        contracts.canonical_json({"v": float("nan")})
    with pytest.raises(TypeError):
        contracts.canonical_json({"v": object()})


def test_utc_helpers_round_trip_with_z_suffix() -> None:
    now = contracts.utcnow()
    assert now.tzinfo is not None
    text = contracts.serialize_utc(now)
    assert text.endswith("Z")
    assert contracts.parse_utc(text) == now
    with pytest.raises(ValueError):
        contracts.serialize_utc(datetime(2030, 1, 1, 0, 0, 0))  # naive rejected


def test_revision_conventions() -> None:
    assert contracts.parse_if_match(None) is None
    assert contracts.parse_if_match("*") is None
    assert contracts.parse_if_match('"3"') == 3
    assert contracts.format_etag(3) == '"3"'
    with pytest.raises(contracts.ContractError) as exc:
        contracts.parse_if_match("oops")
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_IF_MATCH"


def test_idempotency_key_conventions() -> None:
    assert contracts.parse_idempotency_key(None) is None
    key = str(uuid.uuid4())
    assert contracts.parse_idempotency_key(key) == key
    with pytest.raises(contracts.ContractError) as exc:
        contracts.parse_idempotency_key("has spaces!!")
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_IDEMPOTENCY_KEY"
    assert "idempotency_key" in exc.value.field_errors


def test_pagination_conventions() -> None:
    assert contracts.parse_pagination(None, None) == (25, 0)
    assert contracts.parse_pagination(100, 0) == (100, 0)
    with pytest.raises(contracts.ContractError) as exc:
        contracts.parse_pagination(101, 0)
    assert exc.value.status_code == 422
    assert "limit" in exc.value.field_errors
    with pytest.raises(contracts.ContractError):
        contracts.parse_pagination(25, -1)


def test_health_contract_preserved_from_s01() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "x-insight"}
    assert uuid.UUID(response.headers["x-request-id"])


# --- Slice B: correlation + standard error body + size limits (T1) ---


def test_request_id_is_echoed_when_valid_and_generated_otherwise() -> None:
    client = TestClient(app)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/health", headers={"X-Request-ID": sent})
    assert response.headers["x-request-id"] == sent
    response = client.get("/api/v1/health", headers={"X-Request-ID": "x" * 200})
    assert uuid.UUID(response.headers["x-request-id"])
    assert response.headers["x-request-id"] != "x" * 200


def test_unknown_route_returns_standard_body_without_traceback() -> None:
    client = TestClient(app)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/no-such-route", headers={"X-Request-ID": sent})
    assert response.status_code == 404
    body = _body(response)
    assert body == {
        "code": "NOT_FOUND",
        "message": "Not Found",
        "field_errors": {},
        "request_id": sent,
        "retryable": False,
    }
    assert "Traceback" not in response.text
    assert response.headers["x-request-id"] == sent


def test_method_not_allowed_returns_standard_body() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    body = _body(response)
    assert body["code"] == "METHOD_NOT_ALLOWED"
    assert body["request_id"] == response.headers["x-request-id"]


def test_oversized_body_returns_413_standard_body() -> None:
    client = TestClient(app)
    big = "x" * (contracts.MAX_BODY_BYTES + 100)
    response = client.post(
        "/api/v1/health",
        content=big.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 413
    body = _body(response)
    assert body["code"] == "REQUEST_TOO_LARGE"
    assert body["field_errors"] == {}
    assert body["request_id"] == response.headers["x-request-id"]


def test_malformed_idempotency_key_returns_422_field_errors() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/health", headers={"Idempotency-Key": "bad key!!"})
    assert response.status_code == 422
    body = _body(response)
    assert body["code"] == "INVALID_IDEMPOTENCY_KEY"
    assert "idempotency_key" in body["field_errors"]
    assert body["request_id"] == response.headers["x-request-id"]


def test_valid_idempotency_key_passes_through_to_routing() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/health", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 405
    assert _body(response)["code"] == "METHOD_NOT_ALLOWED"


def test_ready_unavailable_database_returns_safe_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from x_insight import db as db_module

    dead = db_module.build_engine("postgresql+psycopg://x_insight@localhost:5599/x_insight_nope")
    monkeypatch.setattr(db_module, "get_engine", lambda: dead)
    try:
        client = TestClient(app)
        sent = str(uuid.uuid4())
        response = client.get("/api/v1/ready", headers={"X-Request-ID": sent})
    finally:
        dead.dispose()
    assert response.status_code == 503
    body = _body(response)
    assert body["code"] == "DB_UNAVAILABLE"
    assert body["request_id"] == sent
    assert body["retryable"] is True
    assert "Traceback" not in response.text
    for secret in ("5599", "x_insight_nope", "POSTGRES", "password"):
        assert secret not in response.text


# --- Slice C: real-PostgreSQL fixtures, readiness-200, audit (T1) ---


@pytest.fixture(scope="session")
def migrated_test_engine():
    """Isolated test database: migrated fresh via the real entry point."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from x_insight import db as db_module

    url = db_module.get_test_database_url()
    # migrations/env.py resolves the runtime DATABASE_URL, mirroring
    # `make migrate`; point it at the isolated test database here.
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # fresh migrate, second run is a no-op
    command.upgrade(cfg, "head")
    engine = db_module.build_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url


@pytest.fixture()
def clean_audit(migrated_test_engine):
    from sqlalchemy import text

    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE audit_events"))
    yield
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE audit_events"))


def test_migration_records_single_head_version(migrated_test_engine) -> None:
    from x_insight import db as db_module

    with migrated_test_engine.connect() as connection:
        assert db_module.get_applied_versions(connection) == ["0007"]


def test_ready_ok_against_real_database(
    monkeypatch: pytest.MonkeyPatch, migrated_test_engine
) -> None:
    from x_insight import db as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    client = TestClient(app)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/ready", headers={"X-Request-ID": sent})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["service"] == "x-insight"
    assert payload["schema_version"] == "0007"
    moment = contracts.parse_utc(payload["checked_at"])
    assert moment.tzinfo is not None
    assert response.headers["x-request-id"] == sent


def test_audit_insert_and_read_share_one_transaction(migrated_test_engine, clean_audit) -> None:
    import uuid as uuid_module

    from x_insight import db as db_module
    from x_insight.operations import audit as audit_module

    details = {"b": 1, "a": [1, 2]}
    with db_module.session_scope(migrated_test_engine) as session:
        event_id = audit_module.record_audit(
            session,
            operation="s02.probe",
            actor="tester",
            request_id=str(uuid_module.uuid4()),
            details=details,
        )
        rows = audit_module.list_audit_events(session)
        assert [row["id"] for row in rows] == [event_id]
        assert rows[0]["operation"] == "s02.probe"
        assert rows[0]["details"] == details
        # Canonical-hash use: stored hash matches the versioned digest.
        assert rows[0]["payload_hash"] == audit_module.audit_payload_hash(details)
        assert rows[0]["payload_hash"] == contracts.canonical_hash(
            {"schema_version": audit_module.SCHEMA_VERSION, "details": details}
        )
        assert rows[0]["occurred_at"].tzinfo is not None
    # Committed: visible from a fresh transaction.
    with db_module.session_scope(migrated_test_engine) as session:
        assert len(audit_module.list_audit_events(session)) == 1


def test_audit_rolls_back_with_its_transaction(migrated_test_engine, clean_audit) -> None:
    from x_insight import db as db_module
    from x_insight.operations import audit as audit_module

    with pytest.raises(RuntimeError, match="boom"):
        with db_module.session_scope(migrated_test_engine) as session:
            audit_module.record_audit(session, operation="s02.probe")
            raise RuntimeError("boom")
    with db_module.session_scope(migrated_test_engine) as session:
        assert audit_module.list_audit_events(session) == []


def test_audit_rejects_empty_operation(migrated_test_engine, clean_audit) -> None:
    from x_insight import db as db_module
    from x_insight.operations import audit as audit_module

    with db_module.session_scope(migrated_test_engine) as session:
        with pytest.raises(contracts.ContractError) as exc:
            audit_module.record_audit(session, operation="  ")
        assert exc.value.status_code == 422
        assert "operation" in exc.value.field_errors


def test_audit_pagination_bounds(migrated_test_engine, clean_audit) -> None:
    from x_insight import db as db_module
    from x_insight.operations import audit as audit_module

    with db_module.session_scope(migrated_test_engine) as session:
        for index in range(3):
            audit_module.record_audit(session, operation=f"s02.probe-{index}")
        assert len(audit_module.list_audit_events(session, limit=2)) == 2
        assert len(audit_module.list_audit_events(session, limit=2, offset=2)) == 1
        with pytest.raises(contracts.ContractError):
            audit_module.list_audit_events(session, limit=101)


def test_ready_incompatible_schema_returns_safe_503(
    monkeypatch: pytest.MonkeyPatch, migrated_test_engine
) -> None:
    """Reachable real PostgreSQL with no/foreign schema version → 503.

    Uses the migrated test database engine (real connection succeeds) while
    the recorded version is forced empty, isolating the incompatibility
    mapping from the unavailability mapping above.
    """
    from x_insight import db as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    monkeypatch.setattr(db_module, "get_applied_versions", lambda connection: [])
    client = TestClient(app)
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    body = _body(response)
    assert body["code"] == "SCHEMA_INCOMPATIBLE"
    assert body["retryable"] is False
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text


def test_ready_foreign_schema_version_returns_safe_503(
    monkeypatch: pytest.MonkeyPatch, migrated_test_engine
) -> None:
    """Reachable DB with a foreign version also reports SCHEMA_INCOMPATIBLE."""
    from x_insight import db as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    monkeypatch.setattr(db_module, "get_applied_versions", lambda connection: ["9999"])
    client = TestClient(app)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/ready", headers={"X-Request-ID": sent})
    assert response.status_code == 503
    body = _body(response)
    assert body["code"] == "SCHEMA_INCOMPATIBLE"
    assert body["retryable"] is False
    assert body["request_id"] == sent
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    assert "9999" not in response.text


def test_health_ok_when_database_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health stays cheap (no DB access) while readiness fails safe."""
    from x_insight import db as db_module

    dead = db_module.build_engine("postgresql+psycopg://x_insight@localhost:5599/x_insight_nope")
    monkeypatch.setattr(db_module, "get_engine", lambda: dead)
    try:
        client = TestClient(app)
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "service": "x-insight"}
        assert uuid.UUID(health.headers["x-request-id"])
        ready = client.get("/api/v1/ready")
        assert ready.status_code == 503
        assert _body(ready)["code"] == "DB_UNAVAILABLE"
    finally:
        dead.dispose()


def test_unexpected_failure_returns_safe_500_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unhandled errors render the standard body (no traceback/secrets)."""
    from x_insight import db as db_module

    def _boom(engine) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("boom SECRET_pw=topsecret Traceback xyz")

    monkeypatch.setattr(db_module, "check_readiness", _boom)
    client = TestClient(app, raise_server_exceptions=False)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/ready", headers={"X-Request-ID": sent})
    assert response.status_code == 500
    body = _body(response)
    assert body == {
        "code": "INTERNAL_ERROR",
        "message": "Internal server error.",
        "field_errors": {},
        "request_id": sent,
        "retryable": False,
    }
    assert "Traceback" not in response.text
    for secret in ("topsecret", "SECRET_pw", "Traceback"):
        assert secret not in response.text


def test_idempotency_key_ignored_for_safe_methods() -> None:
    """Format enforcement is method-scoped: GET ignores a malformed key."""
    client = TestClient(app)
    response = client.get("/api/v1/health", headers={"Idempotency-Key": "bad key!!"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "x-insight"}


def test_post_without_idempotency_key_passes_to_routing() -> None:
    """Presence stays per-command in S02: absent key is not a 422 here."""
    client = TestClient(app)
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert _body(response)["code"] == "METHOD_NOT_ALLOWED"


def test_small_body_passes_size_limit() -> None:
    """Under-limit bodies are not rejected with 413."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/health",
        content=b"x" * 100,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 405
    assert _body(response)["code"] == "METHOD_NOT_ALLOWED"


def test_blank_request_id_generates_fresh_uuid() -> None:
    """Blank/whitespace correlation IDs are replaced, never echoed."""
    client = TestClient(app)
    for sent in ("", "   "):
        response = client.get("/api/v1/health", headers={"X-Request-ID": sent})
        assert response.status_code == 200
        received = response.headers["x-request-id"]
        assert received != sent
        assert uuid.UUID(received)


def test_ready_ok_body_shape_against_real_database(
    monkeypatch: pytest.MonkeyPatch, migrated_test_engine
) -> None:
    """Ready 200 carries Zulu UTC time, schema version, and correlation."""
    from x_insight import db as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    client = TestClient(app)
    sent = str(uuid.uuid4())
    response = client.get("/api/v1/ready", headers={"X-Request-ID": sent})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["service"] == "x-insight"
    assert payload["schema_version"] == "0007"
    assert payload["checked_at"].endswith("Z")
    moment = contracts.parse_utc(payload["checked_at"])
    assert moment.tzinfo is not None
    now = contracts.utcnow()
    assert abs((now - moment).total_seconds()) < 60
    assert response.headers["x-request-id"] == sent


def test_canonical_json_keeps_null_distinct_from_missing() -> None:
    """Plan §4.2: explicit nulls are kept; missing keys stay absent."""
    assert contracts.canonical_json({"a": None}) == b'{"a":null}'
    assert contracts.canonical_json({}) == b"{}"
    assert contracts.canonical_hash({"a": None}) != contracts.canonical_hash({})
    # Nested objects sort keys; arrays keep semantic order.
    raw = contracts.canonical_json({"b": {"d": 2, "c": 1}, "a": 1})
    assert raw == b'{"a":1,"b":{"c":1,"d":2}}'


def test_canonical_json_rejects_infinite() -> None:
    with pytest.raises(TypeError):
        contracts.canonical_json({"v": float("inf")})
    with pytest.raises(TypeError):
        contracts.canonical_json({"v": float("-inf")})


def test_utc_converts_non_utc_to_zulu_and_rejects_naive_parse() -> None:
    from datetime import timedelta, timezone

    eastern = timezone(timedelta(hours=2))
    moment = datetime(2030, 1, 1, 12, 0, 0, tzinfo=eastern)
    text = contracts.serialize_utc(moment)
    assert text == "2030-01-01T10:00:00Z"
    assert contracts.parse_utc(text) == contracts.parse_utc("2030-01-01T10:00:00Z")
    assert contracts.parse_utc("2030-01-01T12:00:00+02:00") == contracts.parse_utc(text)
    with pytest.raises(ValueError):
        contracts.parse_utc("2030-01-01T00:00:00")


def test_audit_uncommitted_row_not_visible_until_commit(migrated_test_engine, clean_audit) -> None:
    """Transaction-scoped helper: flush does not commit by itself."""
    from sqlalchemy.orm import sessionmaker

    from x_insight.operations import audit as audit_module

    factory = sessionmaker(bind=migrated_test_engine, expire_on_commit=False)
    writer = factory()
    reader = factory()
    try:
        audit_module.record_audit(writer, operation="s02.isolation-probe")
        writer.flush()
        assert audit_module.list_audit_events(reader) == []
        writer.rollback()
        assert audit_module.list_audit_events(reader) == []
    finally:
        writer.close()
        reader.close()


def test_audit_list_ordered_by_time(migrated_test_engine, clean_audit) -> None:
    """Reads come back in time order (stable for S03+ consumers)."""
    from datetime import UTC

    from x_insight import db as db_module
    from x_insight.operations import audit as audit_module

    t1 = datetime(2030, 1, 1, 0, 0, 0, tzinfo=UTC)
    t2 = datetime(2030, 1, 1, 0, 0, 1, tzinfo=UTC)
    t3 = datetime(2030, 1, 1, 0, 0, 2, tzinfo=UTC)
    with db_module.session_scope(migrated_test_engine) as session:
        audit_module.record_audit(session, operation="s02.order-2", occurred_at=t2)
        audit_module.record_audit(session, operation="s02.order-1", occurred_at=t1)
        audit_module.record_audit(session, operation="s02.order-3", occurred_at=t3)
        assert [r["operation"] for r in audit_module.list_audit_events(session)] == [
            "s02.order-1",
            "s02.order-2",
            "s02.order-3",
        ]


def test_only_s02_tables_exist(migrated_test_engine) -> None:
    """S02 audit storage persists; S03 adds identity tables; S04 adds only the
    idempotency store; S06 adds patients + encounters; S13 adds only the notes
    table (no draft-content beyond S07 draft_data, no runs, or jobs tables)."""
    from sqlalchemy import text

    with migrated_test_engine.connect() as connection:
        tables = sorted(
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        )
    assert tables == [
        "alembic_version",
        "audit_events",
        "ddi_concepts",
        "ddi_dataset_releases",
        "ddi_interaction_evidence",
        "ddi_source_documents",
        "encounters",
        "idempotency_records",
        "notes",
        "patients",
        "sessions",
        "users",
    ]


def test_audit_roles_and_append_only_grants(migrated_test_engine) -> None:
    """Exit criterion: roles exist; app appends/reads but never updates/deletes."""
    from sqlalchemy import text

    with migrated_test_engine.connect() as connection:
        roles = sorted(
            row[0]
            for row in connection.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'x_insight%'")
            )
        )
        assert roles == ["x_insight_app", "x_insight_migrate", "x_insight_readonly"]

        def _can(role: str, privilege: str) -> bool:
            return bool(
                connection.execute(
                    text(f"SELECT has_table_privilege('{role}', 'audit_events', '{privilege}')")
                ).scalar()
            )

        assert _can("x_insight_app", "SELECT")
        assert _can("x_insight_app", "INSERT")
        assert not _can("x_insight_app", "UPDATE")
        assert not _can("x_insight_app", "DELETE")
        assert _can("x_insight_readonly", "SELECT")
        assert not _can("x_insight_readonly", "INSERT")
        assert _can("x_insight_migrate", "UPDATE")


def test_test_engine_uses_real_postgresql(migrated_test_engine) -> None:
    """Seam T1: backend tests run against real PostgreSQL, never sqlite."""
    from sqlalchemy import text

    assert "postgresql" in str(migrated_test_engine.url)
    assert "+sqlite" not in str(migrated_test_engine.url)
    with migrated_test_engine.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
    assert "PostgreSQL" in version


def test_audit_module_has_no_generic_repository(migrated_test_engine) -> None:
    """No generic update/delete helper: only record + list exist."""
    from x_insight.operations import audit as audit_module

    assert callable(audit_module.record_audit)
    assert callable(audit_module.list_audit_events)
    for name in ("update", "delete", "remove", "save", "get_or_create", "upsert"):
        assert not any(name in attr.lower() for attr in dir(audit_module))
