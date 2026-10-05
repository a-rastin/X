"""Attributed page notes with separation proof (S13, backend only; seam T1).

Through public HTTP + real PostgreSQL (no frontend, no snapshot machinery):

1. Author adds a timestamped note to any wizard page (incl. demographics
   after draft creation) and sees it after resume; a retried idempotent
   command creates ONE note (same key + same body replays, same key +
   changed body is 409 IDEMPOTENCY_CONFLICT).
2. Another physician/admin cannot author/list the draft's notes (403 without
   content, 401 anonymous, 404 unknown/discarded). Actor/time are
   server-derived; notes are append-only (no edit/delete endpoint —
   correction is a new note); note create bumps the encounter revision
   (If-Match fenced, 412 STALE_REVISION on mismatch) so S07 autosave stays
   coherent.
3. Notes stay SEPARATE from history: never in draft_data.history values,
   never analysis_visible — proven via the explicit serializer exclusion
   (``notes.exclude_notes_from_analysis``) plus HTTP assertions that history
   and draft serializers never carry notes. Text is verbatim (literal markup
   kept); bounds are non-empty / max 2000 chars with a page allowlist (422).
4. S40/S41/S59 are recorded (module docstring + pinned test) as the
   mandatory end-to-end note-noninterference checks; they are NOT
   implemented here.
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
from x_insight.cases import history as history_service
from x_insight.cases import notes as notes_service
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


def _create_draft(client, csrf, identifier="0012345678") -> dict:
    created = client.post(
        "/api/v1/patients", json=_valid_patient(identifier), headers=_auth_headers(csrf)
    )
    assert created.status_code == 201, created.text
    return created.json()


def _setup_draft(admin_client, clean_registry, monkeypatch, username, identifier):
    _make_physician(admin_client, username)
    client, csrf, user = _physician_client(clean_registry, monkeypatch, username, "pw123")
    created = _create_draft(client, csrf, identifier)
    return client, csrf, user, created["draft"]["id"]


def _post_note(client, csrf, encounter_id, page, note_text, revision, key=None):
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": page, "text": note_text},
        headers=headers,
    )


def _get_notes(client, encounter_id, params=None):
    return client.get(f"/api/v1/encounters/{encounter_id}/notes", params=params or {})


# --- Slice 1: author adds a timestamped note, resume, idempotent retry ---


def test_author_adds_note_and_sees_it_after_resume(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_resume", "0012345678"
    )
    created = _post_note(client, csrf, encounter_id, "diagnosis", "consider DDx", 1)
    assert created.status_code == 201, created.text
    body = created.json()
    note = body["note"]
    assert uuid.UUID(note["id"])  # opaque UUID note ID
    assert note["encounter_id"] == encounter_id
    assert note["page"] == "diagnosis"
    assert note["author_id"] == user["id"]
    assert note["author_display"] == user["username"]
    assert contracts.parse_utc(note["created_at"]) is not None
    assert note["text"] == "consider DDx"
    assert body["revision"] == 2
    assert body["server_timestamp"] == note["created_at"]
    assert created.headers["etag"] == '"2"'

    listed = _get_notes(client, encounter_id)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"] == [note]
    assert listed.json()["revision"] == 2
    assert listed.headers["etag"] == '"2"'

    # Resume: a brand-new HTTP client re-authenticates and reads server truth.
    fresh_client = TestClient(app)
    login = fresh_client.post(
        "/api/v1/auth/login",
        json={"username": "dr_notes_resume", "password": "pw123", "role": "physician"},
    )
    assert login.status_code == 200
    resumed = fresh_client.get(f"/api/v1/encounters/{encounter_id}/notes")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["total"] == 1
    assert resumed.json()["items"][0]["text"] == "consider DDx"


def test_demographics_page_supported_after_draft_creation(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_demo", "0012345679"
    )
    created = _post_note(client, csrf, encounter_id, "demographics", "lives alone", 1)
    assert created.status_code == 201, created.text
    assert created.json()["note"]["page"] == "demographics"
    listed = _get_notes(client, encounter_id, {"page": "demographics"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_idempotent_retry_creates_one_note(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_replay", "0012345680"
    )
    first = _post_note(client, csrf, encounter_id, "panss", "affect flat", 1, key="note-retry-001")
    assert first.status_code == 201, first.text
    replay = _post_note(client, csrf, encounter_id, "panss", "affect flat", 1, key="note-retry-001")
    assert replay.status_code == 201, replay.text
    assert replay.json()["note"]["id"] == first.json()["note"]["id"]
    assert replay.json()["revision"] == first.json()["revision"] == 2
    listed = _get_notes(client, encounter_id)
    assert listed.json()["total"] == 1


def test_same_key_changed_body_conflicts(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_conflict", "0012345681"
    )
    first = _post_note(client, csrf, encounter_id, "cssrs", "denies SI", 1, key="note-clash-001")
    assert first.status_code == 201, first.text
    clash = _post_note(client, csrf, encounter_id, "cssrs", "reports SI", 1, key="note-clash-001")
    assert clash.status_code == 409, clash.text
    assert clash.json()["code"] == "IDEMPOTENCY_CONFLICT"
    listed = _get_notes(client, encounter_id)
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["text"] == "denies SI"


def test_note_create_bumps_revision_for_autosave_coherence(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_bump", "0012345682"
    )
    assert _post_note(client, csrf, encounter_id, "history", "onset 2020", 1).status_code == 201
    # A stale tab autosaving against the pre-note revision now reconciles first.
    stale = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": "stale"}},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    fresh = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": {"history": "fresh"}},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["revision"] == 3


# --- Slice 2: ownership, server-derived attribution, append-only ---


def test_stranger_and_admin_cannot_author_or_list(
    admin_client, clean_registry, monkeypatch
) -> None:
    owner, owner_csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_owner", "0012345683"
    )
    _make_physician(admin_client, "dr_notes_stranger")
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_notes_stranger", "pw123"
    )
    secret = "owner-only note content"
    assert _post_note(owner, owner_csrf, encounter_id, "effects", secret, 1).status_code == 201

    stranger_post = _post_note(stranger, stranger_csrf, encounter_id, "effects", "hijack", 2)
    assert stranger_post.status_code == 403, stranger_post.text
    stranger_list = stranger.get(f"/api/v1/encounters/{encounter_id}/notes")
    assert stranger_list.status_code == 403, stranger_list.text
    admin_post = _post_note(
        admin_client["client"], admin_client["csrf"], encounter_id, "effects", "hijack", 2
    )
    assert admin_post.status_code == 403, admin_post.text
    admin_list = admin_client["client"].get(f"/api/v1/encounters/{encounter_id}/notes")
    assert admin_list.status_code == 403, admin_list.text
    for denied in (stranger_post, stranger_list, admin_post, admin_list):
        assert secret not in denied.text
        assert "items" not in denied.json()
        assert "note" not in denied.json()

    truth = _get_notes(owner, encounter_id)
    assert truth.status_code == 200
    assert truth.json()["total"] == 1
    assert truth.json()["items"][0]["text"] == secret


def test_anonymous_cannot_author_or_list(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_anon", "0012345685"
    )
    assert _post_note(client, csrf, encounter_id, "proposal", "plan draft", 1).status_code == 201
    bare = TestClient(app)
    assert (
        bare.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "proposal", "text": "anon"},
            headers={"If-Match": '"2"'},
        ).status_code
        == 401
    )
    assert bare.get(f"/api/v1/encounters/{encounter_id}/notes").status_code == 401


def test_unknown_and_discarded_encounters_are_404(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_gone", "0012345686"
    )
    unknown = str(uuid.uuid4())
    assert _post_note(client, csrf, unknown, "diagnosis", "x", 1).status_code == 404
    assert client.get(f"/api/v1/encounters/{unknown}/notes").status_code == 404

    discard = client.post(
        f"/api/v1/encounters/{encounter_id}/discard",
        json={"confirm": True},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert discard.status_code == 200, discard.text
    assert _post_note(client, csrf, encounter_id, "diagnosis", "x", 2).status_code == 404
    assert _get_notes(client, encounter_id).status_code == 404


def test_actor_and_time_are_server_derived(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_server", "0012345687"
    )
    forged = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={
            "page": "diagnosis",
            "text": "server truth",
            "author_id": str(uuid.uuid4()),
            "author_display": "mallory",
            "created_at": "2001-01-01T00:00:00Z",
        },
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert forged.status_code == 422, forged.text
    created = _post_note(client, csrf, encounter_id, "diagnosis", "server truth", 1)
    assert created.status_code == 201, created.text
    note = created.json()["note"]
    assert note["author_id"] == user["id"]
    assert note["author_display"] == user["username"]
    assert note["created_at"] != "2001-01-01T00:00:00Z"


def test_notes_are_append_only(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_append", "0012345688"
    )
    created = _post_note(client, csrf, encounter_id, "effects", "first impression", 1)
    assert created.status_code == 201
    note_id = created.json()["note"]["id"]
    headers = {**_auth_headers(csrf), "If-Match": '"2"'}
    # No edit/delete endpoint exists: correction is a new note.
    assert client.patch(f"/api/v1/encounters/{encounter_id}/notes/{note_id}").status_code in (
        404,
        405,
    )
    assert (
        client.delete(f"/api/v1/encounters/{encounter_id}/notes/{note_id}", headers=headers)
    ).status_code in (404, 405)
    assert client.put(
        f"/api/v1/encounters/{encounter_id}/notes/{note_id}",
        json={"text": "rewrite"},
        headers=headers,
    ).status_code in (404, 405)
    correction = _post_note(client, csrf, encounter_id, "effects", "corrected view", 2)
    assert correction.status_code == 201, correction.text
    assert correction.json()["note"]["id"] != note_id
    assert _get_notes(client, encounter_id).json()["total"] == 2


def test_if_match_fencing_for_notes(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_fence", "0012345689"
    )
    stale = _post_note(client, csrf, encounter_id, "panss", "stale", 7)
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "STALE_REVISION"
    assert _get_notes(client, encounter_id).json()["total"] == 0
    for raw in (None, "*", '"not-a-revision"'):
        headers = {**_auth_headers(csrf)}
        if raw is not None:
            headers["If-Match"] = raw
        response = client.post(
            f"/api/v1/encounters/{encounter_id}/notes",
            json={"page": "panss", "text": "x"},
            headers=headers,
        )
        assert response.status_code == 422, (raw, response.text)


def test_note_create_requires_csrf(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_csrf", "0012345690"
    )
    response = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"page": "diagnosis", "text": "no csrf"},
        headers={"If-Match": '"1"'},
    )
    assert response.status_code == 403, response.text
    assert _get_notes(client, encounter_id).json()["total"] == 0


def test_note_audit_is_attributed_without_note_body(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, user, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_audit", "0012345691"
    )
    secret = "audit-sensitive note wording"
    assert _post_note(client, csrf, encounter_id, "history", secret, 1).status_code == 201
    with clean_registry.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT operation, actor FROM audit_events "
                    "WHERE operation = 'notes.create.success' ORDER BY occurred_at DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
    assert row is not None
    assert row["actor"] == user["username"]
    assert secret not in str(dict(row))


# --- Slice 3: separation from history/analysis, verbatim text, bounds ---


def test_notes_never_appear_in_draft_or_history(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_sep", "0012345692"
    )
    secret = "separate-channel wording"
    assert _post_note(client, csrf, encounter_id, "history", secret, 1).status_code == 201

    draft = client.get(f"/api/v1/encounters/{encounter_id}")
    assert draft.status_code == 200
    assert "notes" not in draft.json()["draft_data"]
    assert secret not in draft.text

    history = client.get(f"/api/v1/encounters/{encounter_id}/history")
    assert history.status_code == 200, history.text
    assert "notes" not in history.json()
    assert secret not in history.text
    assert history.json()["analysis_visible"] is True

    effects = client.get(f"/api/v1/encounters/{encounter_id}/effects")
    assert effects.status_code == 200, effects.text
    assert "notes" not in effects.json()
    assert secret not in effects.text


def test_history_serializers_never_carry_notes(admin_client, clean_registry, monkeypatch) -> None:
    """History/draft serializers exclude notes; the S40 exclusion strips them."""
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_ser", "0012345693"
    )
    assert (
        _post_note(client, csrf, encounter_id, "history", "serializer probe", 1).status_code == 201
    )
    state = history_service.compute_history_state({}, author_id="someone", revision=2)
    assert "notes" not in state
    assert state["analysis_visible"] is True
    projected = notes_service.exclude_notes_from_analysis(
        {"history": {"values": {}}, "notes": [{"text": "must not leak"}]}
    )
    assert "notes" not in projected
    assert projected["history"] == {"values": {}}
    assert notes_service.ANALYSIS_EXCLUDED_CHANNELS == ("notes",)


def test_note_text_preserved_verbatim_including_markup(
    admin_client, clean_registry, monkeypatch
) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_markup", "0012345694"
    )
    literal = "<b>bold</b> & <script>alert(1)</script>  spaced   out"
    created = _post_note(client, csrf, encounter_id, "diagnosis", literal, 1)
    assert created.status_code == 201, created.text
    assert created.json()["note"]["text"] == literal
    assert _get_notes(client, encounter_id).json()["items"][0]["text"] == literal


def test_note_text_bounds(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_bounds", "0012345695"
    )
    empty = _post_note(client, csrf, encounter_id, "diagnosis", "", 1)
    assert empty.status_code == 422, empty.text
    assert "text" in empty.json()["field_errors"]
    too_long = _post_note(client, csrf, encounter_id, "diagnosis", "x" * 2001, 1)
    assert too_long.status_code == 422, too_long.text
    assert "text" in too_long.json()["field_errors"]
    assert _get_notes(client, encounter_id).json()["total"] == 0
    exactly_max = _post_note(client, csrf, encounter_id, "diagnosis", "x" * 2000, 1)
    assert exactly_max.status_code == 201, exactly_max.text
    assert len(exactly_max.json()["note"]["text"]) == 2000


def test_note_page_allowlist(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_pages", "0012345696"
    )
    bad = _post_note(client, csrf, encounter_id, "chart", "no such page", 1)
    assert bad.status_code == 422, bad.text
    assert "page" in bad.json()["field_errors"]
    missing = client.post(
        f"/api/v1/encounters/{encounter_id}/notes",
        json={"text": "no page"},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert missing.status_code == 422
    revision = 1
    for page in notes_service.ALLOWED_PAGES:
        created = _post_note(client, csrf, encounter_id, page, f"note for {page}", revision)
        assert created.status_code == 201, (page, created.text)
        revision = created.json()["revision"]
    assert _get_notes(client, encounter_id).json()["total"] == len(notes_service.ALLOWED_PAGES)


# --- Slice 4: list filtering, pagination, mandatory future checks ---


def test_list_filter_and_pagination(admin_client, clean_registry, monkeypatch) -> None:
    client, csrf, _, encounter_id = _setup_draft(
        admin_client, clean_registry, monkeypatch, "dr_notes_list", "0012345697"
    )
    revision = 1
    for page, note_text in (
        ("diagnosis", "first"),
        ("diagnosis", "second"),
        ("panss", "third"),
    ):
        created = _post_note(client, csrf, encounter_id, page, note_text, revision)
        assert created.status_code == 201, created.text
        revision = created.json()["revision"]
    filtered = _get_notes(client, encounter_id, {"page": "diagnosis"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert [item["text"] for item in filtered.json()["items"]] == ["first", "second"]
    paged = _get_notes(client, encounter_id, {"limit": 1, "offset": 1})
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert [item["text"] for item in paged.json()["items"]] == ["second"]
    assert paged.json()["revision"] == revision
    assert paged.headers["etag"] == contracts.format_etag(revision)

    bad_page = _get_notes(client, encounter_id, {"page": "chart"})
    assert bad_page.status_code == 422, bad_page.text
    too_wide = _get_notes(client, encounter_id, {"limit": 101})
    assert too_wide.status_code == 422, too_wide.text


def test_future_note_noninterference_checks_are_recorded() -> None:
    """S40/S41/S59 stay the mandatory end-to-end note-noninterference checks."""
    docstring = str(notes_service.__doc__)
    for session in ("S40", "S41", "S59"):
        assert session in docstring
    assert "note-noninterference" in docstring
