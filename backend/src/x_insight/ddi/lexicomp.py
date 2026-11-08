"""Install the user-selected, explicit-pair Lexicomp dataset."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from x_insight.contracts import canonical_hash
from x_insight.ddi.checker import _pair_key_for

PROFILE = "lexicomp-explicit-pairs/1"
PARSER = "lexicomp-csv/1"
HEADERS = ["Drug_1", "Drug_2", "Lexicomp_level", "Lexicomp_note"]
LIMITATIONS = [
    "Only explicit source pairs are covered; missing pairs have unavailable coverage.",
    "Source ratings and notes are not a formulation-specific prescription decision.",
]


def normalize(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def drug_id(name: str) -> str:
    return "lex-" + hashlib.sha256(normalize(name).encode("utf-8")).hexdigest()


def content_directory() -> Path:
    return Path(
        os.environ.get("X_INSIGHT_CONTENT_DIR") or Path(__file__).resolve().parents[4] / "content"
    )


def parse_source(
    raw: bytes, manifest: dict[str, Any], model: Path
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    if len(raw) != manifest["bytes"] or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("Lexicomp source size or checksum mismatch")
    if manifest["columns"] != HEADERS or manifest["profile"] != PROFILE:
        raise ValueError("Unexpected Lexicomp manifest")
    names: dict[str, str] = {}
    pairs: set[str] = set()
    ratings: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    with io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != HEADERS:
            raise ValueError("Unexpected Lexicomp CSV header")
        for number, row in enumerate(reader, 1):
            if None in row or any(
                not isinstance(row.get(key), str) or not row[key].strip() for key in HEADERS
            ):
                raise ValueError(f"Invalid Lexicomp record {number}")
            first, second = row["Drug_1"], row["Drug_2"]
            first_id, second_id = drug_id(first), drug_id(second)
            pair = _pair_key_for(first_id, second_id)
            rating = row["Lexicomp_level"]
            if first_id == second_id or pair in pairs or rating not in {"A", "B", "C", "D", "X"}:
                raise ValueError(f"Invalid or duplicate Lexicomp pair at record {number}")
            pairs.add(pair)
            names.setdefault(first_id, first.strip())
            names.setdefault(second_id, second.strip())
            ratings[rating] += 1
            records.append(
                {
                    "first": first_id,
                    "second": second_id,
                    "pair": pair,
                    "rating": rating,
                    "note": row["Lexicomp_note"],
                    "number": number,
                    "metadata": json.dumps(
                        {
                            "source_format": "csv",
                            "source_record_number": number,
                            "source_record": {"Drug_1": first, "Drug_2": second},
                        },
                        ensure_ascii=False,
                    ),
                }
            )
    counts = manifest["counts"]
    if (
        len(records) != counts["rows"]
        or len(names) != counts["catalog_names"]
        or dict(ratings) != counts["ratings"]
    ):
        raise ValueError("Lexicomp source counts mismatch")
    variables = [
        node
        for node in ET.parse(model).getroot().findall(".//VARIABLE")
        if node.findtext("NAME") == "Antipsychotic"
    ]
    outcomes = (
        [node.text for node in variables[0].findall("OUTCOME")] if len(variables) == 1 else []
    )
    if len(outcomes) != 22 or any(not name or drug_id(name) not in names for name in outcomes):
        raise ValueError("Oral model outcomes do not match the Lexicomp catalog")
    return names, records


def install(engine: Any, content: Path | None = None) -> dict[str, Any]:
    root = content or content_directory()
    directory = root / "ddi/lexicomp/v1"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw = (directory / manifest["source_file"]).read_bytes()
    names, records = parse_source(raw, manifest, root / "new-case/v1/oral-antipsychotic.xml")
    digest = canonical_hash(
        {"manifest": manifest, "parser": PARSER, "normalization": "NFKC-trim-casefold/1"}
    )
    version = "ddi-" + digest[:12]
    release_manifest = {**manifest, "source_profile": PROFILE}
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest[:15], 16)}
        )
        existing = connection.execute(
            text("SELECT version FROM ddi_dataset_releases WHERE content_hash=:hash"),
            {"hash": digest},
        ).scalar()
        if existing:
            return {"version": existing, "content_hash": digest, "reused": True}
        release_id = connection.execute(
            text("""
            INSERT INTO ddi_dataset_releases
            (content_hash,version,status,reviewer,reviewed_at,parser_version,terminology_version,
             terminology_checksum,manifest,report,limitations)
            VALUES (:hash,:version,'released_limited','user-selected; not clinically reviewed',
                    now(),:parser,:version,:checksum,CAST(:manifest AS JSONB),
                    CAST(:report AS JSONB),CAST(:limitations AS JSONB)) RETURNING id
        """),
            {
                "hash": digest,
                "version": version,
                "parser": PARSER,
                "checksum": manifest["sha256"],
                "manifest": json.dumps(release_manifest),
                "report": json.dumps(manifest["counts"]),
                "limitations": json.dumps(LIMITATIONS),
            },
        ).scalar_one()
        connection.execute(
            text("""INSERT INTO ddi_source_documents
            (release_id,source_path,checksum,source_format,source_bytes,source_metadata)
            VALUES (:release_id,:path,:checksum,'csv',:raw,CAST(:metadata AS JSONB))"""),
            {
                "release_id": release_id,
                "path": manifest["source_file"],
                "checksum": manifest["sha256"],
                "raw": raw,
                "metadata": json.dumps(manifest),
            },
        )
        connection.execute(
            text("""INSERT INTO ddi_concepts
            (release_id,concept_id,canonical_name,concept_type,catalog_drug_id)
            VALUES (:release_id,:id,:name,'drug',:id)"""),
            [{"release_id": release_id, "id": key, "name": name} for key, name in names.items()],
        )
        statement = text("""INSERT INTO ddi_interaction_evidence
            (release_id,source_path,checksum,source_severity,interacting_name,raw_text,span_start,span_end,
             subject_concept_id,interacting_concept_id,pair_key,direction,source_record_metadata)
            VALUES (:release_id,:path,:checksum,:rating,:name,:note,:number,:number,
                    :first,:second,:pair,'undirected',CAST(:metadata AS JSONB))""")
        for start in range(0, len(records), 1000):
            connection.execute(
                statement,
                [
                    {
                        **row,
                        "release_id": release_id,
                        "path": manifest["source_file"],
                        "checksum": manifest["sha256"],
                        "name": names[row["second"]],
                    }
                    for row in records[start : start + 1000]
                ],
            )
    return {"version": version, "content_hash": digest, "reused": False}


def current_version() -> str:
    manifest = json.loads(
        (content_directory() / "ddi/lexicomp/v1/manifest.json").read_text(encoding="utf-8")
    )
    return (
        "ddi-"
        + canonical_hash(
            {"manifest": manifest, "parser": PARSER, "normalization": "NFKC-trim-casefold/1"}
        )[:12]
    )
