"""Retired page-note HTTP contract; working notes retain draft autosave semantics (T1)."""

from __future__ import annotations

import os
from pathlib import Path

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
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE question_runs, generation_batches, "
                    "notes, encounters, patients CASCADE"
                )
            )
    except Exception:
        pass
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE idempotency_records"))
    except Exception:
        pass


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
    return {
        "client": client,
        "csrf": response.json()["csrf_token"],
        "user": response.json()["user"],
        "engine": clean_registry,
    }


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    created = admin_client["client"].post(
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


def _valid_patient(identifier: str = "0012345678") -> dict:
    return {
        "identifier": identifier,
        "given_name": "Anna",
        "family_name": "Novak",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "+43 699 123456",
    }


def _create_draft(client, csrf, identifier="0012345678") -> dict:
    created = client.post(
        "/api/v1/patients", json=_valid_patient(identifier), headers=_auth_headers(csrf)
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_page_notes_are_absent_from_openapi_and_retired_routes(
    admin_client, clean_registry, monkeypatch
):
    _make_physician(admin_client, "dr_retired_notes")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_retired_notes", "pw123")
    encounter_id = _create_draft(client, csrf)["draft"]["id"]
    schema = client.get("/openapi.json").json()
    assert all("/notes" not in path for path in schema["paths"])
    assert "NoteCreateRequest" not in schema["components"]["schemas"]
    target = f"/api/v1/encounters/{encounter_id}/notes"
    assert client.get(target).status_code == 404
    assert (
        client.post(
            target,
            json={"page": "demographics", "text": "retired"},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/encounters/{encounter_id}").json()["revision"] == 1


def test_working_note_round_trips_after_relogin_and_can_be_cleared(
    admin_client, clean_registry, monkeypatch
):
    _make_physician(admin_client, "dr_working_note")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_working_note", "pw123")
    encounter_id = _create_draft(client, csrf)["draft"]["id"]
    target = f"/api/v1/encounters/{encounter_id}"
    note = "Working reminder <b>literal</b>\nsecond line"
    saved = client.patch(
        target,
        json={"draft_data": {"working_note": note}},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_data"] == {"working_note": note}
    fresh, fresh_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_working_note", "pw123"
    )
    resumed = fresh.get(target)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["draft_data"] == {"working_note": note}
    assert "notes" not in resumed.json()
    stale = fresh.patch(
        target,
        json={"draft_data": {"working_note": "stale"}},
        headers={**_auth_headers(fresh_csrf), "If-Match": '"1"'},
    )
    assert stale.status_code == 412
    cleared = fresh.patch(
        target,
        json={"draft_data": {"working_note": ""}},
        headers={
            **_auth_headers(fresh_csrf),
            "If-Match": contracts.format_etag(resumed.json()["revision"]),
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert fresh.get(target).json()["draft_data"] == {"working_note": ""}
