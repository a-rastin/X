import json
from pathlib import Path

import pytest
from sqlalchemy import event, text

from tests.ddi.test_ddinter_release import fixture_approval, stage
from x_insight.ddi import checker, lexicomp, publish

pytest_plugins = ["tests.ddi.test_publish"]
ROOT = Path(__file__).resolve().parents[3] / "content"


def test_source_validation_and_model_mapping():
    directory = ROOT / "ddi/lexicomp/v1"
    manifest = json.loads((directory / "manifest.json").read_text())
    raw = (directory / manifest["source_file"]).read_bytes()
    names, records = lexicomp.parse_source(
        raw, manifest, ROOT / "new-case/v1/oral-antipsychotic.xml"
    )
    assert len(names) == 1632
    assert len(records) == 127756
    with pytest.raises(ValueError, match="checksum"):
        lexicomp.parse_source(raw + b"x", manifest, ROOT / "new-case/v1/oral-antipsychotic.xml")


def test_install_and_lookup(clean_ddi, tmp_path):
    engine = clean_ddi
    staging, _, _ = stage(tmp_path)
    legacy = publish.publish_release(staging, fixture_approval(staging), engine)["version"]
    result = lexicomp.install(engine, ROOT)
    assert lexicomp.install(engine, ROOT)["reused"]
    assert result["version"] == lexicomp.current_version()
    assert checker.latest_release_version(engine) == legacy
    legacy_pair = checker.check(engine, ["DDInter1", "DDInter2"], legacy)["pairs"][0]
    assert legacy_pair["highest_known_severity"] == "Major"
    version = result["version"]
    for first, second, rating in [
        ("Epinephrine", "Aripiprazole", "A"),
        ("Darbepoetin alfa", "Lisinopril", "B"),
        ("Aripiprazole", "Ticagrelor", "C"),
        ("Oxytocin", "Ziprasidone", "D"),
        ("Clozapine", "Potassium chloride", "X"),
    ]:
        ids = [lexicomp.drug_id(first), lexicomp.drug_id(second)]
        pair = checker.check(engine, ids, version)["pairs"][0]
        assert pair["risk_rating"] == rating
        assert pair["coverage_basis"]["basis"] == "explicit_row"
        assert pair["evidence"][0]["raw_text"]
        assert pair == checker.check(engine, ids[::-1], version)["pairs"][0]
    with pytest.raises(checker.CheckError):
        checker.check(engine, ["unknown"], version)
    assert not checker.check(engine, [ids[0], ids[0]], version)["pairs"]
    candidates = [
        lexicomp.drug_id(name)
        for name in ["Aripiprazole", "Clozapine", "Ziprasidone", "Oxytocin", "Ticagrelor"]
    ]
    current = [lexicomp.drug_id("Epinephrine")]
    assert len(checker.check_candidates(engine, candidates, current, version)["pairs"]) == 5
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM ddi_source_documents "
                    "WHERE source_path='Lexicomp-cleaned.csv' AND source_bytes IS NOT NULL"
                )
            ).scalar_one()
            == 1
        )
        absent = connection.execute(
            text("""
            SELECT a.catalog_drug_id, b.catalog_drug_id FROM ddi_concepts a
            JOIN ddi_concepts b ON a.release_id=b.release_id AND a.concept_id < b.concept_id
            WHERE a.release_id=(SELECT id FROM ddi_dataset_releases WHERE version=:version)
            AND NOT EXISTS (SELECT 1 FROM ddi_interaction_evidence e
                WHERE e.release_id=a.release_id AND e.pair_key=a.concept_id || '|' || b.concept_id)
            LIMIT 1
            """),
            {"version": version},
        ).first()
    pair = checker.check(engine, list(absent), version)["pairs"][0]
    assert pair["risk_rating"] is None and pair["status"] == "coverage_unavailable"
    assert pair["evidence"] == []


def test_failed_import_rolls_back(clean_ddi, monkeypatch):
    original = lexicomp.install(clean_ddi, ROOT)
    original_hash = lexicomp.canonical_hash
    monkeypatch.setattr(lexicomp, "canonical_hash", lambda value: original_hash({"changed": value}))

    def fail(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "INSERT INTO ddi_interaction_evidence" in statement:
            raise RuntimeError("simulated failure")

    event.listen(clean_ddi, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            lexicomp.install(clean_ddi, ROOT)
    finally:
        event.remove(clean_ddi, "before_cursor_execute", fail)
    with clean_ddi.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM ddi_dataset_releases")).scalar_one() == 1
        )
    assert checker.get_release_row(clean_ddi, original["version"])["version"] == original["version"]
