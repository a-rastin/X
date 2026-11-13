"""Public follow-up generation/final editing/signing without a provider or worker."""

from copy import deepcopy

import pytest
from sqlalchemy import text

__all__ = ["admin_client", "clean_registry", "migrated_test_engine", "installed_content"]
from tests.http.test_follow_up_state import (  # noqa: F401
    _make_physician,
    _new_follow_up,
    _open_follow_up,
    _physician_client,
    _save,
    admin_client,
    clean_registry,
    migrated_test_engine,
)
from tests.http.test_new_case import installed_content  # noqa: F401


def headers(csrf, revision, key=None):
    result = {"X-CSRF-Token": csrf, "If-Match": f'"{revision}"'}
    if key:
        result["Idempotency-Key"] = key
    return result


def ready(engine, monkeypatch, admin):
    client, csrf, patient_id, created = _open_follow_up(engine, monkeypatch, admin)
    draft = deepcopy(created["draft_data"])
    fu = draft["follow_up"]
    history = fu["history"]
    history["psychiatric"]["present"] = False
    history["substances"]["present"] = False
    history.update(heart_failure=False, tobacco=False, pregnant=False, breastfeeding=False)
    history["neurological"] = dict.fromkeys(history["neurological"], False)
    history["bmi"].update(source="manual", manual_category="normal")
    for name in (
        "hepatic_failure",
        "renal_failure",
        "glucose_abnormality",
        "hyperlipidemia",
        "hyperprolactinemia",
    ):
        history["conditions"][name]["manual"] = False
    fu["considerations"].update(recovery="controlled", seizure_risk=False, qtc_risk=False)
    eid = created["encounter"]["id"]
    saved = _save(client, csrf, eid, draft, 1)
    assert saved.status_code == 200, saved.text
    return client, csrf, patient_id, eid, saved.json()["revision"], saved.json()["draft_data"]


def test_generate_sign_replay_and_v5_baseline(
    clean_registry, monkeypatch, admin_client, installed_content, tmp_path
):
    client, csrf, patient_id, eid, revision, draft = ready(
        clean_registry, monkeypatch, admin_client
    )
    url = f"/api/v1/encounters/{eid}"
    h = headers(csrf, revision, "fu-generate")
    response = client.post(url + "/follow-up/proposals", json={}, headers=h)
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["payload"]["ranking"]["final_decision"]["terminal"] == "physician_review"
    assert proposal["payload"]["model_not_run_reason"] == "physician_review"
    assert client.post(url + "/follow-up/proposals", json={}, headers=h).json() == response.json()
    assert (
        client.post(url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)).json()[
            "proposal"
        ]["id"]
        == proposal["id"]
    )
    assert (
        client.post(
            url + "/new-case/proposals", json={}, headers=headers(csrf, revision)
        ).status_code
        == 409
    )
    body = {"proposal_id": proposal["id"], "expected_plan_revision": 1}
    assert (
        client.post(url + "/follow-up/sign", json=body, headers=headers(csrf, revision)).status_code
        == 412
    )
    plan = client.patch(
        url + "/secondary-plan",
        json={"text": "Physician final <plan>", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    assert plan.status_code == 200, plan.text
    body["expected_plan_revision"] = plan.json()["revision"]
    signed = client.post(
        url + "/follow-up/sign", json=body, headers=headers(csrf, revision, "fu-sign")
    )
    assert signed.status_code == 200, signed.text
    snap = signed.json()["snapshot"]
    assert snap["snapshot"]["schema_version"] == "signed-encounter-v5"
    assert snap["snapshot"]["draft_data"] == draft
    assert (
        client.post(
            url + "/follow-up/sign", json=body, headers=headers(csrf, revision, "fu-sign")
        ).json()
        == signed.json()
    )
    assert (
        client.post(url + "/follow-up/sign", json=body, headers=headers(csrf, revision)).json()
        == signed.json()
    )
    with clean_registry.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM signed_encounter_snapshots WHERE encounter_id=:id"),
                {"id": eid},
            ).scalar_one()
            == 1
        )
    addendum = client.post(
        url + "/addenda",
        json={"text": "Signed correction", "expected_encounter_revision": revision + 1},
        headers=headers(csrf, revision + 1),
    )
    assert addendum.status_code == 201, addendum.text
    chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
    assert chart["signed_snapshots"][0]["snapshot_hash"] == snap["snapshot_hash"]
    assert chart["addenda"][0]["text"] == "Signed correction"
    next_visit = _new_follow_up(client, csrf, patient_id)
    assert next_visit.status_code == 201, next_visit.text
    baseline = next_visit.json()["draft_data"]["follow_up"]["baseline"]
    assert baseline["source"]["snapshot_id"] == snap["id"]
    assert baseline["source"]["signature_schema_version"] == "signed-encounter-v5"

    report = admin_client["client"].get(f"/api/v1/patients/{patient_id}/report")
    assert report.status_code == 200, report.text
    assert "&lt;plan&gt;" in report.text
    assert "Deterministic follow-up proposal" in report.text

    # Complete staged data, including the v5-after-v5 source, passes both archive gates.
    from x_insight.operations.backup import export_snapshot_tables
    from x_insight.operations.restore import _check_new_case_state, _check_references

    database, inventory, warnings = export_snapshot_tables(clean_registry)
    assert not warnings
    import hashlib
    import uuid

    from x_insight import contracts
    from x_insight.models import inference
    from x_insight.operations import backup, restore

    payloads = backup.build_file_payloads(
        database,
        {
            "cpt_policy_version": inference.CPT_POLICY_VERSION,
            "engine_config": dict(inference.PINNED_ENGINE_CONFIG),
        },
    )
    manifest = backup.build_manifest(
        job_id=uuid.uuid4(),
        actor={"id": snap["signer_id"], "username": snap["signer_username"]},
        created_at=contracts.utcnow(),
        inventory=inventory,
        file_hashes={name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()},
        deployment_generation=database["deployment_generation"],
        warnings=[],
    )
    archive = backup.write_archive(tmp_path / "follow-up-v5.zip", payloads, manifest)
    checked = restore.validate_archive_bytes(archive)
    assert checked["ok"], checked.get("errors")
    staged = checked["database"]["tables"]
    assert _check_new_case_state(staged, "0032") is None
    assert _check_references(staged) is None
    original = deepcopy(staged["follow_up_proposals"][0]["payload"])
    staged["follow_up_proposals"][0]["payload"]["fingerprint_payload"]["effective_inputs"][
        "age"
    ] = -1
    assert _check_references(staged) == "Invalid follow-up proposal evaluated inputs."
    staged["follow_up_proposals"][0]["payload"] = original
    staged["signed_encounter_snapshots"][0]["snapshot_hash"] = "0" * 64
    assert _check_references(staged) == "Invalid follow-up signed snapshot."
    staged["signed_encounter_snapshots"][0]["snapshot_hash"] = snap["snapshot_hash"]
    current = next(
        e for e in staged["encounters"] if e["id"] == next_visit.json()["encounter"]["id"]
    )
    current["draft_data"]["follow_up"]["baseline"]["source"]["snapshot_hash"] = "0" * 64
    assert (
        _check_new_case_state(staged, "0032")
        == "Follow-up baseline source ownership or hash mismatch."
    )


def test_stale_inputs_keep_original_and_final(
    clean_registry, monkeypatch, admin_client, installed_content
):
    client, csrf, _, eid, revision, draft = ready(clean_registry, monkeypatch, admin_client)
    url = f"/api/v1/encounters/{eid}"
    proposal = client.post(
        url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
    ).json()["proposal"]
    client.patch(
        url + "/secondary-plan",
        json={"text": "Keep this final edit", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    draft["follow_up"]["harm"]["self"] = True
    saved = _save(client, csrf, eid, draft, revision)
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    assert client.get(url + "/follow-up/proposals/latest").json()["proposal"]["stale"]
    blocked = client.post(
        url + "/follow-up/sign",
        json={"proposal_id": proposal["id"], "expected_plan_revision": 2},
        headers=headers(csrf, revision),
    )
    assert blocked.status_code == 409, blocked.text
    regenerated = client.post(
        url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
    )
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["proposal"]["id"] != proposal["id"]
    assert client.get(url + "/secondary-plan").json()["text"] == "Keep this final edit"
    assert (
        client.get(url + "/follow-up/proposals/" + proposal["id"]).json()["proposal"]["payload"]
        == proposal["payload"]
    )

    import uuid

    from x_insight.cases import follow_up_content

    foreign = str(uuid.uuid4())
    assert client.get(url + "/follow-up/proposals/" + foreign).status_code == 404
    assert (
        client.post(
            url + "/follow-up/sign",
            json={"proposal_id": foreign, "expected_plan_revision": 2},
            headers=headers(csrf, revision),
        ).status_code
        == 409
    )
    assert (
        client.patch(
            url + "/secondary-plan",
            json={"text": None, "expected_plan_revision": 2},
            headers=headers(csrf, 2),
        ).status_code
        == 422
    )
    _make_physician(admin_client, "foreign_reader")
    stranger, stranger_csrf = _physician_client(
        clean_registry, monkeypatch, "foreign_reader", "pw123"
    )
    assert stranger.get(url + "/follow-up/proposals/latest").status_code == 403
    assert (
        stranger.post(
            url + "/follow-up/proposals", json={}, headers=headers(stranger_csrf, revision)
        ).status_code
        == 403
    )
    assert admin_client["client"].get(url + "/follow-up/proposals/latest").status_code == 403
    settings = client.get("/api/v1/me/unavailable-drugs").json()
    changed = client.patch(
        "/api/v1/me/unavailable-drugs",
        json={"selected_ids": ["oral:chlorpromazine"]},
        headers=headers(csrf, settings["revision"]),
    )
    assert changed.status_code == 200, changed.text
    assert client.get(url + "/follow-up/proposals/latest").json()["proposal"]["stale"]
    fresh = client.post(
        url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
    ).json()["proposal"]
    assert fresh["id"] != regenerated.json()["proposal"]["id"]
    assert client.get(url + "/secondary-plan").json()["text"] == "Keep this final edit"

    def unavailable():
        raise ValueError("Synthetic integrity failure")

    with monkeypatch.context() as patch:
        patch.setattr(follow_up_content, "load_content", unavailable)
        blocked = client.post(
            url + "/follow-up/sign",
            json={"proposal_id": fresh["id"], "expected_plan_revision": 2},
            headers=headers(csrf, revision),
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "FOLLOW_UP_CONTENT_UNAVAILABLE"
    with clean_registry.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM signed_encounter_snapshots WHERE encounter_id=:id"),
                {"id": eid},
            ).scalar_one()
            == 0
        )


def test_concurrent_confirmation_is_one_atomic_signature(
    clean_registry, monkeypatch, admin_client, installed_content
):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID

    from sqlalchemy.orm import Session

    from x_insight.cases import follow_up_plans

    client, csrf, _, eid, revision, _ = ready(clean_registry, monkeypatch, admin_client)
    url = f"/api/v1/encounters/{eid}"
    proposal = client.post(
        url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
    ).json()["proposal"]
    client.patch(
        url + "/secondary-plan",
        json={"text": "Final", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    with clean_registry.connect() as conn:
        author = dict(
            conn.execute(
                text(
                    "SELECT u.* FROM users u JOIN encounters e ON e.author_id=u.id WHERE e.id=:id"
                ),
                {"id": eid},
            )
            .mappings()
            .one()
        )

    def confirm():
        with Session(clean_registry) as session, session.begin():
            return follow_up_plans.sign(
                session, author, UUID(eid), revision, 2, UUID(proposal["id"]), "concurrent-check"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(), range(2)))
    assert results[0] == results[1]
    with clean_registry.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM signed_encounter_snapshots WHERE encounter_id=:id"),
                {"id": eid},
            ).scalar_one()
            == 1
        )


def test_supported_outcomes_use_real_model_and_sign_without_provider(
    clean_registry, monkeypatch, admin_client, installed_content
):
    from x_insight.ddi.lexicomp import drug_id

    client, csrf, _, eid, revision, initial = ready(clean_registry, monkeypatch, admin_client)
    url = f"/api/v1/encounters/{eid}"
    cases = [
        ("physician_review", "controlled", {}, {}),
        (
            "continue_same",
            "controlled",
            {"switch_needed": False, "dose_reduction_possible": False},
            {},
        ),
        (
            "reduce_dose",
            "controlled",
            {"switch_needed": False, "dose_reduction_possible": True},
            {},
        ),
        ("adjunct_only", "controlled", {}, {"tardive_dyskinesia": True}),
        ("same_drug_lai", "partial", {}, {}),
        ("clozapine", "none_or_worse", {"treatment_resistant": True}, {}),
        ("oral_replacement", "partial", {"persistent_harm_risk": False}, {}),
        (
            "oral_replacement",
            "none_or_worse",
            {"treatment_resistant": True},
            {"parkinsonism": True, "tardive_dyskinesia": True},
        ),
    ]
    for terminal, recovery, branches, effects in cases:
        draft = deepcopy(initial)
        fu = draft["follow_up"]
        draft["medications"]["entries"] = [{"catalog_drug_id": drug_id("Olanzapine")}]
        draft["medications"]["selection_dataset_version"] = fu["content_pins"]["lexicomp_version"]
        fu["treatment"] = {
            "main_catalog_drug_id": drug_id("Olanzapine"),
            "formulation": "lai" if terminal == "physician_review" else "oral",
        }
        fu["considerations"].update(recovery=recovery, adherence=terminal != "same_drug_lai")
        fu["branches"].update(branches)
        fu["effects"].update(effects)
        result = _save(client, csrf, eid, draft, revision)
        assert result.status_code == 200, result.text
        revision = result.json()["revision"]
        initial = result.json()["draft_data"]  # keep canonical server attribution
        # Clear branch/effect values between rows, including previously active branches.
        initial["follow_up"]["branches"] = dict.fromkeys(initial["follow_up"]["branches"])
        initial["follow_up"]["effects"] = {
            "sedation": "none",
            **{k: False for k in fu["effects"] if k != "sedation"},
        }
        response = client.post(
            url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
        )
        assert response.status_code == 200, response.text
        proposal = response.json()["proposal"]
        ranking = proposal["payload"]["ranking"]
        assert ranking["final_decision"]["terminal"] == terminal
        if terminal == "oral_replacement":
            assert len(ranking["distribution"]) == 22
            assert 0 < len(ranking["candidates"]) <= 5
            assert abs(sum(c["probability"] for c in ranking["distribution"]) - 1) < 1e-8
            assert proposal["payload"]["model_not_run_reason"] is None
        else:
            assert ranking["distribution"] == []
            assert proposal["payload"]["model_not_run_reason"] == terminal
    # All unavailable is a successful, explicitly limited physician-review outcome.
    settings = client.get("/api/v1/me/unavailable-drugs").json()
    choices = client.get("/api/v1/follow-up/config").json()["formulations"]
    result = client.patch(
        "/api/v1/me/unavailable-drugs",
        json={"selected_ids": [e["id"] for e in choices if e["group"] == "oral"]},
        headers=headers(csrf, settings["revision"]),
    )
    assert result.status_code == 200, result.text
    review = client.post(url + "/follow-up/proposals", json={}, headers=headers(csrf, revision))
    assert review.status_code == 200, review.text
    proposal = review.json()["proposal"]
    assert proposal["payload"]["ranking"]["status"] == "no_eligible_oral_candidate"
    assert proposal["payload"]["ranking"]["final_decision"]["terminal"] == "physician_review"
    client.patch(
        url + "/secondary-plan",
        json={"text": "Reviewed limited scope", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    assert (
        client.post(
            url + "/follow-up/sign",
            json={"proposal_id": proposal["id"], "expected_plan_revision": 2},
            headers=headers(csrf, revision),
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "change", ["create", "settings", "archive", "deactivate", "deactivate_discard"]
)
def test_confirmation_races_have_one_serial_outcome(
    clean_registry, monkeypatch, admin_client, installed_content, change
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, csrf, patient_id, eid, revision, _ = ready(clean_registry, monkeypatch, admin_client)
    url = f"/api/v1/encounters/{eid}"
    proposal = client.post(
        url + "/follow-up/proposals", json={}, headers=headers(csrf, revision)
    ).json()["proposal"]
    client.patch(
        url + "/secondary-plan",
        json={"text": "Race checked final", "expected_plan_revision": 1},
        headers=headers(csrf, 1),
    )
    admin = admin_client["client"]
    admin_headers = {"X-CSRF-Token": admin_client["csrf"]}
    owner = client.get("/api/v1/me").json()["user"]["id"]
    settings = client.get("/api/v1/me/unavailable-drugs").json()
    patient = client.get(f"/api/v1/patients/{patient_id}").json()["patient"]
    draft_set = admin.get(f"/api/v1/physicians/{owner}/open-drafts").json()["draft_set_revision"]
    barrier = Barrier(2)

    def confirm():
        barrier.wait(timeout=10)
        return client.post(
            url + "/follow-up/sign",
            json={"proposal_id": proposal["id"], "expected_plan_revision": 2},
            headers=headers(csrf, revision),
        )

    def mutate():
        barrier.wait(timeout=10)
        if change == "create":
            return _new_follow_up(client, csrf, patient_id)
        if change == "settings":
            return client.patch(
                "/api/v1/me/unavailable-drugs",
                json={"selected_ids": ["oral:chlorpromazine"]},
                headers=headers(csrf, settings["revision"]),
            )
        if change == "archive":
            return admin.post(
                f"/api/v1/patients/{patient_id}/archive",
                headers={**admin_headers, "If-Match": f'"{patient["revision"]}"'},
            )
        return admin.post(
            f"/api/v1/physicians/{owner}/deactivate",
            json={
                "draft_action": "discard" if change.endswith("discard") else "retain",
                "draft_set_revision": draft_set,
            },
            headers=admin_headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation, mutation = pool.submit(confirm), pool.submit(mutate)
        signed, changed = confirmation.result(timeout=30), mutation.result(timeout=30)
    assert signed.status_code in (200, 401, 403, 404, 409), signed.text
    assert changed.status_code in (200, 201, 409), changed.text
    with clean_registry.connect() as connection:
        snapshots = (
            connection.execute(
                text("SELECT snapshot_hash FROM signed_encounter_snapshots WHERE encounter_id=:id"),
                {"id": eid},
            )
            .scalars()
            .all()
        )
    assert len(snapshots) == (1 if signed.status_code == 200 else 0)
    if change == "create":
        assert signed.status_code == 200, signed.text
        if changed.status_code == 409:
            changed = _new_follow_up(client, csrf, patient_id)
        assert changed.status_code == 201, changed.text
        assert (
            changed.json()["draft_data"]["follow_up"]["baseline"]["source"]["snapshot_id"]
            == signed.json()["snapshot"]["id"]
        )
