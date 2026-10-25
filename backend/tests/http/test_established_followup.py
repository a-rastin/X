"""Signed plan evidence controls the established-case follow-up section (T1)."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import update

from tests.http.test_signing import (
    _accept_body,
    _auth_headers,
    _login_physician,
    _new_physician,
    _package,
    _run_bounded_times,
    _sign_body_after_accept,
    _signable_two_question_setup,
    _valid_cpt,
    clean_registry,
    migrated_test_engine,
)
from tests.models.test_proposal_readiness import known_context
from x_insight import contracts
from x_insight.cases import tables as cases_tables
from x_insight.models.workflow_release import content_root, resolve_workflow
from x_insight.reasoning import tables as reasoning_tables
from x_insight.reasoning.snapshots import compute_analysis_fingerprint

__all__ = ["clean_registry", "migrated_test_engine"]

PREFIX = "candidate/history/established_case_clozapine.v1/"


def _new_registration(engine, monkeypatch, username):
    _new_physician(engine, monkeypatch, username)
    client, csrf = _login_physician(engine, monkeypatch, username)
    created = client.post(
        "/api/v1/patients",
        json={
            "identifier": "0012345678",
            "given_name": "Synthetic",
            "family_name": "Patient",
            "sex": "F",
            "age": 30,
            "clinical_status": "first_time",
        },
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    return client, csrf, created.json()["draft"]["id"]


def _patch_draft(client, csrf, encounter_id, revision, draft):
    response = client.patch(
        f"/api/v1/encounters/{encounter_id}",
        json={"draft_data": draft},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _sign_existing(client, csrf, eid, revision, batch, runs, plan_revision=2):
    acceptances, review_revisions = {}, {}
    for run in runs.values():
        review = client.get(f"/api/v1/question-runs/{run['id']}/review").json()
        accepted = client.post(
            f"/api/v1/question-runs/{run['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert accepted.status_code == 200, accepted.text
        acceptances[run["id"]] = accepted.json()["acceptance"]
        review_revisions[run["id"]] = accepted.json()["review_revision"]
    signed = client.post(
        f"/api/v1/encounters/{eid}/sign",
        json=_sign_body_after_accept(
            revision, plan_revision, batch["id"], acceptances, review_revisions
        ),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    return signed.json()


@pytest.mark.parametrize("inclusion", ["yes", "no", "unknown"])
def test_final_plan_inclusion_is_frozen_and_controls_followup(
    clean_registry, monkeypatch, tmp_path, inclusion
):
    client, csrf, encounter_id, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_established"
    )
    path = f"/api/v1/encounters/{encounter_id}"
    assert client.get(path + "/secondary-plan").json()["clozapine_included"] == "unknown"
    plan = client.patch(
        path + "/secondary-plan",
        json={
            "text": "Synthetic final care plan.",
            "expected_plan_revision": 1,
            "clozapine_included": inclusion,
        },
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert plan.status_code == 200, plan.text
    assert client.get(path + "/secondary-plan").json()["clozapine_included"] == inclusion
    history = client.get(path + "/history").json()
    assert not any(
        f["question_key"] == "established_case_clozapine" for f in history["workflow_fields"]
    )
    response = client.patch(
        path,
        json={
            "draft_data": {
                "history": {
                    "values": {
                        "candidate/history/established_case_clozapine.v1/"
                        "h_schizophrenia_established": "Yes"
                    }
                }
            }
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert response.status_code == 422, response.text
    acceptances, review_revisions = {}, {}
    for run in runs.values():
        review = client.get(f"/api/v1/question-runs/{run['id']}/review").json()
        accepted = client.post(
            f"/api/v1/question-runs/{run['id']}/acceptance",
            json=_accept_body(review),
            headers=_auth_headers(csrf),
        )
        assert accepted.status_code == 200, accepted.text
        acceptances[run["id"]] = accepted.json()["acceptance"]
        review_revisions[run["id"]] = accepted.json()["review_revision"]
    signed = client.post(
        path + "/sign",
        json=_sign_body_after_accept(revision, 2, batch["id"], acceptances, review_revisions),
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(revision)},
    )
    assert signed.status_code == 200, signed.text
    frozen = signed.json()["snapshot"]
    assert frozen["snapshot"]["secondary_plan"]["clozapine_included"] == inclusion
    created = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    followup_id = created.json()["encounter"]["id"]
    followup = client.get(f"/api/v1/encounters/{followup_id}/history").json()
    established = [
        f for f in followup["workflow_fields"] if f["question_key"] == "established_case_clozapine"
    ]
    assert len(established) == (15 if inclusion == "yes" else 0)
    if inclusion != "yes":
        confirmation = client.post(
            path + "/addenda",
            json={
                "text": "Explicit confirmation of final-plan clozapine inclusion.",
                "expected_encounter_revision": signed.json()["revision"],
                "clozapine_included": "yes",
            },
            headers={
                **_auth_headers(csrf),
                "If-Match": contracts.format_etag(signed.json()["revision"]),
            },
        )
        assert confirmation.status_code == 201, confirmation.text
        updated = client.get(f"/api/v1/encounters/{followup_id}/history").json()
        assert (
            len(
                [
                    f
                    for f in updated["workflow_fields"]
                    if f["question_key"] == "established_case_clozapine"
                ]
            )
            == 15
        )
        chart = client.get(f"/api/v1/patients/{patient_id}/chart").json()
        assert chart["signed_snapshots"][0]["snapshot_hash"] == frozen["snapshot_hash"]


def test_plan_choice_uses_shared_revision_and_idempotency_fence(clean_registry, monkeypatch):
    client, csrf, eid = _new_registration(clean_registry, monkeypatch, "dr_choice_fences")
    url = f"/api/v1/encounters/{eid}/secondary-plan"
    assert client.get(url).json()["clozapine_included"] == "unknown"
    headers = {**_auth_headers(csrf), "If-Match": '"1"', "Idempotency-Key": "choice-save"}
    body = {
        "text": "Clozapine appears only in this draft text.",
        "expected_plan_revision": 1,
        "clozapine_included": "yes",
    }
    saved = client.patch(url, json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert client.patch(url, json=body, headers=headers).json() == saved.json()
    conflict = client.patch(url, json={**body, "clozapine_included": "no"}, headers=headers)
    assert conflict.status_code == 409, conflict.text
    stale = client.patch(
        url,
        json={**body, "clozapine_included": "no"},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert stale.status_code == 412, stale.text
    for invalid in ("Yes", "", True, 1, {}, []):
        rejected = client.patch(
            url,
            json={**body, "expected_plan_revision": 2, "clozapine_included": invalid},
            headers={**_auth_headers(csrf), "If-Match": '"2"'},
        )
        assert rejected.status_code == 422, (invalid, rejected.text)
    preserved = client.patch(
        url,
        json={"text": "Legacy text-only client save.", "expected_plan_revision": 2},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()["clozapine_included"] == "yes"
    assert not any(
        f["question_key"] == "established_case_clozapine"
        for f in client.get(f"/api/v1/encounters/{eid}/history").json()["workflow_fields"]
    )


def test_old_registration_answers_are_hidden_and_cleaned_without_blocking_generation(
    clean_registry, monkeypatch
):
    client, csrf, eid = _new_registration(clean_registry, monkeypatch, "dr_old_history")
    hidden = PREFIX + "h_schizophrenia_established"
    original_fields = json.loads(
        (content_root() / "history/history.established_case_clozapine.v1.json").read_text()
    )["fields"]
    old_answers = {field["source_path"]: field["permitted"][0] for field in original_fields}
    assert len(old_answers) == 15
    legacy = {
        "history": {
            "values": {**known_context(), **old_answers},
            "provenance": {path: {"source": "clinician_entry"} for path in old_answers},
        }
    }
    # Arrange a pre-change stored draft; only HTTP observes the resulting behavior.
    with clean_registry.begin() as connection:
        connection.execute(
            update(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == uuid.UUID(eid))
            .values(draft_data=legacy)
        )
    endpoint = f"/api/v1/encounters/{eid}"
    loaded = client.get(endpoint).json()
    assert hidden not in loaded["draft_data"]["history"]["values"]
    assert hidden not in loaded["draft_data"]["history"]["provenance"]
    assert not set(old_answers) & set(loaded["draft_data"]["history"]["values"])
    assert not set(old_answers) & set(loaded["draft_data"]["history"]["provenance"])
    history = client.get(endpoint + "/history").json()
    assert hidden not in history["values"]
    assert hidden not in history["provenance"]
    assert not any(f["id"].startswith(PREFIX) for f in history["workflow_fields"])
    assert not any(
        b["source_path"].startswith(PREFIX) for b in history["proposal_readiness"]["blockers"]
    )
    unchanged_echo = _patch_draft(client, csrf, eid, 1, legacy)
    assert hidden not in unchanged_echo["draft_data"]["history"]["values"]
    assert hidden not in unchanged_echo["draft_data"]["history"]["provenance"]
    for method, body in (
        (client.post, {"values": {hidden: "Yes"}}),
        (client.patch, {"draft_data": legacy}),
    ):
        rejected = method(
            endpoint + ("/history" if method == client.post else ""),
            json=body,
            headers={**_auth_headers(csrf), "If-Match": '"2"'},
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["code"] == "HISTORY_NOT_APPLICABLE"
    started = client.post(
        endpoint + "/generation-batches",
        json={},
        headers={**_auth_headers(csrf), "If-Match": '"2"'},
    )
    assert started.status_code == 202, started.text
    assert [r["question_key"] for r in started.json()["question_runs"]] == [
        "pharmacotherapy",
        "high_suicide_clozapine",
        "lai_indication_choice",
        "aggression_clozapine",
    ]
    assert not any(PREFIX in json.dumps(r["projection"]) for r in started.json()["question_runs"])


def test_direct_package_cannot_forge_established_eligibility(clean_registry, monkeypatch):
    client, csrf, eid = _new_registration(clean_registry, monkeypatch, "dr_forged_package")
    package = next(
        p
        for p in resolve_workflow("followup", clozapine_eligible=True)[0]
        if p["manifest"]["question_key"] == "established_case_clozapine"
    )
    for key in ("established_case_clozapine", "forged_key"):
        forged = deepcopy(package)
        forged["manifest"]["question_key"] = key
        response = client.post(
            f"/api/v1/encounters/{eid}/generation-batches",
            json={"package": forged},
            headers={**_auth_headers(csrf), "If-Match": '"1"'},
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "HISTORY_NOT_APPLICABLE"
    assert client.get(f"/api/v1/encounters/{eid}/generation-batches/latest").json()["batch"] is None


def test_legacy_workflow_run_is_readable_but_cannot_start_new_work(clean_registry, monkeypatch):
    client, csrf, eid = _new_registration(clean_registry, monkeypatch, "dr_legacy_workflow")
    url = f"/api/v1/encounters/{eid}"
    saved = _patch_draft(client, csrf, eid, 1, {"history": {"values": known_context()}})
    headers = {**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])}
    started = client.post(url + "/generation-batches", json={}, headers=headers)
    assert started.status_code == 202, started.text
    original = started.json()
    legacy_pin = deepcopy(original["batch"]["pinned_bundle"])
    legacy_pin.pop("application_workflow_contract_version")
    legacy_pin.pop("established_clozapine_eligible")
    legacy_fingerprint, legacy_payload = compute_analysis_fingerprint(
        saved["draft_data"], legacy_pin
    )
    # Pre-placement-version fixture; immutable callers may only read the record.
    with clean_registry.begin() as connection:
        connection.execute(
            update(reasoning_tables.generation_batches)
            .where(reasoning_tables.generation_batches.c.id == uuid.UUID(original["batch"]["id"]))
            .values(
                pinned_bundle=legacy_pin,
                fingerprint=legacy_fingerprint,
                fingerprint_payload=legacy_payload,
            )
        )
        connection.execute(
            update(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == uuid.UUID(original["batch"]["id"]))
            .values(fingerprint=legacy_fingerprint)
        )
    batch_url = "/api/v1/generation-batches/" + original["batch"]["id"]
    historical = client.get(batch_url)
    assert historical.status_code == 200, historical.text
    assert historical.json()["batch"]["pinned_bundle"] == legacy_pin
    assert historical.json()["question_runs"] == [
        {**run, "fingerprint": legacy_fingerprint} for run in original["question_runs"]
    ]
    source = {"source_batch_id": original["batch"]["id"]}
    for rejected in (
        client.get(url + "/proposal-readiness", params=source),
        client.post(url + "/generation-batches", json=source, headers=headers),
    ):
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["code"] == "WORKFLOW_INPUT_CONTRACT_CHANGED"
    fresh = client.post(url + "/generation-batches", json={}, headers=headers)
    assert fresh.status_code == 202, fresh.text
    assert fresh.json()["batch"]["id"] != original["batch"]["id"]
    assert fresh.json()["batch"]["pinned_bundle"]["application_workflow_contract_version"] == (
        "application-workflow-2026-10-24-v1"
    )
    assert client.get(batch_url).json()["batch"]["pinned_bundle"] == legacy_pin


def test_later_signed_plan_qualifies_legacy_patient_but_current_edits_do_not(
    clean_registry, monkeypatch, tmp_path
):
    client, csrf, eid, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_later_plan"
    )
    plan = client.patch(
        f"/api/v1/encounters/{eid}/secondary-plan",
        json={"text": "Historical free-text mention: clozapine.", "expected_plan_revision": 1},
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert plan.status_code == 200, plan.text
    signed = _sign_existing(client, csrf, eid, revision, batch, runs)
    legacy = deepcopy(signed["snapshot"]["snapshot"])
    legacy["schema_version"] = "signed-encounter-v2"
    legacy["secondary_plan"].pop("clozapine_included")
    legacy["draft_data"]["history"]["values"][PREFIX + "h_schizophrenia_established"] = "Yes"
    # Arrange a genuine pre-structured-evidence record; no HTTP may rewrite it.
    with clean_registry.begin() as connection:
        connection.execute(
            update(cases_tables.signed_encounter_snapshots)
            .where(cases_tables.signed_encounter_snapshots.c.encounter_id == uuid.UUID(eid))
            .values(snapshot=legacy, snapshot_hash=contracts.canonical_hash(legacy))
        )
    before = client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"][0]
    created = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    followup_id = created.json()["encounter"]["id"]
    url = f"/api/v1/encounters/{followup_id}"
    values = {
        "h_q1_a": "yes",
        "h_q1_b": "no",
        "candidate/history/no_improvement_clozapine.v1/h_clozapine_treatment_status": (
            "CurrentTreatment"
        ),
        "candidate/history/aggression_clozapine.v1/h_clozapine_treatment_status": (
            "CurrentTreatment"
        ),
    }
    saved = _patch_draft(
        client,
        csrf,
        followup_id,
        1,
        {
            "history": {"values": values},
            "gate": "true",
            "medications": {"items": [{"catalog_drug_id": "clozapine"}]},
        },
    )
    inclusion = client.patch(
        url + "/secondary-plan",
        json={
            "text": "Unsigned follow-up clozapine plan.",
            "expected_plan_revision": 1,
            "clozapine_included": "yes",
        },
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert inclusion.status_code == 200, inclusion.text
    assert not any(
        f["question_key"] == "established_case_clozapine"
        for f in client.get(url + "/history").json()["workflow_fields"]
    )
    for confirmation in (None, "unknown", "no"):
        response = client.post(
            f"/api/v1/encounters/{eid}/addenda",
            json={
                "text": "Free text clozapine mention is not structured Yes.",
                "expected_encounter_revision": signed["revision"],
                "clozapine_included": confirmation,
            },
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(signed["revision"])},
        )
        assert response.status_code == 201, response.text
        assert not any(
            f["question_key"] == "established_case_clozapine"
            for f in client.get(url + "/history").json()["workflow_fields"]
        )
    # Clear the arbitrary medication edit before exercising the real provider pipeline.
    saved = _patch_draft(
        client,
        csrf,
        followup_id,
        saved["revision"],
        {"history": {"values": values}, "gate": "true"},
    )
    package = _package("later_final_plan", "h_q1_a", "h_q1_b")
    package["manifest"]["workflow"] = "followup"
    for branch in package["template"]["branches"]:
        branch["text"] = "Synthetic clozapine mention: {B} outcome."
    started = client.post(
        url + "/generation-batches",
        json={"package": package},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])},
    )
    assert started.status_code == 202, started.text
    endpoint, outcomes = _run_bounded_times(clean_registry, _valid_cpt(), 1)
    try:
        assert [o["status"] for o in outcomes] == ["succeeded"], outcomes
    finally:
        endpoint.stop()
    generated = client.get("/api/v1/generation-batches/" + started.json()["batch"]["id"])
    assert "clozapine" in json.dumps(generated.json()["proposal"])
    assert not any(
        f["question_key"] == "established_case_clozapine"
        for f in client.get(url + "/history").json()["workflow_fields"]
    )
    later = _sign_existing(
        client,
        csrf,
        followup_id,
        saved["revision"],
        started.json()["batch"],
        {r["question_key"]: r for r in started.json()["question_runs"]},
    )
    assert later["snapshot"]["snapshot"]["secondary_plan"]["clozapine_included"] == "yes"
    next_draft = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert next_draft.status_code == 201, next_draft.text
    next_id = next_draft.json()["encounter"]["id"]
    history = client.get(f"/api/v1/encounters/{next_id}/history").json()
    assert (
        len(
            [
                f
                for f in history["workflow_fields"]
                if f["question_key"] == "established_case_clozapine"
            ]
        )
        == 15
    )
    after = client.get(f"/api/v1/patients/{patient_id}/chart").json()["signed_snapshots"]
    assert next(s for s in after if s["encounter_id"] == eid) == before


def test_append_only_confirmation_stales_runs_and_new_generation_requires_visible_answers(
    clean_registry, monkeypatch, tmp_path
):
    client, csrf, eid, patient_id, revision, batch, runs = _signable_two_question_setup(
        clean_registry, monkeypatch, tmp_path, "dr_confirmation_change"
    )
    plan = client.patch(
        f"/api/v1/encounters/{eid}/secondary-plan",
        json={
            "text": "Synthetic nonqualifying plan.",
            "expected_plan_revision": 1,
            "clozapine_included": "no",
        },
        headers={**_auth_headers(csrf), "If-Match": '"1"'},
    )
    assert plan.status_code == 200, plan.text
    signed = _sign_existing(client, csrf, eid, revision, batch, runs)
    created = client.post(
        f"/api/v1/patients/{patient_id}/encounters",
        json={"kind": "follow_up"},
        headers=_auth_headers(csrf),
    )
    assert created.status_code == 201, created.text
    followup_id = created.json()["encounter"]["id"]
    url = f"/api/v1/encounters/{followup_id}"
    saved = _patch_draft(
        client, csrf, followup_id, 1, {"history": {"values": known_context("followup")}}
    )
    started = client.post(
        url + "/generation-batches",
        json={},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])},
    )
    assert started.status_code == 202, started.text
    old = started.json()
    assert old["batch"]["pinned_bundle"]["established_clozapine_eligible"] is False
    confirmed = client.post(
        f"/api/v1/encounters/{eid}/addenda",
        json={
            "text": "Structured final-plan confirmation.",
            "clozapine_included": "yes",
            "expected_encounter_revision": signed["revision"],
        },
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(signed["revision"])},
    )
    assert confirmed.status_code == 201, confirmed.text
    batch_url = "/api/v1/generation-batches/" + old["batch"]["id"]
    historical = client.get(batch_url)
    assert historical.status_code == 200, historical.text
    assert historical.json()["freshness"]["stale"] is True
    assert historical.json()["question_runs"] == old["question_runs"]
    for run in old["question_runs"]:
        review = client.get(f"/api/v1/question-runs/{run['id']}/review")
        assert review.status_code == 200, review.text
        assert review.json()["input_freshness"]["stale"] is True
    history = client.get(url + "/history").json()
    blockers = history["proposal_readiness"]["blockers"]
    assert len(blockers) == 15
    assert all(b["source_path"].startswith(PREFIX) for b in blockers)
    source = {"source_batch_id": old["batch"]["id"]}
    for denied in (
        client.get(url + "/proposal-readiness", params=source),
        client.post(
            url + "/generation-batches",
            json=source,
            headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])},
        ),
    ):
        assert denied.status_code == 422, denied.text
        assert denied.json()["code"] == "WORKFLOW_ELIGIBILITY_CHANGED"
    blocked = client.post(
        url + "/generation-batches",
        json={},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])},
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["code"] == "PROPOSAL_INPUTS_INCOMPLETE"
    values = known_context("followup")
    for field in history["workflow_fields"]:
        if field["question_key"] == "established_case_clozapine":
            values[field["id"]] = field["permitted"][0]
    answered = client.post(
        url + "/history",
        json={"values": values},
        headers={**_auth_headers(csrf), "If-Match": contracts.format_etag(saved["revision"])},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["proposal_readiness"]["ready"] is True
    current = client.post(
        url + "/generation-batches",
        json={},
        headers={
            **_auth_headers(csrf),
            "If-Match": contracts.format_etag(answered.json()["revision"]),
        },
    )
    assert current.status_code == 202, current.text
    keys = [r["question_key"] for r in current.json()["question_runs"]]
    assert keys == [
        "tardive_dyskinesia",
        "akathisia",
        "parkinsonism",
        "acute_dystonia",
        "established_case_clozapine",
        "no_improvement_clozapine",
        "continue_or_adjust",
    ]
    established = next(
        r
        for r in current.json()["question_runs"]
        if r["question_key"] == "established_case_clozapine"
    )
    assert established["status"] == "ready"
    assert len(established["projection"]["variables"]) == 15
    frozen_definition = json.loads(
        (content_root() / "history/history.established_case_clozapine.v1.json").read_text()
    )
    assert [
        (f["id"], f["definition"], f["permitted"])
        for f in history["workflow_fields"]
        if f["question_key"] == "established_case_clozapine"
    ] == [(f["source_path"], f["definition"], f["permitted"]) for f in frozen_definition["fields"]]
    assert client.get(batch_url).json()["question_runs"] == historical.json()["question_runs"]
