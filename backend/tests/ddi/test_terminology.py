"""S17 controlled terminology through the authorized T3 build seam."""

from pathlib import Path

from x_insight.ddi.ingestion import build


def concept(identifier, name, kind="ingredient", catalog=None):
    return {
        "id": identifier,
        "canonical_name": name,
        "concept_type": kind,
        "catalog_drug_id": catalog,
        "source": "synthetic T3 fixture",
    }


def vocabulary(concepts, aliases=None):
    return {"version": "test/1", "concepts": concepts, "aliases": aliases or []}


def monograph(root: Path, subject: str, names: list[str]):
    text = f"Interactions\n\nContraindicated (0)\n\nSerious ({len(names)})\n\n"
    text += "\n\n".join(
        f"{name}\n{name} increases the level of {subject} by mechanism. Avoid combination."
        for name in names
    )
    text += "\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    (root / f"{subject}.txt").write_text(text)


def test_canonical_case_and_whitespace_resolve_through_build(tmp_path):
    monograph(tmp_path, "Subject", ["  TARGET   drug  "])
    data, report = build(
        tmp_path,
        vocabulary(
            [
                concept("drug_subject", "Subject", catalog="catalog_subject"),
                concept("drug_target", "Target Drug"),
            ]
        ),
    )
    entry = data.documents[0].entries[0]
    assert entry.interacting_concept_id == "drug_target"
    assert data.documents[0].subject_concept_id == "drug_subject"
    assert report.terminology_version == "test/1"


def test_approved_aliases_resolve_pending_and_ambiguous_remain_unresolved(tmp_path):
    monograph(tmp_path, "Subject", ["BRAND", "Pending", "Collision"])
    aliases = [
        {
            "name": "Brand",
            "concept_id": "a",
            "source": "fixture",
            "review": {
                "decision": "approved",
                "reviewer": "owner",
                "date": "2026-10-06",
                "record": "fixture approval",
            },
        },
        {
            "name": "Pending",
            "concept_id": "a",
            "source": "fixture",
            "review": {"decision": "pending"},
        },
        {
            "name": "Collision",
            "concept_id": "a",
            "source": "fixture",
            "review": {
                "decision": "approved",
                "reviewer": "owner",
                "date": "2026-10-06",
                "record": "fixture approval",
            },
        },
    ]
    data, report = build(
        tmp_path,
        vocabulary(
            [concept("subject", "Subject"), concept("a", "Target"), concept("b", "Collision")],
            aliases,
        ),
    )
    assert [e.interacting_concept_id for e in data.documents[0].entries] == ["a", None, None]
    assert report.alias_collisions == {"collision": ("a", "b")}
    assert report.pending_reviews[0]["name"] == "Pending"
    assert data.aliases[0].normalized_alias == "brand"
    assert data.aliases[0].review["decision"] == "approved"


def test_unknown_lookalikes_salts_strengths_combinations_are_not_guessed(tmp_path):
    monograph(
        tmp_path,
        "Subject",
        [
            "Hydralazine",
            "Hydroxyzine",
            "Target hydrochloride",
            "Target 5mg",
            "Target/Other",
            "Unknown",
        ],
    )
    data, report = build(
        tmp_path,
        vocabulary(
            [
                concept("s", "Subject"),
                concept("h1", "Hydralazine"),
                concept("h2", "Hydroxyzine"),
                concept("t", "Target"),
            ]
        ),
    )
    assert [e.interacting_concept_id for e in data.documents[0].entries] == [
        "h1",
        "h2",
        None,
        None,
        None,
        None,
    ]
    assert {r["name"] for r in report.unresolved_names} == {
        "Target hydrochloride",
        "Target 5mg",
        "Target/Other",
        "Unknown",
    }
    assert not report.terminology_complete


def test_unordered_pair_identity_preserves_source_order_and_direction(tmp_path):
    monograph(tmp_path, "Alpha", ["Beta"])
    monograph(tmp_path, "Beta", ["Alpha"])
    data, report = build(tmp_path, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
    first, second = [doc.entries[0] for doc in data.documents]
    assert first.pair_concept_ids == second.pair_concept_ids == ("a", "b")
    assert (first.source_subject_name, first.interacting_name) == ("Alpha", "Beta")
    assert (second.source_subject_name, second.interacting_name) == ("Beta", "Alpha")
    assert (first.direction_subject_id, first.direction_object_id) == ("b", "a")
    assert (second.direction_subject_id, second.direction_object_id) == ("a", "b")
    assert data.terminology_version == report.terminology_version == "test/1"


def test_explicit_monograph_subject_wins_over_misleading_filename(tmp_path):
    monograph(tmp_path, "Psyllium", ["Target"])
    path = tmp_path / "Psyllium.txt"
    path.write_text("senna/psyllium (OTC)\n" + path.read_text())
    data, report = build(
        tmp_path,
        vocabulary(
            [
                concept("p", "Psyllium"),
                concept("sp", "senna/psyllium", "combination_drug"),
                concept("t", "Target"),
            ]
        ),
    )
    assert data.documents[0].subject_concept_id == "sp"
    assert data.documents[0].entries[0].source_subject_name == "senna/psyllium"
    assert data.documents[0].entries[0].direction == "unknown"


def test_supplied_psyllium_source_retains_combination_identity(tmp_path):
    import shutil

    source = (
        Path(__file__).resolve().parents[3]
        / "project-documents/medical-documents"
        / "DDI-text/Gastrointestinal Medications/Psyllium.txt"
    )
    shutil.copyfile(source, tmp_path / source.name)
    data, _ = build(
        tmp_path,
        vocabulary([concept("p", "Psyllium"), concept("sp", "senna/psyllium", "combination_drug")]),
    )
    assert data.documents[0].subject_concept_id == "sp"
    assert data.documents[0].subject_name == "senna/psyllium"


def test_invalid_schema_and_requested_missing_configuration_fail(tmp_path):
    import pytest

    monograph(tmp_path, "Subject", ["Target"])
    with pytest.raises(ValueError):
        build(tmp_path, vocabulary([concept("same", "Subject"), concept("same", "Target")]))
    with pytest.raises(FileNotFoundError):
        build(tmp_path, tmp_path / "missing.json")
    with pytest.raises(ValueError):
        build(tmp_path, vocabulary([concept("s", "Subject", "food", "catalog_food")]))


def test_bundled_draft_preserves_pending_source_backed_brand_reviews(tmp_path):
    import shutil

    repo = Path(__file__).resolve().parents[3]
    source = (
        repo
        / "project-documents/medical-documents"
        / "DDI-text/Antidiabetic Agents/Sitagliptin.txt"
    )
    shutil.copyfile(source, tmp_path / source.name)
    data, report = build(tmp_path, repo / "content/ddi/aliases.json")
    assert data.documents[0].subject_concept_id == "drug_sitagliptin"
    assert any(
        row["name"] == "Januvia" and row["review"]["decision"] == "pending"
        for row in report.pending_reviews
    )
    assert report.unresolved_names
    assert not report.terminology_complete


def test_opposing_explicit_directions_do_not_choose_first(tmp_path):
    monograph(tmp_path, "Alpha", ["Beta"])
    path = tmp_path / "Alpha.txt"
    path.write_text(
        path.read_text().replace(
            "Avoid combination.", "Alpha increases the level of Beta by another mechanism."
        )
    )
    data, _ = build(tmp_path, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
    entry = data.documents[0].entries[0]
    assert entry.direction == "unknown"
    assert entry.direction_subject_id is None and entry.direction_object_id is None


def test_collision_resolution_is_independent_of_concept_and_alias_order(tmp_path):
    monograph(tmp_path, "Subject", ["Canonical Clash", "Alias Clash", "Rejected"])
    concepts = [
        concept("s", "Subject"),
        concept("a", "Canonical Clash"),
        concept("b", " CANONICAL  clash "),
    ]
    approval = {
        "decision": "approved",
        "reviewer": "owner",
        "date": "2026-10-06",
        "record": "synthetic fixture approval",
    }
    aliases = [
        {"name": "Alias Clash", "concept_id": identifier, "source": "fixture", "review": approval}
        for identifier in ("a", "b")
    ] + [
        {
            "name": "Rejected",
            "concept_id": "a",
            "source": "fixture",
            "review": {"decision": "rejected"},
        }
    ]
    for rows, names in ((concepts, aliases), (concepts[::-1], aliases[::-1])):
        data, report = build(tmp_path, vocabulary(rows, names))
        assert [entry.interacting_concept_id for entry in data.documents[0].entries] == [
            None,
            None,
            None,
        ]
        assert report.alias_collisions == {
            "alias clash": ("a", "b"),
            "canonical clash": ("a", "b"),
        }
        assert len(report.unresolved_names) == 3


def test_all_entity_types_resolve_but_only_drugs_are_catalog_eligible(tmp_path):
    import pytest

    kinds = ["ingredient", "combination_drug", "herbal", "food", "substance", "other"]
    monograph(tmp_path, "Subject", kinds)
    concepts = [concept("s", "Subject")] + [concept(kind, kind, kind) for kind in kinds]
    data, _ = build(tmp_path, vocabulary(concepts))
    assert [entry.interacting_concept_id for entry in data.documents[0].entries] == kinds
    for kind in kinds:
        rows = [concept("s", "Subject"), concept(kind, kind, kind, "catalog_entity")]
        if kind in {"ingredient", "combination_drug"}:
            candidate, _ = build(tmp_path, vocabulary(rows))
            assert candidate.concepts[1].catalog_available
        else:
            with pytest.raises(ValueError, match="drug-only"):
                build(tmp_path, vocabulary(rows))


def test_unresolved_occurrences_keep_nested_source_hash_and_original_span(tmp_path):
    import hashlib

    sources = tmp_path / "group"
    sources.mkdir()
    monograph(sources, "Unknown Subject", ["Unknown Target", "Other Unknown Target"])
    path = sources / "Unknown Subject.txt"
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    data, report = build(tmp_path, vocabulary([concept("known", "Known")]))
    assert len(data.documents[0].entries) == 2
    subject, first, second = report.unresolved_names
    assert subject == {
        "name": "Unknown Subject",
        "role": "subject",
        "source_path": "group/Unknown Subject.txt",
        "checksum": checksum,
    }
    assert [row["span_start"] for row in (first, second)] == [7, 10]
    assert [row["span_end"] for row in (first, second)] == [8, 11]
    for row in (first, second):
        assert row["source_path"] == "group/Unknown Subject.txt"
        assert row["checksum"] == checksum
        assert row["role"] == "object"
    assert not report.terminology_complete


def test_unreviewed_or_dangling_approved_aliases_cannot_enter_build(tmp_path):
    import pytest

    monograph(tmp_path, "Subject", ["Brand"])
    for target, review in (
        ("s", {"decision": "approved"}),
        (
            "s",
            {"decision": "approved", "reviewer": "agent", "date": "2026-10-06", "record": "draft"},
        ),
        (
            "absent",
            {
                "decision": "approved",
                "reviewer": "owner",
                "date": "2026-10-06",
                "record": "fixture",
            },
        ),
    ):
        aliases = [{"name": "Brand", "concept_id": target, "source": "fixture", "review": review}]
        with pytest.raises(ValueError):
            build(tmp_path, vocabulary([concept("s", "Subject")], aliases))


def test_terminology_cli_matches_library_even_when_source_counts_fail(tmp_path):
    import json
    import subprocess
    import sys
    from dataclasses import asdict

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target", "Unknown"])
    path = sources / "Subject.txt"
    path.write_text(path.read_text().replace("Serious (2)", "Serious (3)"))
    config = tmp_path / "aliases.json"
    config.write_text(json.dumps(vocabulary([concept("s", "Subject"), concept("t", "Target")])))
    output = tmp_path / "output"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "x_insight.ddi",
            "build",
            "--sources",
            str(sources),
            "--terminology",
            str(config),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    data, report = build(sources, config)
    assert completed.returncode == 1
    assert json.loads((output / "candidate_dataset.json").read_text()) == json.loads(
        json.dumps(asdict(data))
    )
    assert json.loads((output / "report.json").read_text()) == json.loads(
        json.dumps(asdict(report))
    )
    assert report.documents[0].anomalies
    assert not report.terminology_complete
    assert "coverage_complete" not in (output / "report.json").read_text()


def test_cli_requested_missing_or_invalid_terminology_fails_with_report(tmp_path):
    import json
    import subprocess
    import sys

    sources = tmp_path / "sources"
    sources.mkdir()
    monograph(sources, "Subject", ["Target"])
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"version": "invalid"}')
    for config in (missing, invalid):
        output = tmp_path / config.stem
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "x_insight.ddi",
                "build",
                "--sources",
                str(sources),
                "--terminology",
                str(config),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 1
        report = json.loads((output / "report.json").read_text())
        assert report["passed"] is False
        assert report["anomalies"] and "build failed" in completed.stderr
        assert not (output / "candidate_dataset.json").exists()


def test_bundled_pending_alias_provenance_contains_each_proposed_name(tmp_path):
    import hashlib
    import re

    repo = Path(__file__).resolve().parents[3]
    monograph(tmp_path, "Subject", ["Unknown"])
    _, report = build(tmp_path, repo / "content/ddi/aliases.json")
    for row in report.pending_reviews:
        match = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?; sha256=([0-9a-f]{64})", row["source"])
        assert match is not None, row["source"]
        source_path, start, end, checksum = match.groups()
        raw = (repo / source_path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == checksum
        lines = raw.decode("utf-8-sig").splitlines()
        span = " ".join(lines[int(start) - 1 : int(end or start)])
        assert row["name"].casefold() in " ".join(span.casefold().split()), row["name"]
        assert row["review"] == {"decision": "pending"}


def test_resolved_pair_keeps_independent_source_severities_and_directions(tmp_path):
    monograph(tmp_path, "Alpha", ["Beta"])
    monograph(tmp_path, "Beta", ["Alpha"])
    path = tmp_path / "Beta.txt"
    path.write_text(
        path.read_text()
        .replace("Serious (1)", "Serious (0)\n\nMonitor Closely (1)")
        .replace("\n\nMonitor Closely (0)", "")
    )
    data, report = build(tmp_path, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
    assert report.passed
    assertions = [entry for doc in data.documents for entry in doc.entries]
    assert [entry.pair_concept_ids for entry in assertions] == [("a", "b"), ("a", "b")]
    assert [entry.source_category for entry in assertions] == ["serious", "monitor_closely"]
    assert [(entry.direction_subject_id, entry.direction_object_id) for entry in assertions] == [
        ("b", "a"),
        ("a", "b"),
    ]


def test_direction_requires_the_complete_named_endpoint(tmp_path):
    monograph(tmp_path, "Alpha", ["Beta"])
    path = tmp_path / "Alpha.txt"
    original = path.read_text()
    for endpoint in ("Alpha/Other", "Alpha hydrochloride", "Alpha 5mg", "Alpha XR"):
        path.write_text(original.replace("of Alpha by mechanism", f"of {endpoint} by mechanism"))
        data, _ = build(tmp_path, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
        entry = data.documents[0].entries[0]
        assert entry.pair_concept_ids == ("a", "b")
        assert entry.direction == "unknown", endpoint
        assert entry.direction_subject_id is None and entry.direction_object_id is None
    for actor in ("Other/Beta", "Other + Beta", "Beta hydrochloride", "Beta 5mg"):
        path.write_text(original.replace("Beta increases", f"{actor} increases"))
        data, _ = build(tmp_path, vocabulary([concept("a", "Alpha"), concept("b", "Beta")]))
        entry = data.documents[0].entries[0]
        assert entry.direction == "unknown", actor
        assert entry.direction_subject_id is None and entry.direction_object_id is None
