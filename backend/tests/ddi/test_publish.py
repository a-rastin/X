"""S18 publish through the public T3 build/publish seam (FR-14, NFR-05).

Slices through ``x_insight.ddi.publish`` + ``python -m x_insight.ddi publish``
only — never private helpers, never SQL-row assertions beyond fixture setup.
Behavior is observed via build output, manifest validation, corpus reports,
and release reads (``list_releases``/``get_release``); SQL is fixture setup
(TRUNCATE) only. Synthetic monographs only; the real 128-file corpus stays
``awaiting_review`` (0 approved aliases + 26 structural failures) and is never
claimed ready here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from x_insight.ddi.ingestion import build


def concept(identifier, name, kind="ingredient", catalog=None):
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": kind,
        "catalog_drug_id": catalog,
        "source": "synthetic S18 fixture",
    }


def vocabulary(concepts, aliases=None):
    return {"version": "test-s18/1", "concepts": concepts, "aliases": aliases or []}


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
        "source": "synthetic S18 fixture",
        "review": {
            "decision": "approved",
            "reviewer": "owner",
            "date": "2026-10-06",
            "record": "synthetic fixture approval",
        },
    }


def _stage(source_dir: Path, staging: Path, terminology=None):
    from dataclasses import asdict

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
    """Manifest pinned to a staging dir's real versions/checksums."""
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


# --- Slice 1: publication rejects unreleasable staging (T3) ---


def test_publish_rejects_count_failures(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    # Corrupt the staged source so declared (1) != parsed (0): drop entry body.
    path = sources / "Subject.txt"
    path.write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(staging, tmp_path / "manifest.json")

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_unresolved_entities_needed_by_release(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Unknown Entity"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject")]))
    manifest = _manifest_for_staging(staging, tmp_path / "manifest.json")

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_absent_provenance(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(
        sources,
        staging,
        vocabulary([concept("s", "Subject"), concept("t", "Target")]),
    )
    manifest = _manifest_for_staging(staging, tmp_path / "manifest.json", reviewer="someone-else")

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_required_unreviewed_evidence(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(
        sources,
        staging,
        vocabulary([concept("s", "Subject"), concept("t", "Target")]),
    )
    # Serious entry present but no reviewed_evidence record approves it.
    manifest = _manifest_for_staging(staging, tmp_path / "manifest.json", reviewed_evidence=[])

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_cli_rejects_failing_staging_with_nonzero_exit(tmp_path, clean_ddi, monkeypatch):
    from x_insight.ddi.__main__ import main

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    path = sources / "Subject.txt"
    path.write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(staging, tmp_path / "manifest.json")
    monkeypatch.setenv("DATABASE_URL", clean_ddi.url.render_as_string(hide_password=False))

    code = main(["publish", "--staging", str(staging), "--manifest", str(manifest)])
    assert code != 0
    from x_insight.ddi import publish

    assert publish.list_releases(clean_ddi) == []


# --- Slice 2: corpus report for owner review (T3) ---


def test_corpus_report_drafts_counts_hashes_and_parser_version(tmp_path):
    from dataclasses import asdict

    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    dataset, report = build(sources, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))

    corpus = publish.build_corpus_report(asdict(dataset), asdict(report))

    assert corpus["discovered"] == 2
    assert corpus["processed"] == 2
    assert corpus["passed"] == 2
    assert corpus["failed"] == 0
    assert corpus["severity_counts"] == {"serious": 2}
    assert corpus["unique_pairs"] == 1
    assert corpus["duplicates"] == 1
    assert corpus["conflicts"] == []
    assert corpus["unknown_names"] == []
    assert corpus["parser_version"] == "ddi-ingestion/0.1.0"
    assert corpus["terminology_version"] == "test-s18/1"
    assert len(corpus["hashes"]["documents"]) == 2
    assert all(row["checksum"] for row in corpus["hashes"]["documents"])


def test_corpus_report_gives_owner_review_records_with_spans(tmp_path):
    from dataclasses import asdict

    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Known", "Mystery"])
    dataset, report = build(sources, vocabulary([concept("s", "Subject")]))

    corpus = publish.build_corpus_report(asdict(dataset), asdict(report))

    high_risk = corpus["review_records"]["high_risk"]
    assert len(high_risk) == 2
    assert {row["interacting_name"] for row in high_risk} == {"Known", "Mystery"}
    assert all(row["span_start"] and row["span_end"] for row in high_risk)
    assert {row["name"] for row in corpus["review_records"]["unresolved"]} >= {"Mystery"}
    # Conflicting severities surface as review records, never silent resolution.
    assert corpus["review_records"]["conflicts"] == corpus["conflicts"]


# --- Slice 3: approved corrections + limited coverage, originals immutable (T3) ---


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


def test_approved_limited_release_keeps_overlay_and_excludes_uncovered(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    (sources / "Gamma.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    staging = tmp_path / "staging"
    _stage(
        sources,
        staging,
        vocabulary([concept("a", "Alpha"), concept("b", "Beta"), concept("g", "Gamma")]),
    )
    included = ["Alpha.txt", "Beta.txt"]
    reviewed = _reviewed_for_staging(staging, included)
    assert len(reviewed) == 2
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    alpha_entry = dataset["documents"][0]["entries"][0]
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
        reviewed_evidence=reviewed,
        corrections=[
            {
                "source_path": alpha_entry["source_path"],
                "span_start": alpha_entry["span_start"],
                "span_end": alpha_entry["span_end"],
                "field": "management_note",
                "corrected_value": "owner-confirmed management wording",
                "reason": "synthetic owner correction",
            }
        ],
        limitations=[
            "Gamma.txt excluded (structural count failure); "
            "pairs without supported coverage remain coverage_unavailable"
        ],
    )

    result = publish.publish_release(staging, manifest, clean_ddi)

    assert result["version"].startswith("ddi-")
    assert result["reused"] is False
    release = publish.get_release(clean_ddi, result["version"])
    assert release is not None
    assert release["status"] == "released_limited"
    assert [d["source_path"] for d in release["documents"]] == included
    assert "Gamma.txt" in json.dumps(release["limitations"])
    assert "coverage_unavailable" in json.dumps(release["limitations"])
    rows = publish.get_evidence(clean_ddi, result["version"])
    assert len(rows) == 2
    overlaid = [r for r in rows if r["correction"] is not None]
    assert len(overlaid) == 1
    # Originals immutable: raw text keeps the source wording, overlay is separate.
    assert "Avoid combination" in overlaid[0]["raw_text"]
    assert overlaid[0]["correction"]["corrected_value"] == "owner-confirmed management wording"


def test_duplicate_directions_preserved_and_severity_separate(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Alpha", ["Beta"])
    monograph(sources, "Beta", ["Alpha"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
    included = ["Alpha.txt", "Beta.txt"]
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )

    result = publish.publish_release(staging, manifest, clean_ddi)
    rows = publish.get_evidence(clean_ddi, result["version"])

    assert len(rows) == 2
    assert {r["pair_key"] for r in rows} == {"a|b"}
    assert {r["source_path"] for r in rows} == {"Alpha.txt", "Beta.txt"}
    # Source severity stays verbatim; management prose stays in raw_text, never inferred.
    assert {r["source_severity"] for r in rows} == {"serious"}
    assert all("Avoid combination" in r["raw_text"] for r in rows)


def test_awaiting_review_manifest_is_never_imported(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(
        staging, tmp_path / "manifest.json", decision="awaiting_review"
    )

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


# --- Slice 4: idempotent, versioned, transactional publish (T3) ---


def _publish_complete(tmp_path, clean_ddi, subjects):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    for subject, names in subjects.items():
        monograph(sources, subject, names)
    concepts = [concept(s.lower(), s) for s in subjects]
    for names in subjects.values():
        for name in names:
            if name.lower() not in {c["id"] for c in concepts}:
                concepts.append(concept(name.lower(), name))
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary(concepts))
    included = sorted(f"{subject}.txt" for subject in subjects)
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, included),
    )
    return publish.publish_release(staging, manifest, clean_ddi), staging, manifest


def test_repeated_publish_of_identical_content_is_idempotent(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    result, staging, manifest = _publish_complete(
        tmp_path, clean_ddi, {"Alpha": ["Beta"], "Beta": ["Alpha"]}
    )
    repeat = publish.publish_release(staging, manifest, clean_ddi)

    assert repeat["version"] == result["version"]
    assert repeat["content_hash"] == result["content_hash"]
    assert repeat["reused"] is True
    assert len(publish.list_releases(clean_ddi)) == 1
    assert len(publish.get_evidence(clean_ddi, result["version"])) == 2


def test_changed_source_creates_a_new_candidate_version(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    first, _, _ = _publish_complete(tmp_path / "first", clean_ddi, {"Alpha": ["Beta"]})
    second, _, _ = _publish_complete(tmp_path / "second", clean_ddi, {"Alpha": ["Beta", "Delta"]})

    assert second["version"] != first["version"]
    assert second["content_hash"] != first["content_hash"]
    versions = {r["version"] for r in publish.list_releases(clean_ddi)}
    assert {first["version"], second["version"]} == versions
    assert publish.get_release(clean_ddi, first["version"]) is not None
    assert publish.get_release(clean_ddi, second["version"]) is not None


def test_invalid_publish_leaves_prior_release_available(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    good, _, _ = _publish_complete(tmp_path / "good", clean_ddi, {"Alpha": ["Beta"]})
    sources = tmp_path / "bad" / "sources"
    sources.mkdir(parents=True)
    monograph(sources, "Alpha", ["Beta"])
    (sources / "Alpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\n"
        "Monitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    bad_staging = tmp_path / "bad" / "staging"
    _stage(
        sources,
        bad_staging,
        vocabulary([concept("a", "Alpha"), concept("b", "Beta")]),
    )
    bad_manifest = _manifest_for_staging(bad_staging, tmp_path / "bad-manifest.json")

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(bad_staging, bad_manifest, clean_ddi)

    assert [r["version"] for r in publish.list_releases(clean_ddi)] == [good["version"]]
    assert publish.get_release(clean_ddi, good["version"]) is not None
    assert len(publish.get_evidence(clean_ddi, good["version"])) == 1


# --- Slice 5: CLI success, manifest shape fuzz, collision, fail-safe (T3) ---


def test_publish_cli_success_path_exit_0_against_test_db(tmp_path, clean_ddi, monkeypatch):
    from x_insight.ddi import publish
    from x_insight.ddi.__main__ import main

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, ["Subject.txt"]),
    )
    monkeypatch.setenv("DATABASE_URL", clean_ddi.url.render_as_string(hide_password=False))

    code = main(["publish", "--staging", str(staging), "--manifest", str(manifest)])

    assert code == 0
    releases = publish.list_releases(clean_ddi)
    assert len(releases) == 1
    release = publish.get_release(clean_ddi, releases[0]["version"])
    assert release is not None
    assert release["status"] == "released_complete"
    rows = publish.get_evidence(clean_ddi, releases[0]["version"])
    assert len(rows) == 1
    assert rows[0]["source_severity"] == "serious"


def test_publish_rejects_bad_manifest_date(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    report = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    manifest_path = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        date="not-a-date",
        reviewed_evidence=_reviewed_for_staging(staging, ["Subject.txt"]),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with pytest.raises(publish.PublishRejected):
        publish.validate_for_publish(dataset, report, manifest)
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest_path, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_scope_decision_mismatch(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        decision="approved_complete",
        coverage={
            "scope": "limited",
            "excluded_sources": [{"path": "Subject.txt", "reason": "synthetic exclusion"}],
        },
        reviewed_evidence=_reviewed_for_staging(staging, ["Subject.txt"]),
        limitations=["synthetic limited-coverage limitation"],
    )

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_correction_referencing_non_span(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, ["Subject.txt"]),
        corrections=[
            {
                "source_path": "Subject.txt",
                "span_start": 9999,
                "span_end": 9999,
                "field": "management_note",
                "corrected_value": "synthetic correction",
                "reason": "synthetic owner correction",
            }
        ],
    )

    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_rejects_collision_touching_included(tmp_path, clean_ddi):
    from x_insight.ddi import publish

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Alpha", ["Beta"])
    staging = tmp_path / "staging"
    _stage(
        sources,
        staging,
        vocabulary(
            [concept("a", "Alpha"), concept("b", "Beta")],
            aliases=[
                {
                    "name": "Beta",
                    "concept_id": "a",
                    "source": "synthetic S18 fixture",
                    "review": {"decision": "pending"},
                }
            ],
        ),
    )
    dataset = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    report = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    assert report["alias_collisions"] != {}
    assert report["unresolved_names"] == []
    manifest_path = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, ["Alpha.txt"]),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with pytest.raises(publish.PublishRejected):
        publish.validate_for_publish(dataset, report, manifest)
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(staging, manifest_path, clean_ddi)
    assert publish.list_releases(clean_ddi) == []


def test_publish_cli_missing_table_fails_safe_via_database_url(tmp_path, clean_ddi):
    from x_insight.ddi import publish
    from x_insight.ddi.__main__ import main

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    staging = tmp_path / "staging"
    _stage(sources, staging, vocabulary([concept("s", "Subject"), concept("t", "Target")]))
    manifest = _manifest_for_staging(
        staging,
        tmp_path / "manifest.json",
        reviewed_evidence=_reviewed_for_staging(staging, ["Subject.txt"]),
    )
    empty_url = clean_ddi.url.set(database="postgres").render_as_string(hide_password=False)

    code = main(
        [
            "publish",
            "--staging",
            str(staging),
            "--manifest",
            str(manifest),
            "--database-url",
            empty_url,
        ]
    )

    assert code != 0
    assert publish.list_releases(clean_ddi) == []
