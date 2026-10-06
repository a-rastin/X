"""Controlled offline terminology. Only exact lowercase/whitespace normalization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

CONCEPT_TYPES = frozenset(
    {"ingredient", "combination_drug", "herbal", "food", "substance", "other"}
)


def normalize(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass(frozen=True)
class DrugConcept:
    id: str
    canonical_name: str
    concept_type: str
    catalog_drug_id: str | None
    source: str
    normalized_name: str = field(init=False)
    catalog_available: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_name", normalize(self.canonical_name))
        object.__setattr__(self, "catalog_available", self.catalog_drug_id is not None)


@dataclass(frozen=True)
class DrugAlias:
    name: str
    concept_id: str
    source: str
    review: dict[str, str]
    normalized_alias: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_alias", normalize(self.name))


class Terminology:
    def __init__(self, payload: Mapping[str, Any], checksum: str):
        _validate(payload)
        self.version = payload["version"]
        self.checksum = checksum
        self.catalog_status = payload.get("catalog_status", "unspecified")
        self.aliases = tuple(DrugAlias(**row) for row in payload["aliases"])
        self.concepts = tuple(DrugConcept(**row) for row in payload["concepts"])
        self.names: dict[str, set[str]] = {}
        for concept in self.concepts:
            self.names.setdefault(normalize(concept.canonical_name), set()).add(concept.id)

        self.pending_reviews = tuple(
            row for row in payload["aliases"] if row["review"]["decision"] == "pending"
        )
        for row in payload["aliases"]:
            if row["review"]["decision"] == "approved":
                self.names.setdefault(normalize(row["name"]), set()).add(row["concept_id"])
        review_names = {name: set(ids) for name, ids in self.names.items()}
        for row in payload["aliases"]:
            if row["review"]["decision"] != "rejected":
                review_names.setdefault(normalize(row["name"]), set()).add(row["concept_id"])
        self.collisions = {
            name: tuple(sorted(ids)) for name, ids in sorted(review_names.items()) if len(ids) > 1
        }

    def resolve(self, name: str) -> str | None:
        candidates = self.names.get(normalize(name), set())
        return next(iter(candidates)) if len(candidates) == 1 else None


def load_terminology(value: str | Path | Mapping[str, Any] | None) -> Terminology | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        payload = dict(value)
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    else:
        raw = Path(value).read_bytes()
        payload = json.loads(raw)
    return Terminology(payload, hashlib.sha256(raw).hexdigest())


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _validate(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) - {
        "version",
        "concepts",
        "aliases",
        "catalog_status",
    }:
        raise ValueError("invalid terminology object or unexpected fields")
    _text(payload.get("version"), "version")
    if "catalog_status" in payload:
        _text(payload["catalog_status"], "catalog_status")
    if not isinstance(payload.get("concepts"), list) or not isinstance(
        payload.get("aliases"), list
    ):
        raise ValueError("concepts and aliases must be lists")
    ids, catalog_ids = set(), set()
    for row in payload["concepts"]:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "canonical_name",
            "concept_type",
            "catalog_drug_id",
            "source",
        }:
            raise ValueError("invalid concept fields")
        for key in ("id", "canonical_name", "source", "concept_type"):
            _text(row[key], key)
        if row["id"] in ids or row["concept_type"] not in CONCEPT_TYPES:
            raise ValueError("duplicate concept ID or invalid concept type")
        ids.add(row["id"])
        catalog_id = row["catalog_drug_id"]
        if catalog_id is not None:
            _text(catalog_id, "catalog_drug_id")
            if (
                row["concept_type"] not in {"ingredient", "combination_drug"}
                or catalog_id in catalog_ids
            ):
                raise ValueError("catalog must contain unique drug-only identifiers")
            catalog_ids.add(catalog_id)
    for row in payload["aliases"]:
        if not isinstance(row, dict) or set(row) != {"name", "concept_id", "source", "review"}:
            raise ValueError("invalid alias fields")
        _text(row["name"], "alias name")
        _text(row["source"], "alias source")
        _text(row["concept_id"], "alias concept_id")
        if row["concept_id"] not in ids:
            raise ValueError("alias references unknown concept")
        review = row["review"]
        if not isinstance(review, dict):
            raise ValueError("alias review must be an object")
        _text(review.get("decision"), "alias review decision")
        if review.get("decision") not in {
            "approved",
            "pending",
            "rejected",
        }:
            raise ValueError("invalid alias review decision")
        if set(review) - {"decision", "reviewer", "date", "record"}:
            raise ValueError("unexpected alias review fields")
        if review["decision"] == "approved":
            for key in ("reviewer", "date", "record"):
                _text(review.get(key), f"approved review {key}")
            if review["reviewer"] != "owner":
                raise ValueError("approved aliases require owner review")
            date.fromisoformat(review["date"])
