"""Deterministic, severity-only DDInter CSV staging and owner-gated publication.

Evidence is JSONL so neither building nor importing materializes the corpus.
Publication rebuilds a private snapshot from preserved bytes: approval binds to
both exact source occurrences and canonical artifacts, and mutable staging files
cannot change between validation and insertion.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from x_insight.contracts import canonical_hash
from x_insight.ddi.terminology import normalize

FORMAT = "ddi-ddinter-stream/1"
PARSER_VERSION = "ddinter-csv/1"
SOURCE_PROFILE = "ddinter-positive-assertions/1"
SOURCE_LABEL = "DDInter supplied CSV exports"
CSV_FILES = tuple(f"ddinter_downloads_code_{code}.csv" for code in "ABDHLPRV")
SOURCE_FILES = (*CSV_FILES, "docs/explanation.txt")
HEADERS = ["DDInterID_A", "Drug_A", "DDInterID_B", "Drug_B", "Level"]
SEVERITIES = ("Major", "Moderate", "Minor", "Unknown")
ID_RE = re.compile(r"DDInter[1-9][0-9]*\Z")
COVERAGE = {
    "scope": "limited",
    "input_completeness": "all_supplied_csvs",
    "pair_coverage": "positive_assertions_only",
    "missing_pair_policy": "coverage_unavailable",
    "clinical_detail_availability": "severity_only",
    "excluded_sources": [],
}
LIMITATIONS = [
    "Complete for the eight supplied CSV exports only; broader DDInter coverage is not claimed.",
    "Positive assertions only: missing pairs have unavailable coverage.",
    "Severity only: pair-specific mechanism, management and citations were not supplied.",
    "Repeated export occurrences do not establish independent clinical corroboration.",
]
ARTIFACTS = (
    "catalog.json",
    "evidence.jsonl",
    "major-review-inventory.jsonl",
    "duplicates.jsonl",
    "report.json",
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(_dump(value) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _RecordLines:
    """csv.reader consumes physical lines; retain exactly the current record."""

    def __init__(self, handle: TextIO):
        self.handle = handle
        self.lines: list[str] = []

    def __iter__(self) -> _RecordLines:
        return self

    def __next__(self) -> str:
        line = next(self.handle)
        self.lines.append(line)
        return line


def records(path: Path) -> Iterator[tuple[int, int, int, list[str], str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        lines = _RecordLines(handle)
        reader = csv.reader(lines, strict=True)
        if next(reader, None) != HEADERS:
            raise ValueError("CSV header must exactly match " + ",".join(HEADERS))
        lines.lines.clear()
        previous = reader.line_num
        for number, fields in enumerate(reader, 1):
            raw = "".join(lines.lines)
            lines.lines.clear()
            yield number, previous + 1, reader.line_num, fields, raw
            previous = reader.line_num


def review_reference(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry[key]
        for key in ("source_path", "checksum", "span_start", "span_end", "source_record_number")
    }


def iter_evidence(root: Path) -> Iterator[dict[str, Any]]:
    with (root / "evidence.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def build_ddinter(sources: str | Path, output: str | Path) -> dict[str, Any]:
    """Write reproducible candidate artifacts, including a never-approved manifest."""
    source_root, root = Path(sources).expanduser().resolve(), Path(output).expanduser().resolve()
    if root == source_root or source_root in root.parents or root in source_root.parents:
        raise ValueError("staging and source directories must be separate")
    missing = [name for name in SOURCE_FILES if not (source_root / name).is_file()]
    if missing:
        raise ValueError("missing required sources: " + ", ".join(missing))
    root.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, Any]] = []
    for name in SOURCE_FILES:
        target = root / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / name, target)
        inventory.append(
            {
                "source_path": name,
                "checksum": file_hash(target),
                "bytes": target.stat().st_size,
                "source_format": "csv" if name in CSV_FILES else "text",
            }
        )
    identities: dict[str, str] = {}
    names: dict[str, str] = {}
    pairs: dict[str, str] = {}
    severity_counts: Counter[str] = Counter()
    occurrence_severities: Counter[str] = Counter()
    file_counts: Counter[str] = Counter()
    anomalies: list[dict[str, Any]] = []
    major_count = duplicates = count = 0
    with (
        (root / "evidence.jsonl").open("w", encoding="utf-8") as evidence,
        (root / "major-review-inventory.jsonl").open("w", encoding="utf-8") as majors,
        (root / "duplicates.jsonl").open("w", encoding="utf-8") as repeated,
    ):
        for source in inventory:
            name = source["source_path"]
            if name not in CSV_FILES:
                continue
            try:
                for number, start, end, fields, raw in records(root / "sources" / name):
                    file_counts[name] += 1
                    problem = None
                    if len(fields) != 5 or any(not value.strip() for value in fields):
                        problem = "record requires exactly five nonempty fields"
                    elif not ID_RE.fullmatch(fields[0]) or not ID_RE.fullmatch(fields[2]):
                        problem = "malformed DDInter ID"
                    elif fields[4] not in SEVERITIES:
                        problem = "unexpected severity"
                    elif fields[0] == fields[2]:
                        problem = "self-pair"
                    if problem:
                        anomalies.append(
                            {
                                "source_path": name,
                                "record_number": number,
                                "span_start": start,
                                "span_end": end,
                                "reason": problem,
                            }
                        )
                        continue
                    first, first_name, second, second_name, severity = fields
                    for identifier, drug_name in ((first, first_name), (second, second_name)):
                        if identifier in identities and identities[identifier] != drug_name:
                            problem = "drug ID/name disagreement"
                        normalized = normalize(drug_name)
                        if normalized in names and names[normalized] != identifier:
                            problem = "normalized name shared by different IDs"
                        identities.setdefault(identifier, drug_name)
                        names.setdefault(normalized, identifier)
                    pair = "|".join(sorted((f"ddinter:{first}", f"ddinter:{second}")))
                    if pair in pairs and pairs[pair] != severity:
                        problem = "conflicting severities for unordered pair"
                    if problem:
                        anomalies.append(
                            {
                                "source_path": name,
                                "record_number": number,
                                "span_start": start,
                                "span_end": end,
                                "reason": problem,
                            }
                        )
                    entry = {
                        "source_path": name,
                        "checksum": source["checksum"],
                        "source_severity": severity,
                        "raw_text": raw,
                        "span_start": start,
                        "span_end": end,
                        "subject_concept_id": f"ddinter:{first}",
                        "interacting_concept_id": f"ddinter:{second}",
                        "interacting_name": second_name,
                        "pair_key": pair,
                        "direction": "unknown",
                        "source_record_number": number,
                        "source_record": dict(zip(HEADERS, fields, strict=True)),
                        "detail_availability": "severity_only",
                        "source_format": "csv",
                        "source_name": SOURCE_LABEL,
                    }
                    evidence.write(_dump(entry) + "\n")
                    count += 1
                    occurrence_severities[severity] += 1
                    if severity == "Major":
                        majors.write(_dump(review_reference(entry)) + "\n")
                        major_count += 1
                    if pair in pairs:
                        repeated.write(_dump({**review_reference(entry), "pair_key": pair}) + "\n")
                        duplicates += 1
                    else:
                        pairs[pair] = severity
                        severity_counts[severity] += 1
            except (ValueError, UnicodeError, csv.Error) as exc:
                anomalies.append({"source_path": name, "reason": str(exc)})
    if count == 0:
        anomalies.append({"source_path": "*", "reason": "no valid CSV evidence records"})
    catalog = [
        {
            "id": f"ddinter:{identifier}",
            "catalog_drug_id": identifier,
            "canonical_name": drug_name,
            "concept_type": "drug",
            "source": SOURCE_LABEL,
        }
        for identifier, drug_name in sorted(identities.items())
    ]
    _write_json(root / "catalog.json", catalog)
    explanation = (root / "sources/docs/explanation.txt").read_text(encoding="utf-8-sig")
    section = re.search(
        r"(?ms)^6\. Criteria for severity annotation\s*(.*?)(?=^7\.|\Z)", explanation
    )
    if not section:
        anomalies.append(
            {
                "source_path": "docs/explanation.txt",
                "reason": "severity definitions section 6 missing",
            }
        )
    report = {
        "format": FORMAT,
        "parser_version": PARSER_VERSION,
        "source_profile": SOURCE_PROFILE,
        "source_label": SOURCE_LABEL,
        "passed": not anomalies,
        "sources": inventory,
        "source_rows": dict(sorted(file_counts.items())),
        "catalog_count": len(catalog),
        "evidence_count": count,
        "unique_pair_count": len(pairs),
        "repeated_occurrences": duplicates,
        "unique_severity_counts": dict(sorted(severity_counts.items())),
        "occurrence_severity_counts": dict(sorted(occurrence_severities.items())),
        "major_occurrences": major_count,
        "major_unique_pairs": sum(value == "Major" for value in pairs.values()),
        "anomalies": anomalies,
        "conflicts": [a for a in anomalies if "conflicting" in a["reason"]],
        "severity_definitions": section.group(1).strip() if section else "",
        "coverage": COVERAGE,
        "limitations": LIMITATIONS,
    }
    _write_json(root / "report.json", report)
    candidate = {
        "format": FORMAT,
        "parser_version": PARSER_VERSION,
        "source_profile": SOURCE_PROFILE,
        "source_label": SOURCE_LABEL,
        "terminology_version": "ddinter-catalog/1",
        "sources": inventory,
        "coverage": COVERAGE,
        "artifacts": {name: file_hash(root / name) for name in ARTIFACTS},
    }
    candidate["candidate_hash"] = canonical_hash(candidate)
    _write_json(root / "candidate_dataset.json", candidate)
    _write_json(
        root / "review-manifest.json",
        {
            "reviewer": "owner",
            "decision": "awaiting_review",
            "date": None,
            "record": None,
            "format": FORMAT,
            "parser_version": PARSER_VERSION,
            "source_profile": SOURCE_PROFILE,
            "candidate_hash": candidate["candidate_hash"],
            "artifacts": candidate["artifacts"],
            "sources": inventory,
            "coverage": COVERAGE,
            "limitations": LIMITATIONS,
            "accept_severity_only": False,
            "accept_unavailable_pair_coverage": False,
            "corrections": [],
            "reviewed_evidence": [],
        },
    )
    return report


def _review_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row.get(key)
        for key in ("source_path", "checksum", "span_start", "span_end", "source_record_number")
    )


def validate_approval(
    candidate: dict[str, Any], report: dict[str, Any], manifest: dict[str, Any], root: Path
) -> None:
    from x_insight.ddi.publish import PublishRejected, _validate_manifest_shape

    _validate_manifest_shape(manifest)

    def reject(message: str) -> None:
        raise PublishRejected("unreviewed_evidence", message)

    if manifest.get("decision") != "approved_limited" or manifest.get("coverage") != COVERAGE:
        reject("DDInter requires approved_limited and complete positive-only coverage declaration")
    if not report.get("passed"):
        reject("candidate has invalid source records")
    for key in (
        "format",
        "parser_version",
        "source_profile",
        "candidate_hash",
        "artifacts",
        "sources",
    ):
        if manifest.get(key) != candidate.get(key):
            reject(f"manifest {key} does not match the revalidated candidate")
    record = manifest.get("record")
    limitations = manifest["limitations"]
    if not isinstance(record, str) or not record.strip():
        reject("approval requires a nonblank string review record")
    if not limitations or any(
        not isinstance(value, str) or not value.strip() for value in limitations
    ):
        reject("approval limitations must be nonblank strings")
    if not set(LIMITATIONS).issubset(limitations):
        reject("approval must retain the explicit DDInter limitations")
    if (
        manifest.get("accept_severity_only") is not True
        or manifest.get("accept_unavailable_pair_coverage") is not True
    ):
        reject("owner must explicitly accept severity-only data and unavailable pair coverage")
    if manifest.get("corrections"):
        reject("DDInter source assertions cannot be overwritten by corrections")
    reviewed = manifest.get("reviewed_evidence", [])
    if not all(isinstance(row, dict) for row in reviewed):
        reject("reviewed_evidence records must identify exact source occurrences")
    for row in reviewed:
        if any(not isinstance(row.get(key), str) for key in ("source_path", "checksum")) or any(
            type(row.get(key)) is not int or row[key] < 1
            for key in ("span_start", "span_end", "source_record_number")
        ):
            reject("reviewed_evidence requires source hash and positive integer record locations")
    keys = [_review_key(row) for row in reviewed]
    expected = set()
    with (root / "major-review-inventory.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            expected.add(_review_key(json.loads(line)))
    if len(keys) != len(set(keys)) or set(keys) != expected:
        reject("Major owner review must identify every included occurrence by hash and location")


def publish_ddinter(
    staging_dir: str | Path, manifest: dict[str, Any], engine: Any
) -> dict[str, Any]:
    """Rebuild source snapshot, check every artifact and approval, atomically import."""
    from sqlalchemy import text

    from x_insight.ddi.publish import PublishRejected, _load_json

    root = Path(staging_dir)
    supplied = _load_json(root / "candidate_dataset.json", "candidate")
    with tempfile.TemporaryDirectory(prefix="ddi-publish-") as folder:
        snapshot = Path(folder) / "validated"
        try:
            report = build_ddinter(root / "sources", snapshot)
        except (OSError, ValueError) as exc:
            raise PublishRejected("absent_provenance", str(exc)) from exc
        candidate = _load_json(snapshot / "candidate_dataset.json", "rebuilt candidate")
        if candidate != supplied:
            raise PublishRejected(
                "absent_provenance", "source bytes or candidate inventory changed"
            )
        for name in ARTIFACTS:
            if (
                not (root / name).is_file()
                or file_hash(root / name) != candidate["artifacts"][name]
            ):
                raise PublishRejected("absent_provenance", f"staged artifact changed: {name}")
        validate_approval(candidate, report, manifest, snapshot)
        content_hash = canonical_hash({"candidate": candidate, "approval": manifest})
        version = f"ddi-{content_hash[:12]}"
        reviewed_at = datetime.fromisoformat(manifest["date"])
        if reviewed_at.tzinfo is None:
            reviewed_at = reviewed_at.replace(tzinfo=UTC)
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(content_hash[:15], 16)}
            )
            existing = connection.execute(
                text("SELECT version FROM ddi_dataset_releases WHERE content_hash = :hash"),
                {"hash": content_hash},
            ).scalar()
            if existing:
                return {"version": existing, "content_hash": content_hash, "reused": True}
            release_id = connection.execute(
                text(
                    "INSERT INTO ddi_dataset_releases (content_hash,version,status,"
                    "reviewer,reviewed_at,"
                    "parser_version,terminology_version,terminology_checksum,manifest,"
                    "report,limitations) "
                    "VALUES (:hash,:version,'released_limited','owner',:reviewed,"
                    ":parser,:terminology,"
                    ":checksum,CAST(:manifest AS JSONB),CAST(:report AS JSONB),"
                    "CAST(:limitations AS JSONB)) RETURNING id"
                ),
                {
                    "hash": content_hash,
                    "version": version,
                    "reviewed": reviewed_at,
                    "parser": PARSER_VERSION,
                    "terminology": candidate["terminology_version"],
                    "checksum": candidate["artifacts"]["catalog.json"],
                    "manifest": _dump(manifest),
                    "report": _dump(report),
                    "limitations": _dump(manifest["limitations"]),
                },
            ).scalar()
            for source in candidate["sources"]:
                connection.execute(
                    text(
                        "INSERT INTO ddi_source_documents (release_id,source_path,"
                        "checksum,source_format,"
                        "source_bytes,source_metadata) VALUES (:rid,:path,:checksum,"
                        ":format,:raw,CAST(:metadata AS JSONB))"
                    ),
                    {
                        "rid": release_id,
                        "path": source["source_path"],
                        "checksum": source["checksum"],
                        "format": source["source_format"],
                        "raw": (snapshot / "sources" / source["source_path"]).read_bytes(),
                        "metadata": _dump(
                            {
                                "source_name": SOURCE_LABEL,
                                "bytes": source["bytes"],
                                "record_count": report["source_rows"].get(source["source_path"], 0),
                            }
                        ),
                    },
                )
            catalog = _load_json(snapshot / "catalog.json", "catalog")
            connection.execute(
                text(
                    "INSERT INTO ddi_concepts (release_id,concept_id,canonical_name,"
                    "concept_type,catalog_drug_id) "
                    "VALUES (:rid,:id,:name,'drug',:catalog)"
                ),
                [
                    {
                        "rid": release_id,
                        "id": row["id"],
                        "name": row["canonical_name"],
                        "catalog": row["catalog_drug_id"],
                    }
                    for row in catalog
                ],
            )
            statement = text(
                "INSERT INTO ddi_interaction_evidence (release_id,source_path,"
                "checksum,source_severity,"
                "interacting_name,raw_text,span_start,span_end,subject_concept_id,"
                "interacting_concept_id,"
                "pair_key,direction,source_record_metadata) VALUES (:rid,"
                ":source_path,:checksum,:source_severity,"
                ":interacting_name,:raw_text,:span_start,:span_end,"
                ":subject_concept_id,:interacting_concept_id,"
                ":pair_key,'unknown',CAST(:metadata AS JSONB))"
            )
            batch = []
            for entry in iter_evidence(snapshot):
                batch.append(
                    {
                        **entry,
                        "rid": release_id,
                        "metadata": _dump(
                            {
                                key: entry[key]
                                for key in (
                                    "source_format",
                                    "source_name",
                                    "source_record",
                                    "source_record_number",
                                    "detail_availability",
                                )
                            }
                        ),
                    }
                )
                if len(batch) == 1000:
                    connection.execute(statement, batch)
                    batch.clear()
            if batch:
                connection.execute(statement, batch)
            actual = connection.execute(
                text(
                    "SELECT count(*),"
                    "count(DISTINCT pair_key) FROM ddi_interaction_evidence WHERE release_id=:rid"
                ),
                {"rid": release_id},
            ).one()
            concept_count = connection.execute(
                text("SELECT count(*) FROM ddi_concepts WHERE release_id=:rid"), {"rid": release_id}
            ).scalar()
            if (
                tuple(actual) != (report["evidence_count"], report["unique_pair_count"])
                or concept_count != report["catalog_count"]
            ):
                raise PublishRejected(
                    "count_failure", "imported counts differ from approved inventory"
                )
        return {"version": version, "content_hash": content_hash, "reused": False}
