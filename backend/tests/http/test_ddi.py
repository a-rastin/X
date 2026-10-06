"""S19 DDI HTTP routes (T1; plan.md §§4.3, 6.3, FR-14/15).

Covers slice 4 at the authenticated-HTTP seam with real PostgreSQL:
- POST /ddi/check + GET /drugs?query=... with session/role enforcement
  (any active session; 401 unauthenticated, 403 CSRF on mutations),
  standard ErrorBody, no tracebacks/secrets.
- Drug-only input {medications:[{catalog_drug_id}], dataset_version}:
  free-text unknown-label and dose/unit/route/frequency/status rejected 422
  even if forged.
- Report pins dataset/catalog/fingerprint; invalid dataset/configuration
  refused; offline execution (network disabled) still succeeds.

Synthetic release only; real corpus stays awaiting_review. Expected values
are worked literals, never implementation output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service as identity_service


def concept(identifier, name, kind="ingredient", catalog=None):
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": kind,
        "catalog_drug_id": catalog,
        "source": "synthetic S19 HTTP fixture",
    }


def vocabulary(concepts, aliases=None):
    return {"version": "test-s19-http/1", "concepts": concepts, "aliases": aliases or []}


def monograph(root: Path, subject: str, names: list[str]):
    body = f"Interactions\n\nContraindicated (0)\n\nSerious ({len(names)})\n\n"
    body += "\n\n".join(
        f"{name}\n{name} increases the level of {subject} by mechanism. Avoid combination."
        for name in names
    )
    body += "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    (root / f"{subject}.txt").write_text(body)


def _stage(source_dir: Path, staging: Path, terminology=None):
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
    return dataset, report


def _manifest_for_staging(staging: Path, path: Path, **overrides):
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    payload = {
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


def _reviewed_for_staging(staging: Path, included: list[str]) -> list[dict]:
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    reviewed = []
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


@pytest.fixture()
def clean_all(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        connection.execute(
            text(
                "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases"
            )
        )
    identity_service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        connection.execute(
            text(
                "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases"
            )
        )
    identity_service.ensure_default_admin(migrated_test_engine)


def _publish_pair(clean_all, tmp_path):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    concepts = [
        concept("alpha", "Alpha", catalog="catalog_alpha"),
        concept("beta", "Beta", catalog="catalog_beta"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, clean_all)


def _login(username="admin", password="admin", role="admin"):
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password, "role": role}
    )
    assert response.status_code == 200
    return client, response.json()["csrf_token"]


def _physician_client(clean_all):
    admin_client, admin_csrf = _login()
    username = f"doc_{os.getpid()}_{id(clean_all) % 100000}"
    created = admin_client.post(
        "/api/v1/physicians",
        json={"username": username, "password": "secret123"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code == 201, created.text
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret123", "role": "physician"},
    )
    assert response.status_code == 200
    assert "research prototype" in response.json()["research_warning"]
    return client, response.json()["csrf_token"]


def _assert_error_body(response, status):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "field_errors", "request_id", "retryable"}
    assert body["request_id"]
    assert "Traceback" not in response.text
    assert "pbkdf2" not in response.text.lower()
    return body


# --- Slice 4: HTTP enforcement (T1) ---


def test_post_check_requires_auth_and_csrf(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    body = {"medications": [{"catalog_drug_id": "catalog_alpha"}], "dataset_version": version}

    anonymous = TestClient(app)
    _assert_error_body(anonymous.post("/api/v1/ddi/check", json=body), 401)

    client, csrf = _login()
    # Missing CSRF on a mutation is 403.
    _assert_error_body(client.post("/api/v1/ddi/check", json=body), 403)
    # Wrong token is also 403 and changes nothing.
    _assert_error_body(
        client.post("/api/v1/ddi/check", json=body, headers={"X-CSRF-Token": "wrong"}),
        403,
    )


def test_post_check_allows_any_active_session(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    body = {
        "medications": [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}],
        "dataset_version": version,
    }
    admin_client, admin_csrf = _login()
    admin_response = admin_client.post(
        "/api/v1/ddi/check", json=body, headers={"X-CSRF-Token": admin_csrf}
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["pairs"][0]["status"] == "interaction_found"

    physician_client, physician_csrf = _physician_client(clean_all)
    physician_response = physician_client.post(
        "/api/v1/ddi/check", json=body, headers={"X-CSRF-Token": physician_csrf}
    )
    assert physician_response.status_code == 200
    assert (
        physician_response.json()["medication_fingerprint"]
        == (admin_response.json()["medication_fingerprint"])
    )


def test_post_check_rejects_excluded_fields_and_free_text(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}

    for forged in (
        {
            "medications": [{"catalog_drug_id": "catalog_alpha", "dose": "5mg"}],
            "dataset_version": version,
        },
        {
            "medications": [{"catalog_drug_id": "catalog_alpha", "unit": "mg"}],
            "dataset_version": version,
        },
        {
            "medications": [{"catalog_drug_id": "catalog_alpha", "route": "oral"}],
            "dataset_version": version,
        },
        {
            "medications": [{"catalog_drug_id": "catalog_alpha", "frequency": "daily"}],
            "dataset_version": version,
        },
        {
            "medications": [{"catalog_drug_id": "catalog_alpha", "status": "active"}],
            "dataset_version": version,
        },
        {"medications": [{"unknown_label": "Mystery Entity"}], "dataset_version": version},
        {
            "medications": [{"catalog_drug_id": "catalog_alpha"}],
            "dataset_version": version,
            "dose": "5mg",
        },
    ):
        _assert_error_body(client.post("/api/v1/ddi/check", json=forged, headers=headers), 422)


def test_post_check_rejects_noncatalog_and_invalid_dataset(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}

    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={
                "medications": [{"catalog_drug_id": "catalog_unknown"}],
                "dataset_version": version,
            },
            headers=headers,
        ),
        422,
    )
    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={
                "medications": [{"catalog_drug_id": "catalog_alpha"}],
                "dataset_version": "bad-version",
            },
            headers=headers,
        ),
        422,
    )
    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={
                "medications": [{"catalog_drug_id": "catalog_alpha"}],
                "dataset_version": "ddi-000000000000",
            },
            headers=headers,
        ),
        404,
    )


def test_post_check_rejects_missing_version_and_empty_drug_id(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}

    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={"medications": [{"catalog_drug_id": "catalog_alpha"}]},
            headers=headers,
        ),
        422,
    )
    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={"medications": [{"catalog_drug_id": ""}], "dataset_version": version},
            headers=headers,
        ),
        422,
    )
    _assert_error_body(
        client.post(
            "/api/v1/ddi/check",
            json={"medications": [], "dataset_version": "ddi-000000000000"},
            headers=headers,
        ),
        404,
    )


def test_revoked_session_cannot_check(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()
    body = {"medications": [{"catalog_drug_id": "catalog_alpha"}], "dataset_version": version}
    before = client.post("/api/v1/ddi/check", json=body, headers={"X-CSRF-Token": csrf})
    assert before.status_code == 200

    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    _assert_error_body(
        client.post("/api/v1/ddi/check", json=body, headers={"X-CSRF-Token": csrf}),
        401,
    )


def test_post_check_pins_versions_and_never_claims_safe(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()
    report = client.post(
        "/api/v1/ddi/check",
        json={"medications": [{"catalog_drug_id": "catalog_alpha"}], "dataset_version": version},
        headers={"X-CSRF-Token": csrf},
    ).json()

    assert report["dataset_version"] == version
    assert report["catalog_version"] == "test-s19-http/1"
    assert len(report["medication_fingerprint"]) == 64
    assert report["generated_at"].endswith("Z")
    assert "safe" not in json.dumps(report).lower()
    assert report["pairs"] == []


def test_get_drugs_requires_auth_filters_and_pages(clean_all, tmp_path):
    _publish_pair(clean_all, tmp_path)

    anonymous = TestClient(app)
    _assert_error_body(anonymous.get("/api/v1/drugs?query=alpha"), 401)

    client, _ = _login()
    full = client.get("/api/v1/drugs")
    assert full.status_code == 200
    payload = full.json()
    assert payload["total"] == 2
    assert {row["catalog_drug_id"] for row in payload["items"]} == {
        "catalog_alpha",
        "catalog_beta",
    }

    filtered = client.get("/api/v1/drugs?query=ALPHA")
    assert filtered.status_code == 200
    assert [row["catalog_drug_id"] for row in filtered.json()["items"]] == ["catalog_alpha"]

    paged = client.get("/api/v1/drugs?limit=1&offset=0")
    assert paged.status_code == 200
    assert len(paged.json()["items"]) == 1
    assert paged.json()["total"] == 2

    _assert_error_body(client.get("/api/v1/drugs?limit=101"), 422)


def test_get_drugs_pins_explicit_dataset_version(clean_all, tmp_path):
    version = _publish_pair(clean_all, tmp_path)["version"]
    client, _ = _login()

    explicit = client.get(f"/api/v1/drugs?dataset_version={version}")
    assert explicit.status_code == 200
    payload = explicit.json()
    assert payload["dataset_version"] == version
    assert payload["catalog_version"] == "test-s19-http/1"
    assert payload["total"] == 2

    _assert_error_body(client.get("/api/v1/drugs?dataset_version=bad-version"), 404)
    _assert_error_body(client.get("/api/v1/drugs?dataset_version=ddi-000000000000"), 404)


def test_offline_execution_with_network_disabled(clean_all, tmp_path, monkeypatch):
    import socket

    version = _publish_pair(clean_all, tmp_path)["version"]
    client, csrf = _login()

    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if host in ("localhost", "127.0.0.1", None):
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError("external network is disabled for deterministic checking")

    def _guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in ("localhost", "127.0.0.1"):
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError("external network is disabled for deterministic checking")

    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)
    response = client.post(
        "/api/v1/ddi/check",
        json={
            "medications": [
                {"catalog_drug_id": "catalog_alpha"},
                {"catalog_drug_id": "catalog_beta"},
            ],
            "dataset_version": version,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["pairs"][0]["status"] == "interaction_found"
    drugs = client.get("/api/v1/drugs?query=beta")
    assert drugs.status_code == 200


def test_health_and_ready_preserved(clean_all):
    client = TestClient(app)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "x-insight"}
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["schema_version"] == "0007"
