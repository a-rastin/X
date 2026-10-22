"""Reviewed starts fail before persistence; saved ready context recovers across sessions."""
# ruff: noqa: F811

from __future__ import annotations

import shutil
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.assessments.test_cssrs import _all_no
from tests.models.test_proposal_readiness import FIRST_PATH, known_context
from tests.worker.test_snapshots import (  # noqa: F401 - shared integration fixtures
    _create_patient,
    _make_physician,
    _patch_draft,
    _physician_client,
    admin_client,
    clean_registry,
    migrated_test_engine,
)
from x_insight.app import app
from x_insight.cases.history import ALLOWED_FIELDS
from x_insight.models.bundles import FOLLOWUP_ORDER, REGISTRATION_ORDER
from x_insight.models.workflow_release import content_root


def _setup(admin_client, clean_registry, monkeypatch, username="dr_reviewed"):
    _make_physician(admin_client, username)
    owner, csrf, _ = _physician_client(clean_registry, monkeypatch, username, "pw123")
    return owner, csrf, _create_patient(owner, csrf)


def _start(owner, csrf, eid, revision, body=None, **extra_headers):
    return owner.post(
        f"/api/v1/encounters/{eid}/generation-batches",
        json=body or {},
        headers={"X-CSRF-Token": csrf, "If-Match": f'"{revision}"', **extra_headers},
    )


def _latest_batch(owner, eid):
    response = owner.get(f"/api/v1/encounters/{eid}/generation-batches/latest")
    assert response.status_code == 200, response.text
    return response.json()["batch"]


def test_complete_core_history_rejects_reviewed_start_before_batch_or_job_creation(
    admin_client, clean_registry, monkeypatch
):
    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch)
    eid = created["draft"]["id"]
    values = {key: spec["permitted"][0] for key, spec in ALLOWED_FIELDS.items()}
    saved = _patch_draft(
        owner, csrf, eid, {"history": {"values": values}, "cssrs": {"answers": _all_no()}}, 1
    )
    assert saved.status_code == 200, saved.text
    history = owner.get(f"/api/v1/encounters/{eid}/history").json()
    assert history["evaluation"]["status"] == "complete"
    endpoint = f"/api/v1/encounters/{eid}/proposal-readiness"
    before = owner.get(f"/api/v1/encounters/{eid}").json()
    readiness = owner.get(endpoint)
    assert readiness.status_code == 200, readiness.text
    assert readiness.json() == history["proposal_readiness"]
    assert readiness.json()["source_revision"] == 2
    assert len(readiness.json()["blockers"]) == 28
    assert owner.get(f"/api/v1/encounters/{eid}").json() == before
    assert _latest_batch(owner, eid) is None
    key = str(uuid.uuid4())
    for _ in range(2):
        blocked = _start(owner, csrf, eid, 2, **{"Idempotency-Key": key})
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["code"] == "PROPOSAL_INPUTS_INCOMPLETE"
        assert set(blocked.json()["field_errors"]) == {
            item["source_path"] for item in readiness.json()["blockers"]
        }
        assert FIRST_PATH in blocked.json()["field_errors"]
        assert _latest_batch(owner, eid) is None
    assert owner.get(f"/api/v1/encounters/{eid}/generation-batches/latest").json() == {
        "batch": None
    }
    # The same idempotency key can succeed after the rejected start is corrected.
    save_headers = {
        "X-CSRF-Token": csrf,
        "If-Match": '"2"',
        "Idempotency-Key": str(uuid.uuid4()),
    }
    saved = owner.post(
        f"/api/v1/encounters/{eid}/history",
        json={"values": known_context()},
        headers=save_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["proposal_readiness"]["ready"] is True
    assert saved.json()["proposal_readiness"]["source_revision"] == 3
    assert saved.json()["proposal_readiness"] == owner.get(endpoint).json()
    replay = owner.post(
        f"/api/v1/encounters/{eid}/history",
        json={"values": known_context()},
        headers=save_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == saved.json()
    started = _start(owner, csrf, eid, 3, **{"Idempotency-Key": key})
    assert started.status_code == 202, started.text
    latest = _latest_batch(owner, eid)
    assert latest["id"] == started.json()["batch"]["id"]
    visible = owner.get("/api/v1/generation-batches/" + latest["id"])
    assert visible.status_code == 200, visible.text
    assert len(visible.json()["question_runs"]) == 5
    assert len(visible.json()["jobs"]) == 1
    assert visible.json()["jobs"][0]["status"] == "queued"


def test_new_registration_resolves_packages_resumes_and_retries_exact_pins(
    admin_client, clean_registry, monkeypatch, tmp_path
):
    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch)
    eid = created["draft"]["id"]
    saved = _patch_draft(
        owner,
        csrf,
        eid,
        {"history": {"values": known_context()}, "cssrs": {"answers": _all_no()}},
        1,
    )
    assert saved.status_code == 200, saved.text
    endpoint = f"/api/v1/encounters/{eid}/generation-batches"
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    started = _start(owner, csrf, eid, 2, **headers)
    assert started.status_code == 202, started.text
    body = started.json()
    assert tuple(r["question_key"] for r in body["question_runs"]) == REGISTRATION_ORDER
    assert all(r["status"] == "ready" for r in body["question_runs"])
    assert body["batch"]["pinned_bundle"]["release_version"] == "2026-10-22"
    assert _start(owner, csrf, eid, 2, **headers).json() == body
    other_browser, other_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_reviewed", "pw123"
    )
    latest = other_browser.get(endpoint + "/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json()["batch"]["id"] == body["batch"]["id"]
    read = other_browser.get("/api/v1/generation-batches/" + body["batch"]["id"])
    assert read.status_code == 200, read.text
    assert read.json()["jobs"][0]["status"] == "queued"
    assert all(
        v["status"] == "observed" for v in body["question_runs"][0]["projection"]["variables"]
    )

    another = _create_patient(owner, csrf, identifier="0012345679")
    another_eid = another["draft"]["id"]
    mismatch = owner.get(
        f"/api/v1/encounters/{another_eid}/proposal-readiness",
        params={"source_batch_id": body["batch"]["id"]},
    )
    assert mismatch.status_code == 422, mismatch.text
    assert (
        _start(owner, csrf, another_eid, 1, {"source_batch_id": body["batch"]["id"]}).status_code
        == 422
    )
    _make_physician(admin_client, "dr_retry_stranger")
    stranger, _, _ = _physician_client(clean_registry, monkeypatch, "dr_retry_stranger", "pw123")
    assert (
        stranger.get(
            f"/api/v1/encounters/{eid}/proposal-readiness",
            params={"source_batch_id": body["batch"]["id"]},
        ).status_code
        == 403
    )

    # Model a broken external content deployment: definitions remain available,
    # but new runs cannot resolve an active release. Existing runs retain pins.
    values = known_context()
    replacement_content = tmp_path / "content"
    shutil.copytree(content_root() / "history", replacement_content / "history")
    (replacement_content / "bundles").mkdir()
    (replacement_content / "bundles" / "active-release.json").write_text(
        '{"schema_version": "unavailable"}'
    )
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(replacement_content))
    unavailable = other_browser.get(f"/api/v1/encounters/{eid}/proposal-readiness")
    assert unavailable.status_code == 422, unavailable.text
    assert unavailable.json()["code"] == "WORKFLOW_CONTENT_UNAVAILABLE"
    ready = other_browser.get(
        f"/api/v1/encounters/{eid}/proposal-readiness",
        params={"source_batch_id": body["batch"]["id"]},
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["ready"] is True
    retry = _start(other_browser, other_csrf, eid, 2, {"source_batch_id": body["batch"]["id"]})
    assert retry.status_code == 202, retry.text
    assert retry.json()["batch"]["pinned_bundle"] == body["batch"]["pinned_bundle"]
    values[FIRST_PATH] = "unknown"
    saved = _patch_draft(
        other_browser,
        other_csrf,
        eid,
        {"history": {"values": values}, "cssrs": {"answers": _all_no()}},
        2,
    )
    assert saved.status_code == 200, saved.text
    blocked = other_browser.get(
        f"/api/v1/encounters/{eid}/proposal-readiness",
        params={"source_batch_id": body["batch"]["id"]},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["ready"] is False
    assert blocked.json()["source_revision"] == 3
    assert blocked.json()["blockers"][0]["status"] == "unknown"
    batch_endpoint = "/api/v1/generation-batches/" + body["batch"]["id"]
    before_denied = other_browser.get(batch_endpoint)
    assert before_denied.status_code == 200, before_denied.text
    denied = _start(other_browser, other_csrf, eid, 3, {"source_batch_id": body["batch"]["id"]})
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "PROPOSAL_INPUTS_INCOMPLETE"
    assert _latest_batch(other_browser, eid)["id"] == body["batch"]["id"]
    after_denied = other_browser.get(batch_endpoint)
    assert after_denied.status_code == 200, after_denied.text
    for key in ("batch", "question_runs", "jobs"):
        assert after_denied.json()[key] == before_denied.json()[key]


def test_followup_resolves_six_reviewed_packages_after_ready_context(
    admin_client, clean_registry, monkeypatch
):
    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch)
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
    blocked = _start(owner, csrf, eid, 1)
    assert blocked.status_code == 422, blocked.text
    assert _latest_batch(owner, eid) is None
    assert len(owner.get(f"/api/v1/encounters/{eid}/history").json()["workflow_fields"]) == 76
    saved = _patch_draft(owner, csrf, eid, {"history": {"values": known_context("followup")}}, 1)
    assert saved.status_code == 200, saved.text
    readiness = owner.get(f"/api/v1/encounters/{eid}/proposal-readiness").json()
    assert readiness["ready"] is True
    started = _start(owner, csrf, eid, 2)
    assert started.status_code == 202, started.text
    assert tuple(run["question_key"] for run in started.json()["question_runs"]) == FOLLOWUP_ORDER
    assert all(run["status"] == "ready" for run in started.json()["question_runs"])


def test_readiness_and_start_keep_auth_revision_lifecycle_and_archive_safeguards(
    admin_client, clean_registry, monkeypatch
):
    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch)
    eid = created["draft"]["id"]
    endpoint = f"/api/v1/encounters/{eid}/proposal-readiness"
    assert TestClient(app).get(endpoint).status_code == 401
    assert admin_client["client"].get(endpoint).status_code == 403
    _make_physician(admin_client, "dr_stranger")
    stranger, stranger_csrf, _ = _physician_client(
        clean_registry, monkeypatch, "dr_stranger", "pw123"
    )
    assert stranger.get(endpoint).status_code == 403
    assert _start(stranger, stranger_csrf, eid, 1).status_code == 403
    assert owner.get(f"/api/v1/encounters/{uuid.uuid4()}/proposal-readiness").status_code == 404
    assert owner.get(endpoint, params={"source_batch_id": str(uuid.uuid4())}).status_code == 404
    assert _start(owner, "wrong-csrf", eid, 1).status_code == 403
    assert _start(owner, csrf, eid, 2).status_code == 412
    archived = admin_client["client"].post(
        f"/api/v1/patients/{created['patient']['id']}/archive",
        headers={"X-CSRF-Token": admin_client["csrf"], "If-Match": '"1"'},
    )
    assert archived.status_code == 200, archived.text
    # Archived author-owned content stays readable; generation remains fenced.
    assert owner.get(endpoint).status_code == 200
    archived_start = _start(owner, csrf, eid, 1)
    assert archived_start.status_code == 409, archived_start.text
    assert archived_start.json()["code"] == "PATIENT_ARCHIVED"
    assert _latest_batch(owner, eid) is None


@pytest.mark.parametrize("lifecycle", ["discarded", "signed"])
def test_readiness_does_not_expose_released_drafts(
    admin_client, clean_registry, monkeypatch, lifecycle
):
    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch)
    eid = created["draft"]["id"]
    # Lifecycle is established by signing/discard suites; seed only this boundary.
    with clean_registry.begin() as connection:
        connection.execute(
            text("UPDATE encounters SET lifecycle=:lifecycle WHERE id=:id"),
            {"lifecycle": lifecycle, "id": uuid.UUID(eid)},
        )
    assert owner.get(f"/api/v1/encounters/{eid}/proposal-readiness").status_code == 404
    assert _start(owner, csrf, eid, 1).status_code == 404
    assert owner.get(f"/api/v1/encounters/{eid}/generation-batches/latest").status_code == 404


def test_frozen_historical_contract_stays_readable_but_requires_new_current_proposal(
    admin_client, clean_registry, monkeypatch
):
    import json
    from copy import deepcopy

    from sqlalchemy import update

    from x_insight.models.question_package import load_question_package
    from x_insight.reasoning.snapshots import compute_analysis_fingerprint
    from x_insight.reasoning.tables import generation_batches, question_runs

    owner, csrf, created = _setup(admin_client, clean_registry, monkeypatch, "dr_old_contract")
    eid = created["draft"]["id"]
    saved = _patch_draft(
        owner,
        csrf,
        eid,
        {"history": {"values": known_context()}, "cssrs": {"answers": _all_no()}},
        1,
    )
    assert saved.status_code == 200, saved.text
    started = _start(owner, csrf, eid, 2)
    assert started.status_code == 202, started.text
    batch = started.json()
    run = batch["question_runs"][0]
    directory = content_root() / "questions/pharmacotherapy/s39-reviewed-v1"
    historic_package = {
        name: json.loads((directory / f"{name}.json").read_text())
        for name in ("manifest", "template", "examples", "review")
    }
    historic_package["network_xml"] = (directory / "network.xml").read_text()
    historic_package["prompt"] = {
        "version": historic_package["manifest"]["prompt_version"],
        "text": (directory / "prompt.txt").read_text(),
    }
    historic_pin = deepcopy(batch["batch"]["pinned_bundle"])
    historic_pin.pop("history_input_contract_version")
    historic_pin.pop("history_input_contract_hash")
    historic_versions = {
        **historic_pin["per_question"]["pharmacotherapy"],
        "package_hash": load_question_package(historic_package).package_hash,
        "package_version": historic_package["manifest"]["version"],
    }
    historic_pin["per_question"]["pharmacotherapy"] = historic_versions
    historic_pin.update(historic_versions)
    historic_fingerprint, historic_payload = compute_analysis_fingerprint(
        saved.json()["draft_data"], historic_pin
    )
    # Historical fixture includes its old package pin and matching fingerprint.
    # Callers cannot rewrite these immutable records through HTTP.
    with clean_registry.begin() as connection:
        connection.execute(
            update(generation_batches)
            .where(generation_batches.c.id == uuid.UUID(batch["batch"]["id"]))
            .values(
                pinned_bundle=historic_pin,
                fingerprint=historic_fingerprint,
                fingerprint_payload=historic_payload,
            )
        )
        connection.execute(
            update(question_runs)
            .where(question_runs.c.batch_id == uuid.UUID(batch["batch"]["id"]))
            .values(fingerprint=historic_fingerprint)
        )
        connection.execute(
            update(question_runs)
            .where(question_runs.c.id == uuid.UUID(run["id"]))
            .values(
                pinned_package=historic_package,
                pinned_versions={**historic_versions, "position": 0},
            )
        )
    batch_url = "/api/v1/generation-batches/" + batch["batch"]["id"]
    historical_read = owner.get(batch_url)
    assert historical_read.status_code == 200, historical_read.text
    historical_run = next(
        r for r in historical_read.json()["question_runs"] if r["id"] == run["id"]
    )
    assert historical_run["projection"] == run["projection"]
    assert historical_run["projection_hash"] == run["projection_hash"]
    retry_body = {"source_batch_id": batch["batch"]["id"]}
    readiness = owner.get(f"/api/v1/encounters/{eid}/proposal-readiness", params=retry_body)
    retry = _start(owner, csrf, eid, 2, retry_body)
    for denied in (readiness, retry):
        assert denied.status_code == 422, denied.text
        assert denied.json()["code"] == "HISTORY_INPUT_CONTRACT_CHANGED"
    assert _latest_batch(owner, eid)["id"] == batch["batch"]["id"]
    current = _start(owner, csrf, eid, 2)
    assert current.status_code == 202, current.text
    assert current.json()["batch"]["id"] != batch["batch"]["id"]
    assert (
        current.json()["batch"]["pinned_bundle"]["history_input_contract_version"]
        == "history-inputs-2026-10-22-v3"
    )
    assert owner.get(batch_url).json()["question_runs"] == historical_read.json()["question_runs"]
