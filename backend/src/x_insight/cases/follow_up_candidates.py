"""Apply follow-up availability and interaction policy to the original decision."""

from dataclasses import asdict
from typing import Any

from x_insight.cases import follow_up_rules
from x_insight.ddi import checker
from x_insight.models import oral_antipsychotic


def calculate_candidates(
    engine: Any,
    *,
    evaluated: dict[str, Any],
    decision: dict[str, Any],
    sex: str,
    model_bytes: bytes | None,
    model_sha256: str,
    formulations: list[dict[str, Any]],
    unavailable: frozenset[str],
    dataset_version: str,
) -> dict[str, Any]:
    """Return original probabilities, all exclusions, and the resulting decision.

    Lookup and model errors propagate: infrastructure failure is not a review outcome.
    """
    state = evaluated["raw"]
    medications = [entry["catalog_drug_id"] for entry in state["medications"]["entries"]]
    main = state["follow_up"]["treatment"]["main_catalog_drug_id"]
    current = checker.check(engine, medications, dataset_version)
    entries = {entry["id"]: entry for entry in formulations}
    terminal = decision["terminal"]
    if terminal not in {
        "oral_replacement",
        "clozapine",
        "same_drug_lai",
        "physician_review",
        "continue_same",
        "reduce_dose",
        "adjunct_only",
    }:
        raise ValueError("Incomplete or unsupported follow-up decision")
    distribution: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    interactions = None
    direct = None
    coverage: Any
    status = "non_model"
    if terminal == "oral_replacement":
        if model_bytes is None:
            raise ValueError("The pinned oral model is unavailable")
        factors = follow_up_rules.model_factors(evaluated)
        evidence = {name: factor["value"] for name, factor in factors.items()}
        ranking = oral_antipsychotic.rank_oral(model_bytes, model_sha256, evidence)
        distribution = [asdict(candidate) for candidate in ranking.distribution]
        for item in distribution:
            entry = entries.get(item["drug_id"])
            if not entry or entry.get("group") != "oral" or not entry.get("catalog_drug_id"):
                raise ValueError("Oral model formulation mapping is unavailable")
            item["catalog_drug_id"] = entry["catalog_drug_id"]
        eligible = [item for item in distribution if item["probability"] > 0]
        interactions = checker.check_candidates(
            engine, [item["catalog_drug_id"] for item in eligible], medications, dataset_version
        )
        for item in distribution:
            catalog_id = item["catalog_drug_id"]
            pairs = [p for p in interactions["pairs"] if catalog_id in (p["drug_a"], p["drug_b"])]
            offending = [p for p in pairs if p.get("risk_rating") in ("D", "X")]
            reasons = []
            if item["probability"] == 0:
                reasons.append("zero_probability")
            if item["drug_id"] in unavailable:
                reasons.append("unavailable")
            if catalog_id == main:
                reasons.append("current_main")
            if offending:
                reasons.append("interaction_dx")
            item.update(
                removed_reason=reasons[0] if reasons else None,
                removal_reasons=reasons,
                offending_pairs=offending,
                coverage_unavailable=[p for p in pairs if p["status"] == "coverage_unavailable"],
            )
        selected = [item for item in distribution if not item["removal_reasons"]][:5]
        status = "available" if selected else "no_eligible_oral_candidate"
    elif terminal in ("clozapine", "same_drug_lai"):
        formulation_id = (
            "oral:clozapine" if terminal == "clozapine" else decision.get("lai_equivalent")
        )
        entry = entries.get(formulation_id)
        if not entry:
            status = "unsupported_direct_mapping"
            direct = {
                "drug_id": formulation_id,
                "removal_reasons": [status],
                "coverage_unavailable": True,
            }
        else:
            catalog_id = entry.get("catalog_drug_id")
            reasons = ["unavailable"] if formulation_id in unavailable else []
            if catalog_id:
                interactions = checker.check_candidates(
                    engine, [catalog_id], medications, dataset_version
                )
                offending = [p for p in interactions["pairs"] if p.get("risk_rating") in ("D", "X")]
                if offending:
                    reasons.append("interaction_dx")
                coverage = [
                    p for p in interactions["pairs"] if p["status"] == "coverage_unavailable"
                ]
            else:
                offending, coverage = [], True
            direct = {
                "drug_id": formulation_id,
                "catalog_drug_id": catalog_id,
                "removal_reasons": reasons,
                "offending_pairs": offending,
                "coverage_unavailable": coverage,
            }
            status = "direct_excluded" if reasons else "direct_available"
    final = dict(decision)
    if status in ("no_eligible_oral_candidate", "unsupported_direct_mapping", "direct_excluded"):
        final.update(terminal="physician_review", reason=status)
    return {
        "status": status,
        "original_decision": decision,
        "final_decision": final,
        "distribution": distribution,
        "candidates": selected,
        "direct": direct,
        "current_interactions": current,
        "candidate_interactions": interactions,
        "sections": follow_up_rules.sections(evaluated, sex, final),
    }
