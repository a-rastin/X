"""Saturation behavior and cross-patient integrity (S58 §4, seams T1/T8).

Through public HTTP (T1) over real PostgreSQL with T8 ``run_once()``:

- While both global provider slots are leased, a third generation sees the
  visible busy state — yet draft saves and slider adjustments stay
  responsive and the independent local calculation still succeeds with
  zero provider calls (no MCP/provider requests on the local path).
- Interleaved adjustments for two patients bind the correct revisions with
  no cross-patient leakage, and repeated scoped reads stay stable (reads
  are served from scoped state; there is no cross-patient response cache
  to go stale — only HTTP ``Cache-Control`` headers).

Slot exclusivity itself is proven by
``test_two_workers_hold_distinct_slots_third_sees_busy`` (S44 §2, cited
not repeated); local progress under saturation extends the S48b
saturation case with saves/sliders and a visible third-batch busy state.
Synthetic fixtures only.
"""

from __future__ import annotations

import time

import pytest as _pytest
from fastapi.testclient import TestClient

from tests.worker.test_local_calculation import (
    _auth_headers,
    _new_physician_batch,
    _setup_encounter,
    _start_batch,
    _succeed_batch,
    clean_registry,
    migrated_test_engine,
    s48a_package,
)
from x_insight import contracts
from x_insight import db as db_module
from x_insight.app import app
from x_insight.reasoning import provider as provider_module
from x_insight.reasoning import worker as worker_module

__all__ = ["clean_registry", "migrated_test_engine"]

#: Responsiveness bound for local writes under saturation (seconds).
#: Local TestClient writes complete in milliseconds; 5s only guards
#: against provider-queue coupling, never asserts a performance target.
RESPONSIVE_BOUND_SECONDS = 5.0


def _saturate(engine) -> None:  # noqa: ANN001
    """Hold both global provider slots (setup only; T8 queue claims)."""
    from x_insight.reasoning import queue as queue_module

    with db_module.session_scope(engine) as session:
        first = queue_module.claim_next_job(session, "worker-s58-a")
    assert first is not None and "job" in first, first
    with db_module.session_scope(engine) as session:
        second = queue_module.claim_next_job(session, "worker-s58-b")
    assert second is not None and "job" in second, second
    assert first["job"]["id"] != second["job"]["id"]


def test_saves_and_sliders_responsive_while_provider_saturated(clean_registry, monkeypatch) -> None:
    """S58 §4: saturated provider slots never block saves/sliders/local work."""
    client_c, csrf_c, encounter_c, revision_c, package_c, started_c = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s58_sat_c", "0012345691"
    )
    _, run_c = _succeed_batch(clean_registry, client_c, package_c, started_c)
    _new_physician_batch(clean_registry, monkeypatch, "dr_s58_sat_a", "0012345692")
    _new_physician_batch(clean_registry, monkeypatch, "dr_s58_sat_b", "0012345693")
    _saturate(clean_registry)

    # A third generation sees the visible busy state without losing its place.
    client_d, _, _, _, _, started_d = _new_physician_batch(
        clean_registry, monkeypatch, "dr_s58_sat_d", "0012345694"
    )
    queued = client_d.get(f"/api/v1/generation-batches/{started_d['batch']['id']}").json()
    assert queued["job"]["status"] == "queued"
    assert queued["queue"]["busy"] is True
    assert queued["queue"]["leased_count"] == 2

    # Draft saves stay responsive while provider slots are saturated.
    begun = time.perf_counter()
    saved = client_c.patch(
        f"/api/v1/encounters/{encounter_c}",
        json={"draft_data": {"history": {"values": {"h_a": "yes", "h_b": "no"}}, "gate": "true"}},
        headers={**_auth_headers(csrf_c), "If-Match": contracts.format_etag(revision_c)},
    )
    save_seconds = time.perf_counter() - begun
    assert saved.status_code == 200, saved.text
    assert save_seconds < RESPONSIVE_BOUND_SECONDS, save_seconds

    # Slider adjustments stay responsive too.
    begun = time.perf_counter()
    posted = client_c.post(
        f"/api/v1/question-runs/{run_c}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf_c),
    )
    slider_seconds = time.perf_counter() - begun
    assert posted.status_code == 200, posted.text
    assert slider_seconds < RESPONSIVE_BOUND_SECONDS, slider_seconds
    revision = posted.json()["revision"]

    # Independent local progress with zero provider/MCP requests.
    stub = provider_module.ControlledStubAdapter(mode="succeed")
    outcome = worker_module.run_once(clean_registry, stub)
    assert outcome["status"] == "succeeded", outcome
    assert outcome.get("job_class") == "local_calculation"
    assert stub.calls == []
    done = client_c.get(f"/api/v1/question-runs/{run_c}/review").json()
    assert done["calculation_state"] == "successfully_recalculated"
    assert done["calculation_result"]["cpt_revision_id"] == revision["id"]


def test_cross_patient_adjustments_bind_correct_revisions(clean_registry, monkeypatch) -> None:
    """S58 §4: interleaved adjustments across patients keep revision integrity."""
    monkeypatch.setattr(db_module, "get_engine", lambda: clean_registry)
    admin = TestClient(app)
    login = admin.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin", "role": "admin"},
    )
    assert login.status_code == 200, login.text
    made = admin.post(
        "/api/v1/physicians",
        json={"username": "dr_s58_cross", "password": "pw123"},
        headers=_auth_headers(login.json()["csrf_token"]),
    )
    assert made.status_code == 201, made.text
    client = TestClient(app)
    session = client.post(
        "/api/v1/auth/login",
        json={"username": "dr_s58_cross", "password": "pw123", "role": "physician"},
    )
    assert session.status_code == 200, session.text
    csrf = session.json()["csrf_token"]

    encounter_a, revision_a = _setup_encounter(client, csrf, "0012345695")
    encounter_b, revision_b = _setup_encounter(client, csrf, "0012345696")
    package = s48a_package()
    started_a = _start_batch(client, csrf, encounter_a, revision_a, package)
    assert started_a.status_code == 202, started_a.text
    started_b = _start_batch(client, csrf, encounter_b, revision_b, package)
    assert started_b.status_code == 202, started_b.text
    _, run_a = _succeed_batch(clean_registry, client, package, started_a.json())
    _, run_b = _succeed_batch(clean_registry, client, package, started_b.json())

    posted_a = client.post(
        f"/api/v1/question-runs/{run_a}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "40",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert posted_a.status_code == 200, posted_a.text
    posted_b = client.post(
        f"/api/v1/question-runs/{run_b}/cpt-adjustments",
        json={
            "node_id": "A",
            "parent_states": [],
            "state": "yes",
            "target_percentage": "70",
            "expected_review_revision": 1,
        },
        headers=_auth_headers(csrf),
    )
    assert posted_b.status_code == 200, posted_b.text
    rev_a = posted_a.json()["revision"]
    rev_b = posted_b.json()["revision"]
    assert rev_a["id"] != rev_b["id"]

    stub = provider_module.ControlledStubAdapter(mode="succeed")
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert worker_module.run_once(clean_registry, stub)["status"] == "succeeded"
    assert stub.calls == []

    review_a = client.get(f"/api/v1/question-runs/{run_a}/review").json()
    review_b = client.get(f"/api/v1/question-runs/{run_b}/review").json()
    assert review_a["calculation_result"]["cpt_revision_id"] == rev_a["id"]
    assert review_b["calculation_result"]["cpt_revision_id"] == rev_b["id"]
    by_node_a = {p["node_id"]: p for p in review_a["calculation_result"]["posteriors"]}
    by_node_b = {p["node_id"]: p for p in review_b["calculation_result"]["posteriors"]}
    assert by_node_a["A"]["probabilities"] == _pytest.approx([0.60, 0.40], abs=1e-6)
    assert by_node_b["A"]["probabilities"] == _pytest.approx([0.30, 0.70], abs=1e-6)

    # Scoped reads stay correct and stable across patients (no shared cache).
    for _ in range(2):
        read_a = client.get(f"/api/v1/encounters/{encounter_a}").json()
        read_b = client.get(f"/api/v1/encounters/{encounter_b}").json()
        assert read_a["encounter"]["id"] == encounter_a
        assert read_b["encounter"]["id"] == encounter_b
        assert read_a["encounter"]["id"] != read_b["encounter"]["id"]
        directory = client.get("/api/v1/patients?query=001234569&limit=25").json()
        found = {item["identifier"] for item in directory["items"]}
        assert {"0012345695", "0012345696"} <= found
