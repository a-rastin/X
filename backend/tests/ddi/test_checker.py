"""S19 deterministic coverage-aware DDI checking (T4; plan.md §§6.1/6.3, FR-14/15).

Slice 1 (red first): pair identity + batched indexed lookup.
- A/B and B/A yield the same pair/evidence.
- Three unique drugs yield three pairs.
- Duplicate aliases (same catalog twice) do not create self-pairs.

Seam T4 only: medications + dataset_version -> coverage/evidence via real
PostgreSQL. Synthetic monographs only here; source-backed Ofloxacin fixture
lands in slice 2. Expected values are worked literals, never implementation
output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def concept(identifier, name, kind="ingredient", catalog=None):
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": kind,
        "catalog_drug_id": catalog,
        "source": "synthetic S19 fixture",
    }


def vocabulary(concepts, aliases=None):
    return {"version": "test-s19/1", "concepts": concepts, "aliases": aliases or []}


def monograph(root: Path, subject: str, names: list[str]):
    text = f"Interactions\n\nContraindicated (0)\n\nSerious ({len(names)})\n\n"
    text += "\n\n".join(
        f"{name}\n{name} increases the level of {subject} by mechanism. Avoid combination."
        for name in names
    )
    text += "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    (root / f"{subject}.txt").write_text(text)


def approved(name, concept_id):
    return {
        "name": name,
        "concept_id": concept_id,
        "source": "synthetic S19 fixture",
        "review": {
            "decision": "approved",
            "reviewer": "owner",
            "date": "2026-10-06",
            "record": "synthetic fixture approval",
        },
    }


def _stage(source_dir: Path, staging: Path, terminology=None):
    from dataclasses import asdict

    from x_insight.ddi.ingestion import build

    dataset, report = build(source_dir, terminology)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "candidate_dataset.json").write_text(
        json.dumps(asdict(dataset), indent=2) + "\n", encoding="utf-8"
    )
    (staging / "report.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )
    return dataset, report


def _manifest_for_staging(staging: Path, path: Path, **overrides):
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "reviewer": "owner",
        "decision": "approved_complete",
        "date": "2026-10-06",
        "record": "synthetic owner review",
        "parser_version": dataset.get("parser_version"),
        "terminology_version": dataset.get("terminology_version"),
        "terminology_checksum": dataset.get("terminology_checksum"),
        "coverage": {"scope": "complete", "excluded_sources": []},
        "corrections": [],
        "reviewed_evidence": [],
        "limitations": [],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _reviewed_for_staging(staging: Path, included: list[str]) -> list[dict]:
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    reviewed = []
    for doc in dataset["documents"]:
        if doc["source_path"] not in included:
            continue
        for entry in doc["entries"]:
            if entry["source_category"] in ("contraindicated", "serious"):
                reviewed.append(
                    {
                        "source_path": doc["source_path"],
                        "span_start": entry["span_start"],
                        "span_end": entry["span_end"],
                        "severity": entry["source_category"],
                    }
                )
    return reviewed


def _publish_complete(tmp_path, engine, subjects):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    for subject, names in subjects.items():
        monograph(sources, subject, names)
    concepts = [concept(s.lower(), s, catalog=f"catalog_{s.lower()}") for s in subjects]
    for names in subjects.values():
        for name in names:
            if name.lower() not in {c["id"] for c in concepts}:
                concepts.append(concept(name.lower(), name, catalog=f"catalog_{name.lower()}"))
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = sorted(f"{subject}.txt" for subject in subjects)
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    result = publish.publish_release(staging, manifest, engine)
    return result


@pytest.fixture(scope="session")
def migrated_test_engine():
    from alembic import command
    from alembic.config import Config

    from x_insight import db as db_module

    url = db_module.get_test_database_url()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    engine = db_module.build_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture()
def clean_ddi(migrated_test_engine):
    from sqlalchemy import text

    with migrated_test_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases"
            )
        )
    yield migrated_test_engine
    with migrated_test_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ddi_interaction_evidence, ddi_concepts, "
                "ddi_source_documents, ddi_dataset_releases"
            )
        )


# --- Slice 1: deterministic pair identity + dedup (T4) ---


def test_ab_and_ba_yield_same_pair_and_evidence(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})
    version = result["version"]

    forward = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], version)
    backward = checker.check(clean_ddi, ["catalog_beta", "catalog_alpha"], version)

    assert len(forward["pairs"]) == 1
    assert len(backward["pairs"]) == 1
    pair_f = forward["pairs"][0]
    pair_b = backward["pairs"][0]
    # Canonical unordered identity: same drug_a/b regardless of input order.
    assert (pair_f["drug_a"], pair_f["drug_b"]) == ("catalog_alpha", "catalog_beta")
    assert (pair_b["drug_a"], pair_b["drug_b"]) == ("catalog_alpha", "catalog_beta")
    assert pair_f["status"] == "interaction_found"
    assert pair_b["status"] == "interaction_found"
    assert pair_f["evidence"] == pair_b["evidence"]
    assert len(pair_f["evidence"]) == 2
    assert {row["source_path"] for row in pair_f["evidence"]} == {"Alpha.txt", "Beta.txt"}


def test_three_unique_drugs_yield_three_pairs(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(
        tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"], "Gamma": []}
    )
    version = result["version"]

    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta", "catalog_gamma"], version)

    assert len(report["pairs"]) == 3
    keys = {(row["drug_a"], row["drug_b"]) for row in report["pairs"]}
    assert keys == {
        ("catalog_alpha", "catalog_beta"),
        ("catalog_alpha", "catalog_gamma"),
        ("catalog_beta", "catalog_gamma"),
    }
    # No self-pairs even with three inputs.
    for row in report["pairs"]:
        assert row["drug_a"] != row["drug_b"]
    # Fingerprint is order-independent.
    shuffled = checker.check(clean_ddi, ["catalog_gamma", "catalog_beta", "catalog_alpha"], version)
    assert shuffled["medication_fingerprint"] == report["medication_fingerprint"]
    assert {(r["drug_a"], r["drug_b"]) for r in shuffled["pairs"]} == keys


def test_duplicate_aliases_do_not_create_self_pairs(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})
    version = result["version"]

    single = checker.check(clean_ddi, ["catalog_alpha", "catalog_alpha"], version)
    assert single["pairs"] == []
    assert len(single["resolved_medications"]) == 1
    assert single["resolved_medications"][0]["catalog_drug_id"] == "catalog_alpha"

    # Duplicate alongside a second drug still yields one pair, not a self-pair.
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta", "catalog_alpha"], version)
    assert len(report["pairs"]) == 1
    assert report["pairs"][0]["drug_a"] == "catalog_alpha"
    assert report["pairs"][0]["drug_b"] == "catalog_beta"


def test_evidence_lookup_is_single_batched_query_for_three_pairs(tmp_path, clean_ddi):
    """Three pairs issue one batched evidence query, never one query per pair."""
    from sqlalchemy import event

    from x_insight.ddi import checker

    result = _publish_complete(
        tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"], "Gamma": []}
    )
    version = result["version"]

    evidence_statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if "ddi_interaction_evidence" in str(statement):
            evidence_statements.append(str(statement))

    event.listen(clean_ddi, "before_cursor_execute", _count)
    try:
        report = checker.check(
            clean_ddi, ["catalog_alpha", "catalog_beta", "catalog_gamma"], version
        )
    finally:
        event.remove(clean_ddi, "before_cursor_execute", _count)

    assert len(report["pairs"]) == 3
    assert len(evidence_statements) == 1
    by_pair = {(row["drug_a"], row["drug_b"]): row for row in report["pairs"]}
    assert set(by_pair) == {
        ("catalog_alpha", "catalog_beta"),
        ("catalog_alpha", "catalog_gamma"),
        ("catalog_beta", "catalog_gamma"),
    }
    assert by_pair[("catalog_alpha", "catalog_beta")]["status"] == "interaction_found"
    assert len(by_pair[("catalog_alpha", "catalog_beta")]["evidence"]) == 2


# --- Slice 2: evidence aggregation + severity + direction (T4) ---


def _write_conflicting_pair(root: Path):
    """Alpha->Beta serious, Beta->Alpha monitor_closely (worked literals)."""
    (root / "Alpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Beta\nBeta increases the level of Alpha by mechanism. Avoid combination.\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    (root / "Beta.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\nMonitor Closely (1)\n\n"
        "Alpha\nAlpha decreases the level of Beta by other mechanism. Use caution.\n\n"
        "Minor (0)\nWarnings\n"
    )


def _publish_conflicting(tmp_path, engine):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    _write_conflicting_pair(sources)
    concepts = [
        concept("a", "Alpha", catalog="catalog_alpha"),
        concept("b", "Beta", catalog="catalog_beta"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, engine)


def test_known_evidence_returns_every_assertion_verbatim(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_conflicting(tmp_path, clean_ddi)
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])

    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert len(pair["evidence"]) == 2
    by_path = {row["source_path"]: row for row in pair["evidence"]}
    assert set(by_path) == {"Alpha.txt", "Beta.txt"}
    # Source severity stays verbatim; management prose stays in raw_text.
    assert by_path["Alpha.txt"]["source_severity"] == "serious"
    assert by_path["Beta.txt"]["source_severity"] == "monitor_closely"
    assert "Avoid combination" in by_path["Alpha.txt"]["raw_text"]
    assert "Use caution" in by_path["Beta.txt"]["raw_text"]
    assert by_path["Alpha.txt"]["management"] is None
    assert by_path["Beta.txt"]["management"] is None
    for row in pair["evidence"]:
        assert row["span_start"] and row["span_end"]
        assert row["checksum"] and row["source_hash"] == row["checksum"]


def test_highest_severity_conflicts_and_unknown_indicators(tmp_path, clean_ddi):
    from sqlalchemy import text

    from x_insight.ddi import checker

    result = _publish_conflicting(tmp_path, clean_ddi)
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    pair = report["pairs"][0]

    # Display priority: serious outranks monitor_closely (not a clinical score).
    assert pair["highest_known_severity"] == "serious"
    assert set(pair["conflicts"]) == {"serious", "monitor_closely"}
    assert pair["has_unknown_severity"] is False

    # Unknown severity stays visible, never hidden as low severity.
    with clean_ddi.begin() as connection:
        release_id = connection.execute(
            text("SELECT id FROM ddi_dataset_releases WHERE version = :v"),
            {"v": result["version"]},
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO ddi_interaction_evidence "
                "(release_id, source_path, checksum, source_severity, interacting_name, "
                "raw_text, span_start, span_end, subject_concept_id, "
                "interacting_concept_id, pair_key, direction) "
                "VALUES (:rid, 'Alpha.txt', 'abc', 'unknown', 'Beta', "
                "'Beta unknown significance text.', 1, 2, 'a', 'b', 'a|b', 'unknown')"
            ),
            {"rid": release_id},
        )
    rerun = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    rerun_pair = rerun["pairs"][0]
    assert len(rerun_pair["evidence"]) == 3
    assert rerun_pair["has_unknown_severity"] is True
    assert rerun_pair["highest_known_severity"] == "serious"
    assert "unknown" in rerun_pair["conflicts"]
    assert any(row["source_severity"] == "unknown" for row in rerun_pair["evidence"])


def test_severity_priority_contraindicated_outranks_all_known(tmp_path, clean_ddi):
    from sqlalchemy import text

    from x_insight.ddi import checker

    # Display priority is the plan §6.1 literal, never a clinical score.
    assert checker.SEVERITY_ORDER == (
        "contraindicated",
        "serious",
        "monitor_closely",
        "minor",
        "unknown",
    )

    result = _publish_conflicting(tmp_path, clean_ddi)
    with clean_ddi.begin() as connection:
        release_id = connection.execute(
            text("SELECT id FROM ddi_dataset_releases WHERE version = :v"),
            {"v": result["version"]},
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO ddi_interaction_evidence "
                "(release_id, source_path, checksum, source_severity, interacting_name, "
                "raw_text, span_start, span_end, subject_concept_id, "
                "interacting_concept_id, pair_key, direction) "
                "VALUES (:rid, 'Beta.txt', 'ci', 'contraindicated', 'Alpha', "
                "'Alpha is contraindicated with Beta by mechanism.', 10, 11, "
                "'b', 'a', 'a|b', 'explicit')"
            ),
            {"rid": release_id},
        )
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    pair = report["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert pair["highest_known_severity"] == "contraindicated"
    assert pair["has_unknown_severity"] is False
    assert set(pair["conflicts"]) == {"contraindicated", "serious", "monitor_closely"}
    assert len(pair["evidence"]) == 3


def _publish_minor_only(tmp_path, engine):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    (sources / "Alpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\nMonitor Closely (0)\n\n"
        "Minor (1)\n\nBeta\nBeta causes a minor effect with Alpha by mechanism. "
        "Minor/Significance Unknown.\n\nWarnings\n"
    )
    concepts = [
        concept("a", "Alpha", catalog="catalog_alpha"),
        concept("b", "Beta", catalog="catalog_beta"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, engine)


def test_minor_outranks_unknown_when_no_higher_known(tmp_path, clean_ddi):
    from sqlalchemy import text

    from x_insight.ddi import checker

    result = _publish_minor_only(tmp_path, clean_ddi)
    with clean_ddi.begin() as connection:
        release_id = connection.execute(
            text("SELECT id FROM ddi_dataset_releases WHERE version = :v"),
            {"v": result["version"]},
        ).scalar()
        connection.execute(
            text(
                "INSERT INTO ddi_interaction_evidence "
                "(release_id, source_path, checksum, source_severity, interacting_name, "
                "raw_text, span_start, span_end, subject_concept_id, "
                "interacting_concept_id, pair_key, direction) "
                "VALUES (:rid, 'Alpha.txt', 'abc', 'unknown', 'Beta', "
                "'Beta unknown significance text.', 1, 2, 'a', 'b', 'a|b', 'unknown')"
            ),
            {"rid": release_id},
        )
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    pair = report["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert len(pair["evidence"]) == 2
    # Known minor still outranks unknown; unknown stays flagged, never hidden.
    assert pair["highest_known_severity"] == "minor"
    assert pair["has_unknown_severity"] is True
    assert set(pair["conflicts"]) == {"minor", "unknown"}


def test_directional_descriptions_retained_as_separate_rows(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_conflicting(tmp_path, clean_ddi)
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    pair = report["pairs"][0]

    assert len(pair["evidence"]) == 2
    directions = {row["direction"] for row in pair["evidence"]}
    assert directions == {"explicit"}
    texts = {row["raw_text"] for row in pair["evidence"]}
    assert len(texts) == 2
    assert any("increases the level of Alpha" in text for text in texts)
    assert any("decreases the level of Beta" in text for text in texts)
    # Duplicate directions from independent sources stay separate rows.
    assert pair["evidence"][0]["source_path"] != pair["evidence"][1]["source_path"]


def test_source_backed_sitagliptin_ofloxacin_both_categories(tmp_path, clean_ddi):
    """Real Sitagliptin parsing (0/4/92/70) keeps Ofloxacin in two categories."""
    import shutil

    from x_insight.ddi.ingestion import build

    repo = Path(__file__).resolve().parents[3]
    src = repo / "project-documents/medical-documents/DDI-text/Antidiabetic Agents/Sitagliptin.txt"
    work = tmp_path / "real"
    work.mkdir()
    shutil.copyfile(src, work / src.name)
    data, report = build(
        work,
        vocabulary(
            [
                concept("sita", "Sitagliptin"),
                concept("oflo", "ofloxacin"),
            ]
        ),
    )
    oflo = [
        entry
        for doc in data.documents
        for entry in doc.entries
        if entry.interacting_name.lower() == "ofloxacin"
    ]
    assert len(oflo) == 2
    assert {entry.source_category for entry in oflo} == {"monitor_closely", "minor"}
    # Worked literals from the monograph, never implementation output.
    assert any("pharmacodynamic synergism" in entry.raw_text for entry in oflo)
    assert any("unspecified interaction mechanism" in entry.raw_text for entry in oflo)

    # Same both-categories pattern flows into checking on a synthetic release.
    from x_insight.ddi import checker

    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "Sitagliptin.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\nMonitor Closely (1)\n\n"
        "ofloxacin\nofloxacin increases effects of sitagliptin by "
        "pharmacodynamic synergism. Use Caution/Monitor.\n\n"
        "Minor (0)\nWarnings\n"
    )
    (sources / "Ofloxacin.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\nMonitor Closely (0)\n\n"
        "Minor (1)\n\nsitagliptin\nsitagliptin, ofloxacin. Mechanism: unspecified "
        "interaction mechanism. Minor/Significance Unknown.\nWarnings\n"
    )
    concepts = [
        concept("sita", "Sitagliptin", catalog="catalog_sitagliptin"),
        concept("oflo", "ofloxacin", catalog="catalog_ofloxacin"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Sitagliptin.txt", "Ofloxacin.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    from x_insight.ddi import publish

    published = publish.publish_release(staging, manifest, clean_ddi)
    checked = checker.check(
        clean_ddi, ["catalog_sitagliptin", "catalog_ofloxacin"], published["version"]
    )
    assert len(checked["pairs"]) == 1
    pair = checked["pairs"][0]
    assert pair["status"] == "interaction_found"
    assert pair["highest_known_severity"] == "monitor_closely"
    assert set(pair["conflicts"]) == {"monitor_closely", "minor"}
    assert len(pair["evidence"]) == 2


# --- Slice 3: explicit coverage vs unavailable (T4) ---


def _publish_limited_excluding_gamma(tmp_path, engine):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    (sources / "Gamma.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    concepts = [
        concept("a", "Alpha", catalog="catalog_alpha"),
        concept("b", "Beta", catalog="catalog_beta"),
        concept("g", "Gamma", catalog="catalog_gamma"),
    ]
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        decision="approved_limited",
        coverage={
            "scope": "limited",
            "excluded_sources": [
                {"path": "Gamma.txt", "reason": "structural count failure pending review"}
            ],
        },
        reviewed_evidence=_reviewed_for_staging(staging, included),
        limitations=[
            "Gamma.txt excluded (structural count failure); "
            "pairs without supported coverage remain coverage_unavailable"
        ],
    )
    return publish.publish_release(staging, manifest, engine)


def test_explicit_coverage_without_evidence_is_covered_no_listed(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(
        tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"], "Gamma": []}
    )
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_gamma"], result["version"])

    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert (pair["drug_a"], pair["drug_b"]) == ("catalog_alpha", "catalog_gamma")
    # Explicit complete review with no rows: covered, never "safe"/"no interaction".
    assert pair["status"] == "covered_no_listed_interaction"
    assert pair["evidence"] == []
    assert pair["highest_known_severity"] is None
    assert pair["has_unknown_severity"] is False
    assert pair["conflicts"] == []
    assert pair["coverage_basis"]["basis"] == "reviewed_complete_coverage"
    assert report["coverage_unavailable_medications"] == []
    assert "safe" not in json.dumps(report).lower()
    assert "no interaction" not in json.dumps(report).lower()


def test_catalog_pairs_lacking_coverage_are_unavailable(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_limited_excluding_gamma(tmp_path, clean_ddi)
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_gamma"], result["version"])

    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert pair["status"] == "coverage_unavailable"
    assert pair["evidence"] == []
    assert pair["coverage_basis"]["basis"] == "limited_coverage_unavailable"
    assert "coverage_unavailable" in json.dumps(report["limitations"]).lower()
    # Known pair in the limited release still resolves when evidence exists.
    known = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    assert known["pairs"][0]["status"] == "interaction_found"


def test_zero_and_one_drug_reports_retain_uncovered_warnings(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_limited_excluding_gamma(tmp_path, clean_ddi)

    empty = checker.check(clean_ddi, [], result["version"])
    assert empty["pairs"] == []
    assert empty["resolved_medications"] == []
    assert empty["limitations"] != []
    assert "coverage_unavailable" in json.dumps(empty["limitations"]).lower()

    single = checker.check(clean_ddi, ["catalog_gamma"], result["version"])
    assert single["pairs"] == []
    assert len(single["resolved_medications"]) == 1
    assert len(single["coverage_unavailable_medications"]) == 1
    assert single["coverage_unavailable_medications"][0]["catalog_drug_id"] == "catalog_gamma"

    complete = _publish_complete(tmp_path / "complete", clean_ddi, {"Solo": []})
    solo_single = checker.check(clean_ddi, ["catalog_solo"], complete["version"])
    assert solo_single["pairs"] == []
    assert solo_single["coverage_unavailable_medications"] == []


def test_unresolved_concepts_are_never_patient_free_text(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})
    # Unresolved ingestion names (e.g. "Mystery Entity") have no catalog ID.
    with pytest.raises(checker.CheckError) as excinfo:
        checker.check(clean_ddi, ["Mystery Entity"], result["version"])
    assert excinfo.value.status_code == 422
    with pytest.raises(checker.CheckError):
        checker.check(clean_ddi, ["catalog_alpha", "catalog_unknown"], result["version"])


# --- Slice 4 (T4 part): pinning, refusal, offline (T1 HTTP lands in test_ddi.py) ---


def test_report_pins_dataset_catalog_and_fingerprint(tmp_path, clean_ddi):
    from x_insight import contracts
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])

    assert report["dataset_version"] == result["version"]
    assert report["catalog_version"] == "test-s19/1"
    expected = contracts.canonical_hash(
        {"medications": ["alpha", "beta"], "dataset_version": result["version"]}
    )
    assert report["medication_fingerprint"] == expected
    assert len(report["medication_fingerprint"]) == 64
    assert report["generated_at"].endswith("Z")
    assert {row["catalog_drug_id"] for row in report["resolved_medications"]} == {
        "catalog_alpha",
        "catalog_beta",
    }


def test_refuses_invalid_dataset_and_configuration(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})

    with pytest.raises(checker.CheckError) as bad_format:
        checker.check(clean_ddi, ["catalog_alpha"], "not-a-version")
    assert bad_format.value.status_code == 422

    with pytest.raises(checker.CheckError) as missing:
        checker.check(clean_ddi, ["catalog_alpha"], "ddi-000000000000")
    assert missing.value.status_code == 404
    assert (
        checker.check(clean_ddi, ["catalog_alpha"], result["version"])["dataset_version"]
        == (result["version"])
    )


def test_refuses_malformed_medications_and_version_types(tmp_path, clean_ddi):
    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})
    version = result["version"]

    for bad_medications in (
        None,
        "catalog_alpha",
        {"drug": "catalog_alpha"},
        [None],
        [""],
        ["   "],
        [123],
    ):
        with pytest.raises(checker.CheckError) as excinfo:
            checker.check(clean_ddi, bad_medications, version)  # type: ignore[arg-type]
        assert excinfo.value.status_code == 422

    for bad_version in (None, "", 123, "Ddi-abcdefabcdef", "ddi-XYZ"):
        with pytest.raises(checker.CheckError) as excinfo:
            checker.check(clean_ddi, ["catalog_alpha"], bad_version)  # type: ignore[arg-type]
        assert excinfo.value.status_code == 422


def test_executes_with_external_network_disabled(tmp_path, clean_ddi, monkeypatch):
    import socket

    from x_insight.ddi import checker

    result = _publish_complete(tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]})

    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if host in ("localhost", "127.0.0.1", None):
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError("external network is disabled for deterministic checking")

    def _guarded_create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in ("localhost", "127.0.0.1"):
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError("external network is disabled for deterministic checking")

    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)
    report = checker.check(clean_ddi, ["catalog_alpha", "catalog_beta"], result["version"])
    assert report["pairs"][0]["status"] == "interaction_found"
