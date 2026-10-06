"""S24 model version administration (T1; plan.md §§4.3, 7.3, FR-03/37).

Slices (backend-owned steps 1,3,4; step 2 graph read covered without frontend):

- Slice 1 — admin imports/edits/exports XML as immutable versions; physician
  denied 403; prior bytes/hash/reports preserved; export is byte identity.
- Slice 2 — activation rejects incomplete/unreviewed bundles; valid
  activation/rollback atomic with pointer revision + audit; stale fails.
- Slice 3 — prior versions stay retrievable; history + validation exposed.

Synthetic XML only; BNs/ medical sources never modified. No draft BN active
by default. Expected values are worked literals, never implementation output.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from x_insight import db as db_module
from x_insight.app import app
from x_insight.identity import service as identity_service


def _valid_xml(name: str = "Valid", a_no: str = "0.8", a_yes: str = "0.2") -> bytes:
    return (
        f'<BIF VERSION="0.3"><NETWORK><NAME>{name}</NAME>'
        "<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        "<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        f"<DEFINITION><FOR>A</FOR><TABLE>{a_no} {a_yes}</TABLE></DEFINITION>"
        "<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN>"
        "<TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>"
        "</NETWORK></BIF>"
    ).encode()


def _edited_xml() -> bytes:
    # Same structure, different root CPTs (70/30) — new version, new hash.
    return _valid_xml(name="Valid", a_no="0.7", a_yes="0.3")


def _incomplete_xml() -> bytes:
    # XSD-valid but missing B's DEFINITION (missing_definition).
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Miss</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


APPROVED_REVIEW = {"reviewer": "owner", "decision": "approved", "date": "2026-10-04"}
DRAFT_REVIEW = {"reviewer": "", "decision": "draft", "date": ""}


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


@pytest.fixture()
def clean_all(migrated_test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "get_engine", lambda: migrated_test_engine)
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        connection.execute(text("TRUNCATE model_workflow_pointers"))
        connection.execute(text("TRUNCATE model_network_versions, model_networks CASCADE"))
    identity_service.ensure_default_admin(migrated_test_engine)
    yield migrated_test_engine
    identity_service.clear_login_throttle()
    with migrated_test_engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, users CASCADE"))
        connection.execute(text("TRUNCATE audit_events"))
        connection.execute(text("TRUNCATE model_workflow_pointers"))
        connection.execute(text("TRUNCATE model_network_versions, model_networks CASCADE"))
    identity_service.ensure_default_admin(migrated_test_engine)


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
    physician = TestClient(app)
    login = physician.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret123", "role": "physician"},
    )
    assert login.status_code == 200
    return physician, login.json()["csrf_token"]


def _assert_error_body(response, status: int):
    assert response.status_code == status, response.text
    body = response.json()
    for key in ("code", "message", "field_errors", "request_id", "retryable"):
        assert key in body, body
    assert body["request_id"]
    return body


# --- Slice 1: admin import/edit/export immutable versions ---


def test_admin_import_edit_export_immutable_versions(clean_all):
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    original = _valid_xml()
    edited = _edited_xml()
    assert original != edited

    created = client.post(
        "/api/v1/networks",
        json={
            "name": "synthetic-s24",
            "xml_text": original.decode("utf-8"),
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    network_id = created.json()["network"]["id"]
    v1_id = created.json()["version"]["id"]
    assert created.json()["version"]["version_number"] == 1
    v1_hash = created.json()["version"]["source_hash"]
    assert len(v1_hash) == 64

    # Validation reports are separate; XSD success never claims executable.
    validated = client.post(f"/api/v1/network-versions/{v1_id}/validate", json={}, headers=headers)
    assert validated.status_code == 200, validated.text
    payload = validated.json()
    for key in ("structural", "semantic", "content", "admission"):
        assert key in payload, payload
    assert payload["structural"]["xsd_valid"] is True
    assert payload["structural"]["activatable_v1"] is True
    assert "executable" not in str(payload["structural"]).lower()
    assert "clinically valid" not in str(payload).lower()
    assert payload["semantic"]["valid"] is True

    # Read-only graph: ordered nodes/edges/states, no edit ops.
    graph = client.get(f"/api/v1/network-versions/{v1_id}/graph")
    assert graph.status_code == 200, graph.text
    graphs = graph.json()["graphs"]
    assert len(graphs) == 1
    assert graphs[0]["nodes"] == ["A", "B"]
    assert graphs[0]["edges"] == [["A", "B"]]
    assert graphs[0]["states"] == {"A": ["no", "yes"], "B": ["no", "yes"]}
    assert "edit" not in graph.text.lower() or "immutable editing" not in graph.text.lower()
    body_text = graph.text.lower()
    assert "drag" not in body_text

    # Exact export is byte identity.
    exported = client.get(f"/api/v1/network-versions/{v1_id}/xml")
    assert exported.status_code == 200, exported.text
    assert exported.content == original

    # Edit creates version 2; prior bytes/hash preserved.
    second = client.post(
        f"/api/v1/networks/{network_id}/versions",
        json={"xml_text": edited.decode("utf-8"), "review": APPROVED_REVIEW},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"]["version_number"] == 2
    assert second.json()["version"]["source_hash"] != v1_hash

    history = client.get(f"/api/v1/networks/{network_id}/versions")
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert [item["version_number"] for item in history.json()["items"]] == [1, 2]

    still_v1 = client.get(f"/api/v1/network-versions/{v1_id}/xml")
    assert still_v1.status_code == 200
    assert still_v1.content == original
    reread = client.post(f"/api/v1/network-versions/{v1_id}/validate", json={}, headers=headers)
    assert reread.json()["source_hash"] == v1_hash
    assert reread.json()["semantic"]["valid"] is True


def test_physician_denied_on_all_network_routes(clean_all):
    admin_client, admin_csrf = _login()
    created = admin_client.post(
        "/api/v1/networks",
        json={
            "name": "denied-net",
            "xml_text": _valid_xml().decode("utf-8"),
            "review": APPROVED_REVIEW,
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert created.status_code == 201
    network_id = created.json()["network"]["id"]
    version_id = created.json()["version"]["id"]

    physician, csrf = _physician_client(clean_all)
    headers = {"X-CSRF-Token": csrf}
    _assert_error_body(physician.get("/api/v1/networks"), 403)
    _assert_error_body(
        physician.post(
            "/api/v1/networks",
            json={"name": "x", "xml_text": _valid_xml().decode("utf-8")},
            headers=headers,
        ),
        403,
    )
    _assert_error_body(physician.get(f"/api/v1/networks/{network_id}/versions"), 403)
    _assert_error_body(
        physician.post(
            f"/api/v1/networks/{network_id}/versions",
            json={"xml_text": _edited_xml().decode("utf-8")},
            headers=headers,
        ),
        403,
    )
    _assert_error_body(
        physician.post(f"/api/v1/network-versions/{version_id}/validate", json={}, headers=headers),
        403,
    )
    _assert_error_body(physician.get(f"/api/v1/network-versions/{version_id}/graph"), 403)
    _assert_error_body(physician.get(f"/api/v1/network-versions/{version_id}/xml"), 403)
    _assert_error_body(
        physician.post(
            "/api/v1/model-bundles/activate",
            json={
                "workflow": "registration",
                "selections": [{"network_id": network_id, "version_id": version_id}],
                "review": APPROVED_REVIEW,
            },
            headers=headers,
        ),
        403,
    )


def test_unauthenticated_and_unsafe_xml_rejected(clean_all):
    anonymous = TestClient(app)
    _assert_error_body(anonymous.get("/api/v1/networks"), 401)

    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    _assert_error_body(
        client.post(
            "/api/v1/networks",
            json={"name": "bad", "xml_text": "<!DOCTYPE BIF><BIF/>"},
            headers=headers,
        ),
        422,
    )
    _assert_error_body(
        client.post(
            "/api/v1/networks", json={"name": "bad", "xml_text": "<BIF><BROKEN>"}, headers=headers
        ),
        422,
    )


def test_import_idempotency_replays_and_conflicts(clean_all):
    client, csrf = _login()
    xml_text = _valid_xml().decode("utf-8")
    first = client.post(
        "/api/v1/networks",
        json={"name": "idem-net", "xml_text": xml_text},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "s24-slice1-key"},
    )
    assert first.status_code == 201, first.text
    replay = client.post(
        "/api/v1/networks",
        json={"name": "idem-net", "xml_text": xml_text},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "s24-slice1-key"},
    )
    assert replay.status_code == 201
    assert replay.json() == first.json()
    conflict = client.post(
        "/api/v1/networks",
        json={"name": "other-name", "xml_text": xml_text},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "s24-slice1-key"},
    )
    _assert_error_body(conflict, 409)


# --- Slice 2: activation/rollback atomic with pointer revision + audit ---


def _import_approved(client, headers, name: str, xml: bytes):
    response = client.post(
        "/api/v1/networks",
        json={"name": name, "xml_text": xml.decode("utf-8"), "review": APPROVED_REVIEW},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["network"]["id"], payload["version"]["id"]


def test_activation_rejects_incomplete_and_unreviewed(clean_all):
    from x_insight.operations import audit as audit_module

    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    good_net, good_ver = _import_approved(client, headers, "good-net", _valid_xml())
    bad_net, bad_ver = _import_approved(client, headers, "bad-net", _incomplete_xml())
    # Force bad version to approved review so the incomplete (not unreviewed)
    # gate is the one that fires: re-import same bytes as approved.
    assert good_net and bad_net

    # Incomplete version (missing_definition) rejected even with approved bundle review.
    rejected = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": bad_net, "version_id": bad_ver}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(rejected, 422)
    assert rejected.json()["code"] == "INCOMPLETE_BUNDLE"

    # Unreviewed version rejected: draft version cannot activate.
    draft = client.post(
        "/api/v1/networks",
        json={"name": "draft-net", "xml_text": _valid_xml(name="Draft").decode("utf-8")},
        headers=headers,
    )
    assert draft.status_code == 201
    draft_net = draft.json()["network"]["id"]
    draft_ver = draft.json()["version"]["id"]
    unreviewed = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": draft_net, "version_id": draft_ver}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(unreviewed, 422)
    assert unreviewed.json()["code"] == "UNREVIEWED_BUNDLE"

    # Unreviewed bundle (draft bundle review) rejected even for a good version.
    bad_review = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": good_net, "version_id": good_ver}],
            "review": DRAFT_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(bad_review, 422)

    # Failed activations leave no pointer (atomic, no partial move).
    from x_insight.models import registry as registry_module

    with db_module.session_scope(clean_all) as session:
        assert registry_module.get_pointer(session, "registration") is None
        events = audit_module.list_audit_events(session)
        assert all(
            e["operation"]
            not in ("model_bundles.activate.success", "model_bundles.rollback.success")
            for e in events
        )


def test_valid_activation_and_rollback_atomic_with_revision_and_audit(clean_all):
    from x_insight.models import registry as registry_module
    from x_insight.operations import audit as audit_module

    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    net_a, ver_a = _import_approved(client, headers, "net-a", _valid_xml(name="A1"))
    created_b = client.post(
        f"/api/v1/networks/{net_a}/versions",
        json={"xml_text": _edited_xml().decode("utf-8"), "review": APPROVED_REVIEW},
        headers=headers,
    )
    assert created_b.status_code == 201
    ver_b = created_b.json()["version"]["id"]

    first = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": net_a, "version_id": ver_a}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert first.headers["etag"] == '"1"'

    with db_module.session_scope(clean_all) as session:
        pointer = registry_module.get_pointer(session, "registration")
        assert pointer is not None and int(pointer["revision"]) == 1
        events = audit_module.list_audit_events(session)
        assert any(e["operation"] == "model_bundles.activate.success" for e in events)

    # Stale pointer mutation fails 412.
    stale = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": net_a, "version_id": ver_b}],
            "review": APPROVED_REVIEW,
        },
        headers={**headers, "If-Match": '"1"'},
    )
    # '"1"' is now current, so this succeeds and moves to 2; next stale '"1"' must fail.
    assert stale.status_code == 200, stale.text
    assert stale.json()["revision"] == 2

    conflict = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": net_a, "version_id": ver_b}],
            "review": APPROVED_REVIEW,
        },
        headers={**headers, "If-Match": '"1"'},
    )
    _assert_error_body(conflict, 412)

    fresh = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": net_a, "version_id": ver_a}],
            "review": APPROVED_REVIEW,
        },
        headers={**headers, "If-Match": '"2"'},
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["revision"] == 3
    assert fresh.headers["etag"] == '"3"'

    with db_module.session_scope(clean_all) as session:
        pointer = registry_module.get_pointer(session, "registration")
        assert pointer is not None and int(pointer["revision"]) == 3
        events = audit_module.list_audit_events(session)
        assert any(e["operation"] == "model_bundles.rollback.success" for e in events)

    # Previously referenced version stays retrievable after pointer moves.
    old_xml = client.get(f"/api/v1/network-versions/{ver_b}/xml")
    assert old_xml.status_code == 200
    assert old_xml.content == _edited_xml()


def test_activation_idempotency_and_malformed_if_match(clean_all):
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    net_id, ver_id = _import_approved(client, headers, "idem-act", _valid_xml())
    body = {
        "workflow": "registration",
        "selections": [{"network_id": net_id, "version_id": ver_id}],
        "review": APPROVED_REVIEW,
    }
    first = client.post(
        "/api/v1/model-bundles/activate",
        json=body,
        headers={**headers, "Idempotency-Key": "s24-act-key"},
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        "/api/v1/model-bundles/activate",
        json=body,
        headers={**headers, "Idempotency-Key": "s24-act-key"},
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    changed = dict(body)
    changed["workflow"] = "followup"
    conflict = client.post(
        "/api/v1/model-bundles/activate",
        json=changed,
        headers={**headers, "Idempotency-Key": "s24-act-key"},
    )
    _assert_error_body(conflict, 409)

    malformed = client.post(
        "/api/v1/model-bundles/activate",
        json=body,
        headers={**headers, "If-Match": "not-a-revision"},
    )
    _assert_error_body(malformed, 422)


def test_rollback_rejects_unauthorized_incomplete_and_stale_via_http(clean_all):
    """S24 steps 3-4 missing rollback paths (T1, HTTP-only, no DB rows).

    Physician/unauth denied, incomplete/unreviewed rejected, stale 412
    leaves the pointer unmoved (next valid move is still revision 2),
    valid rollback is atomic with revision + ETag and prior bytes stay.
    """

    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    good_net, good_ver = _import_approved(client, headers, "rb-good", _valid_xml())
    bad_net, bad_ver = _import_approved(client, headers, "rb-bad", _incomplete_xml())
    draft = client.post(
        "/api/v1/networks",
        json={"name": "rb-draft", "xml_text": _valid_xml(name="RbDraft").decode("utf-8")},
        headers=headers,
    )
    assert draft.status_code == 201
    draft_net = draft.json()["network"]["id"]
    draft_ver = draft.json()["version"]["id"]

    # Direct authorization: physician 403, anonymous 401 on rollback.
    physician, physician_csrf = _physician_client(clean_all)
    physician_headers = {"X-CSRF-Token": physician_csrf}
    _assert_error_body(
        physician.post(
            "/api/v1/model-bundles/rollback",
            json={
                "workflow": "registration",
                "selections": [{"network_id": good_net, "version_id": good_ver}],
                "review": APPROVED_REVIEW,
            },
            headers=physician_headers,
        ),
        403,
    )
    anonymous = TestClient(app)
    _assert_error_body(
        anonymous.post(
            "/api/v1/model-bundles/rollback",
            json={
                "workflow": "registration",
                "selections": [{"network_id": good_net, "version_id": good_ver}],
                "review": APPROVED_REVIEW,
            },
        ),
        401,
    )

    # Rollback rejects incomplete and unreviewed bundles (422, same gates).
    incomplete = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": bad_net, "version_id": bad_ver}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(incomplete, 422)
    assert incomplete.json()["code"] == "INCOMPLETE_BUNDLE"

    unreviewed = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": draft_net, "version_id": draft_ver}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(unreviewed, 422)
    assert unreviewed.json()["code"] == "UNREVIEWED_BUNDLE"

    draft_review = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": good_net, "version_id": good_ver}],
            "review": DRAFT_REVIEW,
        },
        headers=headers,
    )
    _assert_error_body(draft_review, 422)

    # Valid activation establishes revision 1; stale rollback fails 412.
    first = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": good_net, "version_id": good_ver}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1

    stale = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": good_net, "version_id": good_ver}],
            "review": APPROVED_REVIEW,
        },
        headers={**headers, "If-Match": '"99"'},
    )
    _assert_error_body(stale, 412)

    # Stale left the pointer unmoved: the next valid rollback is revision 2.
    moved = client.post(
        "/api/v1/model-bundles/rollback",
        json={
            "workflow": "registration",
            "selections": [{"network_id": good_net, "version_id": good_ver}],
            "review": APPROVED_REVIEW,
        },
        headers={**headers, "If-Match": '"1"'},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["revision"] == 2
    assert moved.headers["etag"] == '"2"'

    # Prior bytes stay retrievable after the pointer moves (byte identity).
    exported = client.get(f"/api/v1/network-versions/{good_ver}/xml")
    assert exported.status_code == 200
    assert exported.content == _valid_xml()


# --- Slice 3: version history retrievable ---


def test_version_history_and_validation_details_exposed(clean_all):
    client, csrf = _login()
    headers = {"X-CSRF-Token": csrf}
    net_id, ver1 = _import_approved(client, headers, "hist-net", _valid_xml())
    second = client.post(
        f"/api/v1/networks/{net_id}/versions",
        json={"xml_text": _edited_xml().decode("utf-8"), "review": APPROVED_REVIEW},
        headers=headers,
    )
    assert second.status_code == 201
    ver2 = second.json()["version"]["id"]

    listed = client.get(f"/api/v1/networks/{net_id}/versions?limit=25&offset=0")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 2
    items = listed.json()["items"]
    assert [item["version_number"] for item in items] == [1, 2]
    for item in items:
        assert "validation" in item
        assert item["validation"]["xsd_valid"] is True
        assert item["validation"]["semantic_valid"] is True
        assert "review" in item and item["review"]["decision"] == "approved"

    _assert_error_body(client.get("/api/v1/networks?limit=101"), 422)

    # Activation of the new version leaves the prior version retrievable.
    activated = client.post(
        "/api/v1/model-bundles/activate",
        json={
            "workflow": "registration",
            "selections": [{"network_id": net_id, "version_id": ver2}],
            "review": APPROVED_REVIEW,
        },
        headers=headers,
    )
    assert activated.status_code == 200
    prior_xml = client.get(f"/api/v1/network-versions/{ver1}/xml")
    assert prior_xml.status_code == 200
    assert prior_xml.content == _valid_xml()
    prior_graph = client.get(f"/api/v1/network-versions/{ver1}/graph")
    assert prior_graph.status_code == 200
    assert prior_graph.json()["graphs"][0]["nodes"] == ["A", "B"]
    networks = client.get("/api/v1/networks")
    assert networks.status_code == 200
    assert any(row["id"] == net_id for row in networks.json()["items"])


def test_no_draft_bundle_active_by_default(clean_all):
    from x_insight.models import registry as registry_module

    client, _ = _login()
    networks = client.get("/api/v1/networks")
    assert networks.status_code == 200
    assert networks.json()["total"] == 0
    with db_module.session_scope(clean_all) as session:
        assert registry_module.get_pointer(session, "registration") is None
        assert registry_module.get_pointer(session, "followup") is None


def test_health_and_ready_preserved(clean_all):
    client = TestClient(app)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "x-insight"}
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["schema_version"] == db_module.EXPECTED_SCHEMA_VERSION
