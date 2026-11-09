"""Real PostgreSQL journey through deterministic proposal and v4 signing."""

import uuid

import pytest
from sqlalchemy import text

from tests.http.test_new_case import (
    admin_client,
    clean_registry,
    clinician,
    create,
    installed_content,
    migrated_test_engine,
    save,
)
from x_insight import contracts

# Imported fixtures retain their normal isolation and real bundled content.
__all__ = [
    "admin_client",
    "clean_registry",
    "clinician",
    "installed_content",
    "migrated_test_engine",
]


def headers(csrf, rev):
    return {
        "X-CSRF-Token": csrf,
        "If-Match": contracts.format_etag(rev),
        "Idempotency-Key": str(uuid.uuid4()),
    }


def ready(client, csrf, lai=False):
    body, url = create(client, csrf)
    draft = client.get(url).json()["draft_data"]
    draft["new_case"]["bmi"].update(source="manual", manual_category="normal")
    draft["new_case"]["considerations"]["lai"] = lai
    result = save(client, csrf, url, draft, 1)
    assert result.status_code == 200, result.text
    result = client.post(
        url + "/new-case/diagnosis-confirmation", json={"confirm": True}, headers=headers(csrf, 2)
    )
    assert result.status_code == 200, result.text
    return body, url, result.json()["revision"]


@pytest.mark.parametrize("lai", [False, True])
def test_complete_journey_and_single_signature(clinician, clean_registry, lai):
    client, csrf, _ = clinician
    patient, url, rev = ready(client, csrf, lai)
    h = headers(csrf, rev)
    result = client.post(url + "/new-case/proposals", json={}, headers=h)
    assert result.status_code == 200, result.text
    proposal = result.json()["proposal"]
    assert client.post(url + "/new-case/proposals", json={}, headers=h).json() == result.json()
    again = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev))
    assert again.json()["proposal"]["id"] == proposal["id"]
    ranking = proposal["payload"]["ranking"]
    assert ranking["status"] == ("model_unavailable" if lai else "available")
    assert len(ranking["candidates"]) == (0 if lai else 5)
    plan = client.patch(
        url + "/secondary-plan",
        json={"text": "My physician plan <script>", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    assert plan.status_code == 200, plan.text
    request = {"proposal_id": proposal["id"], "expected_plan_revision": plan.json()["revision"]}
    h = headers(csrf, rev)
    result = client.post(url + "/new-case/sign", json=request, headers=h)
    assert result.status_code == 200, result.text
    snapshot = result.json()["snapshot"]
    assert snapshot["snapshot"]["schema_version"] == "signed-encounter-v4"
    assert snapshot["batch_id"] is None and snapshot["proposal_id"] is None
    assert snapshot["new_case_proposal_id"] == proposal["id"]
    assert client.post(url + "/new-case/sign", json=request, headers=h).json() == result.json()
    double = client.post(url + "/new-case/sign", json=request, headers=headers(csrf, rev))
    assert double.status_code == 200 and double.json()["snapshot"]["id"] == snapshot["id"]
    chart = client.get("/api/v1/patients/" + patient["patient"]["id"] + "/chart")
    assert chart.status_code == 200 and chart.json()["signed_snapshots"][0]["id"] == snapshot["id"]
    with clean_registry.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM signed_encounter_snapshots")).scalar_one() == 1
        )
        assert conn.execute(text("SELECT count(*) FROM generation_batches")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM reasoning_jobs")).scalar_one() == 0


def test_availability_freshness_and_validation(clinician):
    client, csrf, _ = clinician
    _, url, rev = ready(client, csrf)
    proposal = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev)).json()[
        "proposal"
    ]
    settings = client.get("/api/v1/me/unavailable-drugs").json()
    invalid = client.patch(
        "/api/v1/me/unavailable-drugs",
        json={"selected_ids": ["unknown"]},
        headers=headers(csrf, settings["revision"]),
    )
    assert invalid.status_code == 422
    saved = client.patch(
        "/api/v1/me/unavailable-drugs",
        json={"selected_ids": ["oral:chlorpromazine"]},
        headers=headers(csrf, settings["revision"]),
    )
    assert saved.status_code == 200, saved.text
    assert client.get(url + "/new-case/proposals/" + proposal["id"]).json()["proposal"]["stale"]
    plan = client.patch(
        url + "/secondary-plan",
        json={"text": "final", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    ).json()
    blocked = client.post(
        url + "/new-case/sign",
        json={"proposal_id": proposal["id"], "expected_plan_revision": plan["revision"]},
        headers=headers(csrf, rev),
    )
    assert blocked.status_code == 409, blocked.text
    newer = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev)).json()[
        "proposal"
    ]
    assert newer["payload"]["ranking"]["candidates"][0]["name"] == "Fluphenazine"


def test_new_case_name_contract_is_connected(clinician):
    client, csrf, _ = clinician
    base = {
        "identifier": "0023456789",
        "given_name": "Anne-Marie",
        "family_name": "Smith",
        "sex": "M",
        "birth_year": contracts.utcnow().year - 30,
        "clinical_status": "first_time",
    }
    assert (
        client.post("/api/v1/patients", json=base, headers={"X-CSRF-Token": csrf}).status_code
        == 422
    )
    base["given_name"] = "  Ali  Reza  "
    response = client.post("/api/v1/patients", json=base, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    assert response.json()["patient"]["given_name"] == "Ali  Reza"


def test_names_and_birth_year_correction(clinician, admin_client):
    client, csrf, _ = clinician
    admin = admin_client["client"]
    response = admin.post(
        "/api/v1/physicians",
        json={"username": "nameless", "password": "pw"},
        headers={"X-CSRF-Token": admin_client["csrf"]},
    )
    assert response.status_code == 422
    body, url, rev = ready(client, csrf)
    proposal = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev)).json()[
        "proposal"
    ]
    patient = body["patient"]
    correction = client.patch(
        "/api/v1/patients/" + patient["id"],
        json={"birth_year": contracts.utcnow().year - 40, "given_name": " Ali "},
        headers=headers(csrf, patient["revision"]),
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["patient"]["age"] == 40
    assert correction.json()["patient"]["given_name"] == "Ali"
    assert client.get(url + "/new-case/proposals/" + proposal["id"]).json()["proposal"]["stale"]


def test_concurrent_confirmation_and_followup_baseline(clinician, clean_registry):
    from concurrent.futures import ThreadPoolExecutor

    client, csrf, _ = clinician
    body, url, rev = ready(client, csrf)
    proposal = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev)).json()[
        "proposal"
    ]
    plan = client.patch(
        url + "/secondary-plan",
        json={"text": "Frozen final plan", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    ).json()

    def confirm(_):
        return client.post(
            url + "/new-case/sign",
            json={"proposal_id": proposal["id"], "expected_plan_revision": plan["revision"]},
            headers=headers(csrf, rev),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json()["snapshot"]["id"] == results[1].json()["snapshot"]["id"]
    followup = client.post(
        "/api/v1/patients/" + body["patient"]["id"] + "/encounters",
        json={"kind": "follow_up"},
        headers={"X-CSRF-Token": csrf},
    )
    assert followup.status_code == 201, followup.text
    draft = followup.json()["draft_data"]
    assert draft["panss"]["answers"] == {}
    assert draft["followup_baseline"]["prior_plan"] == "Frozen final plan"
    assert (
        draft["medications"]["dataset_version"]
        == proposal["payload"]["versions"]["lexicomp_version"]
    )
    assert not any("Tardive" in key for key in draft["history"]["values"])


def test_signed_backup_and_reference_validation(clinician, clean_registry, tmp_path):
    import hashlib

    from x_insight.models import inference
    from x_insight.operations import backup, restore

    client, csrf, user = clinician
    _, url, rev = ready(client, csrf)
    proposal = client.post(url + "/new-case/proposals", json={}, headers=headers(csrf, rev)).json()[
        "proposal"
    ]
    plan = client.patch(
        url + "/secondary-plan",
        json={"text": "Signed plan", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    ).json()
    signed = client.post(
        url + "/new-case/sign",
        json={"proposal_id": proposal["id"], "expected_plan_revision": plan["revision"]},
        headers=headers(csrf, rev),
    )
    assert signed.status_code == 200, signed.text
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
        file_hashes={n: hashlib.sha256(b).hexdigest() for n, b in payloads.items()},
        deployment_generation=snapshot["deployment_generation"],
        warnings=warnings,
    )
    archive = backup.write_archive(tmp_path / "signed-new-case.zip", payloads, manifest)
    checked = restore.validate_archive_bytes(archive)
    assert checked["ok"], checked.get("errors")
    staged = checked["database"]["tables"]
    assert staged["signed_encounter_snapshots"][0]["new_case_proposal_id"] == proposal["id"]
    staged["new_case_proposals"][0]["payload"]["input_hash"] = "forged"
    assert restore._check_references(staged) is not None
