"""Shared chart + follow-up draft entry (S14, seam T1, backend only).

Through public HTTP + real PostgreSQL (no frontend, no signing/S49, no DDI):

Slice 1 — shared chart read:
- any active physician can read demographics/signed chart of another
  physician's patient; admin has no ordinary clinical route (403);
  anonymous is 401; unknown patient is 404.
- chart carries demographics + occupancy badge only; stranger-draft content
  never leaks (no draft_data/notes/history values/author/revision).

Slice 2 — follow-up creation with baseline copy (extends create_open_draft):
- baseline optional inline {history_values, prior_scores, medications?,
  provenance_note?} + optional baseline_encounter_id; copied history gets
  copied_baseline provenance + pending reconciliation; PANSS/C-SSRS stay {}.
- without baseline: empty history + not_required.
- slot 409 generic, no leak; different patients independent.

Slice 3 — badges/chronology/phone-reconciliation + honest proposal:
- chronology derives from encounters table signed rows + open-draft badge;
- GET /encounters/{id}/followup-baseline author-only with historical label;
- chart proposal is honestly unavailable (literal generation_not_implemented);
- phone update via existing history phone_update path.

No migration, no signed/projection tables, no bypass-sign production route.
Test-only signed baseline fixture (if any) lives in-memory here, never as a
production route.
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
            connection.execute(text("TRUNCATE notes, encounters, patients"))
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


def _create_patient(client, csrf, identifier="0012345678") -> dict:
    response = client.post(
        "/api/v1/patients", json=_valid_patient(identifier), headers=_auth_headers(csrf)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _save(client, csrf, encounter_id, draft_data, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.patch(
        f"/api/v1/encounters/{encounter_id}", json={"draft_data": draft_data}, headers=headers
    )


# --- Slice 1: shared chart read ---


def test_physician_reads_stranger_patient_chart_without_draft_content(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_owner")
    _make_physician(admin_client, "dr_chart_reader")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_owner", "pw123")
    reader, _, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_reader", "pw123")
    created = _create_patient(owner, owner_csrf, "0012345601")
    patient_id = created["patient"]["id"]
    encounter_id = created["draft"]["id"]
    secret = {"private": "owner-only clinical content"}
    assert _save(owner, owner_csrf, encounter_id, secret, 1).status_code == 200

    chart = reader.get(f"/api/v1/patients/{patient_id}/chart")
    assert chart.status_code == 200, chart.text
    body = chart.json()
    assert body["patient"]["identifier"] == "0012345601"
    assert body["patient"]["given_name"] == "Anna"
    assert body["signed_encounters"] == []
    assert body["chronology"] == []
    assert set(body["open_draft"].keys()) == {"exists"}
    assert body["open_draft"]["exists"] is True
    # No draft content, no author oracle, no history/notes values anywhere.
    assert "owner-only clinical content" not in chart.text
    assert "draft_data" not in body
    assert "author_id" not in chart.text
    assert "private" not in body
    for forbidden in ("history", "notes", "values", "provenance", "draft"):
        assert forbidden not in body, forbidden


def test_chart_admin_forbidden_anonymous_unauthenticated_unknown_not_found(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_owner2")
    owner, owner_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_chart_owner2", "pw123"
    )
    created = _create_patient(owner, owner_csrf, "0012345602")
    patient_id = created["patient"]["id"]

    denied = admin_client["client"].get(f"/api/v1/patients/{patient_id}/chart")
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"

    anon = TestClient(app).get(f"/api/v1/patients/{patient_id}/chart")
    assert anon.status_code == 401, anon.text

    missing = owner.get(f"/api/v1/patients/{uuid.uuid4()}/chart")
    assert missing.status_code == 404, missing.text


def test_chart_open_draft_badge_false_when_slot_free(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_free")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_free", "pw123")
    created = _create_patient(client, csrf, "0012345603")
    patient_id = created["patient"]["id"]
    encounter_id = created["draft"]["id"]
    # Registration draft occupies the slot at first.
    assert client.get(f"/api/v1/patients/{patient_id}/chart").json()["open_draft"] == {
        "exists": True
    }
    discard = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert discard.status_code == 200, discard.text
    chart = client.get(f"/api/v1/patients/{patient_id}/chart")
    assert chart.status_code == 200, chart.text
    assert chart.json()["open_draft"] == {"exists": False}


# --- Slice 2: follow-up creation with baseline copy ---


BASELINE_HISTORY = {
    "h_exposure_dopamine_blocker": "yes",
    "h_exposure_duration_3m": "yes",
    "h_onset_timing": "during_exposure",
}


def _followup_baseline(prior_panss: int | None = 82, prior_cssrs: int | None = 3) -> dict:
    prior_scores: dict = {}
    if prior_panss is not None:
        prior_scores["panss_total"] = prior_panss
    if prior_cssrs is not None:
        prior_scores["cssrs_severity"] = prior_cssrs
    return {
        "history_values": dict(BASELINE_HISTORY),
        "prior_scores": prior_scores,
        "medications": [{"catalog_drug_id": "demo-haloperidol"}],
        "provenance_note": "copied from signed baseline",
    }


def _create_followup(client, csrf, patient_id, baseline=None, key=None):
    headers = _auth_headers(csrf)
    if key is not None:
        headers["Idempotency-Key"] = key
    body: dict = {"kind": "follow_up"}
    if baseline is not None:
        body["baseline"] = baseline
    return client.post(f"/api/v1/patients/{patient_id}/encounters", json=body, headers=headers)


def test_followup_with_baseline_copies_history_pending_panss_cssrs_empty(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fu_owner")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_owner", "pw123")
    created = _create_patient(client, csrf, "0012345611")
    patient_id = created["patient"]["id"]
    registration_id = created["draft"]["id"]
    discard = client.post(
        f"/api/v1/encounters/{registration_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert discard.status_code == 200, discard.text

    response = _create_followup(client, csrf, patient_id, _followup_baseline())
    assert response.status_code == 201, response.text
    body = response.json()
    encounter_id = body["encounter"]["id"]
    assert body["encounter"]["kind"] == "follow_up"
    assert response.headers["etag"] == '"1"'
    assert body["revision"] == 1
    draft = body["draft_data"]
    # History copied with provenance + pending reconciliation.
    assert draft["history"]["values"] == BASELINE_HISTORY
    for field in BASELINE_HISTORY:
        assert draft["history"]["provenance"][field]["source"] == "copied_baseline"
    assert draft["history"]["reconciliation"]["status"] == "pending"
    # PANSS/C-SSRS answers start EMPTY; prior scores display as historical only.
    assert draft["panss"]["answers"] == {}
    assert draft["cssrs"]["answers"] == {}
    assert draft["followup_baseline"]["prior_scores"] == {
        "panss_total": 82,
        "cssrs_severity": 3,
    }
    assert "answers" not in draft["followup_baseline"]["prior_scores"]
    # Author-only read still enforced; author sees the same body back.
    reread = client.get(f"/api/v1/encounters/{encounter_id}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["draft_data"] == draft


def test_followup_without_baseline_empty_history_not_required(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fu_plain")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_plain", "pw123")
    created = _create_patient(client, csrf, "0012345612")
    patient_id = created["patient"]["id"]
    registration_id = created["draft"]["id"]
    assert (
        client.post(
            f"/api/v1/encounters/{registration_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 200
    )

    response = _create_followup(client, csrf, patient_id)
    assert response.status_code == 201, response.text
    draft = response.json()["draft_data"]
    assert draft["history"]["values"] == {}
    assert draft["history"]["reconciliation"]["status"] == "not_required"
    assert draft["panss"]["answers"] == {}
    assert draft["cssrs"]["answers"] == {}
    assert "followup_baseline" not in draft


def test_followup_baseline_validation_rejects_bad_history(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fu_bad")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_bad", "pw123")
    created = _create_patient(client, csrf, "0012345613")
    patient_id = created["patient"]["id"]
    registration_id = created["draft"]["id"]
    assert (
        client.post(
            f"/api/v1/encounters/{registration_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 200
    )

    bad = _create_followup(client, csrf, patient_id, {"history_values": {"dose": "5mg"}})
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "VALIDATION_FAILED"
    # Failed create left the slot free.
    retry = _create_followup(client, csrf, patient_id)
    assert retry.status_code == 201, retry.text


def test_concurrent_followup_creates_conflict_generically(
    admin_client, clean_registry, monkeypatch
) -> None:
    import threading

    _make_physician(admin_client, "dr_fu_race_a")
    _make_physician(admin_client, "dr_fu_race_b")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_race_a", "pw123")
    created = _create_patient(owner, owner_csrf, "0012345614")
    patient_id = created["patient"]["id"]
    registration_id = created["draft"]["id"]
    assert (
        owner.post(
            f"/api/v1/encounters/{registration_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(owner_csrf), "If-Match": '"1"'},
        ).status_code
        == 200
    )
    # One author writes private content first via a winning create? No — the
    # race itself proves the slot: all racers send baseline copy bodies.
    barrier = threading.Barrier(5)
    outcomes: list[int] = []
    bodies: list[tuple[int, str]] = []

    def _attempt(username: str) -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        response = thread_client.post(
            f"/api/v1/patients/{patient_id}/encounters",
            json={"kind": "follow_up", "baseline": _followup_baseline()},
            headers=_auth_headers(csrf),
        )
        outcomes.append(response.status_code)
        bodies.append((response.status_code, response.text))

    threads = [
        threading.Thread(target=_attempt, args=(name,))
        for name in (
            "dr_fu_race_a",
            "dr_fu_race_a",
            "dr_fu_race_b",
            "dr_fu_race_b",
            "dr_fu_race_b",
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == [201] + [409] * 4, outcomes
    for status, raw in bodies:
        if status == 409:
            assert '"OPEN_DRAFT_EXISTS"' in raw
            assert "draft_data" not in raw
            assert "author_id" not in raw
            assert "h_exposure_dopamine_blocker" not in raw
        else:
            assert "copied_baseline" in raw
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE patient_id = :pid AND lifecycle = 'draft'"),
            {"pid": patient_id},
        ).scalar_one()
    assert drafts == 1


def test_followup_drafts_independent_across_patients(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fu_multi")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_multi", "pw123")
    first = _create_patient(client, csrf, "0012345615")
    second = _create_patient(client, csrf, "0012345616")
    for created in (first, second):
        assert (
            client.post(
                f"/api/v1/encounters/{created['draft']['id']}/discard",
                json={"confirm": True},
                headers={**_auth_headers(csrf), "If-Match": '"1"'},
            ).status_code
            == 200
        )
    one = _create_followup(client, csrf, first["patient"]["id"], _followup_baseline())
    two = _create_followup(client, csrf, second["patient"]["id"])
    assert one.status_code == 201, one.text
    assert two.status_code == 201, two.text
    assert one.json()["draft_data"]["history"]["reconciliation"]["status"] == "pending"
    assert two.json()["draft_data"]["history"]["reconciliation"]["status"] == "not_required"


# --- Slice 3: badges / chronology / phone-history-reconciliation / proposal ---


def _followup_id(client, csrf, identifier, baseline=None) -> tuple[str, str, dict]:
    created = _create_patient(client, csrf, identifier)
    registration_id = created["draft"]["id"]
    assert (
        client.post(
            f"/api/v1/encounters/{registration_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 200
    )
    response = _create_followup(client, csrf, created["patient"]["id"], baseline)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["encounter"]["id"], created["patient"]["id"], body["draft_data"]


def test_chart_chronology_lists_signed_rows_and_badges_open_draft(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chrono")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chrono", "pw123")
    created = _create_patient(client, csrf, "0012345621")
    patient_id = created["patient"]["id"]
    # No signed rows in prod yet: chronology is empty, badge tracks the slot.
    chart = client.get(f"/api/v1/patients/{patient_id}/chart")
    assert chart.status_code == 200, chart.text
    assert chart.json()["chronology"] == []
    assert chart.json()["open_draft"] == {"exists": True}
    # Chronology carries references only — never clinical content.
    assert "draft_data" not in chart.text
    assert "values" not in chart.json()


def test_followup_baseline_endpoint_author_only_with_historical_scores(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fub_owner")
    _make_physician(admin_client, "dr_fub_stranger")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fub_owner", "pw123")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_fub_stranger", "pw123")
    encounter_id, _, _ = _followup_id(owner, owner_csrf, "0012345622", _followup_baseline())

    response = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert response.headers["etag"] == '"1"'
    assert body["baseline"]["history_values"] == BASELINE_HISTORY
    assert body["reconciliation"]["status"] == "pending"
    # Prior scores are distinctly labeled historical — never current answers.
    assert body["prior_scores_display"] == {
        "panss_total": {"value": 82, "historical": True},
        "cssrs_severity": {"value": 3, "historical": True},
    }
    assert body["encounter"]["id"] == encounter_id

    # Stranger/admin/anonymous/missing get 403/404/401 without content.
    denied = stranger.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert denied.status_code == 403, denied.text
    assert "h_exposure_dopamine_blocker" not in denied.text
    admin_denied = admin_client["client"].get(
        f"/api/v1/encounters/{encounter_id}/followup-baseline"
    )
    assert admin_denied.status_code == 403, admin_denied.text
    assert (
        TestClient(app).get(f"/api/v1/encounters/{encounter_id}/followup-baseline").status_code
        == 401
    )  # noqa: E501
    assert owner.get(f"/api/v1/encounters/{uuid.uuid4()}/followup-baseline").status_code == 404


def test_followup_baseline_endpoint_empty_without_baseline(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fub_plain")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fub_plain", "pw123")
    encounter_id, _, _ = _followup_id(client, csrf, "0012345623")
    response = client.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert response.status_code == 200, response.text
    assert response.json()["baseline"] is None
    assert response.json()["reconciliation"]["status"] == "not_required"
    assert response.json()["prior_scores_display"] == {}


def test_chart_proposal_honestly_unavailable(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_prop")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_prop", "pw123")
    created = _create_patient(client, csrf, "0012345624")
    chart = client.get(f"/api/v1/patients/{created['patient']['id']}/chart")
    assert chart.status_code == 200, chart.text
    # Literal honest status — no fake successful proposal.
    assert chart.json()["proposal"] == {
        "status": "unavailable",
        "reason": "generation_not_implemented",
    }


def test_phone_update_via_existing_history_path_then_chart_still_clean(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_phone")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_phone", "pw123")
    encounter_id, patient_id, _ = _followup_id(client, csrf, "0012345625", _followup_baseline())
    saved = client.post(
        f"/api/v1/encounters/{encounter_id}/history",
        json={
            "values": dict(BASELINE_HISTORY),
            "reconciliation": {"status": "reconciled"},
            "phone_update": "+43 699 777",
        },
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["phone_update"] == "+43 699 777"
    assert saved.json()["reconciliation"]["status"] == "reconciled"
    assert saved.headers["etag"] == '"2"'
    # Baseline endpoint reflects the reconciled state with the same history.
    reread = client.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert reread.status_code == 200, reread.text
    assert reread.json()["reconciliation"]["status"] == "reconciled"
    # The shared chart still carries no draft content or phone text.
    chart = client.get(f"/api/v1/patients/{patient_id}/chart")
    assert chart.status_code == 200, chart.text
    assert "+43 699 777" not in chart.text
    assert "draft_data" not in chart.json()


def test_extended_create_honors_etag_and_idempotency_conventions(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_conv")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_conv", "pw123")
    created = _create_patient(client, csrf, "0012345626")
    patient_id = created["patient"]["id"]
    registration_id = created["draft"]["id"]
    assert (
        client.post(
            f"/api/v1/encounters/{registration_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        ).status_code
        == 200
    )
    # Revision/ETag: fresh follow-up is revision 1 with a matching ETag.
    first = _create_followup(client, csrf, patient_id, _followup_baseline(), key="fu-conv-001")
    assert first.status_code == 201, first.text
    assert first.json()["revision"] == 1
    assert first.headers["etag"] == '"1"'
    assert first.json()["server_timestamp"]
    # Idempotency: same key + same body replays without a second draft; the
    # slot conflict would otherwise be a 409, not a replayed 201.
    replay = _create_followup(client, csrf, patient_id, _followup_baseline(), key="fu-conv-001")
    assert replay.status_code == 201, replay.text
    assert replay.json()["encounter"]["id"] == first.json()["encounter"]["id"]
    assert replay.headers["etag"] == '"1"'
    # Same key + changed body is a 409 key conflict.
    conflict = _create_followup(client, csrf, patient_id, None, key="fu-conv-001")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    # Malformed idempotency key is 422 at the edge (not routed to creation).
    bad_key = _create_followup(client, csrf, patient_id, None, key="not valid!!")
    assert bad_key.status_code == 422, bad_key.text
