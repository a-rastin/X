"""Replay stored proposals through the transient shared calculation path."""

from array import array
from copy import deepcopy

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.http import test_follow_up_plans as follow_up_http
from tests.http import test_new_case_plans as new_case_http
from tests.http.test_new_case import (
    admin_client,
    clean_registry,
    clinician,
    installed_content,
    migrated_test_engine,
)
from x_insight import contracts
from x_insight.cases.reasoning_exploration import explore_proposal
from x_insight.ddi.lexicomp import drug_id
from x_insight.models import oral_antipsychotic as oral

__all__ = [
    "admin_client",
    "clean_registry",
    "clinician",
    "installed_content",
    "migrated_test_engine",
]


def _proposal(client, url, workflow, headers):
    response = client.post(f"{url}/{workflow}/proposals", json={}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["proposal"]


def _explore(engine, proposal, **kwargs):
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        return explore_proposal(session, proposal, **kwargs)


def _stable_ranking(ranking):
    ranking = deepcopy(ranking)
    for field in ("interactions", "current_interactions", "candidate_interactions"):
        if ranking.get(field) is not None:
            ranking[field].pop("generated_at")
    return ranking


def _check_baseline(result, proposal):
    payload = proposal["payload"]
    assert result["patient"] == result["scenario"]
    assert _stable_ranking(result["patient"]["ranking"]) == _stable_ranking(payload["ranking"])
    assert contracts.canonical_json(result["patient"]["sections"]) == contracts.canonical_json(
        payload["sections"]
    )
    assert all(not changes for changes in result["diff"].values())
    assert all(edge["path_state"] == "both" for edge in result["active_graph"]["edges"])


@pytest.mark.parametrize("bmi_source", ["calculated", "manual"])
def test_new_case_frozen_replay_and_bmi_normalization(clinician, clean_registry, bmi_source):
    client, csrf, _ = clinician
    _, url, revision = new_case_http.ready(client, csrf)
    draft = client.get(url).json()["draft_data"]
    registration = draft["new_case"]
    registration["bmi"].update(height_cm="170", weight_kg="85", source=bmi_source)
    registration["labs"]["present"] = True
    registration["labs"]["values"]["ast"] = "45"
    registration["conditions"]["hepatic_failure"]["manual"] = False
    saved = new_case_http.save(client, csrf, url, draft, revision)
    assert saved.status_code == 200, saved.text
    proposal = _proposal(client, url, "new-case", new_case_http.headers(csrf, revision + 1))

    baseline = _explore(clean_registry, proposal)
    _check_baseline(baseline, proposal)
    assert baseline["identity"]["assessment_hash_origin"] == "resolved_from_pinned_versions"
    assert baseline["patient"]["state"]["evidence"] == proposal["payload"]["bn_evidence"]
    bmi_reset = _explore(clean_registry, proposal, overrides={"new_case.bmi.source": None})
    if bmi_source == "calculated":
        for field in ("diff", "active_graph", "selected_attribution"):
            assert bmi_reset[field] == baseline[field]
    else:
        assert bmi_reset["diff"]["raw_inputs"] == {
            "new_case.bmi.source": {"patient": "manual", "scenario": "calculated"}
        }
        assert bmi_reset["diff"]["factors"]["Weight_Gain_Risk"] == {
            "patient": False,
            "scenario": True,
        }
        return

    snapshot = deepcopy(proposal)
    assessments = _explore(
        clean_registry,
        proposal,
        overrides={"panss.answers.P1": 6, "cssrs.answers.css_i4_recent": True},
    )
    assert assessments["scenario"]["assessments"]["panss"]["status"] == "partial"
    assert set(assessments["diff"]["assessments"]) == {"panss", "cssrs"}
    assert assessments["scenario"]["sections"]["care_setting"]["value"] == "inpatient"
    assert not assessments["diff"]["probabilities"]
    assert not assessments["diff"]["factors"]
    edges = assessments["active_graph"]["edges"]
    assert any(
        edge["source"] == "source:panss.answers.P1" and edge["target"] == "assessment:panss"
        for edge in edges
    )
    assert not any(edge["kind"] == "bn_dependency" and "panss" in edge["source"] for edge in edges)

    source_path = "new_case.conditions.hepatic_failure.manual"
    changes = {
        source_path: None,
        "new_case.tobacco": True,
        "new_case.substances.marijuana": True,
        "new_case.considerations.qtc_risk": True,
        "new_case.considerations.avoid_sedation": True,
    }
    simulated = _explore(clean_registry, proposal, overrides=changes)
    factors = simulated["scenario"]["factors"]
    assert factors["Hepatic_Failure"]["value"] is True
    assert factors["Hepatic_Failure"]["simulated_override"] == [source_path]
    assert factors["Hepatic_Failure"]["provenance"]["manual_actor_id"] is None
    assert factors["Hepatic_Failure"]["provenance"]["manual_recorded_at"] is None
    assert factors["Marijuana_Smoking"]["value"] is False
    assert "simulated_override" not in factors["Marijuana_Smoking"]
    assert factors["Smoking"]["value"] is True
    assert factors["QTc_Prolongation_Risk"]["value"] is True
    assert factors["Sedative_Effects_Concern"]["value"] is True
    assert simulated["diff"]["probabilities"]
    reordered = _explore(clean_registry, proposal, overrides=dict(reversed(changes.items())))
    for field in ("diff", "active_graph", "selected_attribution"):
        assert simulated[field] == reordered[field]
    assert proposal == snapshot

    gated = _explore(
        clean_registry,
        proposal,
        overrides={
            "new_case.substances.present": True,
            "new_case.substances.marijuana": True,
            "new_case.considerations.lai": True,
        },
    )
    assert gated["scenario"]["factors"]["Marijuana_Smoking"]["value"] is True
    assert gated["scenario"]["ranking"]["status"] == "model_unavailable"
    assert gated["scenario"]["ranking"]["distribution"] == []
    assert gated["selected_attribution"]["scenario"] is None
    assert any(
        edge["kind"] == "bn_dependency" and edge["scenario_state"] == "superseded"
        for edge in gated["active_graph"]["edges"]
    )
    with pytest.raises(contracts.ContractError) as error:
        _explore(clean_registry, proposal, overrides={"panss.answers.P1": True})
    assert error.value.status_code == 422


def test_follow_up_frozen_replay_and_active_branch_changes(
    clean_registry, monkeypatch, admin_client, installed_content
):
    client, csrf, _, encounter_id, revision, draft = follow_up_http.ready(
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
    saved = follow_up_http._save(client, csrf, encounter_id, draft, revision)
    assert saved.status_code == 200, saved.text
    url = f"/api/v1/encounters/{encounter_id}"
    proposal = _proposal(client, url, "follow-up", follow_up_http.headers(csrf, revision + 1))

    baseline = _explore(clean_registry, proposal)
    _check_baseline(baseline, proposal)
    assert baseline["identity"]["assessment_hash_origin"] == "historical_byte_pin"
    assert baseline["patient"]["ranking"]["original_decision"]["terminal"] == "continue_same"
    assert baseline["patient"]["ranking"]["distribution"] == []

    partial = _explore(
        clean_registry,
        proposal,
        overrides={
            "follow_up.considerations.recovery": "none_or_worse",
            "cssrs.answers.css_i4_recent": True,
        },
    )
    assert partial["scenario"]["status"] == "partial"
    assert partial["scenario"]["active_questions"] == ["treatment_resistant"]
    assert partial["scenario"]["ranking"] is None
    assert partial["scenario"]["sections"]["care_setting"]["setting"] == "inpatient"
    assert any(
        node["id"] == "rule:terminal:continue_same" and node["scenario_state"] == "superseded"
        for node in partial["active_graph"]["nodes"]
    )

    modeled = _explore(
        clean_registry,
        proposal,
        overrides={
            "follow_up.considerations.recovery": "none_or_worse",
            "follow_up.branches.treatment_resistant": True,
            "follow_up.effects.parkinsonism": True,
        },
        selected_treatment_id="oral:olanzapine",
    )
    ranking = modeled["scenario"]["ranking"]
    assert ranking["original_decision"]["reason"] == "effect_replacement"
    assert modeled["scenario"]["factors"]["Parkinsonism"]["value"] is True
    assert len(ranking["distribution"]) == 22
    selected = next(row for row in ranking["distribution"] if row["drug_id"] == "oral:olanzapine")
    assert "current_main" in selected["removal_reasons"]
    assert modeled["selected_attribution"]["patient"] is None
    assert (
        modeled["selected_attribution"]["scenario"]["posterior_probability"]
        == selected["probability"]
    )
    assert any(
        edge["kind"] == "candidate_filter" and "current_main" in edge["target"]
        for edge in modeled["active_graph"]["edges"]
    )

    unresolved = _explore(
        clean_registry,
        proposal,
        overrides={
            "follow_up.effects.parkinsonism": True,
            "follow_up.history.conditions.hepatic_failure.manual": None,
        },
    )
    assert unresolved["scenario"]["status"] == "partial"
    assert unresolved["scenario"]["factors"]["Hepatic_Failure"]["value"] is None
    assert unresolved["scenario"]["ranking"] is None

    for overrides, terminal in (
        ({"follow_up.branches.dose_reduction_possible": True}, "reduce_dose"),
        ({"follow_up.effects.tardive_dyskinesia": True}, "adjunct_only"),
        (
            {
                "follow_up.considerations.recovery": "none_or_worse",
                "follow_up.branches.treatment_resistant": True,
            },
            "clozapine",
        ),
        (
            {
                "follow_up.considerations.recovery": "partial",
                "follow_up.considerations.adherence": False,
            },
            "same_drug_lai",
        ),
    ):
        side = _explore(clean_registry, proposal, overrides=overrides)["scenario"]
        assert side["ranking"]["original_decision"]["terminal"] == terminal
        assert side["ranking"]["distribution"] == []

    modeled_draft = saved.json()["draft_data"]
    modeled_draft["follow_up"]["effects"]["parkinsonism"] = True
    saved = follow_up_http._save(client, csrf, encounter_id, modeled_draft, revision + 1)
    assert saved.status_code == 200, saved.text
    modeled_proposal = _proposal(
        client, url, "follow-up", follow_up_http.headers(csrf, revision + 2)
    )
    modeled_baseline = _explore(clean_registry, modeled_proposal)
    _check_baseline(modeled_baseline, modeled_proposal)
    assert contracts.canonical_json(
        modeled_baseline["patient"]["state"]["model_factors"]
    ) == contracts.canonical_json(modeled_proposal["payload"]["model_factors"])
    assert len(modeled_baseline["patient"]["ranking"]["distribution"]) == 22
    departed = _explore(
        clean_registry,
        modeled_proposal,
        overrides={"follow_up.effects.parkinsonism": False},
    )
    assert departed["scenario"]["ranking"]["original_decision"]["terminal"] == "continue_same"
    assert departed["scenario"]["ranking"]["distribution"] == []
    assert departed["selected_attribution"]["patient"] is not None
    assert departed["selected_attribution"]["scenario"] is None
    assert any(
        edge["kind"] == "bn_dependency" and edge["scenario_state"] == "superseded"
        for edge in departed["active_graph"]["edges"]
    )


def test_cpt_dependence_survives_zero_local_attribution(monkeypatch):
    # The selected column depends on both parents, but A's coalition marginals
    # cancel with uniform priors and the observed row where both are False.
    monkeypatch.setattr(oral, "PARENTS", ("A", "B"))
    monkeypatch.setattr(oral, "OUTCOMES", ("Test", "Other"))
    monkeypatch.setattr(oral, "_TREATMENT_IDS", ("oral:test", "oral:other"))
    probabilities = (0.5, 0.75, 0.625, 0.375)
    table = memoryview(array("d", (p for value in probabilities for p in (value, 1 - value))))
    priors = ((0.5, 0.5), (0.5, 0.5))
    monkeypatch.setattr(
        oral,
        "_load_cached",
        lambda *_: oral._LoadedModel(
            table, priors, lambda row, target: oral._explain(table, priors, row, target)
        ),
    )
    attribution = oral._explain(table, priors, 0, "oral:test")
    assert dict(attribution.contributions)["A"] == 0
    assert oral.dependent_parents(b"fixture", "fixture", "oral:test") == ("A", "B")
