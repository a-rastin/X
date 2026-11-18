"""Exercise the simulation wire contract against immutable stored proposals."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Text, func, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by

from tests.http import test_follow_up_plans as follow_up_http
from tests.http import test_new_case_plans as new_case_http
from tests.http.test_new_case import (
    admin_client,
    clean_registry,
    clinician,
    installed_content,
    migrated_test_engine,
)
from tests.http.test_patients import _make_physician, _physician_client
from x_insight import contracts
from x_insight.app import app
from x_insight.cases import new_case, tables
from x_insight.cases.reasoning_contracts import SimulationRequest, SimulationResponse
from x_insight.ddi.lexicomp import drug_id
from x_insight.identity import tables as identity_tables
from x_insight.operations.audit import audit_events
from x_insight.probability_review import tables as review_tables
from x_insight.reasoning import coordinator, provider
from x_insight.reasoning import tables as reasoning_tables

__all__ = [
    "admin_client",
    "clean_registry",
    "clinician",
    "installed_content",
    "migrated_test_engine",
]

FIXTURE = Path(__file__).resolve().parents[3] / "web/tests/reasoning-simulation-contract.json"
DOMAIN_TABLES = (
    tables.encounters,
    tables.new_case_proposals,
    tables.follow_up_proposals,
    tables.secondary_plans,
    tables.signed_encounter_snapshots,
    reasoning_tables.proposal_snapshots,
    reasoning_tables.original_baselines,
    reasoning_tables.generation_batches,
    reasoning_tables.question_runs,
    reasoning_tables.reasoning_jobs,
    reasoning_tables.reasoning_job_attempts,
    review_tables.question_review_states,
    review_tables.cpt_revisions,
    review_tables.calculation_results,
    review_tables.probability_acceptances,
    audit_events,
    identity_tables.idempotency_records,
)


def _schema_fixture():
    request = SimulationRequest.model_json_schema()
    response = SimulationResponse.model_json_schema()
    identity = response["$defs"]["Identity"]["properties"]
    return {
        "schema_version": request["properties"]["schema_version"]["const"],
        "algorithm_version": identity["algorithm_version"]["const"],
        "workflows": identity["workflow"]["enum"],
        "treatment_ids": list(request["properties"]["selected_treatment_id"]["anyOf"][0]["enum"]),
        "change_kinds": list(request["properties"]["changes"]["items"]["discriminator"]["mapping"]),
        "evaluation_statuses": response["$defs"]["Evaluation"]["properties"]["status"]["enum"],
        "response_fields": response["required"],
    }


def test_shared_schema_fixture():
    assert json.loads(FIXTURE.read_text()) == _schema_fixture()
    schema = app.openapi()["paths"]["/api/v1/encounters/{encounter_id}/reasoning/simulate"]["post"]
    success = schema["responses"]["200"]["content"]["application/json"]["schema"]
    assert success["$ref"] == "#/components/schemas/SimulationResponse"


def _domain_snapshot(engine):
    with engine.connect() as connection:
        snapshots = {}
        for table in DOMAIN_TABLES:
            rows = func.jsonb_agg(
                aggregate_order_by(table.table_valued(), *table.primary_key.columns)
            )
            digest = func.md5(func.coalesce(rows.cast(Text), "[]"))
            snapshots[table.name] = tuple(
                connection.execute(select(func.count(), digest).select_from(table)).one()
            )
        return snapshots


def _proposal(client, url, workflow, headers):
    response = client.post(f"{url}/{workflow}/proposals", json={}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["proposal"]


def _request(proposal, *changes, treatment=None, sequence=0):
    return {
        "schema_version": "reasoning-simulation-v1",
        "baseline_proposal_id": proposal["id"],
        "selected_treatment_id": treatment,
        "client_request_id": sequence,
        "changes": list(changes),
    }


def _simulate(client, url, csrf, request):
    return client.post(f"{url}/reasoning/simulate", json=request, headers={"X-CSRF-Token": csrf})


def _result(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert SimulationResponse.model_validate(result).model_dump(mode="json") == result
    assert "generated_at" not in response.text
    return result


def _no_external_execution(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Simulation entered a provider or worker execution path.")

    monkeypatch.setattr(provider, "post_json", unexpected)
    monkeypatch.setattr(provider.BoundedProviderAdapter, "_mcp_read", unexpected)
    monkeypatch.setattr(coordinator, "execute_claimed_job", unexpected)


def test_new_case_simulation_is_deterministic_and_stateless(clinician, clean_registry, monkeypatch):
    client, csrf, _ = clinician
    _, url, revision = new_case_http.ready(client, csrf)
    proposal = _proposal(client, url, "new-case", new_case_http.headers(csrf, revision))
    snapshot = _domain_snapshot(clean_registry)
    _no_external_execution(monkeypatch)
    baseline = _result(_simulate(client, url, csrf, _request(proposal)))
    assert baseline["identity"]["workflow"] == "new-case-v1"
    assert baseline["identity"]["baseline_input_hash"] == proposal["input_hash"]
    assert baseline["identity"]["availability_snapshot_hash"] == contracts.canonical_hash(
        proposal["payload"]["availability_snapshot"]
    )
    assert baseline["patient"] == baseline["scenario"]
    assert len(baseline["comparison_rows"]) == 22
    assert len(baseline["selected_attribution"]["patient"]["contributions"]) == 16
    assert next(
        control for control in baseline["controls"] if control["control_id"] == "harm.self"
    )["affected_outputs"] == ["section"]

    qtc = {"kind": "boolean", "control_id": "qtc_risk", "value": True}
    panss = {"kind": "panss", "item_id": "P1", "value": 6}
    assessment_only = _result(
        _simulate(
            client,
            url,
            csrf,
            _request(proposal, panss, treatment="oral:aripiprazole"),
        )
    )
    assert assessment_only["diff"]["banner_kind"] == "assessment_changed"
    assert assessment_only["diff"]["probabilities"] == {}

    query = _request(proposal, qtc, panss, treatment="oral:aripiprazole")
    result = _result(_simulate(client, url, csrf, query))
    assert result == _result(_simulate(client, url, csrf, query))
    assert result["diff"]["probabilities"]
    assert result["scenario"]["assessments"]["panss"]["status"] == "partial"
    assert result["identity"]["scenario_hash"] != baseline["identity"]["scenario_hash"]

    reordered = _result(
        _simulate(
            client,
            url,
            csrf,
            _request(proposal, panss, qtc, treatment="oral:olanzapine", sequence=1),
        )
    )
    assert reordered["identity"]["scenario_hash"] == result["identity"]["scenario_hash"]
    assert reordered["identity"]["selected_treatment_id"] == "oral:olanzapine"
    assert reordered["identity"]["client_request_id"] == 1
    no_op = _result(_simulate(client, url, csrf, _request(proposal, {**qtc, "value": False})))
    assert no_op["identity"]["scenario_hash"] == baseline["identity"]["scenario_hash"]

    lai = {"kind": "boolean", "control_id": "lai", "value": True}
    gated = _result(_simulate(client, url, csrf, _request(proposal, lai)))
    assert gated["scenario"]["model_status"] == "model_unavailable"
    assert gated["selected_attribution"]["scenario"] is None
    assert all(row["scenario_probability"] is None for row in gated["comparison_rows"])
    assert _domain_snapshot(clean_registry) == snapshot


def test_author_and_frozen_baseline_errors(clinician, clean_registry, admin_client, monkeypatch):
    client, csrf, _ = clinician
    _, url, revision = new_case_http.ready(client, csrf)
    proposal = _proposal(client, url, "new-case", new_case_http.headers(csrf, revision))
    body = _request(proposal)
    assert _simulate(TestClient(app), url, "", body).status_code == 401
    assert _simulate(client, url, "invalid", body).status_code == 403
    assert _simulate(admin_client["client"], url, admin_client["csrf"], body).status_code == 403
    _make_physician(admin_client, "otherphysician")
    other, other_csrf, _ = _physician_client(clean_registry, monkeypatch, "otherphysician", "pw123")
    assert _simulate(other, url, other_csrf, body).status_code == 403
    _, other_url = new_case_http.create(client, csrf, identifier="0012345686")
    assert _simulate(client, other_url, csrf, body).status_code == 404

    for bad in (
        {"changes": [{"kind": "panss", "item_id": "P1", "value": True}]},
        {"changes": [{"kind": "panss", "item_id": "G17", "value": 2}]},
        {"changes": [{"kind": "cssrs", "item_id": "css_i1_recent", "value": "yes"}]},
        {"changes": [{"kind": "boolean", "control_id": "qtc_risk", "value": 1}]},
        {"changes": [{"kind": "boolean", "control_id": "qtc_risk", "value": True}] * 2},
        {"changes": [{"kind": "choice", "control_id": "bmi.source", "value": "bad"}]},
        {
            "changes": [
                {"kind": "boolean", "control_id": "follow_up.effects.parkinsonism", "value": True}
            ]
        },
        {"changes": [{"kind": "boolean", "control_id": "qtc_risk", "value": True}]},
        {"client_request_id": False},
        {"selected_treatment_id": "oral:unknown"},
        {"baseline_proposal_id": "not-a-uuid"},
        {"final_plan_text": "not allowed"},
    ):
        response = _simulate(client, url, csrf, {**body, **bad})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "VALIDATION_FAILED"

    def unavailable(*args, **kwargs):
        raise contracts.ContractError(409, "NEW_CASE_MODEL_UNAVAILABLE", "Missing model.")

    with monkeypatch.context() as scoped:
        scoped.setattr(new_case, "validate_pins", unavailable)
        conflict = _simulate(client, url, csrf, body)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "REASONING_BASELINE_UNAVAILABLE"

    payload = proposal["payload"]
    payload["raw_inputs"]["new_case"]["considerations"]["qtc_risk"] = True
    with clean_registry.begin() as connection:
        connection.execute(
            update(tables.new_case_proposals)
            .where(tables.new_case_proposals.c.id == proposal["id"])
            .values(payload=payload)
        )
    conflict = _simulate(client, url, csrf, body)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "REASONING_BASELINE_UNAVAILABLE"


def test_follow_up_exposes_branch_and_direct_results(
    clean_registry, monkeypatch, admin_client, installed_content
):
    client, csrf, _, eid, revision, draft = follow_up_http.ready(
        clean_registry, monkeypatch, admin_client
    )
    main = drug_id("Olanzapine")
    follow_up = draft["follow_up"]
    draft["medications"]["entries"] = [{"catalog_drug_id": main}]
    draft["medications"]["selection_dataset_version"] = follow_up["content_pins"][
        "lexicomp_version"
    ]
    follow_up["treatment"].update(main_catalog_drug_id=main, formulation="oral")
    follow_up["considerations"]["adherence"] = True
    follow_up["branches"].update(switch_needed=False, dose_reduction_possible=False)
    saved = follow_up_http._save(client, csrf, eid, draft, revision)
    assert saved.status_code == 200, saved.text
    url = f"/api/v1/encounters/{eid}"
    proposal = _proposal(client, url, "follow-up", follow_up_http.headers(csrf, revision + 1))
    snapshot = _domain_snapshot(clean_registry)
    _no_external_execution(monkeypatch)
    baseline = _result(_simulate(client, url, csrf, _request(proposal)))
    assert baseline["identity"]["workflow"] == "follow-up-v1"
    assert baseline["patient"]["final_decision"]["terminal"] == "continue_same"
    assert baseline["selected_attribution"] is None

    recovery = {"kind": "choice", "control_id": "recovery", "value": "none_or_worse"}
    cssrs = {"kind": "cssrs", "item_id": "css_i4_recent", "value": True}
    partial = _result(_simulate(client, url, csrf, _request(proposal, recovery, cssrs)))
    assert partial["evaluation"]["scenario"]["status"] == "partial"
    assert partial["scenario"]["sections"]["care_setting"]["setting"] == "inpatient"
    assert next(
        control
        for control in partial["controls"]
        if control["control_id"] == "branches.treatment_resistant"
    )["active"]
    assert partial["comparison_rows"] == []

    resistant = {
        "kind": "nullable_boolean",
        "control_id": "branches.treatment_resistant",
        "value": True,
    }
    direct = _result(_simulate(client, url, csrf, _request(proposal, recovery, resistant)))
    assert direct["scenario"]["final_decision"]["terminal"] == "clozapine"
    assert direct["scenario"]["direct"]["treatment_id"] == "oral:clozapine"
    assert direct["comparison_rows"] == []

    unresolved_flag = {
        "kind": "nullable_boolean",
        "control_id": "conditions.hepatic_failure.manual",
        "value": None,
    }
    parkinsonism = {"kind": "boolean", "control_id": "effects.parkinsonism", "value": True}
    unresolved = _result(
        _simulate(
            client,
            url,
            csrf,
            _request(
                proposal,
                unresolved_flag,
                parkinsonism,
            ),
        )
    )
    assert unresolved["evaluation"]["scenario"]["status"] == "partial"
    assert unresolved["scenario"]["factors"]["Hepatic_Failure"]["value"] is None

    modeled = _result(
        _simulate(
            client,
            url,
            csrf,
            _request(
                proposal,
                parkinsonism,
                treatment="oral:olanzapine",
            ),
        )
    )
    row = next(
        row for row in modeled["comparison_rows"] if row["treatment_id"] == "oral:olanzapine"
    )
    assert row["patient_probability"] is None
    assert "current_main" in row["scenario_exclusion_reasons"]
    assert row["scenario_eligible_rank"] is None
    assert modeled["selected_attribution"]["patient"] is None
    assert _domain_snapshot(clean_registry) == snapshot
