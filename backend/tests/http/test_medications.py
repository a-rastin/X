"""S20 medications + DDI into encounter history (T1 backend only; no frontend/e2e).

Slice 1 (red first): drug-only draft storage via S07 autosave + dedicated
GET/POST preview routes (S09-S12 pattern).

Through public HTTP + real PostgreSQL (synthetic DDI releases only; real
corpus stays awaiting_review, 0 approved aliases):
- Author-only save/retrieve preserves the drug-only list across reload/re-login.
- Server rejects free-text unknown-label and dose/unit/route/frequency/status
  forgeries with 422 (extra=forbid, never free text).
- Unknown catalog IDs are 422, never accepted.
- Author-only (403 strangers/admin without content, 401 anonymous,
  404 unknown/discarded), If-Match/ETag 412 stale, missing/malformed 422,
  CSRF 403, per-command idempotency (medications.save) same-key replay and
  same-key changed-body 409, audit without secrets, standard ErrorBody.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service


def concept(identifier, name, kind="ingredient", catalog=None):
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": kind,
        "catalog_drug_id": catalog,
        "source": "synthetic S20 fixture",
    }


def vocabulary(concepts, aliases=None):
    return {"version": "test-s20/1", "concepts": concepts, "aliases": aliases or []}


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
    service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        try:
            connection.execute(text("TRUNCATE notes, encounters, patients"))
        except Exception:
            pass
        try:
            connection.execute(text("TRUNCATE idempotency_records"))
        except Exception:
            pass
        try:
            connection.execute(
                text(
                    "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                    "ddi_source_documents, ddi_dataset_releases"
                )
            )
        except Exception:
            pass
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        try:
            connection.execute(text("TRUNCATE notes, encounters, patients"))
        except Exception:
            pass
        try:
            connection.execute(text("TRUNCATE idempotency_records"))
        except Exception:
            pass
        try:
            connection.execute(
                text(
                    "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                    "ddi_source_documents, ddi_dataset_releases"
                )
            )
        except Exception:
            pass
    service.ensure_default_admin(migrated_test_engine)


def _publish_complete(clean_all, tmp_path, subjects=("Alpha", "Beta"), extra_concepts=()):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    # Alpha<->Beta evidence pair; Gamma catalog exists but has no rows.
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    concepts = [
        concept("alpha", "Alpha", catalog="catalog_alpha"),
        concept("beta", "Beta", catalog="catalog_beta"),
        concept("gamma", "Gamma", catalog="catalog_gamma"),
    ]
    concepts.extend(extra_concepts)
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
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"], response.json()["user"]


def _make_physician(admin_client, admin_csrf, username, password="pw123"):
    created = admin_client.post(
        "/api/v1/physicians",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code == 201, created.text
    return created.json()["user"]


def _physician_client(username, password="pw123"):
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "role": "physician"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return client, body["csrf_token"], body["user"]


def _valid_patient(identifier="0012345678"):
    return {
        "identifier": identifier,
        "given_name": "Anna",
        "family_name": "Novak",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "+43 699 123456",
    }


def _create_draft(client, csrf, identifier="0012345678"):
    created = client.post(
        "/api/v1/patients",
        json=_valid_patient(identifier),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _get_meds(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/medications")


def _post_meds(client, csrf, encounter_id, medications, revision, **extra):
    headers = {"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(revision)}
    if "key" in extra and extra["key"] is not None:
        headers["Idempotency-Key"] = extra["key"]
    body: dict = {"medications": medications}
    if "dataset_version" in extra:
        body["dataset_version"] = extra["dataset_version"]
    if "reconciliation" in extra:
        body["reconciliation"] = extra["reconciliation"]
    # Allow forged top-level keys for 422 probing.
    for key in ("dose", "unknown_label"):
        if key in extra:
            body[key] = extra[key]
    return client.post(f"/api/v1/encounters/{encounter_id}/medications", json=body, headers=headers)


def _setup_draft_with_release(clean_all, tmp_path, username, identifier):
    version = _publish_complete(clean_all, tmp_path)["version"]
    admin_client, admin_csrf, _ = _login()
    _make_physician(admin_client, admin_csrf, username)
    client, csrf, user = _physician_client(username)
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"], version


# --- Slice 1: drug-only storage ---


def test_medications_empty_initially(clean_all, tmp_path) -> None:
    client, _, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_empty", "0012350001"
    )
    response = _get_meds(client, encounter_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entries"] == []
    assert body["revision"] == 1
    assert response.headers["etag"] == '"1"'
    assert body["dataset_version"] is None or isinstance(body["dataset_version"], (str, type(None)))
    assert version.startswith("ddi-")


def test_medications_save_persists_and_resumes(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_save", "0012350002"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_gamma"}]
    saved = _post_meds(client, csrf, encounter_id, meds, 1)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert [entry["catalog_drug_id"] for entry in body["entries"]] == [
        "catalog_alpha",
        "catalog_gamma",
    ]
    assert body["revision"] == 2
    assert saved.headers["etag"] == '"2"'
    assert body["dataset_version"] == version

    reread = _get_meds(client, encounter_id).json()
    assert [entry["catalog_drug_id"] for entry in reread["entries"]] == [
        "catalog_alpha",
        "catalog_gamma",
    ]
    assert reread["revision"] == 2
    # Shared autosave body carries the same section.
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["revision"] == 2
    assert [
        entry["catalog_drug_id"] for entry in draft["draft_data"]["medications"]["entries"]
    ] == [
        "catalog_alpha",
        "catalog_gamma",
    ]

    # Resume after re-login preserves the list.
    relogin = TestClient(app)
    login = relogin.post(
        "/api/v1/auth/login",
        json={"username": "dr_med_save", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = relogin.get(f"/api/v1/encounters/{encounter_id}/medications").json()
    assert [entry["catalog_drug_id"] for entry in resumed["entries"]] == [
        "catalog_alpha",
        "catalog_gamma",
    ]


def test_medications_rejects_forged_regimen_and_free_text(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_forge", "0012350003"
    )
    headers = {"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)}
    for forged in (
        [{"catalog_drug_id": "catalog_alpha", "dose": "5mg"}],
        [{"catalog_drug_id": "catalog_alpha", "unit": "mg"}],
        [{"catalog_drug_id": "catalog_alpha", "route": "oral"}],
        [{"catalog_drug_id": "catalog_alpha", "frequency": "daily"}],
        [{"catalog_drug_id": "catalog_alpha", "status": "active"}],
        [{"unknown_label": "Mystery Entity"}],
    ):
        response = client.post(
            f"/api/v1/encounters/{encounter_id}/medications",
            json={"medications": forged, "dataset_version": version},
            headers=headers,
        )
        assert response.status_code == 422, (forged, response.text)
    # Top-level forgeries are also rejected.
    for body in (
        {"medications": [{"catalog_drug_id": "catalog_alpha"}], "dose": "5mg"},
        {"medications": [{"catalog_drug_id": "catalog_alpha"}], "unknown_label": "x"},
    ):
        response = client.post(
            f"/api/v1/encounters/{encounter_id}/medications",
            json={**body, "dataset_version": version},
            headers=headers,
        )
        assert response.status_code == 422, (body, response.text)
    assert _get_meds(client, encounter_id).json()["revision"] == 1


def test_medications_rejects_unknown_catalog(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_unknown", "0012350004"
    )
    response = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_unknown"}], 1)
    assert response.status_code == 422, response.text
    assert _get_meds(client, encounter_id).json()["entries"] == []


def test_medications_author_only_and_auth(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_auth", "0012350005"
    )
    admin_client, admin_csrf, _ = _login()
    stranger_name = "dr_med_stranger"
    _make_physician(admin_client, admin_csrf, stranger_name)
    stranger, _, _ = _physician_client(stranger_name)
    assert _get_meds(stranger, encounter_id).status_code == 403
    assert _get_meds(admin_client, encounter_id).status_code == 403
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/medications").status_code == 401
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/ddi-report").status_code == 401
    missing = uuid.uuid4()
    assert client.get(f"/api/v1/encounters/{missing}/medications").status_code == 404
    assert client.get(f"/api/v1/encounters/{missing}/ddi-report").status_code == 404
    # Discarded reads as 404 for the author as well.
    discarded = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)},
    )
    assert discarded.status_code == 200, discarded.text
    assert _get_meds(client, encounter_id).status_code == 404
    assert client.get(f"/api/v1/encounters/{encounter_id}/ddi-report").status_code == 404


def test_medications_stale_412_and_if_match_422(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_rev", "0012350006"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}]
    stale = _post_meds(client, csrf, encounter_id, meds, 99)
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    for raw in (None, "*", "not-a-revision"):
        headers = {"X-CSRF-Token": csrf}
        if raw is not None:
            headers["If-Match"] = raw
        denied = client.post(
            f"/api/v1/encounters/{encounter_id}/medications",
            json={"medications": meds},
            headers=headers,
        )
        assert denied.status_code == 422, (raw, denied.text)
    assert _get_meds(client, encounter_id).json()["revision"] == 1


def test_medications_csrf_403(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_csrf", "0012350007"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}]
    no_csrf = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": meds},
        headers={"If-Match": contracts.format_etag(1)},
    )
    assert no_csrf.status_code == 403, no_csrf.text
    wrong = client.post(
        f"/api/v1/encounters/{encounter_id}/medications",
        json={"medications": meds},
        headers={"X-CSRF-Token": "wrong", "If-Match": contracts.format_etag(1)},
    )
    assert wrong.status_code == 403, wrong.text
    assert _get_meds(client, encounter_id).json()["revision"] == 1
    assert csrf


def test_medications_idempotency_replay_and_conflict(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_idem", "0012350008"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}]
    first = _post_meds(client, csrf, encounter_id, meds, 1, key="med-key-001")
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 2
    replay = _post_meds(client, csrf, encounter_id, meds, 1, key="med-key-001")
    assert replay.status_code == 200, replay.text
    assert replay.json()["revision"] == 2
    conflict = _post_meds(
        client, csrf, encounter_id, [{"catalog_drug_id": "catalog_beta"}], 2, key="med-key-001"
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _get_meds(client, encounter_id).json()["revision"] == 2


def test_medications_audit_without_secrets(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_audit", "0012350009"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}]
    saved = _post_meds(client, csrf, encounter_id, meds, 1)
    assert saved.status_code == 200, saved.text
    with clean_all.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT operation, actor, details FROM audit_events "
                    "WHERE operation = 'medications.save.success'"
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    details = json.dumps(rows[0]["details"]).lower()
    assert "catalog_alpha" not in details
    assert "password" not in details
    assert "pbkdf2" not in details
    assert "token" not in details
    body = saved.json()
    assert set(body) >= {
        "entries",
        "provenance",
        "reconciliation",
        "dataset_version",
        "revision",
    }
    assert "Traceback" not in saved.text


def test_no_bypass_sign_route_exists(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_med_nosign", "0012350010"
    )
    for method in ("post", "put", "patch"):
        response = getattr(client, method)(
            f"/api/v1/encounters/{encounter_id}/sign",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code in (404, 405), response.text
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = " ".join(openapi.json()["paths"].keys())
    assert "/sign" not in paths


# --- Slice 2: fresh versioned report, stale fencing, reference persistence ---


def _get_report(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/ddi-report")


def test_ddi_report_current_after_save_returns_pinned_shape(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_rep_cur", "0012350021"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}]
    saved = _post_meds(client, csrf, encounter_id, meds, 1)
    assert saved.status_code == 200, saved.text
    stored_fp = saved.json()["medication_fingerprint"]
    assert len(stored_fp) == 64

    response = _get_report(client, encounter_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["report_status"] == "current"
    assert body["reason"] is None
    assert body["report"] is not None
    report = body["report"]
    # S19 pinned shape.
    assert report["dataset_version"] == version
    assert report["catalog_version"] == "test-s20/1"
    assert report["medication_fingerprint"] == stored_fp
    assert len(report["medication_fingerprint"]) == 64
    assert report["generated_at"].endswith("Z")
    assert {row["catalog_drug_id"] for row in report["resolved_medications"]} == {
        "catalog_alpha",
        "catalog_beta",
    }
    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert {pair["drug_a"], pair["drug_b"]} == {"catalog_alpha", "catalog_beta"}
    assert pair["status"] == "interaction_found"
    assert pair["highest_known_severity"] == "serious"
    assert pair["evidence"]
    assert response.headers["etag"] == '"2"'


def test_ddi_report_pending_before_any_save(clean_all, tmp_path) -> None:
    client, _, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_rep_nosave", "0012350022"
    )
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "pending"
    assert body["report"] is None
    assert body["reason"] in ("dataset_not_pinned", "stale_fingerprint", "reconciliation_required")


def test_changed_medications_invalidate_cached_report(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_rep_stale", "0012350023"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}]
    saved = _post_meds(client, csrf, encounter_id, meds, 1)
    assert saved.status_code == 200
    assert _get_report(client, encounter_id).json()["report_status"] == "current"

    # Change the reconciled list through the shared PATCH autosave path.
    patched = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={
            "draft_data": {
                "medications": {
                    "entries": [{"catalog_drug_id": "catalog_alpha"}],
                    "provenance": {},
                    "reconciliation": {"status": "not_required", "baseline_encounter_id": None},
                    "dataset_version": saved.json()["dataset_version"],
                    "catalog_version": saved.json().get("catalog_version"),
                    "medication_fingerprint": saved.json()["medication_fingerprint"],
                    "generated_at": saved.json().get("generated_at"),
                }
            }
        },
        headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(2)},
    )
    assert patched.status_code == 200, patched.text

    stale = _get_report(client, encounter_id).json()
    # Never present the old rows as current: explicit pending with no report.
    assert stale["report_status"] == "pending"
    assert stale["reason"] == "stale_fingerprint"
    assert stale["report"] is None
    assert stale["stored_fingerprint"] == saved.json()["medication_fingerprint"]
    assert stale["medication_fingerprint"] != stale["stored_fingerprint"]

    # A fresh strict save makes the report current again.
    refreshed = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_alpha"}], 3)
    assert refreshed.status_code == 200, refreshed.text
    current = _get_report(client, encounter_id).json()
    assert current["report_status"] == "current"
    assert current["report"]["medication_fingerprint"] == refreshed.json()["medication_fingerprint"]
    assert current["report"]["pairs"] == []


def test_report_reference_persisted_for_run_snapshots(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_rep_ref", "0012350024"
    )
    meds = [{"catalog_drug_id": "catalog_alpha"}]
    saved = _post_meds(client, csrf, encounter_id, meds, 1)
    assert saved.status_code == 200
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    section = draft["draft_data"]["medications"]
    assert section["dataset_version"] == version
    assert section["catalog_version"] == "test-s20/1"
    assert len(section["medication_fingerprint"]) == 64
    assert section["generated_at"].endswith("Z")
    # GET preview carries the same reference.
    preview = _get_meds(client, encounter_id).json()
    assert preview["dataset_version"] == version
    assert preview["medication_fingerprint"] == section["medication_fingerprint"]


# --- Slice 3: coverage/severity display data (backend supplies) ---


def _publish_limited(clean_all, tmp_path):
    from x_insight.ddi import publish

    sources = tmp_path / "sources_lim"
    sources.mkdir(parents=True, exist_ok=True)
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    concepts = [
        concept("alpha", "Alpha", catalog="catalog_alpha"),
        concept("beta", "Beta", catalog="catalog_beta"),
        concept("gamma", "Gamma", catalog="catalog_gamma"),
    ]
    staging = tmp_path / "staging_lim"
    _stage(sources, staging, vocabulary(concepts))
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
    return publish.publish_release(staging, manifest, clean_all)


def _publish_conflict(clean_all, tmp_path):
    from x_insight.ddi import publish

    sources = tmp_path / "sources_conf"
    sources.mkdir(parents=True, exist_ok=True)
    # Alpha lists Beta under Serious; Beta lists Alpha under Minor.
    (sources / "Alpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Beta\nBeta increases the level of Alpha by mechanism. Avoid combination."
        "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    (sources / "Beta.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\n"
        "Monitor Closely (0)\n\nMinor (1)\n\n"
        "Alpha\nAlpha increases the level of Beta by unspecified mechanism."
        "\nWarnings\n"
    )
    concepts = [
        concept("alpha", "Alpha", catalog="catalog_alpha"),
        concept("beta", "Beta", catalog="catalog_beta"),
    ]
    staging = tmp_path / "staging_conf"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest_conf.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, clean_all)


def test_covered_pair_without_rows_is_explicit_complete_basis(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_cov_complete", "0012350031"
    )
    # Gamma has no evidence rows in the complete release.
    saved = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_gamma"}], 1)
    assert saved.status_code == 200, saved.text
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    report = body["report"]
    assert report["pairs"] == []
    # Zero-row report keeps the catalog warning channel visible.
    assert isinstance(report["limitations"], list)
    assert report["coverage_unavailable_medications"] == []

    two = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_gamma"}],
        2,
    )
    assert two.status_code == 200, two.text
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    pair = body["report"]["pairs"][0]
    assert pair["status"] == "covered_no_listed_interaction"
    assert pair["coverage_basis"]["basis"] == "reviewed_complete_coverage"
    assert pair["evidence"] == []


def test_limited_release_marks_uncovered_pairs_and_meds(clean_all, tmp_path) -> None:
    admin_client, admin_csrf, _ = _login()
    # Publish the limited release first, then create the draft against it.
    limited_version = _publish_limited(clean_all, tmp_path)["version"]
    _make_physician(admin_client, admin_csrf, "dr_cov_lim")
    client, csrf, _ = _physician_client("dr_cov_lim")
    created = _create_draft(client, csrf, "0012350032")
    encounter_id = created["draft"]["id"]
    saved = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_gamma"}],
        1,
        dataset_version=limited_version,
    )
    assert saved.status_code == 200, saved.text
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    report = body["report"]
    assert report["dataset_version"] == limited_version
    pair = report["pairs"][0]
    assert pair["status"] == "coverage_unavailable"
    assert pair["coverage_basis"]["basis"] == "limited_coverage_unavailable"
    assert pair["evidence"] == []
    # Every resolved med is listed uncovered in a limited release.
    assert {row["catalog_drug_id"] for row in report["coverage_unavailable_medications"]} == {
        "catalog_alpha",
        "catalog_gamma",
    }
    assert report["limitations"] == ["synthetic limited scope: Gamma.txt excluded"]
    # Single-drug limited report also carries the warning.
    single = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_gamma"}], 2)
    assert single.status_code == 200
    solo = _get_report(client, encounter_id).json()
    assert solo["report_status"] == "current"
    assert solo["report"]["pairs"] == []
    assert len(solo["report"]["coverage_unavailable_medications"]) == 1


def test_severity_evidence_conflicts_verbatim_and_no_absent_claim(clean_all, tmp_path) -> None:
    admin_client, admin_csrf, _ = _login()
    conflict_version = _publish_conflict(clean_all, tmp_path)["version"]
    _make_physician(admin_client, admin_csrf, "dr_cov_conf")
    client, csrf, _ = _physician_client("dr_cov_conf")
    created = _create_draft(client, csrf, "0012350033")
    encounter_id = created["draft"]["id"]
    saved = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}],
        1,
        dataset_version=conflict_version,
    )
    assert saved.status_code == 200, saved.text
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    pair = body["report"]["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert pair["highest_known_severity"] == "serious"
    assert pair["has_unknown_severity"] is False
    assert sorted(pair["conflicts"]) == ["minor", "serious"]
    assert len(pair["evidence"]) == 2
    for row in pair["evidence"]:
        assert row["source_severity"] in ("serious", "minor")
        assert row["raw_text"]
        assert row["source_path"] in ("Alpha.txt", "Beta.txt")
        assert isinstance(row["span_start"], int)
        assert isinstance(row["span_end"], int)
        assert row["checksum"]
        assert row["source_hash"] == row["checksum"]
    blob = json.dumps(body["report"]).lower()
    assert "safe" not in blob
    assert "no interaction" not in blob


def test_ddi_report_works_without_provider_configuration(clean_all, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PROVIDER_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MCP_URL", raising=False)
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_cov_offline", "0012350034"
    )
    saved = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_alpha"}], 1)
    assert saved.status_code == 200
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    assert body["report"]["pairs"] == []


# --- Slice 4: follow-up reconciliation (S14 pattern, test-only baselines) ---


def _free_slot(client, csrf, created):
    encounter_id = created["draft"]["id"]
    discarded = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)},
    )
    assert discarded.status_code == 200, discarded.text
    return created["patient"]["id"]


def test_followup_copies_medications_pending_report_stays_pending(clean_all, tmp_path) -> None:
    version = _publish_complete(clean_all, tmp_path)["version"]
    admin_client, admin_csrf, _ = _login()
    _make_physician(admin_client, admin_csrf, "dr_fu_med")
    client, csrf, user = _physician_client("dr_fu_med")
    created = _create_draft(client, csrf, "0012350041")
    patient_id = _free_slot(client, csrf, created)
    baseline_id = "test-signed-baseline-001"
    started = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={
            "kind": "follow_up",
            "baseline": {
                "history_values": {"h_exposure_dopamine_blocker": "yes"},
                "prior_scores": {"panss_total": 68},
                "medications": [
                    {"catalog_drug_id": "catalog_alpha"},
                    {"catalog_drug_id": "catalog_beta"},
                ],
                "provenance_note": "test-only signed baseline snapshot",
            },
            "baseline_encounter_id": baseline_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    draft = started.json()["draft_data"]
    assert [entry["catalog_drug_id"] for entry in draft["medications"]["entries"]] == [
        "catalog_alpha",
        "catalog_beta",
    ]
    for catalog_id in ("catalog_alpha", "catalog_beta"):
        prov = draft["medications"]["provenance"][catalog_id]
        assert prov["source"] == "copied_baseline"
        assert prov["author_id"] == user["id"]
        assert prov["baseline_encounter_id"] == baseline_id
    assert draft["medications"]["reconciliation"] == {
        "status": "pending",
        "baseline_encounter_id": baseline_id,
    }
    # Explicit reconcile is required: pending stays pending with no rows.
    pending = _get_report(client, encounter_id).json()
    assert pending["report_status"] == "pending"
    assert pending["reason"] == "reconciliation_required"
    assert pending["report"] is None
    preview = _get_meds(client, encounter_id).json()
    assert preview["reconciliation"]["status"] == "pending"
    assert version.startswith("ddi-")


def test_followup_explicit_reconcile_makes_report_current(clean_all, tmp_path) -> None:
    _publish_complete(clean_all, tmp_path)
    admin_client, admin_csrf, _ = _login()
    _make_physician(admin_client, admin_csrf, "dr_fu_recon")
    client, csrf, _ = _physician_client("dr_fu_recon")
    created = _create_draft(client, csrf, "0012350042")
    patient_id = _free_slot(client, csrf, created)
    baseline_id = "test-signed-baseline-002"
    started = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={
            "kind": "follow_up",
            "baseline": {
                "history_values": {"h_exposure_dopamine_blocker": "yes"},
                "prior_scores": {},
                "medications": [{"catalog_drug_id": "catalog_alpha"}],
                "provenance_note": "test-only signed baseline snapshot",
            },
            "baseline_encounter_id": baseline_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    assert _get_report(client, encounter_id).json()["report_status"] == "pending"

    # Saving the same list still pending does not clear the gate.
    still = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}],
        1,
        reconciliation={"status": "pending", "baseline_encounter_id": baseline_id},
    )
    assert still.status_code == 200, still.text
    assert _get_report(client, encounter_id).json()["report_status"] == "pending"

    # Explicit reconcile with the same list makes the report current.
    done = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}],
        2,
        reconciliation={"status": "reconciled", "baseline_encounter_id": baseline_id},
    )
    assert done.status_code == 200, done.text
    assert done.json()["reconciliation"]["status"] == "reconciled"
    current = _get_report(client, encounter_id).json()
    assert current["report_status"] == "current"
    assert current["report"] is not None
    assert current["report"]["pairs"] == []
    # Prior signed lists/reports are unchanged: no sign route, no mutation.
    openapi = client.get("/openapi.json")
    assert "/sign" not in " ".join(openapi.json()["paths"].keys())


# --- S20 exit gaps: order-independent fingerprint, error fencing, escape,
# --- zero-row empty save, follow-up isolation + persisted reference ---


def test_fingerprint_order_independent_pins_and_fresh_version(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_fp_order", "0012350051"
    )
    forward = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}],
        1,
    )
    assert forward.status_code == 200, forward.text
    fp_forward = forward.json()["medication_fingerprint"]
    assert len(fp_forward) == 64
    assert forward.json()["dataset_version"] == version
    assert forward.json()["catalog_version"] == "test-s20/1"
    assert forward.json()["generated_at"].endswith("Z")
    assert forward.headers["etag"] == '"2"'

    # Same set reversed on the same draft pins the same fingerprint.
    reversed_save = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_beta"}, {"catalog_drug_id": "catalog_alpha"}],
        2,
    )
    assert reversed_save.status_code == 200, reversed_save.text
    assert reversed_save.json()["medication_fingerprint"] == fp_forward
    current = _get_report(client, encounter_id).json()
    assert current["report_status"] == "current"
    assert current["report"]["medication_fingerprint"] == fp_forward

    # Changed list yields a fresh versioned fingerprint, never the old rows.
    changed = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_alpha"}], 3)
    assert changed.status_code == 200, changed.text
    assert changed.json()["medication_fingerprint"] != fp_forward
    refreshed = _get_report(client, encounter_id).json()
    assert refreshed["report_status"] == "current"
    assert refreshed["report"]["medication_fingerprint"] == changed.json()["medication_fingerprint"]
    assert refreshed["report"]["pairs"] == []


def test_report_error_when_dataset_unavailable_never_stale(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, _ = _setup_draft_with_release(
        clean_all, tmp_path, "dr_rep_err", "0012350052"
    )
    saved = _post_meds(client, csrf, encounter_id, [{"catalog_drug_id": "catalog_alpha"}], 1)
    assert saved.status_code == 200, saved.text
    assert _get_report(client, encounter_id).json()["report_status"] == "current"

    # Removing the pinned release makes the report error, never stale rows.
    with clean_all.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases"
            )
        )
    errored = _get_report(client, encounter_id).json()
    assert errored["report_status"] == "error"
    assert errored["reason"] == "dataset_unavailable"
    assert errored["report"] is None
    # Saved list is kept through the dataset outage.
    assert [
        entry["catalog_drug_id"] for entry in _get_meds(client, encounter_id).json()["entries"]
    ] == ["catalog_alpha"]


def _publish_markup_release(clean_all, tmp_path):
    from x_insight.ddi import publish

    sources = tmp_path / "sources_markup"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "Alpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Beta\nBeta increases the level of Alpha by mechanism <b>e2e-bold</b> "
        "<script>alert(1)</script>. Avoid combination."
        "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    (sources / "Beta.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\n"
        "Monitor Closely (0)\n\nMinor (1)\n\n"
        "Alpha\nAlpha increases the level of Beta by other mechanism. Minor significance."
        "\nWarnings\n"
    )
    concepts = [
        concept("alpha", "Alpha", catalog="catalog_alpha"),
        concept("beta", "Beta", catalog="catalog_beta"),
    ]
    staging = tmp_path / "staging_markup"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest_markup.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, clean_all)


def test_escape_verbatim_and_no_provider_import(clean_all, tmp_path) -> None:
    markup_version = _publish_markup_release(clean_all, tmp_path)["version"]
    admin_client, admin_csrf, _ = _login()
    _make_physician(admin_client, admin_csrf, "dr_esc_markup")
    client, csrf, _ = _physician_client("dr_esc_markup")
    created = _create_draft(client, csrf, "0012350053")
    encounter_id = created["draft"]["id"]
    saved = _post_meds(
        client,
        csrf,
        encounter_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}],
        1,
        dataset_version=markup_version,
    )
    assert saved.status_code == 200, saved.text
    response = _get_report(client, encounter_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["report_status"] == "current"
    pair = body["report"]["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert pair["highest_known_severity"] == "serious"
    assert sorted(pair["conflicts"]) == ["minor", "serious"]
    texts = [row["raw_text"] for row in pair["evidence"]]
    assert any("<b>e2e-bold</b>" in (text or "") for text in texts)
    assert any("<script>alert(1)</script>" in (text or "") for text in texts)
    for row in pair["evidence"]:
        assert row["source_path"] in ("Alpha.txt", "Beta.txt")
        assert isinstance(row["span_start"], int)
        assert isinstance(row["span_end"], int)
        assert row["checksum"] and row["source_hash"] == row["checksum"]
    blob = json.dumps(body["report"]).lower()
    assert "safe" not in blob
    assert "no interaction" not in blob
    # DDI works with no provider import: checker never imports provider code.
    checker_path = Path(__file__).resolve().parents[2] / "src" / "x_insight" / "ddi" / "checker.py"
    assert "provider" not in checker_path.read_text(encoding="utf-8").lower()


def test_empty_save_zero_row_report_never_safe(clean_all, tmp_path) -> None:
    client, csrf, _, encounter_id, version = _setup_draft_with_release(
        clean_all, tmp_path, "dr_empty_zero", "0012350054"
    )
    saved = _post_meds(client, csrf, encounter_id, [], 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["medication_fingerprint"] is not None
    assert len(saved.json()["medication_fingerprint"]) == 64
    body = _get_report(client, encounter_id).json()
    assert body["report_status"] == "current"
    assert body["report"]["pairs"] == []
    assert body["report"]["dataset_version"] == version
    assert body["report"]["resolved_medications"] == []
    blob = json.dumps(body["report"]).lower()
    assert "safe" not in blob
    assert "no interaction" not in blob
    # Empty list resumes across reload.
    assert _get_meds(client, encounter_id).json()["entries"] == []


def test_followup_reconcile_persists_reference_and_isolates_previous(clean_all, tmp_path) -> None:
    version = _publish_complete(clean_all, tmp_path)["version"]
    admin_client, admin_csrf, _ = _login()
    _make_physician(admin_client, admin_csrf, "dr_fu_iso")
    client, csrf, _ = _physician_client("dr_fu_iso")
    # Baseline patient keeps its own saved list/report.
    baseline_created = _create_draft(client, csrf, "0012350055")
    baseline_encounter = baseline_created["draft"]["id"]
    baseline_saved = _post_meds(
        client, csrf, baseline_encounter, [{"catalog_drug_id": "catalog_alpha"}], 1
    )
    assert baseline_saved.status_code == 200, baseline_saved.text
    baseline_fp = baseline_saved.json()["medication_fingerprint"]
    baseline_patient = baseline_created["patient"]["id"]
    # Free the second slot for the follow-up patient only.
    follow_created = _create_draft(client, csrf, "0012350056")
    follow_patient = _free_slot(client, csrf, follow_created)
    baseline_id = "test-signed-baseline-iso"
    started = client.post(
        f"/api/v1/patients/{follow_patient}/encounters",
        json={
            "kind": "follow_up",
            "baseline": {
                "history_values": {"h_exposure_dopamine_blocker": "yes"},
                "prior_scores": {},
                "medications": [
                    {"catalog_drug_id": "catalog_alpha"},
                    {"catalog_drug_id": "catalog_beta"},
                ],
                "provenance_note": "test-only signed baseline snapshot",
            },
            "baseline_encounter_id": baseline_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert started.status_code == 201, started.text
    follow_id = started.json()["encounter"]["id"]
    done = _post_meds(
        client,
        csrf,
        follow_id,
        [{"catalog_drug_id": "catalog_alpha"}, {"catalog_drug_id": "catalog_beta"}],
        1,
        reconciliation={"status": "reconciled", "baseline_encounter_id": baseline_id},
    )
    assert done.status_code == 200, done.text
    current = _get_report(client, follow_id).json()
    assert current["report_status"] == "current"
    assert current["report"]["medication_fingerprint"] == done.json()["medication_fingerprint"]
    # Current report reference persists in draft_data for S40 snapshots.
    draft = client.get(f"/api/v1/encounters/{follow_id}").json()
    section = draft["draft_data"]["medications"]
    assert section["dataset_version"] == version
    assert section["catalog_version"] == "test-s20/1"
    assert section["medication_fingerprint"] == current["report"]["medication_fingerprint"]
    assert section["generated_at"].endswith("Z")
    assert section["reconciliation"]["status"] == "reconciled"
    # Previous list/report is unchanged by the follow-up reconcile.
    assert _get_meds(client, baseline_encounter).json()["medication_fingerprint"] == baseline_fp
    assert (
        _get_report(client, baseline_encounter).json()["report"]["medication_fingerprint"]
        == baseline_fp
    )
    assert baseline_patient != follow_patient
