"""Calculate candidate rankings and their interactions for a new-case proposal."""

from dataclasses import asdict
from typing import Any

from x_insight.ddi import checker
from x_insight.models import oral_antipsychotic


def calculate_candidates(
    engine: Any,
    *,
    lai: bool,
    evidence: dict[str, bool],
    model_bytes: bytes | None,
    model_sha256: str,
    formulations: list[dict[str, Any]],
    unavailable: frozenset[str],
    medications: list[str],
    dataset_version: str,
) -> dict[str, Any]:
    if lai:
        return {
            "status": "model_unavailable",
            "distribution": [],
            "candidates": [],
            "interactions": None,
        }
    if model_bytes is None:
        raise ValueError("The pinned oral model is unavailable")

    ranking = oral_antipsychotic.rank_oral(model_bytes, model_sha256, evidence, unavailable)
    catalog = {
        entry["id"]: entry["catalog_drug_id"] for entry in formulations if entry["group"] == "oral"
    }
    candidate_ids = [catalog[candidate.drug_id] for candidate in ranking.candidates]
    interactions = checker.check_candidates(engine, candidate_ids, medications, dataset_version)
    return {
        "status": "available",
        "distribution": [asdict(candidate) for candidate in ranking.distribution],
        "candidates": [asdict(candidate) for candidate in ranking.candidates],
        "interactions": interactions,
    }
