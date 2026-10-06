"""S19 deterministic coverage-aware DDI checking (seam T4; plan.md §§6.1/6.3).

Public seam: :func:`check` / :class:`DDIChecker.check` —
``(medications, dataset_version) -> DDIReport`` per plan §6.3, with the
database engine injected (DI) as the first argument of the module function
or the constructor of the class. HTTP readers use the same public seam:
:func:`get_release_row` / :func:`list_concepts` /
:func:`latest_release_version` (plan §§3.2, 12.1: callers use the owning
module's public interface, never private helpers or foreign tables).

Coverage mechanism (minimal explicit, no new table, no parallel publish path):
- The S18 review manifest scope is the explicit coverage declaration.
- ``released_complete`` pins that every catalog pair was reviewed: a pair
  with evidence rows is ``interaction_found``; a catalog pair with no rows
  is ``covered_no_listed_interaction`` with basis
  ``reviewed_complete_coverage``.
- ``released_limited`` pins that excluded material is uncovered: a pair
  with evidence rows is still ``interaction_found``; a pair without rows is
  ``coverage_unavailable`` with basis ``limited_coverage_unavailable`` and
  the release ``limitations`` are copied into the report. Every resolved
  medication in a limited release is listed in
  ``coverage_unavailable_medications`` because limited scope cannot certify
  any drug's full coverage; zero/one-drug reports therefore retain the
  release limitations as uncovered-catalog warnings.
- Uncovered pairs are never "safe"/"no interaction"; absence is only claimed
  via ``covered_no_listed_interaction`` on an explicit complete basis. Never
  synthesized from missing rows.

Lookup is one batched indexed query (``(release_id, pair_key)`` uses
``ix_ddi_evidence_release_pair``) — no N+1 per-pair queries. No LLM, no
network, no fuzzy matcher, no salt/strip/split. Severity display priority
``contraindicated > serious > monitor_closely > minor > unknown`` is display
only; unknown stays visible via ``has_unknown_severity``.
"""

from __future__ import annotations

import re
from typing import Any

SEVERITY_ORDER = ("contraindicated", "serious", "monitor_closely", "minor", "unknown")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITY_ORDER)}
_VERSION_RE = re.compile(r"\Addi-[0-9a-f]{12}\Z")


class CheckError(Exception):
    """A checker refusal that already knows its safe HTTP rendering."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}


def _pair_key_for(first: str, second: str) -> str:
    low, high = sorted((first, second))
    return f"{low}|{high}"


def _rank(severity: Any) -> int | None:
    if not isinstance(severity, str):
        return None
    return SEVERITY_RANK.get(severity.lower())


def _sort_rank(value: Any) -> int:
    rank = _rank(value)
    return rank if rank is not None else 99


def _fetch_release_row(engine: Any, version: str) -> dict[str, Any]:
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT id, version, content_hash, status, reviewer, "
                        "parser_version, terminology_version, terminology_checksum, "
                        "limitations, manifest FROM ddi_dataset_releases "
                        "WHERE version = :version"
                    ),
                    {"version": version},
                )
                .mappings()
                .first()
            )
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    if row is None:
        raise CheckError(404, "DATASET_NOT_FOUND", "DDI dataset version not found.")
    return dict(row)


def _fetch_catalog(engine: Any, release_id: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT concept_id, canonical_name, concept_type, catalog_drug_id "
                        "FROM ddi_concepts WHERE release_id = :rid "
                        "AND catalog_drug_id IS NOT NULL ORDER BY catalog_drug_id ASC"
                    ),
                    {"rid": release_id},
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    return [dict(row) for row in rows]


def get_release_row(engine: Any, version: str) -> dict[str, Any]:
    """Public release reader (same interface ``check`` uses internally)."""
    return _fetch_release_row(engine, version)


def list_concepts(engine: Any, release_id: Any) -> list[dict[str, Any]]:
    """Public catalog reader for one release (display order, HTTP seam)."""
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT concept_id, canonical_name, concept_type, catalog_drug_id "
                        "FROM ddi_concepts WHERE release_id = :rid "
                        "AND catalog_drug_id IS NOT NULL ORDER BY canonical_name ASC"
                    ),
                    {"rid": release_id},
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    return [dict(row) for row in rows]


def latest_release_version(engine: Any) -> str | None:
    """Newest published release version (oldest-first, publish-compatible)."""
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text("SELECT version FROM ddi_dataset_releases ORDER BY created_at ASC")
                )
                .mappings()
                .all()
            )
    except Exception:
        return None
    if not rows:
        return None
    return str(rows[-1]["version"])


def _fetch_evidence_batch(
    engine: Any, release_id: Any, pair_keys: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """One indexed query for every pair key (batched, no N+1)."""
    from sqlalchemy import bindparam, text

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in pair_keys}
    if not pair_keys:
        return grouped
    try:
        with engine.connect() as connection:
            stmt = text(
                "SELECT source_path, checksum, source_severity, interacting_name, "
                "raw_text, span_start, span_end, subject_concept_id, "
                "interacting_concept_id, pair_key, direction, correction "
                "FROM ddi_interaction_evidence WHERE release_id = :rid "
                "AND pair_key IN :keys ORDER BY source_path ASC, span_start ASC"
            ).bindparams(bindparam("keys", expanding=True))
            rows = connection.execute(stmt, {"rid": release_id, "keys": pair_keys}).mappings().all()
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    for row in rows:
        record = dict(row)
        key = record.get("pair_key")
        if key in grouped:
            grouped[key].append(record)
    return grouped


def _evidence_entry(row: dict[str, Any]) -> dict[str, Any]:
    correction = row.get("correction")
    management: str | None = None
    if isinstance(correction, dict) and isinstance(correction.get("corrected_value"), str):
        management = correction["corrected_value"]
    return {
        "source_severity": row.get("source_severity"),
        "direction": row.get("direction", "unknown"),
        "raw_text": row.get("raw_text"),
        "management": management,
        "source_path": row.get("source_path"),
        "span_start": row.get("span_start"),
        "span_end": row.get("span_end"),
        "checksum": row.get("checksum"),
        "source_hash": row.get("checksum"),
        "subject_concept_id": row.get("subject_concept_id"),
        "interacting_concept_id": row.get("interacting_concept_id"),
    }


def _aggregate_pair(
    drug_a: str,
    drug_b: str,
    rows: list[dict[str, Any]],
    release_status: str,
) -> dict[str, Any]:
    evidence = [_evidence_entry(row) for row in rows]
    severities = [row.get("source_severity") for row in rows]
    known = [s for s in severities if _rank(s) is not None and str(s).lower() != "unknown"]
    has_unknown = (
        any(
            (not isinstance(s, str))
            or (str(s).lower() not in SEVERITY_RANK)
            or (str(s).lower() == "unknown")
            for s in severities
        )
        if severities
        else False
    )
    # Unknown severity stays visible; highest_known excludes it when known exists.
    highest: str | None = None
    if known:
        highest = str(sorted(set(known), key=_sort_rank)[0])
    elif severities:
        highest = "unknown"
    distinct = sorted({str(s) for s in severities}) if severities else []
    conflicts = distinct if len(distinct) > 1 else []
    if evidence:
        status = "interaction_found"
        basis: dict[str, Any] = {
            "basis": "reviewed_evidence",
            "release_status": release_status,
            "detail": f"{len(evidence)} reviewed assertion(s) for this pair",
        }
    elif release_status == "released_complete":
        status = "covered_no_listed_interaction"
        basis = {
            "basis": "reviewed_complete_coverage",
            "release_status": release_status,
            "detail": "explicit complete-coverage review with zero evidence rows for this pair",
        }
    else:
        status = "coverage_unavailable"
        basis = {
            "basis": "limited_coverage_unavailable",
            "release_status": release_status,
            "detail": "limited-coverage release cannot certify absence for this pair",
        }
    return {
        "drug_a": drug_a,
        "drug_b": drug_b,
        "status": status,
        "highest_known_severity": highest,
        "has_unknown_severity": has_unknown,
        "conflicts": conflicts,
        "evidence": evidence,
        "coverage_basis": basis,
    }


class DDIChecker:
    """Deterministic checker with the database engine injected (DI)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def check(self, medications: list[str], dataset_version: str) -> dict[str, Any]:
        return check(self._engine, medications, dataset_version)


def check(engine: Any, medications: list[str], dataset_version: str) -> dict[str, Any]:
    """Check one medication list against one pinned dataset release.

    ``medications`` is drug-only catalog IDs (no free text, no dose/route).
    Unresolved ingestion names are never accepted here — unknown catalog IDs
    raise :class:`CheckError` (422). Pairs are unique unordered concept pairs;
    duplicates collapse and never create self-pairs.
    """
    from x_insight.contracts import canonical_hash, serialize_utc, utcnow

    if not isinstance(dataset_version, str) or not _VERSION_RE.match(dataset_version):
        raise CheckError(
            422,
            "INVALID_DATASET_VERSION",
            "dataset_version must look like 'ddi-<12 hex characters>'.",
            {"dataset_version": ["Must look like 'ddi-<12 hex characters>'."]},
        )
    if not isinstance(medications, list):
        raise CheckError(
            422,
            "INVALID_MEDICATIONS",
            "medications must be a list of catalog drug IDs.",
            {"medications": ["Must be a list."]},
        )
    for entry in medications:
        if not isinstance(entry, str) or not entry.strip():
            raise CheckError(
                422,
                "INVALID_MEDICATION",
                "Each medication must be a catalog drug ID.",
                {"medications": ["Each entry must be a non-empty catalog_drug_id string."]},
            )

    release = _fetch_release_row(engine, dataset_version)
    release_id = release["id"]
    status = str(release.get("status", ""))
    if status not in ("released_complete", "released_limited"):
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.")
    catalog = _fetch_catalog(engine, release_id)
    by_catalog = {row["catalog_drug_id"]: row for row in catalog}
    for catalog_id in medications:
        if catalog_id not in by_catalog:
            raise CheckError(
                422,
                "INVALID_MEDICATION",
                f"Unknown catalog drug: {catalog_id}.",
                {"medications": [f"Unknown catalog_drug_id: {catalog_id}."]},
            )

    # Deduplicate equivalent concepts while preserving display provenance:
    # same catalog twice (or two catalogs mapping to one concept, if a reviewed
    # rule ever allowed it) collapses to one resolved medication.
    concept_by_id: dict[str, dict[str, Any]] = {}
    for catalog_id in medications:
        row = by_catalog[catalog_id]
        concept_id = str(row["concept_id"])
        if concept_id not in concept_by_id:
            concept_by_id[concept_id] = row
    resolved = sorted(concept_by_id.values(), key=lambda r: str(r["catalog_drug_id"]))
    resolved_medications = [
        {
            "catalog_drug_id": row["catalog_drug_id"],
            "concept_id": row["concept_id"],
            "canonical_name": row["canonical_name"],
            "concept_type": row["concept_type"],
        }
        for row in resolved
    ]

    concept_ids = sorted(concept_by_id)
    fingerprint = canonical_hash({"medications": concept_ids, "dataset_version": dataset_version})

    # Unique unordered pairs, canonical (drug_a, drug_b) sorted by catalog ID.
    catalog_by_concept = {str(r["concept_id"]): str(r["catalog_drug_id"]) for r in resolved}
    pairs_keys: list[tuple[str, str, str]] = []
    for index in range(len(concept_ids)):
        for other in range(index + 1, len(concept_ids)):
            first_concept = concept_ids[index]
            second_concept = concept_ids[other]
            first_catalog = catalog_by_concept[first_concept]
            second_catalog = catalog_by_concept[second_concept]
            drug_a, drug_b = sorted((first_catalog, second_catalog))
            # Pair lookup key is canonical over concept IDs (matches publish).
            pair_key = _pair_key_for(first_concept, second_concept)
            pairs_keys.append((drug_a, drug_b, pair_key))
    pairs_keys.sort()
    evidence_by_key = _fetch_evidence_batch(engine, release_id, [key for _, _, key in pairs_keys])

    pairs = [
        _aggregate_pair(drug_a, drug_b, evidence_by_key[pair_key], status)
        for drug_a, drug_b, pair_key in pairs_keys
    ]

    if status == "released_limited":
        coverage_unavailable_medications = list(resolved_medications)
    else:
        coverage_unavailable_medications = []

    limitations = release.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    # Zero/one-drug reports keep the release limitations as the warning channel.
    terminology_version = release.get("terminology_version") or ""
    return {
        "dataset_version": dataset_version,
        "catalog_version": terminology_version,
        "medication_fingerprint": fingerprint,
        "resolved_medications": resolved_medications,
        "coverage_unavailable_medications": coverage_unavailable_medications,
        "pairs": pairs,
        "limitations": list(limitations),
        "generated_at": serialize_utc(utcnow()),
    }
