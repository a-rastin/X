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

import os
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


def _fetch_catalog(
    engine: Any, release_id: Any, catalog_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    from sqlalchemy import bindparam, text

    if catalog_ids == []:
        return []
    try:
        with engine.connect() as connection:
            sql = (
                "SELECT concept_id, canonical_name, concept_type, catalog_drug_id "
                "FROM ddi_concepts WHERE release_id = :rid AND catalog_drug_id IS NOT NULL "
            )
            params: dict[str, Any] = {"rid": release_id}
            if catalog_ids is not None:
                sql += "AND catalog_drug_id IN :ids "
                params["ids"] = sorted(set(catalog_ids))
            statement = text(sql + "ORDER BY catalog_drug_id ASC")
            if catalog_ids is not None:
                statement = statement.bindparams(bindparam("ids", expanding=True))
            rows = connection.execute(statement, params).mappings().all()
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    return [dict(row) for row in rows]


def get_release_row(engine: Any, version: str) -> dict[str, Any]:
    """Public release reader (same interface ``check`` uses internally)."""
    return _fetch_release_row(engine, version)


def validate_catalog_selection(engine: Any, catalog_ids: list[str], version: str) -> None:
    """Validate a resumable selection association without generating a DDI report."""
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise CheckError(422, "INVALID_DATASET_VERSION", "Invalid selection dataset version.")
    release = get_release_row(engine, version)
    if release.get("status") not in ("released_complete", "released_limited"):
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.")
    found = {row["catalog_drug_id"] for row in _fetch_catalog(engine, release["id"], catalog_ids)}
    missing = sorted(set(catalog_ids) - found)
    if missing:
        raise CheckError(
            422,
            "INVALID_MEDICATION",
            f"Unknown catalog drug: {missing[0]}.",
            {"medications": [f"Unknown catalog_drug_id: {missing[0]}."]},
        )


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


def search_concepts(
    engine: Any, release_id: Any, query: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Filter, count and page the immutable catalog entirely in PostgreSQL."""
    from sqlalchemy import text

    needle = " ".join((query or "").lower().split())
    where = (
        "release_id=:rid AND catalog_drug_id IS NOT NULL AND "
        "(:needle='' OR lower(catalog_drug_id)=:needle OR ("
        "NOT EXISTS (SELECT 1 FROM ddi_concepts exact WHERE exact.release_id=:rid "
        "AND lower(exact.catalog_drug_id)=:needle) AND ("
        "strpos(lower(canonical_name),:needle)>0 OR strpos(lower(catalog_drug_id),:needle)>0)))"
    )
    params = {"rid": release_id, "needle": needle, "limit": limit, "offset": offset}
    try:
        with engine.connect() as connection:
            total = connection.execute(
                text("SELECT count(*) FROM ddi_concepts WHERE " + where), params
            ).scalar()
            rows = (
                connection.execute(
                    text(
                        "SELECT concept_id,canonical_name,concept_type,"
                        "catalog_drug_id FROM ddi_concepts WHERE "
                        + where
                        + " ORDER BY canonical_name ASC,catalog_drug_id ASC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI catalog is unavailable.") from exc
    return [dict(row) for row in rows], int(total or 0)


def source_details(release: dict[str, Any]) -> dict[str, Any]:
    manifest = release.get("manifest") or {}
    if manifest.get("source_profile") == "ddinter-positive-assertions/1":
        return {
            "source_label": "DDInter supplied CSV exports",
            "source_profile": manifest["source_profile"],
            "detail_availability": "severity_only",
        }
    return {"source_label": "Medication monograph catalog"}


def latest_release_version(engine: Any) -> str | None:
    """Configured immutable default, or newest release with deterministic ties."""
    from sqlalchemy import text

    configured = os.environ.get("DDI_DEFAULT_DATASET_VERSION")
    if configured:
        try:
            get_release_row(engine, configured)
        except CheckError as exc:
            raise CheckError(
                503, "INVALID_DEFAULT_DATASET", "Configured default DDI release is unavailable."
            ) from exc
        return configured
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT version FROM ddi_dataset_releases ORDER BY created_at "
                    "DESC,version DESC LIMIT 1"
                )
            ).scalar()
    except Exception as exc:
        raise CheckError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.") from exc
    return str(value) if value else None


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
                "interacting_concept_id, pair_key, direction, correction, source_record_metadata "
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
        **(row.get("source_record_metadata") or {}),
    }


def _aggregate_pair(
    drug_a: str,
    drug_b: str,
    rows: list[dict[str, Any]],
    release_status: str,
    source_profile: str | None = None,
) -> dict[str, Any]:
    evidence = [_evidence_entry(row) for row in rows]
    severities = [row.get("source_severity") for row in rows]
    order = (
        ("Major", "Moderate", "Minor", "Unknown")
        if source_profile == "ddinter-positive-assertions/1"
        else SEVERITY_ORDER
    )
    ranks = {value.lower(): index for index, value in enumerate(order)}
    known = [
        value
        for value in severities
        if isinstance(value, str) and value.lower() in ranks and value.lower() != "unknown"
    ]
    has_unknown = any(
        not isinstance(value, str) or value.lower() not in ranks or value.lower() == "unknown"
        for value in severities
    )
    highest: str | None = None
    if known:
        highest = min(set(known), key=lambda value: ranks[value.lower()])
    elif severities:
        highest = "Unknown" if source_profile == "ddinter-positive-assertions/1" else "unknown"
    distinct = sorted({str(s) for s in severities}) if severities else []
    conflicts = distinct if len(distinct) > 1 else []
    if evidence:
        status = "interaction_found"
        basis: dict[str, Any] = {
            "basis": "reviewed_evidence",
            "release_status": release_status,
            "detail": (
                f"{len(evidence)} source occurrence(s); "
                "repeated exports do not establish independent corroboration"
                if source_profile == "ddinter-positive-assertions/1"
                else f"{len(evidence)} reviewed assertion(s) for this pair"
            ),
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
    catalog = _fetch_catalog(engine, release_id, medications)
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
        _aggregate_pair(
            drug_a,
            drug_b,
            evidence_by_key[pair_key],
            status,
            source_details(release).get("source_profile"),
        )
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
        **source_details(release),
    }
