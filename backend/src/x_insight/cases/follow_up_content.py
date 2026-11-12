"""Offline, hash-checked content for the follow-up workflow."""

import hashlib
import json
from typing import Any

from x_insight import contracts
from x_insight.ddi.lexicomp import content_directory
from x_insight.models.oral_antipsychotic import PARENTS

FILES = frozenset(
    {
        "follow-up/v1/factors.json",
        "follow-up/v1/lai-equivalences.json",
        "follow-up/v1/rules.json",
        "new-case/v1/labs.json",
        "new-case/v1/antipsychotic-formulations.json",
        "new-case/v1/oral-antipsychotic.xml",
        "new-case/v1/oral-model-manifest.json",
        "ddi/lexicomp/v1/manifest.json",
        "ddi/lexicomp/v1/Lexicomp-cleaned.csv",
    }
)


def load_content() -> dict[str, Any]:
    root = content_directory()
    manifest = json.loads((root / "follow-up/v1/manifest.json").read_bytes())
    if manifest.get("workflow_version") != "follow-up-v1" or set(manifest["files"]) != FILES:
        raise ValueError("Invalid follow-up manifest")
    assets = {}
    for path, expected in manifest["files"].items():
        raw = (root / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Follow-up content checksum mismatch: {path}")
        if path.endswith(".json"):
            assets[path] = json.loads(raw)
    factors = assets["follow-up/v1/factors.json"]
    rules = assets["follow-up/v1/rules.json"]
    equivalents = assets["follow-up/v1/lai-equivalences.json"]
    formulations = assets["new-case/v1/antipsychotic-formulations.json"]
    model = assets["new-case/v1/oral-model-manifest.json"]
    lexicomp = assets["ddi/lexicomp/v1/manifest.json"]
    inventory = {item["id"]: item for item in formulations["entries"]}
    if (
        factors["version"] != manifest["factor_mapping_version"]
        or rules["version"] != manifest["recommendation_rule_version"]
        or list(factors["sources"]) != list(PARENTS)
        or not all(isinstance(source, str) and source for source in factors["sources"].values())
        or equivalents["version"] != "follow-up-lai-equivalences-v1"
        or set(rules["graph_terminals"])
        != {"n1", "n6", "n7", "n6_2", "n6_3", "n6_3_2", "n18", "n22", "n24"}
        or rules["graph_terminals"]["n1"] != "physician_review_current_lai"
        or not rules["priority"]
        or not rules["independent_recommendations"]
        or model["sha256"] != manifest["files"]["new-case/v1/oral-antipsychotic.xml"]
        or lexicomp["sha256"] != manifest["files"]["ddi/lexicomp/v1/Lexicomp-cleaned.csv"]
    ):
        raise ValueError("Invalid follow-up content structure")
    seen = set()
    for pair in equivalents["equivalences"]:
        oral, lai = pair["oral"], pair["lai"]
        if (
            oral in seen
            or oral not in inventory
            or lai not in inventory
            or inventory[oral]["group"] != "oral"
            or inventory[lai]["group"] != "lai"
            or oral.removeprefix("oral:") != lai.removeprefix("lai:")
        ):
            raise ValueError("Invalid follow-up LAI equivalence")
        seen.add(oral)
    supported = {
        key
        for key in inventory
        if key.startswith("oral:") and "lai:" + key.removeprefix("oral:") in inventory
    }
    if seen != supported:
        raise ValueError("Incomplete follow-up LAI equivalences")
    return {"manifest": manifest, "hash": contracts.canonical_hash(manifest), "assets": assets}
