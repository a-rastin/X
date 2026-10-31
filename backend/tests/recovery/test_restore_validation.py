"""Validate and stage restores without touching live state (S55, seams T10/T1).

Through public authenticated HTTP on real PostgreSQL + real S54 backup
archives via public HTTP (``POST /api/v1/backups`` + download), no mocks of
owned modules, no private-helper mirror tests, no DB-row asserts for
behavior. Synthetic two-node network only; no clinical content. Expected
literals are worked fixtures, never implementation output.

Covers tasks.md S55 §§1-4 (FR-41, NFR-02/04; plan.md §10.3 steps 1-3):

- §1: valid app-generated archive stages into isolated staging storage,
  reports backup date/schema/content/checksums/impact, returns a stable
  immutable confirmation digest (GET matches POST).
- §2: traversal / absolute path / symlink / oversized expansion (monkeypatched
  MAX_*), corrupt hash, unsupported schema, missing artifact, malformed DB
  content all fail BEFORE any live mutation.
- §3: uploaded bytes are untrusted and never executed as SQL/superuser nor via
  COPY/host-file/program access (asserted via observable behavior: hostile SQL
  never changes live reads); resource limits enforced; singleton admin,
  patient/encounter/reference and model/effective-artifact consistency checked.
- §4: failed staging leaves live patients/login/jobs unchanged (real live-side
  HTTP read checks); replace-upload/expiry invalidates prior confirmations
  (expired flag). No restore-over-live route exists (no POST /restores/commit).
- Gates/audit: admin-only (anon 401 / physician 403 / admin 200),
  private,no-store on reads, safe audit (restores.validate.success/failed,
  denied leaves no success), malformed/unknown IDs.

Isolation: per-test BACKUP_STAGING_DIR + RESTORE_STAGING_DIR under tmp_path
(env overrides, no global state); truncate covers restore_jobs (+ S54 tables)
so sequential suites sharing one test DB stay green. App-schema literals accept
0017 through 0021 here (0018 only adds the staging table; clinical rows identical) —
stale-literal updates for the current head are in this file and
test_backup.py; test_contracts drift remains a separate carryover.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid as uuid_module
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service

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

# 0018 adds only restore_jobs (staging metadata, excluded from portable
# archives like sessions); clinical rows are identical to 0017. 0019, 0020,
# and 0021 are also accepted (a fresh backup reports the current head).
SUPPORTED_APP_SCHEMAS = frozenset(
    {"0017", "0018", "0019", "0020", "0021", "0022", "0023", "0024", "0025"}
)


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
                "ddi_concepts, ddi_interaction_evidence, model_networks, "
                "model_network_versions, model_workflow_pointers, backup_jobs, "
                "restore_jobs, provider_settings CASCADE"
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
def clean_registry(migrated_test_engine, monkeypatch, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    restores = tmp_path / "restores"
    restores.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BACKUP_STAGING_DIR", str(backups))
    monkeypatch.setenv("RESTORE_STAGING_DIR", str(restores))
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


def _admin_client(clean_registry, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    return admin, login.json()["csrf_token"]


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


def _login_physician(clean_registry, monkeypatch, username: str):
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pw123", "role": "physician"},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def _register_patient(client, csrf, identifier: str) -> dict[str, Any]:
    made = client.post(
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
    assert made.status_code == 201, made.text
    return made.json()


def _import_network(admin, admin_csrf, name: str) -> dict[str, Any]:
    response = admin.post(
        "/api/v1/networks",
        json={"name": name, "xml_text": TWO_NODE_XML.decode("utf-8")},
        headers=_auth_headers(admin_csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_valid_blob(admin, admin_csrf, key: str | None = None) -> tuple[bytes, str]:
    headers = dict(_auth_headers(admin_csrf))
    if key is not None:
        headers["Idempotency-Key"] = key
    created = admin.post("/api/v1/backups", headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "succeeded", created.json()
    backup_id = created.json()["id"]
    downloaded = admin.get(f"/api/v1/backups/{backup_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    return bytes(downloaded.content), backup_id


def _setup_valid_archive(clean_registry, monkeypatch, tmp_path, tag: str) -> dict[str, Any]:
    """Minimal real S54 archive via public HTTP: physician + patient + network."""
    _new_physician(clean_registry, monkeypatch, f"dr_s55_{tag}")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, f"dr_s55_{tag}")
    _register_patient(physician, physician_csrf, "0012345678")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _import_network(admin, admin_csrf, f"s55-net-{tag}")
    blob, backup_id = _make_valid_blob(
        admin, admin_csrf, key=f"s55-{tag}-{uuid_module.uuid4().hex[:8]}"
    )
    return {
        "admin": admin,
        "admin_csrf": admin_csrf,
        "physician": physician,
        "physician_csrf": physician_csrf,
        "blob": blob,
        "backup_id": backup_id,
    }


def _post_restore(admin, admin_csrf, blob: bytes):
    return admin.post(
        "/api/v1/restores/validate",
        content=blob,
        headers={**_auth_headers(admin_csrf), "Content-Type": "application/zip"},
    )


def _read_entries(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob), "r") as handle:
        return {name: handle.read(name) for name in handle.namelist()}


def _rezip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name in sorted(entries):
            out.writestr(name, entries[name])
    return buffer.getvalue()


def _mutate_db(blob: bytes, fn) -> bytes:
    """Mutate database.json in place (row counts unchanged) + refresh its artifact hash."""
    entries = _read_entries(blob)
    manifest = json.loads(entries["manifest.json"].decode("utf-8"))
    database = json.loads(entries["database.json"].decode("utf-8"))
    fn(database)
    entries["database.json"] = json.dumps(database, sort_keys=True).encode("utf-8")
    manifest["artifacts"]["database.json"] = hashlib.sha256(entries["database.json"]).hexdigest()
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    return _rezip(entries)


def _mutate_manifest(blob: bytes, fn) -> bytes:
    entries = _read_entries(blob)
    manifest = json.loads(entries["manifest.json"].decode("utf-8"))
    fn(manifest)
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    return _rezip(entries)


def _assert_hex64(value: Any) -> None:
    assert isinstance(value, str) and len(value) == 64, value
    int(value, 16)


# --- S55 §1: valid archive stages with report + stable digest ---


def test_valid_archive_stages_with_report_and_stable_digest(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S55 §1: staging is isolated, reported, and digest-stable across GET/POST."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "valid")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    posted = _post_restore(admin, admin_csrf, blob)
    assert posted.status_code == 201, posted.text
    assert posted.headers["Cache-Control"] == "private, no-store"
    job = posted.json()
    assert job["status"] == "succeeded", job
    assert job["expired"] is False
    _assert_hex64(job["confirmation_digest"])
    assert job["archive_sha256"] == hashlib.sha256(blob).hexdigest()
    assert job["archive_bytes"] == len(blob)

    report = job["report"]
    assert report["backup_id"] == job["backup_id"] == fixture["backup_id"]
    assert report["backup_created_at"]
    contracts.parse_utc(report["backup_created_at"])
    assert report["manifest_schema"] == "backup-v1"
    assert report["app_schema_version"] in SUPPORTED_APP_SCHEMAS
    assert job["app_schema_version"] in SUPPORTED_APP_SCHEMAS
    assert report["archive_sha256"] == job["archive_sha256"]
    assert report["archive_bytes"] == len(blob)
    assert isinstance(report["artifacts"], dict) and report["artifacts"]["database.json"]
    assert isinstance(report["inventory"], dict) and len(report["inventory"]) >= 30
    assert "sessions" not in report["inventory"]
    assert report["staged_counts"]["patients"] >= 1
    assert report["live_counts"]["patients"] >= 1
    assert report["replacement_impact"]["patients"]["staged"] >= 1
    assert "re-entry" in report["key_consequence"] or "re-entry" in report["key_consequence"]
    assert report["confirmation_digest"] == job["confirmation_digest"]
    assert report["commit_available"] is False
    assert "commit" in report["commit_note"].lower() or "S56" in report["commit_note"]

    # Stable immutable digest: GET matches POST exactly.
    fetched = admin.get(f"/api/v1/restores/{job['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["Cache-Control"] == "private, no-store"
    assert fetched.json()["confirmation_digest"] == job["confirmation_digest"]
    assert fetched.json()["report"] == job["report"]

    # Isolated staging: per-job zip under RESTORE_STAGING_DIR, mode 0600, never live DB.
    staged = Path(os.environ["RESTORE_STAGING_DIR"]) / f"{job['id']}.zip"
    assert staged.is_file()
    assert staged.read_bytes() == blob
    assert (staged.stat().st_mode & 0o777) == 0o600

    # Public shape never exposes a path or an enabled Commit.
    assert "archive_path" not in job
    dumped = json.dumps(job).lower()
    assert "archive_path" not in dumped
    assert "commit_url" not in dumped and "commit_endpoint" not in dumped


# --- S55 §2: negatives fail before live mutation ---


def test_rejects_traversal(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: traversal entries fail staging; live reads unchanged."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "traversal")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    entries["../evil.txt"] = b"escape"
    hostile = _rezip(entries)

    before = fixture["physician"].get("/api/v1/patients").json()["total"]
    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert job["error_code"] == "RESTORE_VALIDATION_FAILED"
    assert "traversal" in (job["error_message"] or "").lower()
    assert fixture["physician"].get("/api/v1/patients").json()["total"] == before


def test_rejects_absolute_path(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: absolute-path entries fail staging."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "abspath")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    entries["/tmp/evil.txt"] = b"absolute"
    hostile = _rezip(entries)

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "absolute" in (job["error_message"] or "").lower()


def test_rejects_symlink(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: symlink entries fail staging before any mutation."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "symlink")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name in sorted(entries):
            out.writestr(name, entries[name])
        link = zipfile.ZipInfo("evil-link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        out.writestr(link, "networks/manifest.json")
    hostile = buffer.getvalue()

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "symlink" in (job["error_message"] or "").lower()


def test_rejects_oversized_expansion(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: zip-bomb guard fires via monkeypatched MAX_EXPANDED_BYTES."""
    from x_insight.operations import restore as restore_module

    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "oversize")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    monkeypatch.setattr(restore_module, "MAX_EXPANDED_BYTES", 64)
    posted = _post_restore(admin, admin_csrf, blob)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert (
        "expand" in (job["error_message"] or "").lower()
        or "bound" in (job["error_message"] or "").lower()
    )


def test_rejects_corrupt_hash(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: tampered bytes without manifest refresh fail the hash check."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "corrupt")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    database = json.loads(entries["database.json"].decode("utf-8"))
    database["tables"]["patients"][0]["given_name"] = "MUTATED-NAME"
    entries["database.json"] = json.dumps(database, sort_keys=True).encode("utf-8")
    hostile = _rezip(entries)  # manifest artifact hash now stale on purpose

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "hash mismatch" in (job["error_message"] or "").lower()


def test_rejects_unsupported_schema(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: unknown manifest schema and unknown app schema both fail."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "schema")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    bad_envelope = _mutate_manifest(blob, lambda m: m.update({"schema_version": "backup-v0"}))
    first = _post_restore(admin, admin_csrf, bad_envelope)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "failed", first.json()
    assert "schema" in (first.json()["error_message"] or "").lower()

    bad_app = _mutate_manifest(blob, lambda m: m.update({"app_schema_version": "9999"}))
    second = _post_restore(admin, admin_csrf, bad_app)
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "failed", second.json()
    assert "app schema" in (second.json()["error_message"] or "").lower()


def test_rejects_missing_artifact(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: a dropped required entry fails before live mutation."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "missing")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    del entries["policies.json"]
    hostile = _rezip(entries)

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "missing required entry" in (job["error_message"] or "").lower()


def test_rejects_malformed_db_content(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §2: malformed patient content fails content checks (hash refreshed)."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "malformed")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    def _break(database: dict[str, Any]) -> None:
        database["tables"]["patients"][0]["identifier"] = "BAD-ID"

    hostile = _mutate_db(blob, _break)
    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "malformed" in (job["error_message"] or "").lower()


# --- S55 §3: restricted handling + singleton/refs/model checks ---


def test_rejects_singleton_violation(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §3: renamed/deactivated/duplicated admin fails the singleton check."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "singleton")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    def _break(database: dict[str, Any]) -> None:
        for row in database["tables"]["users"]:
            if row.get("role") == "admin":
                row["username"] = "root"

    hostile = _mutate_db(blob, _break)
    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "singleton admin" in (job["error_message"] or "").lower()


def test_rejects_broken_references(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §3: dangling encounter→patient links fail the reference check."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "refs")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    def _break(database: dict[str, Any]) -> None:
        database["tables"]["encounters"][0]["patient_id"] = str(uuid_module.uuid4())

    hostile = _mutate_db(blob, _break)
    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "references broken" in (job["error_message"] or "").lower()


def test_rejects_model_effective_violation(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §3: network bytes drifting from the staged row fail model checks."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "model")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    entries = _read_entries(blob)
    manifest = json.loads(entries["manifest.json"].decode("utf-8"))
    network_names = sorted(n for n in entries if n.startswith("networks/"))
    assert network_names, "valid archive must carry at least one network export"
    target = network_names[0]
    tampered = entries[target].replace(b"TwoNode", b"TwoNoDe")
    assert tampered != entries[target]
    entries[target] = tampered
    # Refresh only the file hash so the failure lands in model consistency,
    # not in the earlier hash-mismatch gate.
    manifest["artifacts"][target] = hashlib.sha256(tampered).hexdigest()
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    hostile = _rezip(entries)

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    assert job["status"] == "failed", job
    assert "model consistency broken" in (job["error_message"] or "").lower()


def test_untrusted_content_never_executes_and_limits_enforced(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S55 §3: hostile SQL/COPY/host paths never run; bounds enforced observably."""
    from x_insight.operations import restore as restore_module

    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "untrusted")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    physician = fixture["physician"]

    entries = _read_entries(blob)
    entries["evil.sql"] = (
        b"COPY patients TO PROGRAM 'id'; DROP TABLE patients; "
        b"COPY (SELECT 1) TO '/tmp/pwned'; SELECT pg_read_file('/etc/passwd');"
    )
    hostile = _rezip(entries)
    before_total = physician.get("/api/v1/patients").json()["total"]

    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    job = posted.json()
    # Extra untrusted entries are inert staging bytes, never executed: live
    # reads are unchanged and no host content is echoed back.
    assert physician.get("/api/v1/patients").json()["total"] == before_total
    assert physician.get("/api/v1/patients").status_code == 200
    assert "/etc/passwd" not in json.dumps(job)
    assert "root:" not in json.dumps(job)

    # Resource limits are observable: tiny per-file bound fails a real archive.
    monkeypatch.setattr(restore_module, "MAX_FILE_BYTES", 10)
    limited = _post_restore(admin, admin_csrf, blob)
    assert limited.status_code == 201, limited.text
    assert limited.json()["status"] == "failed", limited.json()
    assert "bound" in (limited.json()["error_message"] or "").lower()

    # Entry-count bound likewise fails before any live mutation.
    monkeypatch.setattr(restore_module, "MAX_FILE_BYTES", 100 * 1024 * 1024)
    monkeypatch.setattr(restore_module, "MAX_ZIP_ENTRIES", 1)
    crowded = _post_restore(admin, admin_csrf, blob)
    assert crowded.status_code == 201, crowded.text
    assert crowded.json()["status"] == "failed", crowded.json()

    # Request-size bound is enforced at the router (413, no job minted).
    monkeypatch.setattr(restore_module, "MAX_ZIP_ENTRIES", 4096)
    monkeypatch.setattr(restore_module, "MAX_ARCHIVE_BYTES", 10)
    too_big = _post_restore(admin, admin_csrf, blob)
    assert too_big.status_code == 413, too_big.text
    assert physician.get("/api/v1/patients").json()["total"] == before_total


# --- S55 §4: live unchanged on failure; replace/expiry invalidates ---


def test_failed_staging_leaves_live_unchanged(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §4: failed staging keeps patients/login/jobs readable and intact."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "liveintact")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]
    physician = fixture["physician"]

    patient_before = physician.get("/api/v1/patients").json()
    me_before = admin.get("/api/v1/me").json()
    backup_before = admin.get(f"/api/v1/backups/{fixture['backup_id']}").json()
    assert admin.get(f"/api/v1/backups/{fixture['backup_id']}/download").status_code == 200

    entries = _read_entries(blob)
    entries["../evil.txt"] = b"escape"
    hostile = _rezip(entries)
    posted = _post_restore(admin, admin_csrf, hostile)
    assert posted.status_code == 201, posted.text
    failed = posted.json()
    assert failed["status"] == "failed", failed
    # Failed staging stages no archive bytes into the isolated dir.
    assert not (Path(os.environ["RESTORE_STAGING_DIR"]) / f"{failed['id']}.zip").exists()

    # Real live-side read checks (public HTTP, never private helpers).
    assert physician.get("/api/v1/patients").json() == patient_before
    assert admin.get("/api/v1/me").json() == me_before
    assert admin.get(f"/api/v1/backups/{fixture['backup_id']}").json() == backup_before
    assert admin.get(f"/api/v1/backups/{fixture['backup_id']}/download").status_code == 200
    # Login still works after the failure.
    fresh = TestClient(app)
    relogin = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_s55_liveintact", "password": "pw123", "role": "physician"},
    )
    assert relogin.status_code == 200, relogin.text
    admin_relogin = TestClient(app)
    assert (
        admin_relogin.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin", "role": "admin"},
        ).status_code
        == 200
    )


def test_replace_upload_and_expiry_invalidate_prior(clean_registry, monkeypatch, tmp_path) -> None:
    """S55 §4: a replacing upload expires the prior confirmation (expired flag)."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "expiry")
    admin, admin_csrf = fixture["admin"], fixture["admin_csrf"]

    first = _post_restore(admin, admin_csrf, fixture["blob"])
    assert first.status_code == 201, first.text
    job1 = first.json()
    assert job1["status"] == "succeeded", job1
    assert job1["expired"] is False
    assert job1["expires_at"]
    _assert_hex64(job1["confirmation_digest"])

    second_blob, _ = _make_valid_blob(
        admin, admin_csrf, key=f"s55-expiry-2-{uuid_module.uuid4().hex[:8]}"
    )
    second = _post_restore(admin, admin_csrf, second_blob)
    assert second.status_code == 201, second.text
    job2 = second.json()
    assert job2["status"] == "succeeded", job2
    assert job2["id"] != job1["id"]
    assert job2["expired"] is False

    reread = admin.get(f"/api/v1/restores/{job1['id']}").json()
    assert reread["id"] == job1["id"]
    # The digest value itself is immutable, but the confirmation is stale.
    assert reread["confirmation_digest"] == job1["confirmation_digest"]
    assert reread["expired"] is True


def test_failed_replacement_leaves_prior_confirmation_usable(
    clean_registry, monkeypatch, tmp_path
) -> None:
    """S55 §4: a failed replacement keeps the prior confirmation usable."""
    fixture = _setup_valid_archive(clean_registry, monkeypatch, tmp_path, "expiryfailed")
    admin, admin_csrf, blob = fixture["admin"], fixture["admin_csrf"], fixture["blob"]

    first = _post_restore(admin, admin_csrf, blob)
    assert first.status_code == 201, first.text
    job1 = first.json()
    assert job1["status"] == "succeeded", job1
    assert job1["expired"] is False
    _assert_hex64(job1["confirmation_digest"])

    entries = _read_entries(blob)
    database = json.loads(entries["database.json"].decode("utf-8"))
    database["tables"]["patients"][0]["given_name"] = "MUTATED-NAME"
    entries["database.json"] = json.dumps(database, sort_keys=True).encode("utf-8")
    corrupt = _rezip(entries)  # manifest artifact hash now stale on purpose

    second = _post_restore(admin, admin_csrf, corrupt)
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "failed", second.json()

    reread = admin.get(f"/api/v1/restores/{job1['id']}").json()
    assert reread["id"] == job1["id"]
    assert reread["expired"] is False
    assert reread["confirmation_digest"] == job1["confirmation_digest"]


# --- Gates, audit, IDs, no restore-over-live ---


def test_admin_gates_and_private_headers(clean_registry, monkeypatch, tmp_path) -> None:
    """Admin-only staging gates + private,no-store on the progress reads."""
    _new_physician(clean_registry, monkeypatch, "dr_s55_gate")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s55_gate")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    anonymous = TestClient(app)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    _register_patient(physician, physician_csrf, "0012345678")
    _import_network(admin, admin_csrf, "s55-gate-net")
    blob, _ = _make_valid_blob(admin, admin_csrf, key="s55-gate-1")

    assert anonymous.post("/api/v1/restores/validate", content=blob).status_code == 401
    denied = physician.post(
        "/api/v1/restores/validate",
        content=blob,
        headers={**_auth_headers(physician_csrf), "Content-Type": "application/zip"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"

    created = _post_restore(admin, admin_csrf, blob)
    assert created.status_code == 201, created.text
    restore_id = created.json()["id"]

    assert anonymous.get(f"/api/v1/restores/{restore_id}").status_code == 401
    assert physician.get(f"/api/v1/restores/{restore_id}").status_code == 403
    allowed = admin.get(f"/api/v1/restores/{restore_id}")
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["Cache-Control"] == "private, no-store"

    forged = physician.post(
        "/api/v1/restores/validate",
        content=blob,
        headers={"X-CSRF-Token": "wrong", "Content-Type": "application/zip"},
    )
    assert forged.status_code == 403, forged.text


def test_safe_audit_and_malformed_ids_and_no_commit(clean_registry, monkeypatch, tmp_path) -> None:
    """Safe validate audit, denied→no success, malformed/unknown IDs, no commit."""
    _new_physician(clean_registry, monkeypatch, "dr_s55_audit")
    physician, physician_csrf = _login_physician(clean_registry, monkeypatch, "dr_s55_audit")
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin, admin_csrf = _admin_client(clean_registry, monkeypatch)
    admin_id = admin.get("/api/v1/me").json()["user"]["id"]
    _register_patient(physician, physician_csrf, "0012345678")
    _import_network(admin, admin_csrf, "s55-audit-net")
    blob, _ = _make_valid_blob(admin, admin_csrf, key="s55-audit-1")

    before_success = admin.get(
        "/api/v1/audit-events", params={"operation": "restores.validate.success"}
    ).json()["total"]
    created = _post_restore(admin, admin_csrf, blob)
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "succeeded"

    events = admin.get(
        "/api/v1/audit-events", params={"operation": "restores.validate.success"}
    ).json()
    assert events["total"] == before_success + 1
    event = next(i for i in events["items"] if i["details"].get("restore_id") == job["id"])
    assert event["actor_id"] == admin_id
    assert event["details"]["archive_sha256"] == job["archive_sha256"]
    assert event["details"]["confirmation_digest"] == job["confirmation_digest"]
    lowered = json.dumps(event).lower()
    for token in ("password", "passwd", "secret", "private_key", "csrf_token"):
        assert token not in lowered, token

    entries = _read_entries(blob)
    entries["../evil.txt"] = b"escape"
    failed_post = _post_restore(admin, admin_csrf, _rezip(entries))
    assert failed_post.json()["status"] == "failed"
    failed_id = failed_post.json()["id"]
    failed_events = admin.get(
        "/api/v1/audit-events", params={"operation": "restores.validate.failed"}
    ).json()
    assert failed_events["total"] >= 1
    assert any(i["details"].get("restore_id") == failed_id for i in failed_events["items"])

    before = {
        op: admin.get("/api/v1/audit-events", params={"operation": op}).json()["total"]
        for op in ("restores.validate.success", "restores.validate.failed")
    }
    denied = physician.post(
        "/api/v1/restores/validate",
        content=blob,
        headers={**_auth_headers(physician_csrf), "Content-Type": "application/zip"},
    )
    assert denied.status_code == 403
    assert physician.get(f"/api/v1/restores/{job['id']}").status_code == 403
    after = {
        op: admin.get("/api/v1/audit-events", params={"operation": op}).json()["total"]
        for op in ("restores.validate.success", "restores.validate.failed")
    }
    assert after == before

    missing = str(uuid_module.uuid4())
    assert admin.get(f"/api/v1/restores/{missing}").status_code == 404
    malformed = admin.get("/api/v1/restores/not-a-uuid")
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["code"] == "VALIDATION_FAILED"
    empty = admin.post(
        "/api/v1/restores/validate",
        content=b"",
        headers={**_auth_headers(admin_csrf), "Content-Type": "application/zip"},
    )
    assert empty.status_code == 422, empty.text

    # No restore-over-live: S55 must not offer any commit/switch/rollback route.
    assert admin.post("/api/v1/restores/commit", headers=_auth_headers(admin_csrf)).status_code in (
        404,
        405,
    )
    assert "commit" not in json.dumps(job).lower() or "commit_available" in json.dumps(job)
    assert job["report"]["commit_available"] is False


def test_provider_archive_needs_original_managed_key_before_restore(
    clean_registry, monkeypatch, tmp_path
):
    """Public backup/restore never transfers a deployment key or rotates ciphertext."""
    from x_insight.reasoning.provider_key import initialize_key

    monkeypatch.delenv("X_INSIGHT_PROVIDER_DATA_KEY", raising=False)
    monkeypatch.delenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", raising=False)
    key_path = tmp_path / "secrets" / "provider-data.key"
    monkeypatch.setenv("X_INSIGHT_PROVIDER_DATA_KEY_FILE", str(key_path))
    initialize_key(clean_registry)
    original_key = key_path.read_bytes()
    admin, csrf = _admin_client(clean_registry, monkeypatch)
    saved = admin.put(
        "/api/v1/api-settings",
        headers=_auth_headers(csrf),
        json={
            "base_url": "https://api.openai.com/v1/chat/completions",
            "model": "synthetic-model",
            "api_key": "synthetic-restored-api-key",
        },
    )
    assert saved.status_code == 200, saved.text
    blob, _ = _make_valid_blob(admin, csrf)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert not any(".key" in name or "secrets" in name for name in archive.namelist())
        assert all(original_key not in archive.read(name) for name in archive.namelist())
    key_path.unlink()
    missing = _post_restore(admin, csrf, blob)
    assert missing.status_code == 201, missing.text
    assert missing.json()["status"] == "failed"
    assert "original deployment key" in missing.text
    assert not key_path.exists()
    key_path.write_bytes(b"x" * 32)
    key_path.chmod(0o600)
    wrong = _post_restore(admin, csrf, blob)
    assert wrong.status_code == 201, wrong.text
    assert wrong.json()["status"] == "failed"
    key_path.write_bytes(original_key)
    recovered = _post_restore(admin, csrf, blob)
    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["status"] == "succeeded"
    # Recheck at commit too: a key lost after staging cannot destroy live settings.
    key_path.unlink()
    staged = recovered.json()
    committed = admin.post(
        f"/api/v1/restores/{staged['id']}/commit",
        headers=_auth_headers(csrf),
        json={"confirmation_digest": staged["confirmation_digest"]},
    )
    assert committed.status_code == 409, committed.text
    assert admin.get("/api/v1/api-settings").json()["api_key"] == "configured"


def _provider_approval_archive(clean_registry, monkeypatch):
    monkeypatch.delenv("X_INSIGHT_PROVIDER_ALLOWED_HOSTS", raising=False)
    admin, csrf = _admin_client(clean_registry, monkeypatch)
    approvals = [(True, False), (False, True), (False, False)]
    revisions = []
    for local, proxy in approvals:
        saved = admin.put(
            "/api/v1/api-settings",
            headers=_auth_headers(csrf),
            json={
                "base_url": "https://provider.example/custom",
                "model": "synthetic",
                "secret_action": "clear",
                "local_endpoint_approved": local,
                "trusted_proxy_approved": proxy,
            },
        )
        assert saved.status_code == 200, saved.text
        revisions.append(saved.json()["revision"])
    blob, _ = _make_valid_blob(admin, csrf)
    return admin, csrf, blob, revisions, approvals


def test_restore_rejects_ambiguous_provider_approvals(clean_registry, monkeypatch):
    admin, csrf, blob, _, _ = _provider_approval_archive(clean_registry, monkeypatch)
    for field in ("local_endpoint_approved", "trusted_proxy_approved"):
        for value in ("false", "true", 0, 1, None, [], {}):
            hostile = _mutate_db(
                blob,
                lambda database: database["tables"]["provider_settings"][0].update({field: value}),
            )
            staged = _post_restore(admin, csrf, hostile)
            assert staged.status_code == 201, staged.text
            assert staged.json()["status"] == "failed", (field, value, staged.text)
            assert "approval" in staged.json()["error_message"].lower()
        missing = _mutate_db(
            blob, lambda database: database["tables"]["provider_settings"][0].pop(field)
        )
        assert _post_restore(admin, csrf, missing).json()["status"] == "failed"


@pytest.mark.parametrize("legacy", [False, True])
def test_restore_roundtrips_boolean_provider_approvals_and_legacy_defaults(
    clean_registry, monkeypatch, legacy
):
    admin, csrf, blob, revisions, approvals = _provider_approval_archive(
        clean_registry, monkeypatch
    )
    if legacy:

        def omit_approvals(database):
            for row in database["tables"]["provider_settings"]:
                row.pop("local_endpoint_approved")
                row.pop("trusted_proxy_approved")

        blob = _mutate_db(blob, omit_approvals)
        blob = _mutate_manifest(blob, lambda manifest: manifest.update(app_schema_version="0023"))
        approvals = [(False, False)] * len(revisions)
    staged = _post_restore(admin, csrf, blob)
    assert staged.status_code == 201, staged.text
    assert staged.json()["status"] == "succeeded", staged.text
    job = staged.json()
    committed = admin.post(
        f"/api/v1/restores/{job['id']}/commit",
        headers=_auth_headers(csrf),
        json={"confirmation_digest": job["confirmation_digest"]},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["commit_status"] == "succeeded", committed.text
    recovered_admin, _ = _admin_client(clean_registry, monkeypatch)
    for revision, (local, proxy) in zip(revisions, approvals, strict=True):
        recovered = recovered_admin.get(f"/api/v1/api-settings/revisions/{revision}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["local_endpoint_approved"] is local
        assert recovered.json()["trusted_proxy_approved"] is proxy


def test_legacy_restore_cannot_smuggle_provider_approvals(clean_registry, monkeypatch):
    admin, csrf, blob, _, _ = _provider_approval_archive(clean_registry, monkeypatch)
    legacy = _mutate_manifest(blob, lambda manifest: manifest.update(app_schema_version="0023"))
    staged = _post_restore(admin, csrf, legacy)
    assert staged.status_code == 201, staged.text
    assert staged.json()["status"] == "failed", staged.text
    assert "cannot grant endpoint approvals" in staged.json()["error_message"]
