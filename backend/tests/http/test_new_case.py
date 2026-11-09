import uuid
from copy import deepcopy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.http.test_patients import (
    _make_physician,
    _physician_client,
)
from tests.http.test_patients import (
    admin_client as admin_client,
)
from tests.http.test_patients import (
    clean_registry as clean_registry,
)
from tests.http.test_patients import (
    migrated_test_engine as migrated_test_engine,
)
from x_insight import contracts
from x_insight.ddi.lexicomp import drug_id, install
from x_insight.models.registry import install_bundled_oral_model


@pytest.fixture(scope="module")
def installed_content(migrated_test_engine):
    install(migrated_test_engine)
    with Session(migrated_test_engine) as session, session.begin():
        install_bundled_oral_model(session)


@pytest.fixture
def clinician(admin_client, clean_registry, monkeypatch, installed_content):
    _make_physician(admin_client, "newcase")
    client, csrf, user = _physician_client(clean_registry, monkeypatch, "newcase", "pw123")
    return client, csrf, user


def create(client, csrf, **overrides):
    payload = {
        "identifier": "0012345678",
        "given_name": "علی",
        "family_name": "رضایی",
        "sex": "F",
        "birth_year": contracts.utcnow().year - 30,
        "clinical_status": "first_time",
        **overrides,
    }
    response = client.post("/api/v1/patients", json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    body = response.json()
    return body, "/api/v1/encounters/" + body["draft"]["id"]


def save(client, csrf, url, draft, revision, key=None):
    return client.patch(
        url,
        json={"draft_data": draft},
        headers={
            "X-CSRF-Token": csrf,
            "If-Match": contracts.format_etag(revision),
            "Idempotency-Key": key or str(uuid.uuid4()),
        },
    )


def test_creation_and_ordinary_diagnosis_gate(clinician):
    client, csrf, _ = clinician
    body, url = create(client, csrf, birth_year=contracts.utcnow().year - 100)
    assert body["patient"]["age"] == 100
    assert body["draft"]["workflow_version"] == "new-case-v1"
    draft = client.get(url).json()["draft_data"]
    assert draft["panss"] == {"answers": {}}
    assert draft["new_case"]["content_pins"]["model_version_id"]
    preview = client.get(url + "/new-case/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["age"] == 100
    assert client.get(url + "/diagnosis").json()["can_proceed"] is False
    validation = client.post(
        url + "/new-case/validate",
        json={"destination": "considerations"},
        headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["can_navigate"] is False
    assert validation.json()["navigation_errors"][0]["code"] == "diagnosis_not_positive"
    assert (
        client.post(
            url + "/diagnosis/bypass",
            json={},
            headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)},
        ).status_code
        == 409
    )


def test_autosave_metadata_pins_catalog_and_overrides(clinician):
    client, csrf, user = clinician
    _, url = create(client, csrf)
    draft = client.get(url).json()["draft_data"]
    for key, value in (("registration_year", 1999), ("content_pins", {})):
        forged = deepcopy(draft)
        forged["new_case"][key] = value
        assert save(client, csrf, url, forged, 1).status_code == 422
    forged = deepcopy(draft)
    forged["diagnosis"]["new_case_assertion"] = {"status": "definite_diagnosis"}
    assert save(client, csrf, url, forged, 1).status_code == 422
    forged = deepcopy(draft)
    forged["medications"]["entries"] = [{"catalog_drug_id": "invented"}]
    assert save(client, csrf, url, forged, 1).status_code == 422
    draft["new_case"]["labs"] = {"present": True, "values": {"fbs": "100.00"}}
    draft["new_case"]["conditions"]["glucose_abnormality"]["manual"] = False
    draft["medications"]["entries"] = [{"catalog_drug_id": drug_id("Lisinopril")}] * 2
    response = save(client, csrf, url, draft, 1)
    assert response.status_code == 200, response.text
    saved = response.json()["draft_data"]
    assert saved["new_case"]["labs"]["values"]["fbs"] == "100"
    assert len(saved["medications"]["entries"]) == 1
    answer = saved["new_case"]["conditions"]["glucose_abnormality"]
    assert answer["manual_actor_id"] == user["id"]
    assert answer["manual_recorded_at"]
    assert save(client, csrf, url, draft, 1).status_code == 412
    saved["new_case"]["labs"]["values"]["fbs"] = "110"
    assert save(client, csrf, url, saved, 2).status_code == 200
    flag = client.get(url + "/new-case/preview").json()["flags"]["glucose_abnormality"]
    assert flag["suggested"] is True and flag["effective"] is False
    assert flag["manual_recorded_at"] == answer["manual_recorded_at"]


def test_assertion_replay_and_invalidation(clinician):
    client, csrf, _ = clinician
    _, url = create(client, csrf)
    draft = client.get(url).json()["draft_data"]
    draft["diagnosis"]["answers"] = {"a_delusions": "unknown"}
    draft["new_case"]["bmi"].update(source="manual", manual_category="normal")
    assert save(client, csrf, url, draft, 1).status_code == 200
    headers = {
        "X-CSRF-Token": csrf,
        "If-Match": contracts.format_etag(2),
        "Idempotency-Key": str(uuid.uuid4()),
    }
    response = client.post(
        url + "/new-case/diagnosis-assertion", json={"confirm": True}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            url + "/new-case/diagnosis-assertion", json={"confirm": True}, headers=headers
        ).json()
        == response.json()
    )
    asserted = response.json()["draft_data"]
    assert asserted["diagnosis"]["answers"] == {}
    assert asserted["diagnosis"]["new_case_assertion"]["actor_id"]
    assert client.get(url + "/diagnosis").json()["can_proceed"] is True
    assert client.get(url + "/new-case/preview").json()["can_complete"] is True
    asserted["diagnosis"]["answers"] = {"a_delusions": "no"}
    changed = save(client, csrf, url, asserted, 3)
    assert changed.status_code == 200, changed.text
    assert changed.json()["draft_data"]["diagnosis"]["new_case_assertion"] is None
    assert not client.get(url + "/new-case/preview").json()["can_complete"]


def test_author_csrf_stale_and_legacy_boundaries(
    clinician, admin_client, clean_registry, monkeypatch
):
    client, csrf, _ = clinician
    _, url = create(client, csrf)
    assert client.post(url + "/new-case/validate", json={"destination": "panss"}).status_code == 403
    assert (
        client.post(
            url + "/new-case/validate",
            json={"destination": "panss"},
            headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(99)},
        ).status_code
        == 412
    )
    _make_physician(admin_client, "stranger")
    stranger, stranger_csrf, _ = _physician_client(clean_registry, monkeypatch, "stranger", "pw123")
    assert stranger.get(url + "/new-case/preview").status_code == 403
    assert (
        stranger.post(
            url + "/new-case/diagnosis-assertion",
            json={"confirm": True},
            headers={"X-CSRF-Token": stranger_csrf, "If-Match": contracts.format_etag(1)},
        ).status_code
        == 403
    )
    legacy, legacy_url = create(client, csrf, identifier="0012345679", birth_year=None, age=30)
    assert legacy["draft"]["workflow_version"] == "legacy-v1"
    assert client.get(legacy_url + "/new-case/preview").status_code == 409
    draft = client.get(url).json()["draft_data"]
    assert save(client, csrf, legacy_url, draft, 1).status_code == 422


def test_archive_blocks_assertion(clinician, clean_registry):
    client, csrf, _ = clinician
    body, url = create(client, csrf)
    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE patients SET archived=true WHERE id=:id"), {"id": body["patient"]["id"]}
        )
    response = client.post(
        url + "/new-case/diagnosis-assertion",
        json={"confirm": True},
        headers={"X-CSRF-Token": csrf, "If-Match": contracts.format_etag(1)},
    )
    assert response.status_code == 409


def test_positive_assertion_preserves_answers_and_partial_panss(clinician):
    from tests.assessments.test_diagnosis import _qualifying_answers
    from x_insight.assessments.released import get_released_definition

    client, csrf, _ = clinician
    _, url = create(client, csrf)
    draft = client.get(url).json()["draft_data"]
    answers = _qualifying_answers()
    draft["diagnosis"]["answers"] = answers
    item = get_released_definition("panss")["items"][0]
    draft["panss"]["answers"] = {item["id"]: 1}
    response = save(client, csrf, url, draft, 1)
    assert response.status_code == 200, response.text
    assert client.get(url + "/diagnosis").json()["can_proceed"] is True
    response = client.post(
        url + "/new-case/diagnosis-assertion",
        json={"confirm": True},
        headers={
            "X-CSRF-Token": csrf,
            "If-Match": contracts.format_etag(2),
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    stored = client.get(url).json()["draft_data"]
    assert stored["diagnosis"]["answers"] == answers
    assert stored["panss"]["answers"] == {item["id"]: 1}
    assert client.get(url + "/new-case/preview").json()["can_complete_step"]["panss"]


def test_database_workflow_identity_cannot_change(clinician, clean_registry):
    client, csrf, _ = clinician
    body, _ = create(client, csrf)
    with pytest.raises(DBAPIError, match="workflow identity is immutable"):
        with clean_registry.begin() as connection:
            connection.execute(
                text("UPDATE encounters SET workflow_version='legacy-v1' WHERE id=:id"),
                {"id": body["draft"]["id"]},
            )


def test_new_case_backup_preserves_pinned_content(clinician, clean_registry, tmp_path):
    import hashlib

    from x_insight.models import inference
    from x_insight.operations import backup, restore

    client, csrf, user = clinician
    body, url = create(client, csrf)
    draft = client.get(url).json()["draft_data"]
    draft["new_case"]["conditions"]["renal_failure"]["manual"] = False
    response = save(client, csrf, url, draft, 1)
    assert response.status_code == 200, response.text
    snapshot, inventory, warnings = backup.export_snapshot_tables(clean_registry)
    payloads = backup.build_file_payloads(
        snapshot,
        {
            "cpt_policy_version": inference.CPT_POLICY_VERSION,
            "engine_config": dict(inference.PINNED_ENGINE_CONFIG),
        },
    )
    manifest = backup.build_manifest(
        job_id=uuid.uuid4(),
        actor=user,
        created_at=contracts.utcnow(),
        inventory=inventory,
        file_hashes={name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()},
        deployment_generation=snapshot["deployment_generation"],
        warnings=warnings,
    )
    archive = backup.write_archive(tmp_path / "new-case.zip", payloads, manifest)
    checked = restore.validate_archive_bytes(archive)
    assert checked["ok"], checked.get("errors")
    restored = next(
        row
        for row in checked["database"]["tables"]["encounters"]
        if row["id"] == body["draft"]["id"]
    )
    assert restored["workflow_version"] == "new-case-v1"
    assert restored["draft_data"] == response.json()["draft_data"]
    assert len(archive) <= backup.MAX_ARCHIVE_BYTES
    assert sum(map(len, payloads.values())) <= backup.MAX_EXPANDED_BYTES
