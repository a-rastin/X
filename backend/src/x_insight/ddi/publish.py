"""S18 immutable DDI release publication (seam T3; plan.md §§6.1-6.2, FR-14/NFR-05).

Public seam: :func:`publish_release` (staging dir + reviewed manifest -> DB
release) plus :func:`build_corpus_report`, :func:`list_releases` and
:func:`get_release`. The CLI (``python -m x_insight.ddi publish``) only routes
to :func:`publish_release`; all gates live here.

Gates (every violation raises :class:`PublishRejected`, never a partial import):
- staging holds ``candidate_dataset.json`` + ``report.json`` from ``build``;
  missing files, bad JSON, or missing provenance (parser/terminology
  versions, per-document checksums) are rejected;
- manifest must be owner-reviewed (``reviewer == "owner"``) with an explicit
  ``approved_complete``/``approved_limited`` decision — ``awaiting_review``
  (the default for the real corpus: 0 approved aliases + 26 structural
  failures) is never imported;
- ``approved_complete`` requires every document passed, no unresolved names,
  and no excluded sources; ``approved_limited`` requires every *included*
  document passed with excluded sources named with reasons + limitations;
- any unresolved name needed by the release (included source) is rejected;
- every contraindicated/serious assertion in the included set needs a
  matching ``reviewed_evidence`` record (source path + span); collisions
  touching included names are rejected;
- corrections are overlays referencing source spans (original columns stay
  unchanged); each must match a real entry span.

Import is one transaction (staging validation + DB insert): the content hash
(``contracts.canonical_hash`` over dataset + manifest core) anchors
idempotency — same hash returns the same version with no duplicate rows; a
changed source yields a new hash/version; any failure rolls back so the prior
released dataset stays readable. Duplicate-direction evidence stays separate
rows; ``source_severity`` keeps the monograph category verbatim while
management prose stays in ``raw_text`` (never inferred).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ponytail: single module owns staging validation + import transaction as one
# coherent operation (S18 step 1); split a repository layer out when S19 readers
# need reuse beyond get_release/get_evidence.
APPROVED_DECISIONS = ("approved_complete", "approved_limited")
STATUS_BY_DECISION = {
    "approved_complete": "released_complete",
    "approved_limited": "released_limited",
}
HIGH_RISK = ("contraindicated", "serious")


class PublishRejected(Exception):
    """A publish gate refused the release (message names the reason)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PublishRejected("absent_provenance", f"{label} not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishRejected("absent_provenance", f"{label} unreadable: {exc}") from None


def _load_staging(staging_dir: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(staging_dir)
    if not root.is_dir():
        raise PublishRejected("absent_provenance", f"staging is not a directory: {staging_dir}")
    dataset = _load_json(root / "candidate_dataset.json", "candidate_dataset.json")
    report = _load_json(root / "report.json", "report.json")
    if not isinstance(dataset, dict) or not isinstance(report, dict):
        raise PublishRejected("absent_provenance", "staging files must hold JSON objects")
    for key in ("parser_version", "documents"):
        if key not in dataset or key not in report:
            raise PublishRejected("absent_provenance", f"staging is missing '{key}'")
    return dataset, report


def _load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    manifest = _load_json(Path(manifest_path), "review manifest")
    if not isinstance(manifest, dict):
        raise PublishRejected("absent_provenance", "review manifest must hold a JSON object")
    return manifest


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    if manifest.get("reviewer") != "owner":
        raise PublishRejected("absent_provenance", "manifest reviewer must be 'owner'")
    decision = manifest.get("decision")
    if decision not in APPROVED_DECISIONS:
        raise PublishRejected(
            "awaiting_review" if decision == "awaiting_review" else "unreviewed_evidence",
            f"manifest decision '{decision}' is not an explicit owner approval",
        )
    try:
        datetime.fromisoformat(str(manifest.get("date", "")))
    except ValueError:
        raise PublishRejected("absent_provenance", "manifest date must be ISO-8601") from None
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("scope") not in ("complete", "limited"):
        raise PublishRejected(
            "absent_provenance", "manifest coverage.scope must be complete|limited"
        )
    expected_scope = "complete" if decision == "approved_complete" else "limited"
    if coverage["scope"] != expected_scope:
        raise PublishRejected(
            "unreviewed_evidence",
            f"decision {decision} requires coverage scope '{expected_scope}'",
        )
    for key in ("corrections", "reviewed_evidence", "limitations"):
        if not isinstance(manifest.get(key), list):
            raise PublishRejected("absent_provenance", f"manifest '{key}' must be a list")


def validate_for_publish(
    dataset: dict[str, Any], report: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Run every publish gate; return the included-source set on success."""
    _validate_manifest_shape(manifest)
    if dataset.get("parser_version") != manifest.get("parser_version"):
        raise PublishRejected("absent_provenance", "manifest parser_version must match the dataset")
    if dataset.get("terminology_version") != manifest.get("terminology_version"):
        raise PublishRejected(
            "absent_provenance", "manifest terminology_version must match the dataset"
        )
    if (dataset.get("terminology_checksum") or None) != (
        manifest.get("terminology_checksum") or None
    ):
        # Both absent (no terminology) is provenance-complete; any mismatch is not.
        if dataset.get("terminology_checksum") or manifest.get("terminology_checksum"):
            raise PublishRejected(
                "absent_provenance", "manifest terminology_checksum must match the dataset"
            )
    coverage = manifest["coverage"]
    excluded = coverage.get("excluded_sources", [])
    if not isinstance(excluded, list):
        raise PublishRejected("absent_provenance", "coverage.excluded_sources must be a list")
    excluded_paths = set()
    for row in excluded:
        if not isinstance(row, dict) or not row.get("path") or not row.get("reason"):
            raise PublishRejected(
                "absent_provenance", "each excluded source needs 'path' + 'reason'"
            )
        excluded_paths.add(row["path"])
    if coverage["scope"] == "complete" and excluded_paths:
        raise PublishRejected("unreviewed_evidence", "a complete release excludes nothing")
    if coverage["scope"] == "limited":
        if not excluded_paths:
            raise PublishRejected(
                "unreviewed_evidence", "a limited release must name its excluded sources"
            )
        if not manifest.get("limitations"):
            raise PublishRejected(
                "unreviewed_evidence", "a limited release must list its limitations"
            )

    checks_by_path = {}
    for check in report.get("documents", []):
        if not isinstance(check, dict) or not check.get("source_path") or not check.get("checksum"):
            raise PublishRejected("absent_provenance", "report documents need path + checksum")
        checks_by_path[check["source_path"]] = check
    docs_by_path = {}
    for doc in dataset.get("documents", []):
        if not isinstance(doc, dict) or not doc.get("source_path") or not doc.get("checksum"):
            raise PublishRejected("absent_provenance", "dataset documents need path + checksum")
        docs_by_path[doc["source_path"]] = doc
    if set(checks_by_path) != set(docs_by_path):
        raise PublishRejected("absent_provenance", "dataset/report document inventory must agree")

    included = sorted(set(docs_by_path) - excluded_paths)
    if not included:
        raise PublishRejected("unreviewed_evidence", "release includes no sources")
    for path in included:
        check = checks_by_path[path]
        if not check.get("passed", False):
            anomalies = check.get("anomalies", [])
            raise PublishRejected(
                "count_failure",
                f"{path} failed release eligibility: {anomalies[0] if anomalies else 'failed'}",
            )
    for row in report.get("unresolved_names", []):
        if isinstance(row, dict) and row.get("source_path") in included:
            raise PublishRejected(
                "unresolved_entities", f"unresolved name needed by release: {row.get('name')}"
            )
    collisions = report.get("alias_collisions", {}) or {}
    if collisions:
        included_names = set()
        for path in included:
            for entry in docs_by_path[path].get("entries", []):
                name = entry.get("interacting_name") if isinstance(entry, dict) else None
                if name:
                    included_names.add(" ".join(str(name).lower().split()))
        clashing = sorted(set(collisions) & included_names)
        if clashing:
            raise PublishRejected(
                "unreviewed_evidence", f"alias collisions needed by release: {clashing[0]}"
            )
    reviewed = set()
    for row in manifest.get("reviewed_evidence", []):
        if isinstance(row, dict):
            reviewed.add((row.get("source_path"), row.get("span_start"), row.get("span_end")))
    for path in included:
        for entry in docs_by_path[path].get("entries", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("source_category") in HIGH_RISK:
                key = (path, entry.get("span_start"), entry.get("span_end"))
                if key not in reviewed:
                    raise PublishRejected(
                        "unreviewed_evidence",
                        f"unreviewed {entry.get('source_category')} evidence: "
                        f"{path}:{entry.get('span_start')}-{entry.get('span_end')}",
                    )
    for correction in manifest.get("corrections", []):
        if not isinstance(correction, dict):
            raise PublishRejected("absent_provenance", "correction records must be objects")
        path = correction.get("source_path")
        if path not in docs_by_path:
            raise PublishRejected("absent_provenance", "correction references an unknown source")
        spans = {
            (e.get("span_start"), e.get("span_end"))
            for e in docs_by_path[path].get("entries", [])
            if isinstance(e, dict)
        }
        if (correction.get("span_start"), correction.get("span_end")) not in spans:
            raise PublishRejected(
                "absent_provenance", "correction must reference a real source span"
            )
        if not correction.get("field") or "corrected_value" not in correction:
            raise PublishRejected("absent_provenance", "correction needs field + corrected_value")
    return {"included": included, "excluded": sorted(excluded_paths)}


def compute_content_hash(dataset: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Stable content hash anchoring idempotency (same content -> same version)."""
    from x_insight.contracts import canonical_hash

    docs = []
    for doc in sorted(dataset.get("documents", []), key=lambda d: d.get("source_path", "")):
        docs.append(
            {
                "source_path": doc.get("source_path"),
                "checksum": doc.get("checksum"),
                "entries": doc.get("entries", []),
                "subject_concept_id": doc.get("subject_concept_id"),
                "subject_name": doc.get("subject_name"),
            }
        )
    core = {
        "schema": "ddi-release/1",
        "parser_version": dataset.get("parser_version"),
        "terminology_version": dataset.get("terminology_version"),
        "terminology_checksum": dataset.get("terminology_checksum"),
        "concepts": dataset.get("concepts", []),
        "documents": docs,
        "manifest": {
            "reviewer": manifest.get("reviewer"),
            "decision": manifest.get("decision"),
            "date": manifest.get("date"),
            "record": manifest.get("record"),
            "parser_version": manifest.get("parser_version"),
            "terminology_version": manifest.get("terminology_version"),
            "terminology_checksum": manifest.get("terminology_checksum"),
            "coverage": manifest.get("coverage"),
            "corrections": manifest.get("corrections"),
            "reviewed_evidence": manifest.get("reviewed_evidence"),
            "limitations": manifest.get("limitations"),
        },
    }
    return canonical_hash(core)


def build_corpus_report(
    dataset: dict[str, Any], report: dict[str, Any], manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Draft the owner-review corpus report (pure; no DB, no I/O)."""
    docs = dataset.get("documents", [])
    checks = {c.get("source_path"): c for c in report.get("documents", []) if isinstance(c, dict)}
    severity_counts: dict[str, int] = {}
    pairs: dict[str, list[dict[str, Any]]] = {}
    duplicates = 0
    conflicts: list[dict[str, Any]] = []
    high_risk: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for entry in doc.get("entries", []):
            if not isinstance(entry, dict):
                continue
            category = entry.get("source_category", "unknown")
            severity_counts[category] = severity_counts.get(category, 0) + 1
            pair = entry.get("pair_concept_ids")
            key = "|".join(pair) if pair else f"raw:{entry.get('interacting_name')}"
            pairs.setdefault(key, []).append(
                {
                    "source_path": entry.get("source_path"),
                    "source_category": category,
                    "span_start": entry.get("span_start"),
                    "span_end": entry.get("span_end"),
                }
            )
            if entry.get("source_category") in HIGH_RISK:
                high_risk.append(
                    {
                        "source_path": entry.get("source_path"),
                        "source_category": category,
                        "interacting_name": entry.get("interacting_name"),
                        "raw_text": entry.get("raw_text"),
                        "span_start": entry.get("span_start"),
                        "span_end": entry.get("span_end"),
                    }
                )
    unique_pairs = len(pairs)
    for key, rows in pairs.items():
        if len(rows) > 1:
            duplicates += len(rows) - 1
            severities = {r["source_category"] for r in rows}
            if len(severities) > 1:
                conflicts.append({"pair": key, "severities": sorted(severities), "rows": rows})
    for path, check in checks.items():
        for anomaly in check.get("anomalies", []):
            anomalies.append({"source_path": path, "anomaly": anomaly})
    passed_docs = sum(1 for c in checks.values() if c.get("passed", False))
    unresolved = list(report.get("unresolved_names", []) or [])
    return {
        "schema_version": 1,
        "parser_version": dataset.get("parser_version"),
        "terminology_version": dataset.get("terminology_version"),
        "terminology_checksum": dataset.get("terminology_checksum"),
        "discovered": len(docs),
        "processed": len(docs),
        "passed": passed_docs,
        "failed": len(docs) - passed_docs,
        "severity_counts": severity_counts,
        "unique_pairs": unique_pairs,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "unknown_names": unresolved,
        "hashes": {
            "documents": [
                {"source_path": d.get("source_path"), "checksum": d.get("checksum")}
                for d in docs
                if isinstance(d, dict)
            ],
            "terminology_checksum": dataset.get("terminology_checksum"),
        },
        "review_records": {
            "high_risk": high_risk,
            "conflicts": conflicts,
            "unresolved": unresolved,
            "anomalies": anomalies,
        },
        "manifest_decision": (manifest or {}).get("decision", "awaiting_review"),
    }


def list_releases(engine: Any) -> list[dict[str, Any]]:
    """Every published release, oldest first (prior releases stay readable)."""
    from sqlalchemy import text

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT version, content_hash, status, reviewer, reviewed_at, "
                "parser_version, terminology_version, terminology_checksum, "
                "limitations, created_at FROM ddi_dataset_releases "
                "ORDER BY created_at ASC"
            )
        ).mappings()
        return [dict(row) for row in rows]


def get_release(engine: Any, version: str) -> dict[str, Any] | None:
    """One published release with its corpus snapshot, or None."""
    from sqlalchemy import text

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT version, content_hash, status, reviewer, reviewed_at, "
                    "parser_version, terminology_version, terminology_checksum, "
                    "manifest, report, limitations, created_at "
                    "FROM ddi_dataset_releases WHERE version = :version"
                ),
                {"version": version},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        release = dict(row)
        release["documents"] = [
            dict(r)
            for r in connection.execute(
                text(
                    "SELECT source_path, checksum FROM ddi_source_documents "
                    "WHERE release_id = "
                    "(SELECT id FROM ddi_dataset_releases WHERE version = :version) "
                    "ORDER BY source_path ASC"
                ),
                {"version": version},
            )
            .mappings()
            .all()
        ]
        count = connection.execute(
            text(
                "SELECT count(*) AS n FROM ddi_interaction_evidence WHERE release_id = "
                "(SELECT id FROM ddi_dataset_releases WHERE version = :version)"
            ),
            {"version": version},
        ).scalar()
        release["evidence_count"] = int(count or 0)
        return release


def get_evidence(engine: Any, version: str) -> list[dict[str, Any]]:
    """Every evidence row of one release (originals preserved, overlays separate)."""
    from sqlalchemy import text

    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT source_path, checksum, source_severity, interacting_name, "
                    "raw_text, span_start, span_end, subject_concept_id, "
                    "interacting_concept_id, pair_key, direction, correction "
                    "FROM ddi_interaction_evidence WHERE release_id = "
                    "(SELECT id FROM ddi_dataset_releases WHERE version = :version) "
                    "ORDER BY source_path ASC, span_start ASC"
                ),
                {"version": version},
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]


def _pair_key(entry: dict[str, Any]) -> str | None:
    pair = entry.get("pair_concept_ids")
    if pair and len(pair) == 2 and all(pair):
        first, second = sorted(pair)
        return f"{first}|{second}"
    return None


def publish_release(
    staging_dir: str | Path, manifest_path: str | Path, engine: Any
) -> dict[str, Any]:
    """Validate staging + manifest, then import the release in one transaction."""
    from sqlalchemy import text

    dataset, report = _load_staging(staging_dir)
    manifest = _load_manifest(manifest_path)
    gate = validate_for_publish(dataset, report, manifest)
    included = set(gate["included"])
    content_hash = compute_content_hash(dataset, manifest)
    version = f"ddi-{content_hash[:12]}"
    corpus = build_corpus_report(dataset, report, manifest)

    with engine.begin() as connection:
        existing = (
            connection.execute(
                text("SELECT version FROM ddi_dataset_releases WHERE content_hash = :hash"),
                {"hash": content_hash},
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return {"version": existing["version"], "content_hash": content_hash, "reused": True}
        reviewed_at = datetime.fromisoformat(str(manifest["date"]))
        if reviewed_at.tzinfo is None:
            reviewed_at = reviewed_at.replace(tzinfo=UTC)
        release_id = connection.execute(
            text(
                "INSERT INTO ddi_dataset_releases "
                "(content_hash, version, status, reviewer, reviewed_at, "
                "parser_version, terminology_version, terminology_checksum, "
                "manifest, report, limitations) "
                "VALUES (:hash, :version, :status, :reviewer, :reviewed_at, "
                ":parser, :term_version, :term_checksum, "
                "CAST(:manifest AS JSONB), CAST(:report AS JSONB), "
                "CAST(:limitations AS JSONB)) RETURNING id"
            ),
            {
                "hash": content_hash,
                "version": version,
                "status": STATUS_BY_DECISION[str(manifest["decision"])],
                "reviewer": manifest["reviewer"],
                "reviewed_at": reviewed_at,
                "parser": dataset.get("parser_version"),
                "term_version": dataset.get("terminology_version"),
                "term_checksum": dataset.get("terminology_checksum"),
                "manifest": json.dumps(manifest),
                "report": json.dumps(corpus),
                "limitations": json.dumps(manifest.get("limitations", [])),
            },
        ).scalar()
        corrections_by_span = {}
        for correction in manifest.get("corrections", []):
            corrections_by_span[
                (
                    correction.get("source_path"),
                    correction.get("span_start"),
                    correction.get("span_end"),
                )
            ] = correction
        for doc in dataset.get("documents", []):
            if doc.get("source_path") not in included:
                continue
            connection.execute(
                text(
                    "INSERT INTO ddi_source_documents (release_id, source_path, checksum) "
                    "VALUES (:release_id, :path, :checksum)"
                ),
                {
                    "release_id": release_id,
                    "path": doc.get("source_path"),
                    "checksum": doc.get("checksum"),
                },
            )
            for entry in doc.get("entries", []):
                key = (doc.get("source_path"), entry.get("span_start"), entry.get("span_end"))
                connection.execute(
                    text(
                        "INSERT INTO ddi_interaction_evidence "
                        "(release_id, source_path, checksum, source_severity, "
                        "interacting_name, raw_text, span_start, span_end, "
                        "subject_concept_id, interacting_concept_id, pair_key, "
                        "direction, correction) "
                        "VALUES (:release_id, :path, :checksum, :severity, "
                        ":name, :raw, :start, :end, :subject, :object, :pair, "
                        ":direction, CAST(:correction AS JSONB))"
                    ),
                    {
                        "release_id": release_id,
                        "path": entry.get("source_path"),
                        "checksum": doc.get("checksum"),
                        "severity": entry.get("source_category"),
                        "name": entry.get("interacting_name"),
                        "raw": entry.get("raw_text"),
                        "start": entry.get("span_start"),
                        "end": entry.get("span_end"),
                        "subject": entry.get("subject_concept_id"),
                        "object": entry.get("interacting_concept_id"),
                        "pair": _pair_key(entry),
                        "direction": entry.get("direction", "unknown"),
                        "correction": json.dumps(corrections_by_span[key])
                        if key in corrections_by_span
                        else None,
                    },
                )
        for row in dataset.get("concepts", []):
            connection.execute(
                text(
                    "INSERT INTO ddi_concepts "
                    "(release_id, concept_id, canonical_name, concept_type, catalog_drug_id) "
                    "VALUES (:release_id, :concept_id, :name, :type, :catalog) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "release_id": release_id,
                    "concept_id": row.get("id"),
                    "name": row.get("canonical_name"),
                    "type": row.get("concept_type"),
                    "catalog": row.get("catalog_drug_id"),
                },
            )
    # Import committed as one coherent operation; surface a stable summary.
    return {"version": version, "content_hash": content_hash, "reused": False}
