"""Shared chart + follow-up draft entry (S14, T1, backend only).

Through public HTTP + real PostgreSQL (no sqlite, no test-only prod route);
S49 signing is NOT a dependency — the baseline travels as a test-only inline
snapshot (``baseline={history_values, prior_scores, medications}`` + opaque
``baseline_encounter_id``), never via a bypass-sign production route.

Slices (tasks.md S14, plan.md §§2.2-2.3; FR-20-23):
1. Any active physician can read demographics/signed chart and create a
   follow-up when the single-draft slot is free; only the author can read/edit
   the draft and its derived artifacts (GET followup-baseline author-only;
   strangers/admin 403 without content, anon 401, unknown 404).
2. History/medications copy with baseline provenance (``copied_baseline`` +
   ``baseline_encounter_id``) and reconciliation ``pending``; PANSS/C-SSRS
   start unanswered (``answers {}``) with prior scores shown historical-only
   (``historical: True`` display, never as answers).
3. Concurrent creates for one patient yield one 201 + generic 409
   OPEN_DRAFT_EXISTS without leak; different patients stay independent.
   S48d/S51 demographic-change staleness is out-of-scope (hook documented
   only — chart exposes the demographics + baseline shapes that hook consumes).
4. Chronology, draft badges, phone/history/effect pages, and honest
   ``unavailable/generation_not_implemented`` proposal (no fake success).

Handoff: follow-ups start via POST /patients/{id}/encounters {kind:
follow_up, baseline?, baseline_encounter_id?} with CSRF + fresh
Idempotency-Key; reads use the session cookie; writes use If-Match/ETag.
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


def _truncate_cases(engine) -> None:
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
    _truncate_cases(migrated_test_engine)
    service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    service.clear_login_throttle()
    _truncate_cases(migrated_test_engine)
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
    csrf = response.json()["csrf_token"]
    user = response.json()["user"]
    return {"client": client, "csrf": csrf, "user": user, "engine": clean_registry}


def _auth_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_physician(admin_client, username: str, password: str = "pw123") -> dict:
    client = admin_client["client"]
    created = client.post(
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


def _valid_payload(identifier: str = "0012345678", **overrides):
    payload = {
        "identifier": identifier,
        "given_name": "Anna",
        "family_name": "Novak",
        "sex": "F",
        "age": 30,
        "clinical_status": "first_time",
        "phone": "+43 699 123456",
    }
    payload.update(overrides)
    return payload


def _create_patient(client, csrf, identifier="0012345678"):
    response = client.post(
        "/api/v1/patients", json=_valid_payload(identifier), headers=_auth_headers(csrf)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _discard(client, csrf, encounter_id, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers=headers,
    )


def _create_followup(
    client, csrf, patient_id, baseline=None, baseline_encounter_id=None, kind="follow_up", key=None
):
    headers = _auth_headers(csrf)
    if key is not None:
        headers = {**headers, "Idempotency-Key": key}
    body: dict = {"kind": kind}
    if baseline is not None:
        body["baseline"] = baseline
    if baseline_encounter_id is not None:
        body["baseline_encounter_id"] = baseline_encounter_id
    return client.post(f"/api/v1/patients/{patient_id}/encounters", json=body, headers=headers)


def _free_slot_for_followup(client, csrf, created: dict) -> str:
    """Discard the registration draft from POST /patients; return patient_id."""
    encounter_id = created["draft"]["id"]
    patient_id = created["patient"]["id"]
    discarded = _discard(client, csrf, encounter_id, 1)
    assert discarded.status_code == 200, discarded.text
    return patient_id


# Test-only signed baseline fixture (inline snapshot; S49 is not a dependency).
BASELINE_HISTORY = {
    "h_exposure_dopamine_blocker": "yes",
    "h_monitoring_baseline": "no",
    "h_onset_timing": "unknown",
}
BASELINE_MEDICATIONS = [
    {"catalog_drug_id": "demo-aspirin"},
    {"catalog_drug_id": "demo-metformin"},
]
BASELINE_SCORES = {"panss_total": 68, "cssrs_severity": 3}
BASELINE_NOTE = "test-only signed baseline snapshot"
BASELINE_ENCOUNTER_ID = "test-signed-baseline-001"


def _baseline_payload(**overrides) -> dict:
    payload = {
        "history_values": dict(BASELINE_HISTORY),
        "prior_scores": dict(BASELINE_SCORES),
        "medications": [dict(entry) for entry in BASELINE_MEDICATIONS],
        "provenance_note": BASELINE_NOTE,
    }
    payload.update(overrides)
    return payload


# --- Slice 1: shared reads + author-only draft/derived artifacts ---


def test_shared_chart_readable_by_any_physician_and_admin_without_draft_content(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_owner")
    _make_physician(admin_client, "dr_chart_stranger")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_owner", "pw123")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_stranger", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = created["patient"]["id"]
    identifier = created["patient"]["identifier"]

    readers = [
        owner.get(f"/api/v1/patients/{patient_id}/chart"),
        stranger.get(f"/api/v1/patients/{patient_id}/chart"),
        admin_client["client"].get(f"/api/v1/patients/{patient_id}/chart"),
    ]
    for response in readers:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["patient"]["identifier"] == identifier
        assert body["patient"]["given_name"] == "Anna"
        # Signed chart is empty until S49 signing exists.
        assert body["signed_encounters"] == []
        assert body["chronology"] == []
        # Slot badge only: occupancy, never author/content/revision.
        assert body["open_draft"] == {"exists": True}
        assert body["proposal"]["status"] == "unavailable"
        assert "draft_data" not in body
        assert "author_id" not in body
        assert "revision" not in body
        assert "owner-only" not in response.text


def test_chart_auth_gates_anon_401_and_unknown_404(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_gate")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_gate", "pw123")
    created = _create_patient(client, csrf)
    patient_id = created["patient"]["id"]

    anon = TestClient(app)
    assert anon.get(f"/api/v1/patients/{patient_id}/chart").status_code == 401

    missing = uuid.uuid4()
    assert client.get(f"/api/v1/patients/{missing}/chart").status_code == 404
    assert anon.get(f"/api/v1/encounters/{missing}/followup-baseline").status_code == 401


def test_followup_create_read_edit_author_only(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_fu_owner")
    _make_physician(admin_client, "dr_fu_stranger")
    owner, owner_csrf, owner_user = _physician_client(
        clean_registry, monkeypatch, "dr_fu_owner", "pw123"
    )
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_fu_stranger", "pw123"
    )
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)

    # Slot free: any active physician can start a follow-up draft.
    started = _create_followup(owner, owner_csrf, patient_id, key=f"fu-create-{uuid.uuid4()}")
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    assert started.json()["encounter"]["kind"] == "follow_up"
    assert started.json()["encounter"]["author_id"] == owner_user["id"]
    assert started.headers["etag"] == '"1"'

    # Author reads/edits the draft and its derived baseline artifact.
    assert owner.get(f"/api/v1/encounters/{encounter_id}").status_code == 200
    baseline = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["revision"] == 1
    assert baseline.headers["etag"] == '"1"'

    patched = owner.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"working_note": "follow-up plan"}},
        headers={**_auth_headers(owner_csrf), "If-Match": '"1"'},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == 2

    # Strangers (other physician, admin) get 403 without content on every
    # draft/derived route; the failed writes changed nothing.
    secret_marker = "follow-up plan"
    for denied in (
        stranger.get(f"/api/v1/encounters/{encounter_id}"),
        stranger.patch(
            f"/api/v1/encounters/{encounter_id}",
            json={"draft_data": {"hijack": True}},
            headers={**_auth_headers(stranger_csrf), "If-Match": '"2"'},
        ),
        stranger.post(
            f"/api/v1/encounters/{encounter_id}/discard",
            json={"confirm": True},
            headers={**_auth_headers(stranger_csrf), "If-Match": '"2"'},
        ),
        stranger.get(f"/api/v1/encounters/{encounter_id}/followup-baseline"),
        stranger.get(f"/api/v1/encounters/{encounter_id}/history"),
        stranger.get(f"/api/v1/encounters/{encounter_id}/effects"),
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}"),
        admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/followup-baseline"),
    ):
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"
        assert secret_marker not in denied.text
        assert "draft_data" not in denied.json()

    truth = owner.get(f"/api/v1/encounters/{encounter_id}")
    assert truth.json()["draft_data"] == {"working_note": "follow-up plan"}
    assert truth.json()["revision"] == 2

    # Unknown draft is 404 for the author; anon is 401 without content.
    assert owner.get(f"/api/v1/encounters/{uuid.uuid4()}/followup-baseline").status_code == 404
    anon = TestClient(app)
    assert anon.get(f"/api/v1/encounters/{encounter_id}/followup-baseline").status_code == 401


def test_admin_reads_chart_but_not_followup_baseline(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_fu_admin")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_fu_admin", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)
    started = _create_followup(owner, owner_csrf, patient_id)
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]

    chart = admin_client["client"].get(f"/api/v1/patients/{patient_id}/chart")
    assert chart.status_code == 200, chart.text
    assert chart.json()["open_draft"] == {"exists": True}

    denied = admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert denied.status_code == 403, denied.text
    assert "draft_data" not in denied.json()


# --- Slice 2: baseline copy with provenance + pending, empty answers ---


def test_followup_baseline_copy_provenance_pending_and_empty_answers(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_base_copy")
    owner, owner_csrf, owner_user = _physician_client(
        clean_registry, monkeypatch, "dr_base_copy", "pw123"
    )
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)

    started = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(),
        baseline_encounter_id=BASELINE_ENCOUNTER_ID,
        key=f"fu-base-{uuid.uuid4()}",
    )
    assert started.status_code == 201, started.text
    body = started.json()
    encounter_id = body["encounter"]["id"]
    draft_data = body["draft_data"]

    # History values are copied verbatim with per-field baseline provenance.
    assert draft_data["history"]["values"] == BASELINE_HISTORY
    for field_id in BASELINE_HISTORY:
        provenance = draft_data["history"]["provenance"][field_id]
        assert provenance["source"] == "copied_baseline"
        assert provenance["author_id"] == owner_user["id"]
        assert provenance["baseline_encounter_id"] == BASELINE_ENCOUNTER_ID
        assert provenance["recorded_at"]
    # Reconciliation is required before review; phone starts untouched.
    assert draft_data["history"]["reconciliation"] == {
        "status": "pending",
        "baseline_encounter_id": BASELINE_ENCOUNTER_ID,
    }
    assert draft_data["history"]["phone_update"] is None
    # PANSS/C-SSRS always start unanswered, even with prior scores present.
    assert draft_data["panss"] == {"answers": {}}
    assert draft_data["cssrs"] == {"answers": {}}
    # The stored snapshot keeps prior scores/meds/note for historical display.
    snapshot = draft_data["followup_baseline"]
    assert snapshot["prior_scores"] == BASELINE_SCORES
    assert snapshot["medications"] == BASELINE_MEDICATIONS
    assert snapshot["provenance_note"] == BASELINE_NOTE
    assert snapshot["baseline_encounter_id"] == BASELINE_ENCOUNTER_ID

    # Author-only baseline preview: same copy + historical-only score display.
    preview = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline")
    assert preview.status_code == 200, preview.text
    state = preview.json()
    assert state["baseline"]["history_values"] == BASELINE_HISTORY
    assert state["baseline"]["medications"] == BASELINE_MEDICATIONS
    assert state["baseline"]["provenance_note"] == BASELINE_NOTE
    assert state["baseline"]["baseline_encounter_id"] == BASELINE_ENCOUNTER_ID
    assert state["reconciliation"] == {
        "status": "pending",
        "baseline_encounter_id": BASELINE_ENCOUNTER_ID,
    }
    assert state["prior_scores_display"] == {
        "panss_total": {"value": 68, "historical": True},
        "cssrs_severity": {"value": 3, "historical": True},
    }
    # Historical display is never seeded as current answers.
    authored = owner.get(f"/api/v1/encounters/{encounter_id}").json()["draft_data"]
    assert authored["panss"] == {"answers": {}}
    assert authored["cssrs"] == {"answers": {}}


def test_prior_scores_never_become_answers_and_partial_scores_allowed(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_base_partial")
    owner, owner_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_base_partial", "pw123"
    )
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)

    started = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(prior_scores={"panss_total": 51}),
        baseline_encounter_id=BASELINE_ENCOUNTER_ID,
    )
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    draft_data = started.json()["draft_data"]
    assert draft_data["panss"] == {"answers": {}}
    assert draft_data["cssrs"] == {"answers": {}}

    preview = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline").json()
    assert preview["prior_scores_display"] == {
        "panss_total": {"value": 51, "historical": True},
    }


def test_followup_without_baseline_has_empty_shell(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_base_empty")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_base_empty", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)

    started = _create_followup(owner, owner_csrf, patient_id)
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    draft_data = started.json()["draft_data"]
    assert draft_data["history"]["values"] == {}
    assert draft_data["history"]["reconciliation"] == {
        "status": "not_required",
        "baseline_encounter_id": None,
    }
    assert draft_data["panss"] == {"answers": {}}
    assert draft_data["cssrs"] == {"answers": {}}
    assert "followup_baseline" not in draft_data

    preview = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline").json()
    assert preview["baseline"] is None
    assert preview["reconciliation"]["status"] == "not_required"
    assert preview["prior_scores_display"] == {}


def test_followup_baseline_validation_rejects_bad_shapes(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_base_valid")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_base_valid", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)

    # Baseline copy applies to follow-up drafts only.
    registration = _create_followup(
        owner, owner_csrf, patient_id, baseline=_baseline_payload(), kind="registration"
    )
    assert registration.status_code == 422, registration.text

    # Unknown top-level baseline key is rejected.
    unknown = _create_followup(owner, owner_csrf, patient_id, baseline={"bogus": {}})
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["code"] == "VALIDATION_FAILED"

    # FR-14-excluded regimen fields never copy, even when forged.
    excluded = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(history_values={"dose": "10mg"}),
    )
    assert excluded.status_code == 422, excluded.text

    # Unknown prior-score keys are rejected (historical display only).
    bad_score = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(prior_scores={"madrs_total": 12}),
    )
    assert bad_score.status_code == 422, bad_score.text

    # None of the rejected shapes occupied the slot.
    assert owner.get(f"/api/v1/patients/{patient_id}/chart").json()["open_draft"] == {
        "exists": False
    }


# --- Slice 3: single-slot races, independence, S48d/S51 hook note ---


def test_sequential_second_followup_conflicts_generically_without_leak(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_race_seq_a")
    _make_physician(admin_client, "dr_race_seq_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_race_seq_a", "pw123")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_race_seq_b", "pw123")
    created = _create_patient(client_a, csrf_a)
    patient_id = _free_slot_for_followup(client_a, csrf_a, created)

    first = _create_followup(
        client_a,
        csrf_a,
        patient_id,
        baseline=_baseline_payload(),
        baseline_encounter_id=BASELINE_ENCOUNTER_ID,
    )
    assert first.status_code == 201, first.text
    assert first.json()["draft_data"]["history"]["values"] == BASELINE_HISTORY

    for contender, csrf in ((client_a, csrf_a), (client_b, csrf_b)):
        denied = _create_followup(contender, csrf, patient_id)
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "OPEN_DRAFT_EXISTS"
        assert "copied_baseline" not in denied.text
        assert "draft_data" not in denied.json()
        assert "author_id" not in denied.json()

    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE lifecycle = 'draft'")
        ).scalar_one()
    assert drafts == 1


def test_concurrent_followup_creates_allow_one_draft_only(
    admin_client, clean_registry, monkeypatch
) -> None:
    import threading

    _make_physician(admin_client, "dr_race_con_a")
    _make_physician(admin_client, "dr_race_con_b")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_race_con_a", "pw123")
    created = _create_patient(owner, owner_csrf, "0111111111")
    patient_id = created["patient"]["id"]
    assert _discard(owner, owner_csrf, created["draft"]["id"], 1).status_code == 200

    barrier = threading.Barrier(7)
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
            json={"kind": "follow_up"},
            headers=_auth_headers(csrf),
        )
        outcomes.append(response.status_code)
        bodies.append((response.status_code, response.text))

    threads = [
        threading.Thread(target=_attempt, args=(name,))
        for name in (
            "dr_race_con_a",
            "dr_race_con_a",
            "dr_race_con_a",
            "dr_race_con_b",
            "dr_race_con_b",
            "dr_race_con_b",
            "dr_race_con_b",
        )
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == [201] + [409] * 6, outcomes
    for status, body in bodies:
        if status == 409:
            assert '"OPEN_DRAFT_EXISTS"' in body
            assert "draft_data" not in body
            assert "author_id" not in body
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE patient_id = :pid AND lifecycle = 'draft'"),
            {"pid": patient_id},
        ).scalar_one()
    assert drafts == 1


def test_different_patients_have_independent_followup_slots(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_multi_slot")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_multi_slot", "pw123")
    first = _create_patient(owner, owner_csrf, "0222222222")
    second = _create_patient(owner, owner_csrf, "0333333333")
    first_id = _free_slot_for_followup(owner, owner_csrf, first)
    second_id = _free_slot_for_followup(owner, owner_csrf, second)

    for patient_id in (first_id, second_id):
        created = _create_followup(owner, owner_csrf, patient_id)
        assert created.status_code == 201, created.text
        assert created.json()["encounter"]["patient_id"] == patient_id

    assert owner.get(f"/api/v1/patients/{first_id}/chart").json()["open_draft"] == {"exists": True}
    assert owner.get(f"/api/v1/patients/{second_id}/chart").json()["open_draft"] == {"exists": True}


def test_s48d_s51_demographic_staleness_is_documented_hook_only(
    admin_client, clean_registry, monkeypatch
) -> None:
    """S48d/S51 owns relevant-change staleness; this session exposes the hook shapes only.

    The chart read carries the shared demographics plus the follow-up's pinned
    baseline snapshot that the future hook will compare. No affected-result
    invalidation happens here: the baseline preview stays readable and no
    stale/freshness verdict is fabricated.
    """
    _make_physician(admin_client, "dr_hook_note")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_hook_note", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)
    started = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(),
        baseline_encounter_id=BASELINE_ENCOUNTER_ID,
    )
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]

    chart = owner.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["patient"]["identifier"] == created["patient"]["identifier"]
    assert chart["open_draft"] == {"exists": True}
    assert "stale" not in chart
    assert "freshness" not in chart

    preview = owner.get(f"/api/v1/encounters/{encounter_id}/followup-baseline").json()
    assert preview["baseline"]["baseline_encounter_id"] == BASELINE_ENCOUNTER_ID
    assert preview["reconciliation"]["status"] == "pending"
    assert all(
        entry.get("historical") is True for entry in preview["prior_scores_display"].values()
    )


# --- Slice 4: chronology/badges/pages/honest proposal, no bypass-sign ---


def test_chart_chronology_badge_and_honest_proposal_transitions(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_chart_flow")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_chart_flow", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = created["patient"]["id"]

    # Registration draft occupies the slot: badge on, no signed history yet.
    chart = owner.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["open_draft"] == {"exists": True}
    assert chart["signed_encounters"] == []
    assert chart["chronology"] == []
    assert chart["proposal"] == {"status": "unavailable", "reason": "generation_not_implemented"}

    # Freeing the slot flips the badge; occupying it with a follow-up flips it back.
    assert _discard(owner, owner_csrf, created["draft"]["id"], 1).status_code == 200
    assert owner.get(f"/api/v1/patients/{patient_id}/chart").json()["open_draft"] == {
        "exists": False
    }
    started = _create_followup(owner, owner_csrf, patient_id)
    assert started.status_code == 201, started.text
    chart = owner.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["open_draft"] == {"exists": True}
    assert chart["chronology"] == []
    # Honest unavailable: verbatim reason, never a fake successful proposal.
    assert chart["proposal"]["status"] == "unavailable"
    assert chart["proposal"]["reason"] == "generation_not_implemented"
    assert "generated" not in chart["proposal"]["reason"]
    assert "success" not in str(chart["proposal"]).lower()


def test_followup_phone_history_effect_pages_author_only_with_review_path(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_pages_owner")
    _make_physician(admin_client, "dr_pages_stranger")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_pages_owner", "pw123")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_pages_stranger", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)
    started = _create_followup(
        owner,
        owner_csrf,
        patient_id,
        baseline=_baseline_payload(),
        baseline_encounter_id=BASELINE_ENCOUNTER_ID,
    )
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]
    revision = started.json()["revision"]

    # Review navigation path: chart -> draft -> baseline/history/effects share
    # one encounter id and one revision fence (ETag + revision field agree).
    draft = owner.get(f"/api/v1/encounters/{encounter_id}")
    assert draft.status_code == 200, draft.text
    assert draft.json()["encounter"]["kind"] == "follow_up"
    assert draft.headers["etag"] == contracts.format_etag(revision)

    history = owner.get(f"/api/v1/encounters/{encounter_id}/history")
    assert history.status_code == 200, history.text
    hstate = history.json()
    assert hstate["values"] == BASELINE_HISTORY
    assert hstate["phone_update"] is None
    assert hstate["reconciliation"] == {
        "status": "pending",
        "baseline_encounter_id": BASELINE_ENCOUNTER_ID,
    }
    for field_id in BASELINE_HISTORY:
        assert hstate["provenance"][field_id]["source"] == "copied_baseline"
    assert hstate["analysis_visible"] is True
    assert hstate["revision"] == revision

    effects = owner.get(f"/api/v1/encounters/{encounter_id}/effects")
    assert effects.status_code == 200, effects.text
    estate = effects.json()
    assert set(estate["effects"]) == {
        "tardive_dyskinesia",
        "akathisia",
        "parkinsonism",
        "acute_dystonia",
    }
    assert set(estate["definition_versions"]) == set(estate["effects"])
    assert estate["revision"] == revision

    # Strangers see neither page content nor the review path.
    for denied in (
        stranger.get(f"/api/v1/encounters/{encounter_id}/history"),
        stranger.get(f"/api/v1/encounters/{encounter_id}/effects"),
    ):
        assert denied.status_code == 403, denied.text
        assert "copied_baseline" not in denied.text
        assert "values" not in denied.json()

    # Phone update travels on the history page state (analysis-visible false
    # identifier channel, like S06): author-only strict save keeps the copy.
    saved = owner.post(
        f"/api/v1/encounters/{encounter_id}/history",
        json={
            "values": dict(BASELINE_HISTORY),
            "reconciliation": {
                "status": "pending",
                "baseline_encounter_id": BASELINE_ENCOUNTER_ID,
            },
            "phone_update": "+43 699 999999",
        },
        headers={**_auth_headers(owner_csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["phone_update"] == "+43 699 999999"
    assert saved.json()["values"] == BASELINE_HISTORY


def test_no_bypass_sign_production_route_exists(admin_client, clean_registry, monkeypatch) -> None:
    """Test-only baselines stay inline; S49 real sign validates (no bypass)."""
    _make_physician(admin_client, "dr_nosign")
    owner, owner_csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_nosign", "pw123")
    created = _create_patient(owner, owner_csrf)
    patient_id = _free_slot_for_followup(owner, owner_csrf, created)
    started = _create_followup(owner, owner_csrf, patient_id)
    assert started.status_code == 201, started.text
    encounter_id = started.json()["encounter"]["id"]

    for method in ("put", "patch"):
        response = getattr(owner, method)(
            f"/api/v1/encounters/{encounter_id}/sign",
            json={},
            headers=_auth_headers(owner_csrf),
        )
        assert response.status_code in (404, 405), response.text
    # S49 real sign exists (POST validates, no bypass): empty body is 422.
    empty_post = owner.post(
        f"/api/v1/encounters/{encounter_id}/sign",
        json={},
        headers=_auth_headers(owner_csrf),
    )
    assert empty_post.status_code == 422, empty_post.text

    openapi = owner.get("/openapi.json")
    assert openapi.status_code == 200
    paths = " ".join(openapi.json()["paths"].keys())
    # S49 ships the real sign/addenda/secondary-plan routes (no bypass shortcut).
    assert "/api/v1/encounters/{encounter_id}/sign" in paths
    # The only bypass route is the S09 diagnosis bypass; no follow-up or
    # fake-success signing bypass exists for the test-only baseline fixture.
    assert "followup" not in paths.lower().replace("followup-baseline", "")
