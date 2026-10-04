"""Patient registration and directory (S06, seam T1, backend only).

Slices (tasks.md S06, plan.md §§2.2-2.3, 4; FR-10, FR-23) through public HTTP
+ real PostgreSQL; no frontend, no clinical content beyond demographics:
1. Physician-only atomic create returns patient + registration draft + server
   timestamp + revision; admin denied, unauthenticated denied; identifier
   round-trips as text; readback for any active authenticated user.
2. Validation failures are 422 with field_errors (ages, decimal ages,
   non-ASCII digits, malformed ID, missing sex/status, invalid names) plus a
   valid NFC-normalized Unicode name passing.
3. Concurrency + idempotency + archived conflict: one winner, 409 losers,
   replay without duplicates, changed-body conflict.
4. Directory search: name/ID substrings, status filter, bounded pagination,
   stable order, shared across physicians, archived opt-in only.

Handoff: physician clients are the registration interface; the response
carries patient + draft (+ encounter alias) + server_timestamp + revision.
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


def _patient_count(engine) -> int:
    with engine.begin() as connection:
        return int(connection.execute(text("SELECT count(*) FROM patients")).scalar_one())


def _audit_count(engine, operation: str) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE operation = :op"),
                {"op": operation},
            ).scalar_one()
        )


# --- Slice 1: atomic create + readback + authorization ---


def test_physician_create_returns_patient_draft_timestamp_revision(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_register")
    client, csrf, user = _physician_client(clean_registry, monkeypatch, "dr_register", "pw123")
    response = client.post("/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf))
    assert response.status_code == 201, response.text
    body = response.json()
    patient = body["patient"]
    # Ten-digit ID round-trips as TEXT with the leading zeros intact.
    assert patient["identifier"] == "0012345678"
    assert isinstance(patient["identifier"], str)
    uuid.UUID(str(patient["id"]))
    assert patient["given_name"] == "Anna"
    assert patient["family_name"] == "Novak"
    assert patient["sex"] == "F"
    assert patient["age"] == 30
    assert patient["clinical_status"] == "first_time"
    assert patient["phone"] == "+43 699 123456"
    assert patient["archived"] is False
    assert patient["revision"] == 1
    assert body["revision"] == 1
    # Registration draft: registration kind, draft lifecycle, authored by creator.
    draft = body["draft"]
    assert draft["patient_id"] == patient["id"]
    assert draft["kind"] == "registration"
    assert draft["lifecycle"] == "draft"
    assert draft["author_id"] == user["id"]
    assert draft["revision"] == 1
    assert body["encounter"]["id"] == draft["id"]
    # Server timestamp is real UTC Zulu time, not an echo.
    moment = contracts.parse_utc(body["server_timestamp"])
    assert moment.tzinfo is not None
    assert abs((contracts.utcnow() - moment).total_seconds()) < 60
    assert _patient_count(clean_registry) == 1
    assert _audit_count(clean_registry, "patients.create.success") == 1


def test_create_readback_shared_across_physicians_and_admin(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_a")
    _make_physician(admin_client, "dr_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_a", "pw123")
    created = client_a.post(
        "/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf_a)
    )
    assert created.status_code == 201, created.text
    patient_id = created.json()["patient"]["id"]
    # Author, another physician, and admin all read the same demographics.
    for reader in (
        client_a.get(f"/api/v1/patients/{patient_id}"),
        _physician_client(clean_registry, monkeypatch, "dr_b", "pw123")[0].get(
            f"/api/v1/patients/{patient_id}"
        ),
        admin_client["client"].get(f"/api/v1/patients/{patient_id}"),
    ):
        assert reader.status_code == 200, reader.text
        assert reader.json()["patient"]["identifier"] == "0012345678"
        assert reader.json()["patient"]["id"] == patient_id
    assert client_a.get(f"/api/v1/patients/{uuid.uuid4()}").status_code == 404


def test_create_authorization_admin_denied_unauthenticated_denied(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_auth")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_auth", "pw123")
    # Admin cannot register (403, plan §2.1); no patient is created.
    denied = admin_client["client"].post(
        "/api/v1/patients",
        json=_valid_payload("0012345679"),
        headers=_auth_headers(admin_client["csrf"]),
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "FORBIDDEN"
    # No session at all is 401 (reads too).
    anonymous = TestClient(app)
    assert anonymous.post("/api/v1/patients", json=_valid_payload("0012345680")).status_code == 401
    assert anonymous.get("/api/v1/patients").status_code == 401
    # Same physician session without CSRF is 403 and creates nothing.
    assert client.post("/api/v1/patients", json=_valid_payload("0012345681")).status_code == 403
    assert _patient_count(clean_registry) == 0


# --- Slice 2: validation field errors 422 ---


@pytest.mark.parametrize(
    "mutate",
    [
        {"age": 17},
        {"age": 100},
        {"age": 30.5},
        {"age": "30"},
        {"age": True},
        {"identifier": "123"},
        {"identifier": "00123456789"},
        {"identifier": "abcdefghij"},
        {"identifier": "00123456 8"},
        {"identifier": "００１２３４５６７８"},  # fullwidth digits, not ASCII
        {"identifier": "٠٠١٢٣٤٥٦٧٨"},  # Arabic-Indic digits, not ASCII
        {"identifier": 12345678},  # numeric: leading zero cannot survive
        {"identifier": ""},
        {"sex": "X"},
        {"sex": "m"},
        {"clinical_status": "new"},
        {"clinical_status": ""},
        {"given_name": ""},
        {"given_name": "John Doe"},
        {"given_name": "Anne-Marie"},
        {"given_name": "O'Brien"},
        {"given_name": "John3"},
        {"given_name": "   "},
        {"family_name": ""},
        {"family_name": "Van Helsing2"},
        {"phone": 12345},  # phone is text, never a number
    ],
)
def test_invalid_demographics_fail_with_field_errors(
    admin_client, clean_registry, monkeypatch, mutate
) -> None:
    _make_physician(admin_client, "dr_valid")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_valid", "pw123")
    payload = _valid_payload("0023456789")
    payload.update(mutate)
    response = client.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf))
    assert response.status_code == 422, f"{mutate}: {response.text}"
    assert response.json()["field_errors"], f"{mutate}: no field_errors"
    assert _patient_count(clean_registry) == 0
    assert _audit_count(clean_registry, "patients.create.success") == 0


@pytest.mark.parametrize("missing", ["sex", "clinical_status", "identifier", "given_name", "age"])
def test_missing_required_fields_fail_with_field_errors(
    admin_client, clean_registry, monkeypatch, missing
) -> None:
    _make_physician(admin_client, "dr_missing")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_missing", "pw123")
    payload = _valid_payload("0033456789")
    del payload[missing]
    response = client.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf))
    assert response.status_code == 422, f"missing {missing}: {response.text}"
    assert missing in response.json()["field_errors"], response.json()
    assert _patient_count(clean_registry) == 0


def test_valid_unicode_name_after_nfc_passes(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_unicode")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_unicode", "pw123")
    # Decomposed input (e + combining acute) normalizes to NFC "José".
    response = client.post(
        "/api/v1/patients",
        json=_valid_payload("0043456789", given_name="José", family_name="Müller"),
        headers=_auth_headers(csrf),
    )
    assert response.status_code == 201, response.text
    patient = response.json()["patient"]
    assert patient["given_name"] == "José"
    assert patient["family_name"] == "Müller"
    # Phone is optional: omitted stays null, empty stays empty.
    for identifier, phone in (("0053456789", None), ("0063456789", "")):
        payload = _valid_payload(identifier)
        if phone is None:
            del payload["phone"]
        else:
            payload["phone"] = phone
        created = client.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf))
        assert created.status_code == 201, created.text
        assert created.json()["patient"]["phone"] == phone


def test_create_audit_is_safe_and_attributed(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_auditp")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_auditp", "pw123")
    assert (
        client.post(
            "/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf)
        ).status_code
        == 201
    )
    with db_module.session_scope(clean_registry) as session:
        rows = (
            session.execute(
                text(
                    "SELECT operation, actor, details FROM audit_events "
                    "WHERE operation = 'patients.create.success'"
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["actor"] == "dr_auditp"
    blob = contracts.canonical_json(rows[0]["details"]).decode().lower()
    assert "0012345678" in blob
    for secret in ("password", "pbkdf2", "token_hash", "csrf", "hash"):
        assert secret not in blob, f"audit leaked {secret}: {blob}"


# --- Slice 3: concurrency + idempotency + archived conflict ---


def test_sequential_duplicate_identifier_conflicts(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_dup")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_dup", "pw123")
    first = client.post("/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf))
    assert first.status_code == 201, first.text
    duplicate = client.post(
        "/api/v1/patients",
        json=_valid_payload(family_name="Other"),
        headers=_auth_headers(csrf),
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "CONFLICT"
    assert duplicate.json()["field_errors"]["identifier"] == ["Already exists."]
    assert _patient_count(clean_registry) == 1
    assert _audit_count(clean_registry, "patients.create.success") == 1


def test_duplicate_archived_identifier_conflicts(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_arch")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_arch", "pw123")
    assert (
        client.post(
            "/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf)
        ).status_code
        == 201
    )
    # Archiving does not release the identifier: uniqueness spans archived rows.
    with clean_registry.begin() as connection:
        connection.execute(text("UPDATE patients SET archived = true"))
    retry = client.post("/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf))
    assert retry.status_code == 409, retry.text
    assert retry.json()["code"] == "CONFLICT"
    assert _patient_count(clean_registry) == 1


def test_concurrent_same_identifier_creates_exactly_one_patient(
    admin_client, clean_registry, monkeypatch
) -> None:
    import threading

    _make_physician(admin_client, "dr_race")
    barrier = threading.Barrier(6)
    outcomes: list[int] = []

    def _attempt() -> None:
        thread_client = TestClient(app)
        login = thread_client.post(
            "/api/v1/auth/login",
            json={"username": "dr_race", "password": "pw123", "role": "physician"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        barrier.wait(timeout=10)
        response = thread_client.post(
            "/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf)
        )
        outcomes.append(response.status_code)

    threads = [threading.Thread(target=_attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == [201] + [409] * 5, outcomes
    assert _patient_count(clean_registry) == 1
    assert _audit_count(clean_registry, "patients.create.success") == 1
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE lifecycle = 'draft'")
        ).scalar_one()
    assert drafts == 1


def test_idempotent_create_replays_without_duplicates(
    admin_client, clean_registry, monkeypatch
) -> None:
    _make_physician(admin_client, "dr_idem")
    client, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_idem", "pw123")
    headers = {**_auth_headers(csrf), "Idempotency-Key": "patients-create-001"}
    first = client.post("/api/v1/patients", json=_valid_payload(), headers=headers)
    assert first.status_code == 201, first.text
    first_body = first.json()
    # Same key + same body returns the original IDs (no duplicate, no audit).
    replay = client.post("/api/v1/patients", json=_valid_payload(), headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.json()["patient"]["id"] == first_body["patient"]["id"]
    assert replay.json()["draft"]["id"] == first_body["draft"]["id"]
    assert _patient_count(clean_registry) == 1
    assert _audit_count(clean_registry, "patients.create.success") == 1
    # Same key + changed body is 409 and creates nothing further.
    conflict = client.post(
        "/api/v1/patients",
        json=_valid_payload("0099999999"),
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert _patient_count(clean_registry) == 1
    # Malformed keys are rejected at the edge like every other command.
    bad_key = client.post(
        "/api/v1/patients",
        json=_valid_payload("0088888888"),
        headers={**_auth_headers(csrf), "Idempotency-Key": "not valid!!"},
    )
    assert bad_key.status_code == 422, bad_key.text


def test_idempotency_scoped_per_physician(admin_client, clean_registry, monkeypatch) -> None:
    _make_physician(admin_client, "dr_key_a")
    _make_physician(admin_client, "dr_key_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_key_a", "pw123")
    client_b, csrf_b, _ = _physician_client(clean_registry, monkeypatch, "dr_key_b", "pw123")
    headers_a = {**_auth_headers(csrf_a), "Idempotency-Key": "shared-key-001"}
    headers_b = {**_auth_headers(csrf_b), "Idempotency-Key": "shared-key-001"}
    first = client_a.post("/api/v1/patients", json=_valid_payload("0111111111"), headers=headers_a)
    assert first.status_code == 201, first.text
    # Same key from another physician is a different (operation, actor) scope.
    second = client_b.post("/api/v1/patients", json=_valid_payload("0222222222"), headers=headers_b)
    assert second.status_code == 201, second.text
    assert second.json()["patient"]["id"] != first.json()["patient"]["id"]
    assert _patient_count(clean_registry) == 2


# --- Slice 4 backend: directory search ---


def _seed_directory(admin_client, clean_registry, monkeypatch):
    _make_physician(admin_client, "dr_search_a")
    _make_physician(admin_client, "dr_search_b")
    client_a, csrf_a, _ = _physician_client(clean_registry, monkeypatch, "dr_search_a", "pw123")
    seed = [
        _valid_payload(
            "1012345678", given_name="Anna", family_name="Novak", clinical_status="first_time"
        ),
        _valid_payload(
            "1023456789", given_name="Annette", family_name="Berger", clinical_status="established"
        ),
        _valid_payload(
            "1098765432", given_name="José", family_name="Müller", clinical_status="established"
        ),
    ]
    for payload in seed:
        created = client_a.post("/api/v1/patients", json=payload, headers=_auth_headers(csrf_a))
        assert created.status_code == 201, created.text
    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE patients SET archived = true WHERE identifier = '1023456789'")
        )
    return client_a


def test_search_name_and_id_substrings_case_insensitive(
    admin_client, clean_registry, monkeypatch
) -> None:
    client_a = _seed_directory(admin_client, clean_registry, monkeypatch)
    # Family-name substring, case-insensitive.
    by_name = client_a.get("/api/v1/patients?query=nov")
    assert by_name.status_code == 200, by_name.text
    assert [item["identifier"] for item in by_name.json()["items"]] == ["1012345678"]
    # Given-name substring in lowercase matches the NFC-stored name.
    by_unicode = client_a.get("/api/v1/patients", params={"query": "mül"})
    assert by_unicode.status_code == 200, by_unicode.text
    assert [item["identifier"] for item in by_unicode.json()["items"]] == ["1098765432"]
    # Identifier substring matches across non-archived rows.
    by_id = client_a.get("/api/v1/patients?query=101234")
    assert by_id.status_code == 200
    assert [item["identifier"] for item in by_id.json()["items"]] == ["1012345678"]
    # LIKE wildcards in the query match literally, not as patterns.
    wildcard = client_a.get("/api/v1/patients?query=%25")
    assert wildcard.status_code == 200
    assert wildcard.json()["items"] == []
    assert wildcard.json()["total"] == 0


def test_search_status_filter_pagination_and_archived_opt_in(
    admin_client, clean_registry, monkeypatch
) -> None:
    client_a = _seed_directory(admin_client, clean_registry, monkeypatch)
    # Default listing excludes archived.
    defaulted = client_a.get("/api/v1/patients")
    assert defaulted.status_code == 200
    assert defaulted.json()["limit"] == 25
    assert defaulted.json()["total"] == 2
    assert [item["identifier"] for item in defaulted.json()["items"]] == [
        "1012345678",
        "1098765432",
    ]
    # Archived patients appear only with the explicit opt-in.
    included = client_a.get("/api/v1/patients?include_archived=true")
    assert included.status_code == 200
    assert included.json()["total"] == 3
    assert [item["identifier"] for item in included.json()["items"]] == [
        "1012345678",
        "1023456789",
        "1098765432",
    ]
    # Clinical-status filter composes with the archived default.
    established = client_a.get("/api/v1/patients?clinical_status=established")
    assert established.status_code == 200
    assert [item["identifier"] for item in established.json()["items"]] == ["1098765432"]
    first_time = client_a.get("/api/v1/patients?clinical_status=first_time&include_archived=true")
    assert [item["identifier"] for item in first_time.json()["items"]] == ["1012345678"]
    # Invalid status is 422 with field errors; pagination stays bounded.
    assert client_a.get("/api/v1/patients?clinical_status=new").status_code == 422
    assert client_a.get("/api/v1/patients?clinical_status=new").json()["field_errors"][
        "clinical_status"
    ]
    page = client_a.get("/api/v1/patients?limit=1&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert [item["identifier"] for item in page.json()["items"]] == ["1098765432"]
    assert client_a.get("/api/v1/patients?limit=101").status_code == 422
    assert client_a.get("/api/v1/patients?limit=0").status_code == 422


def test_search_shared_across_physicians_and_admin(
    admin_client, clean_registry, monkeypatch
) -> None:
    client_a = _seed_directory(admin_client, clean_registry, monkeypatch)
    client_b, _, _ = _physician_client(clean_registry, monkeypatch, "dr_search_b", "pw123")
    mine = client_a.get("/api/v1/patients").json()
    theirs = client_b.get("/api/v1/patients").json()
    assert [item["id"] for item in theirs["items"]] == [item["id"] for item in mine["items"]]
    admin_list = admin_client["client"].get("/api/v1/patients")
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == mine["total"]
    # Unauthenticated directory reads are denied.
    assert TestClient(app).get("/api/v1/patients").status_code == 401


# --- Migration 0004 hardening: grants + single-open-draft index ---


def test_patient_table_grants(clean_registry) -> None:
    """App reads/writes (no delete), readonly reads, migrate owns DDL."""
    from sqlalchemy import text

    with clean_registry.connect() as connection:

        def _can(role: str, privilege: str, table: str) -> bool:
            return bool(
                connection.execute(
                    text(f"SELECT has_table_privilege('{role}', '{table}', '{privilege}')")
                ).scalar()
            )

        for table in ("patients", "encounters"):
            assert _can("x_insight_app", "SELECT", table)
            assert _can("x_insight_app", "INSERT", table)
            assert _can("x_insight_app", "UPDATE", table)
            assert not _can("x_insight_app", "DELETE", table)
            assert _can("x_insight_readonly", "SELECT", table)
            assert not _can("x_insight_readonly", "INSERT", table)
            assert _can("x_insight_migrate", "UPDATE", table)


def test_single_open_draft_partial_index_exists(clean_registry) -> None:
    with clean_registry.begin() as connection:
        definition = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'one_open_draft_per_patient'")
        ).scalar_one()
    assert "lifecycle = 'draft'" in definition


def test_second_open_draft_rejected_at_database(admin_client, clean_registry, monkeypatch) -> None:
    """Partial unique index holds the S07 slot even before draft routes exist."""
    import uuid as uuid_module

    from sqlalchemy.exc import IntegrityError

    from x_insight import db as db_module

    _make_physician(admin_client, "dr_slot")
    client, csrf, user = _physician_client(clean_registry, monkeypatch, "dr_slot", "pw123")
    created = client.post("/api/v1/patients", json=_valid_payload(), headers=_auth_headers(csrf))
    assert created.status_code == 201, created.text
    patient_id = created.json()["patient"]["id"]
    with db_module.session_scope(clean_registry) as session:
        try:
            session.execute(
                text(
                    "INSERT INTO encounters (id, patient_id, kind, author_id, lifecycle, "
                    "revision, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), :pid, 'registration', :author, 'draft', 1, now(), now())"
                ),
                {"pid": patient_id, "author": user["id"]},
            )
            session.flush()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("second open draft was not rejected")
    with clean_registry.begin() as connection:
        drafts = connection.execute(
            text("SELECT count(*) FROM encounters WHERE lifecycle = 'draft'")
        ).scalar_one()
    assert drafts == 1
    assert uuid_module.UUID(patient_id)
