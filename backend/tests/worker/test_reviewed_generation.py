"""Empty-body first generation and cross-browser recovery through real HTTP."""
# ruff: noqa: F811

from __future__ import annotations

import uuid

from tests.worker.test_snapshots import (  # noqa: F401 - shared integration fixtures
    _create_patient,
    _make_physician,
    _patch_draft,
    _physician_client,
    admin_client,
    clean_registry,
    migrated_test_engine,
)
from x_insight.models.bundles import REGISTRATION_ORDER
from x_insight.models.workflow_release import history_fields, resolve_workflow


def test_new_registration_resolves_packages_resumes_and_retries_exact_pins(
    admin_client,
    clean_registry,
    monkeypatch,
):
    _make_physician(admin_client, "dr_reviewed")
    owner, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_reviewed", "pw123")
    created = _create_patient(owner, csrf)
    eid = created["draft"]["id"]
    endpoint = f"/api/v1/encounters/{eid}/generation-batches"
    assert owner.get(endpoint + "/latest").json() == {"batch": None}
    headers = {"X-CSRF-Token": csrf, "If-Match": '"1"', "Idempotency-Key": str(uuid.uuid4())}
    started = owner.post(endpoint, json={}, headers=headers)
    assert started.status_code == 202, started.text
    body = started.json()
    assert tuple(r["question_key"] for r in body["question_runs"]) == REGISTRATION_ORDER
    assert all(r["status"] == "needs_clarification" for r in body["question_runs"])
    assert body["batch"]["pinned_bundle"]["release_version"] == "2026-10-22"
    assert owner.post(endpoint, json={}, headers=headers).json() == body
    # Another session has no browser storage and recovers the same durable batch.
    other_browser, other_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_reviewed", "pw123"
    )
    latest = other_browser.get(endpoint + "/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json()["batch"]["id"] == body["batch"]["id"]
    retry = other_browser.post(
        endpoint,
        json={"source_batch_id": body["batch"]["id"]},
        headers={
            "X-CSRF-Token": other_csrf,
            "If-Match": '"1"',
        },
    )
    assert retry.status_code == 202, retry.text
    assert (
        retry.json()["batch"]["pinned_bundle"]["bundle_hash"]
        == body["batch"]["pinned_bundle"]["bundle_hash"]
    )
    # Now save the reviewed fields: the universal first question queues work.
    fields = history_fields()
    values = {
        path: fields[path]["permitted"][0]
        for package in resolve_workflow("registration")[0]
        for path in package["manifest"]["applicability"]["required_fields"]
    }
    saved = _patch_draft(other_browser, other_csrf, eid, {"history": {"values": values}}, 1)
    assert saved.status_code == 200, saved.text
    ready = other_browser.post(
        endpoint,
        json={},
        headers={
            "X-CSRF-Token": other_csrf,
            "If-Match": '"2"',
        },
    )
    assert ready.status_code == 202, ready.text
    assert ready.json()["question_runs"][0]["status"] == "ready"
    read = other_browser.get("/api/v1/generation-batches/" + ready.json()["batch"]["id"])
    assert read.status_code == 200, read.text
    assert read.json()["jobs"][0]["status"] == "queued"
    assert all(
        v["status"] == "observed"
        for v in ready.json()["question_runs"][0]["projection"]["variables"]
    )
    history = other_browser.get(f"/api/v1/encounters/{eid}/history").json()
    assert len(history["workflow_fields"]) == 53
    _make_physician(admin_client, "dr_reviewed_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_reviewed_stranger", "pw123")
    assert stranger.get(endpoint + "/latest").status_code == 403


def test_followup_resolves_six_reviewed_packages(admin_client, clean_registry, monkeypatch):
    from x_insight.models.bundles import FOLLOWUP_ORDER

    _make_physician(admin_client, "dr_reviewed_followup")
    owner, csrf, _ = _physician_client(clean_registry, monkeypatch, "dr_reviewed_followup", "pw123")
    created = _create_patient(owner, csrf)
    discarded = owner.post(
        f"/api/v1/encounters/{created['draft']['id']}/discard",
        json={"confirm": True},
        headers={"X-CSRF-Token": csrf, "If-Match": '"1"'},
    )
    assert discarded.status_code == 200, discarded.text
    followup = owner.post(
        f"/api/v1/patients/{created['patient']['id']}/encounters",
        json={"kind": "follow_up"},
        headers={"X-CSRF-Token": csrf},
    )
    assert followup.status_code == 201, followup.text
    eid = followup.json()["encounter"]["id"]
    started = owner.post(
        f"/api/v1/encounters/{eid}/generation-batches",
        json={},
        headers={"X-CSRF-Token": csrf, "If-Match": '"1"'},
    )
    assert started.status_code == 202, started.text
    assert tuple(run["question_key"] for run in started.json()["question_runs"]) == FOLLOWUP_ORDER
    assert all(run["status"] == "needs_clarification" for run in started.json()["question_runs"])
    history = owner.get(f"/api/v1/encounters/{eid}/history")
    assert history.status_code == 200, history.text
    assert len(history.json()["workflow_fields"]) == 76
