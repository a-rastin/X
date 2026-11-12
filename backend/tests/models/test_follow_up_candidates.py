import json
from pathlib import Path

import pytest

from x_insight.cases import follow_up_candidates as subject
from x_insight.models import oral_antipsychotic as oral

ROOT = Path(__file__).resolve().parents[3]
CONTENT = ROOT / "content/new-case/v1"
SHA = "f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a"


def args():
    return dict(
        engine=None,
        evaluated={
            "raw": {
                "medications": {"entries": [{"catalog_drug_id": "current"}]},
                "follow_up": {"treatment": {"main_catalog_drug_id": "current"}},
            }
        },
        decision={"terminal": "oral_replacement", "reason": "test"},
        sex="M",
        model_bytes=(CONTENT / "oral-antipsychotic.xml").read_bytes(),
        model_sha256=SHA,
        formulations=json.loads((CONTENT / "antipsychotic-formulations.json").read_text())[
            "entries"
        ],
        unavailable=frozenset(),
        dataset_version="ddi-2adbb775258d",
    )


def test_full_distribution_filters_backfills_and_preserves_coverage(monkeypatch):
    options = args()
    monkeypatch.setattr(
        subject.follow_up_rules,
        "model_factors",
        lambda _: {name: {"value": False} for name in oral.PARENTS},
    )
    monkeypatch.setattr(subject.follow_up_rules, "sections", lambda e, s, d: d["terminal"])
    ranking = oral.rank_oral(options["model_bytes"], SHA, dict.fromkeys(oral.PARENTS, False))
    ids = {
        e["id"]: e["catalog_drug_id"] for e in options["formulations"] if e.get("catalog_drug_id")
    }
    excluded = {ids[c.drug_id] for c in ranking.distribution[:5]}
    calls = []
    monkeypatch.setattr(subject.checker, "check", lambda e, m, v: {"pairs": [], "medications": m})

    def candidate_check(engine, candidates, medications, version):
        calls.append((candidates, medications, version))
        return {
            "pairs": [
                dict(
                    drug_a=c,
                    drug_b="current",
                    risk_rating="D" if c in excluded else None,
                    status="interaction_found" if c in excluded else "coverage_unavailable",
                )
                for c in candidates
            ]
        }

    monkeypatch.setattr(subject.checker, "check_candidates", candidate_check)
    result = subject.calculate_candidates(**options)
    assert len(result["distribution"]) == 22
    assert result["candidates"][0]["rank"] == 6
    assert result["candidates"][0]["probability"] == ranking.distribution[5].probability
    assert result["distribution"][0]["offending_pairs"]
    assert result["candidates"][0]["coverage_unavailable"]
    assert calls[0][1] == ["current"]
    options["unavailable"] = frozenset(c.drug_id for c in ranking.distribution)
    result = subject.calculate_candidates(**options)
    assert result["status"] == "no_eligible_oral_candidate"
    assert result["sections"] == "physician_review"


def test_direct_exclusions_and_model_failure(monkeypatch):
    options = args()
    monkeypatch.setattr(subject.follow_up_rules, "sections", lambda e, s, d: d["terminal"])
    monkeypatch.setattr(subject.checker, "check", lambda *a: {"pairs": []})
    monkeypatch.setattr(
        subject.checker,
        "check_candidates",
        lambda *a: {
            "pairs": [
                {
                    "drug_a": "clozapine",
                    "drug_b": "current",
                    "risk_rating": "X",
                    "status": "interaction_found",
                }
            ]
        },
    )
    options["formulations"] = [
        {"id": "oral:clozapine", "catalog_drug_id": "clozapine", "group": "oral"}
    ]
    options["decision"] = {"terminal": "clozapine", "reason": "test"}
    options["model_bytes"] = None
    result = subject.calculate_candidates(**options)
    assert result["status"] == "direct_excluded"
    assert result["direct"]["offending_pairs"]
    assert result["final_decision"]["terminal"] == "physician_review"
    options["decision"] = {"terminal": "oral_replacement", "reason": "test"}
    with pytest.raises(ValueError, match="model"):
        subject.calculate_candidates(**options)


def test_main_exclusion_direct_availability_and_invalid_model(monkeypatch):
    options = args()
    monkeypatch.setattr(
        subject.follow_up_rules,
        "model_factors",
        lambda _: {name: {"value": False} for name in oral.PARENTS},
    )
    monkeypatch.setattr(subject.follow_up_rules, "sections", lambda e, s, d: d["terminal"])
    monkeypatch.setattr(subject.checker, "check", lambda *a: {"pairs": []})
    monkeypatch.setattr(subject.checker, "check_candidates", lambda *a: {"pairs": []})
    ranked = oral.rank_oral(options["model_bytes"], SHA, dict.fromkeys(oral.PARENTS, False))
    first = next(e for e in options["formulations"] if e["id"] == ranked.distribution[0].drug_id)
    options["evaluated"]["raw"]["follow_up"]["treatment"]["main_catalog_drug_id"] = first[
        "catalog_drug_id"
    ]
    result = subject.calculate_candidates(**options)
    assert "current_main" in result["distribution"][0]["removal_reasons"]
    assert result["candidates"][0]["rank"] == 2
    options["model_bytes"] = b"corrupt"
    with pytest.raises(ValueError):
        subject.calculate_candidates(**options)
    options["decision"] = {
        "terminal": "same_drug_lai",
        "reason": "test",
        "lai_equivalent": "lai:test",
    }
    options["formulations"] = [{"id": "lai:test", "group": "lai"}]
    options["unavailable"] = frozenset({"lai:test"})
    result = subject.calculate_candidates(**options)
    assert result["status"] == "direct_excluded"
    assert result["direct"]["coverage_unavailable"] is True
    options["formulations"] = []
    assert subject.calculate_candidates(**options)["status"] == "unsupported_direct_mapping"
