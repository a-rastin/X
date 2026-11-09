import json
from pathlib import Path

import pytest

from x_insight.cases.new_case_candidates import calculate_candidates
from x_insight.models import oral_antipsychotic as oral

ROOT = Path(__file__).resolve().parents[3]
CONTENT = ROOT / "content/new-case/v1"
SHA = "f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a"


def arguments():
    return {
        "engine": None,
        "lai": False,
        "evidence": dict.fromkeys(oral.PARENTS, False),
        "model_bytes": (CONTENT / "oral-antipsychotic.xml").read_bytes(),
        "model_sha256": SHA,
        "formulations": json.loads((CONTENT / "antipsychotic-formulations.json").read_text())[
            "entries"
        ],
        "unavailable": frozenset({"oral:chlorpromazine"}),
        "medications": ["current-medication"],
        "dataset_version": "ddi-2adbb775258d",
    }


def test_ranking_uses_formulation_ids_and_checks_only_selected_candidates(monkeypatch):
    calls = []

    def check(engine, candidates, medications, version):
        calls.append((candidates, medications, version))
        return {"pairs": []}

    monkeypatch.setattr("x_insight.cases.new_case_candidates.checker.check_candidates", check)
    options = arguments()
    result = calculate_candidates(**options)
    assert len(result["distribution"]) == 22
    assert result["distribution"][0]["removed_reason"] == "unavailable"
    assert [item["name"] for item in result["candidates"]] == [
        "Fluphenazine",
        "Haloperidol",
        "Loxapine",
        "Molindone",
        "Perphenazine",
    ]
    catalog = {
        entry["id"]: entry["catalog_drug_id"]
        for entry in options["formulations"]
        if entry["group"] == "oral"
    }
    assert calls == [
        (
            [catalog[item["drug_id"]] for item in result["candidates"]],
            ["current-medication"],
            "ddi-2adbb775258d",
        )
    ]


def test_lai_does_not_load_model_or_check_interactions(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("LAI must not invoke the oral model or interaction checker")

    monkeypatch.setattr(oral, "rank_oral", fail)
    monkeypatch.setattr("x_insight.cases.new_case_candidates.checker.check_candidates", fail)
    options = arguments()
    options.update(lai=True, model_bytes=None)
    assert calculate_candidates(**options)["status"] == "model_unavailable"


def test_missing_or_corrupt_oral_model_is_not_a_supported_empty_result():
    options = arguments()
    options["model_bytes"] = None
    with pytest.raises(ValueError):
        calculate_candidates(**options)
    options["model_bytes"] = b"corrupt"
    with pytest.raises(ValueError):
        calculate_candidates(**options)
