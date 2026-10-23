"""C-SSRS form and distinct results (live cssrs.v2, backend only; seams T2/T1).

Through public HTTP + real PostgreSQL and the T2 evaluator (no frontend):

1. Fresh ``{}`` is complete: five recent unknowns, severity 0, flags empty.
   Unknown is not a negative. Skip persists ``not_assessed``.
2. Explicit all-no is the only ``no_positive_items`` result. A higher yes
   does not fill lower levels. Severity 4 or 5 adds ``high_risk_alert``.
3. Retired ids are stripped, not errors, and are not returned. Invalid live
   values suppress the score. Null recent items become unknown.
4. Author-only access holds. Resume preserves the five normalized answers.

C-SSRS state lives in ``draft_data["cssrs"]``; answers travel through the
existing S07 PATCH autosave, preview through ``GET .../cssrs`` only.
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


def _patch(client, csrf, encounter_id, draft_data, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(
        f"/api/v1/encounters/{encounter_id}", json={"draft_data": draft_data}, headers=headers
    )


def _get_cssrs(client, encounter_id):
    return client.get(f"/api/v1/encounters/{encounter_id}/cssrs")


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


# Live-item literals. Retired lifetime, intensity, behavior, and lethality
# ids are not part of this fixture.
CSSRS_RECENT = (
    "css_i1_recent",
    "css_i2_recent",
    "css_i3_recent",
    "css_i4_recent",
    "css_i5_recent",
)
NULL_SCORE = {"ideation_severity_recent": None}


def _all_unknown() -> dict:
    return dict.fromkeys(CSSRS_RECENT, "unknown")


def _all_no() -> dict:
    return dict.fromkeys(CSSRS_RECENT, "no")


def _level3_complete() -> dict:
    answers = _all_no()
    answers["css_i3_recent"] = "yes"
    return answers


def _assert_recent_only(result: dict) -> None:
    assert set(result["scores"]) == {"ideation_severity_recent"}
    assert "behavior" not in result["findings"]
    assert "lethality" not in result["findings"]
    assert "intensity" not in result["findings"]


def _live():
    return get_released_definition("cssrs")


# --- Fresh complete unknowns, skip, explicit negatives ---


def test_fresh_form_is_complete_with_five_unknowns(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs1", "0012347001"
    )
    direct = evaluate(_live(), {})
    assert direct["status"] == "complete"
    assert direct["missing_item_ids"] == []
    assert direct["item_errors"] == {}
    assert direct["scores"] == {"ideation_severity_recent": 0}
    assert direct["findings"]["flags"] == []
    assert direct["findings"]["windows"] == {"recent_ideation": "past_month"}
    assert direct["definition_version"] == "v2"
    _assert_recent_only(direct)

    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == _all_unknown()
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v2"
    assert body["revision"] == 1
    assert preview.headers["etag"] == '"1"'


def test_skip_persists_not_assessed(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs2", "0012347002"
    )
    skipped = {"__skipped": True}
    direct = evaluate(_live(), skipped)
    assert direct["status"] == "not_assessed"
    assert direct["scores"] == NULL_SCORE
    assert direct["findings"] == {"skipped": True}
    assert direct["missing_item_ids"] == list(CSSRS_RECENT)

    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": skipped}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == skipped
    assert body["evaluation"] == direct
    assert body["evaluation"]["status"] == "not_assessed"
    assert body["revision"] == 2
    assert preview.headers["etag"] == '"2"'


def test_complete_explicit_negatives_give_no_ideation_result(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs3", "0012347003"
    )
    answers = _all_no()
    direct = evaluate(_live(), answers)
    assert direct["status"] == "complete"
    assert direct["item_errors"] == {}
    assert direct["missing_item_ids"] == []
    assert direct["scores"] == {"ideation_severity_recent": 0}
    assert direct["findings"]["flags"] == ["no_positive_items"]
    assert direct["findings"]["windows"] == {"recent_ideation": "past_month"}
    assert direct["definition_version"] == "v2"
    _assert_recent_only(direct)

    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    preview = _get_cssrs(client, encounter_id)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["answers"] == answers
    assert body["evaluation"] == direct
    assert body["definition_version"] == "v2"
    assert preview.headers["etag"] == '"2"'


def test_level3_yes_does_not_fill_lower_levels(admin_client, clean_registry, monkeypatch) -> None:
    direct = evaluate(_live(), {"css_i3_recent": "yes"})
    assert direct["status"] == "complete"
    assert direct["scores"] == {"ideation_severity_recent": 3}
    assert direct["findings"]["flags"] == ["clinical_review"]
    assert "high_risk_alert" not in direct["findings"]["flags"]
    _assert_recent_only(direct)

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs4", "0012347004"
    )
    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": {"css_i3_recent": "yes"}}}, 1)
    assert saved.status_code == 200, saved.text
    body = _get_cssrs(client, encounter_id).json()
    assert body["answers"] == {
        "css_i1_recent": "unknown",
        "css_i2_recent": "unknown",
        "css_i3_recent": "yes",
        "css_i4_recent": "unknown",
        "css_i5_recent": "unknown",
    }
    assert body["evaluation"] == direct


def test_severity_four_and_five_raise_high_risk_alert(
    admin_client, clean_registry, monkeypatch
) -> None:
    level4 = _all_no()
    level4["css_i2_recent"] = "yes"
    level4["css_i4_recent"] = "yes"
    direct = evaluate(_live(), level4)
    assert direct["status"] == "complete"
    assert direct["scores"] == {"ideation_severity_recent": 4}
    assert direct["findings"]["flags"] == ["clinical_review", "high_risk_alert"]
    _assert_recent_only(direct)
    level5 = _all_unknown()
    level5["css_i5_recent"] = "yes"
    assert evaluate(_live(), level5)["scores"] == {"ideation_severity_recent": 5}
    assert evaluate(_live(), level5)["findings"]["flags"] == [
        "clinical_review",
        "high_risk_alert",
    ]

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs5", "0012347005"
    )
    assert _patch(client, csrf, encounter_id, {"cssrs": {"answers": level4}}, 1).status_code == 200
    body = _get_cssrs(client, encounter_id).json()
    assert body["answers"] == level4
    assert body["evaluation"] == direct


def test_retired_ids_are_stripped_and_null_becomes_unknown(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs6", "0012347006"
    )
    stored = _all_no()
    stored["css_i1_recent"] = None
    stored.update(
        {
            "css_i2_lifetime": "yes",
            "css_frequency": 9,
            "css_beh_actual_recent": "yes",
            "css_leth_actual_recent": 2,
            "css_beh_nssi_lifetime": "yes",
        }
    )
    direct = evaluate(_live(), stored)
    assert direct["status"] == "complete"
    assert direct["item_errors"] == {}
    assert direct["scores"] == {"ideation_severity_recent": 0}
    assert direct["findings"]["flags"] == []
    _assert_recent_only(direct)

    assert _patch(client, csrf, encounter_id, {"cssrs": {"answers": stored}}, 1).status_code == 200
    body = _get_cssrs(client, encounter_id).json()
    assert body["answers"] == {
        "css_i1_recent": "unknown",
        "css_i2_recent": "no",
        "css_i3_recent": "no",
        "css_i4_recent": "no",
        "css_i5_recent": "no",
    }
    assert body["evaluation"] == direct
    draft = client.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["cssrs"]["answers"] == body["answers"]


def test_invalid_live_values_suppress_scores(admin_client, clean_registry, monkeypatch) -> None:
    base = _level3_complete()
    for bad in ("maybe", 0, 1, True):
        answers = dict(base)
        answers["css_i1_recent"] = bad
        result = evaluate(_live(), answers)
        assert "css_i1_recent" in result["item_errors"], bad
        assert result["status"] == "partial"
        assert result["scores"] == NULL_SCORE
        assert result["findings"] == {}
    for item_id, bad in (
        ("css_i1_current", "yes"),
        ("css_beh_actual_now", "no"),
        ("PX", 1),
        ("__draft", True),
    ):
        answers = dict(base)
        answers[item_id] = bad
        result = evaluate(_live(), answers)
        assert item_id in result["item_errors"], item_id
        assert result["status"] == "partial"
        assert result["scores"] == NULL_SCORE

    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs9", "0012347009"
    )
    revision = 1
    for item_id, bad in (("css_i1_recent", "maybe"), ("PX", 1)):
        answers = dict(base)
        answers[item_id] = bad
        saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, revision)
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        body = _get_cssrs(client, encounter_id).json()
        assert item_id in body["evaluation"]["item_errors"], (item_id, bad)
        assert body["evaluation"]["scores"] == NULL_SCORE
        assert body["evaluation"]["findings"] == {}


def test_cssrs_author_only_and_auth(admin_client, clean_registry, monkeypatch) -> None:
    client, _, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs10", "0012347010"
    )
    _make_physician(admin_client, "dr_cssrs_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_cssrs_stranger", "pw123")
    assert stranger.get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 403
    assert admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 403
    assert TestClient(app).get(f"/api/v1/encounters/{encounter_id}/cssrs").status_code == 401
    assert client.get(f"/api/v1/encounters/{uuid.uuid4()}/cssrs").status_code == 404


def test_resume_preserves_completeness_after_patch_and_get(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_cssrs11", "0012347011"
    )
    answers = _level3_complete()
    direct = evaluate(_live(), answers)
    saved = _patch(client, csrf, encounter_id, {"cssrs": {"answers": answers}}, 1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    before = _get_cssrs(client, encounter_id).json()
    assert before["evaluation"] == direct
    assert before["evaluation"]["status"] == "complete"

    fresh = TestClient(app)
    login = fresh.post(
        "/api/v1/auth/login",
        json={"username": "dr_cssrs11", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh.get(f"/api/v1/encounters/{encounter_id}/cssrs")
    assert resumed.status_code == 200, resumed.text
    resumed_body = resumed.json()
    assert resumed_body["answers"] == answers
    assert resumed_body["evaluation"] == direct
    assert resumed_body["evaluation"]["scores"] == {"ideation_severity_recent": 3}
    assert resumed_body["evaluation"]["findings"]["flags"] == ["clinical_review"]
    assert resumed_body["revision"] == 2
    assert resumed.headers["etag"] == '"2"'
    draft = fresh.get(f"/api/v1/encounters/{encounter_id}").json()
    assert draft["draft_data"]["cssrs"]["answers"] == answers
    assert draft["revision"] == 2
