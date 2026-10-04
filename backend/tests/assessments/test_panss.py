"""PANSS without implicit minimum answers (S10, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 evaluator (no frontend):

1. Fresh form has 30 unanswered items and null scores (no default "1",
   no hidden zero); Skip persists ``not_assessed``.
2. Fully answered all-1 yields 7/7/16/30 and all-7 yields 49/49/112/210,
   via both direct ``evaluate()`` (T2) and the GET route (T1, no drift).
3. One missing required item suppresses the total to null, invalid /
   out-of-range / non-integer input is rejected server-side, and resumed
   answers preserve completeness after PATCH + GET.
4. Total bands are informational_only + not_a_treatment_gate (no treatment
   gate, no owner review, no awaiting_review).

PANSS state lives in ``draft_data["panss"]``; answers travel through the
existing S07 PATCH autosave, preview through ``GET .../panss`` only.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.assessments import evaluate
from x_insight.assessments.released import get_released_definition
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
            connection.execute(text("TRUNCATE encounters, patients"))
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


def _patch(client, csrf, encounter_id, draft_data, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(
        f"/api/v1/encounters/{encounter_id}", json={"draft_data": draft_data}, headers=headers
    )


def _get_panss(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/panss")


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


# Independent literal fixtures (worked from PANSS.md scoring, not from code).
PANSS_IDS = (
    ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    + ["N1", "N2", "N3", "N4", "N5", "N6", "N7"]
    + [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "G6",
        "G7",
        "G8",
        "G9",
        "G10",
        "G11",
        "G12",
        "G13",
        "G14",
        "G15",
        "G16",
    ]
)


def _all_ones() -> dict:
    return dict.fromkeys(PANSS_IDS, 1)


def _all_sevens() -> dict:
    return dict.fromkeys(PANSS_IDS, 7)


# --- Slice 1: fresh form has 30 unanswered items and null scores ---


def test_fresh_form_has_30_unanswered_and_null_total(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss1", "0012346001"
    )
    direct = evaluate(get_released_definition("panss"), {})
    assert direct["status"] == "unanswered"
    assert direct["missing_item_ids"] == PANSS_IDS
    assert direct["item_errors"] == {}
    assert direct["scores"] == {
        "positive": None,
        "negative": None,
        "general": None,
        "total": None,
    }
    assert direct["definition_version"] == "v1"

    preview = _get_panss(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == {}
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"]["total"] is None
    assert body["definition_version"] == "v1"
    assert body["revision"] == 1
    assert preview.headers["etag"] == '"1"'


def test_skip_persists_not_assessed(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss2", "0012346002"
    )
    skipped = {"__skipped": True}
    direct = evaluate(get_released_definition("panss"), skipped)
    assert direct["status"] == "not_assessed"
    assert direct["scores"] == {
        "positive": None,
        "negative": None,
        "general": None,
        "total": None,
    }

    saved = _patch(client, csrf, encounter_id, {"panss": {"answers": skipped}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_panss(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == skipped
    assert body["evaluation"] == direct
    assert body["evaluation"]["status"] == "not_assessed"
    assert body["revision"] == 2
    assert preview.headers["etag"] == '"2"'


# --- Slice 2: exact arithmetic all-1 and all-7 via T2 and T1 ---


def test_all_absent_yields_minimum_via_evaluator_and_route(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss3", "0012346003"
    )
    answers = _all_ones()
    direct = evaluate(get_released_definition("panss"), answers)
    assert direct["status"] == "complete"
    assert direct["item_errors"] == {}
    # Independently worked: 7x1 / 7x1 / 16x1 / 30x1.
    assert direct["scores"] == {"positive": 7, "negative": 7, "general": 16, "total": 30}

    saved = _patch(client, csrf, encounter_id, {"panss": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_panss(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"] == {
        "positive": 7,
        "negative": 7,
        "general": 16,
        "total": 30,
    }
    assert body["definition_version"] == "v1"
    assert preview.headers["etag"] == '"2"'


def test_all_extreme_yields_maximum_via_evaluator_and_route(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss4", "0012346004"
    )
    answers = _all_sevens()
    direct = evaluate(get_released_definition("panss"), answers)
    assert direct["status"] == "complete"
    # Independently worked: 7x7=49 / 7x7=49 / 16x7=112 / 210.
    assert direct["scores"] == {
        "positive": 49,
        "negative": 49,
        "general": 112,
        "total": 210,
    }

    saved = _patch(client, csrf, encounter_id, {"panss": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_panss(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v1"


def test_total_bands_are_informational_only_not_a_treatment_gate(
    admin_client, clean_registry, monkeypatch
) -> None:
    direct_min = evaluate(get_released_definition("panss"), _all_ones())
    assert direct_min["findings"]["total_band"]["informational_only"] is True
    assert direct_min["findings"]["total_band"]["not_a_treatment_gate"] is True
    direct_max = evaluate(get_released_definition("panss"), _all_sevens())
    assert direct_max["findings"]["total_band"]["range"] == ">=116"
    assert direct_max["findings"]["total_band"]["informational_only"] is True
    assert direct_max["findings"]["total_band"]["not_a_treatment_gate"] is True

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss5", "0012346005"
    )
    assert (
        _patch(client, csrf, encounter_id, {"panss": {"answers": _all_ones()}}, 1).status_code
        == 200
    )
    body = _get_panss(client, encounter_id).json()
    assert body["evaluation"]["findings"]["total_band"]["informational_only"] is True
    assert body["evaluation"]["findings"]["total_band"]["not_a_treatment_gate"] is True


# --- Slice 3: missing suppresses total; invalid rejected; resume preserves ---


def test_one_missing_suppresses_total_to_null(admin_client, clean_registry, monkeypatch) -> None:
    answers = _all_ones()
    del answers["G16"]
    direct = evaluate(get_released_definition("panss"), answers)
    assert direct["status"] == "partial"
    assert direct["missing_item_ids"] == ["G16"]
    assert direct["scores"]["total"] is None
    assert direct["scores"]["positive"] == 7
    assert direct["scores"]["negative"] == 7
    assert direct["scores"]["general"] is None

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss6", "0012346006"
    )
    assert _patch(client, csrf, encounter_id, {"panss": {"answers": answers}}, 1).status_code == 200
    body = _get_panss(client, encounter_id).json()
    assert body["evaluation"] == direct
    assert body["evaluation"]["scores"]["total"] is None


def test_invalid_out_of_range_noninteger_rejected_server_side(
    admin_client, clean_registry, monkeypatch
) -> None:
    for bad in (0, 8, 2.5, "3", True, None):
        result = evaluate(get_released_definition("panss"), {**_all_ones(), "P1": bad})
        assert "P1" in result["item_errors"], bad
        assert result["status"] == "partial"
        assert result["scores"]["total"] is None

    undeclared = evaluate(get_released_definition("panss"), {**_all_ones(), "PX": 1})
    assert "PX" in undeclared["item_errors"]
    assert undeclared["scores"]["total"] is None

    # Same rejection is visible through the GET route (never zero-filled).
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss7", "0012346007"
    )
    revision = 1
    for bad in (0, 8, 2.5, "3", True, None):
        answers = {**_all_ones(), "P1": bad}
        saved = _patch(client, csrf, encounter_id, {"panss": {"answers": answers}}, revision)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        body = _get_panss(client, encounter_id).json()
        assert "P1" in body["evaluation"]["item_errors"], bad
        assert body["evaluation"]["scores"]["total"] is None


def test_resume_preserves_completeness_after_patch_and_get(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss8", "0012346008"
    )
    answers = _all_ones()
    direct = evaluate(get_released_definition("panss"), answers)
    saved = _patch(client, csrf, encounter_id, {"panss": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    before = _get_panss(client, encounter_id).json()
    assert before["evaluation"] == direct
    assert before["evaluation"]["status"] == "complete"

    # Resume after restart: a fresh client re-authenticates and sees the same state.
    fresh = TestClient(app)
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_panss8", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh.get(f"/api/v1/encounters/{encounter_id}/panss")
    assert resumed.status_code == 200, resumed.text
    resumed_body = resumed.json()
    assert resumed_body["answers"] == answers
    assert resumed_body["evaluation"] == direct
    assert resumed_body["evaluation"]["scores"] == {
        "positive": 7,
        "negative": 7,
        "general": 16,
        "total": 30,
    }
    assert resumed_body["revision"] == 2
    assert resumed.headers["etag"] == '"2"'
    # The shared autosave body carries the answers through GET /encounters.
    draft = fresh.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["panss"]["answers"] == answers
    assert draft["revision"] == 2


# --- Privacy + auth contracts (S07 carried forward) ---


def test_panss_author_only_and_auth(admin_client, clean_registry, monkeypatch) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_panss9", "0012346009"
    )
    _make_physician(admin_client, "dr_panss_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_panss_stranger", "pw123")
    assert stranger.get(f"/api/v1/encounters/{encounter_id}/panss").status_code == 403

    # Administrator is not the author either.
    assert admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/panss").status_code == 403
    # Anonymous reads are 401.
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/panss").status_code == 401
    # Unknown encounter is 404, not 403.
    assert client.get(f"/api/v1/encounters/{uuid.uuid4()}/panss").status_code == 404
