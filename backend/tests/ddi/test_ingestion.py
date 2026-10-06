"""DDI ingestion through the public build seam (S15, seam T3, FR-14/NFR-05).

Slices through ``x_insight.ddi.ingestion.build`` only — the private
preprocessor is never tested directly:
1. Fixture provenance + monograph discovery (this slice).
2. Declared/parsed counts 0/4/92/70 with traceable spans, chrome-free text.
3. Actual interaction section identified; nav headings ignored; stop marker.
4. Duplicate-pair assertions preserved (ofloxacin in two categories).
5. Removed entry fails count validation but still yields an anomaly report.
6. CLI agrees with the library (exit codes + written staging files).

Provenance (originals never modified; copies verified by hash):
- Real source: ``project-documents/medical-documents/DDI-text/``
  ``Antidiabetic Agents/Sitagliptin.txt``. tasks.md S15 names it
  ``docs/medical-docs/DDI-text/...`` — that path does not exist; the
  ``project-documents/`` prefix above is the verified actual location.
- sha256 ``e7c9bc45ed5b3f829dfe8e29b015ee727645db2f3ed6cad8de99fea2dcd4022f``,
  1264 lines per ``wc -l`` (utf-8 BOM present), 1265 decoded lines.
- Real category headings: ``Contraindicated (0)``, ``Serious (4)``,
  ``Monitor Closely (92)``, ``Minor (70)`` — expected counts below are
  these monograph-declared literals, never the parser's own totals.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from x_insight.ddi.ingestion import build

# --- Fixture provenance (recorded from the monograph, not parser output) ---

SOURCE_RELATIVE = Path(
    "project-documents/medical-documents/DDI-text/Antidiabetic Agents/Sitagliptin.txt"
)
SOURCE_SHA256 = "e7c9bc45ed5b3f829dfe8e29b015ee727645db2f3ed6cad8de99fea2dcd4022f"
SOURCE_FILENAME = "Sitagliptin.txt"

# Monograph-declared category counts, read off the ``Name (N)`` headings.
EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 4,
    "monitor_closely": 92,
    "minor": 70,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _stage_sitagliptin(tmp_path: Path) -> Path:
    """Copy the untouched original into an isolated source dir, verifying hash."""
    original = _repo_root() / SOURCE_RELATIVE
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest == SOURCE_SHA256, f"original moved: {digest}"
    staged = tmp_path / SOURCE_FILENAME
    shutil.copyfile(original, staged)
    return tmp_path


# --- Slice 1: discovery + provenance ---


def test_build_discovers_monograph_with_provenance(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)

    dataset, report = build(source_dir, None)

    assert [d.source_path for d in dataset.documents] == [SOURCE_FILENAME]
    assert dataset.documents[0].checksum == SOURCE_SHA256
    assert report.documents[0].expected_counts == EXPECTED_COUNTS


# --- Slice 2: exact counts, traceable spans, chrome-free text ---


def test_build_parses_declared_counts_with_spans(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    assert report.documents[0].parsed_counts == EXPECTED_COUNTS
    by_category: dict[str, list] = {key: [] for key in EXPECTED_COUNTS}
    for entry in dataset.documents[0].entries:
        by_category.setdefault(entry.source_category, []).append(entry)
    assert {k: len(v) for k, v in by_category.items()} == EXPECTED_COUNTS

    serious = {e.interacting_name: e for e in by_category["serious"]}
    assert set(serious) == {"erdafitinib", "ethanol", "sotorasib", "tepotinib"}
    erdafitinib = serious["erdafitinib"]
    assert (erdafitinib.span_start, erdafitinib.span_end) == (101, 105)
    assert erdafitinib.raw_text == (
        "erdafitinib will increase the level or effect of sitagliptin by "
        "P-glycoprotein (MDR1) efflux transporter. Avoid or Use Alternate Drug. "
        "If coadministration unavoidable, separate administration by at least 6 hr "
        "before or after administration of P-gp substrates with narrow "
        "therapeutic index."
    )


def test_entries_keep_raw_text_and_valid_spans(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)

    dataset, _ = build(source_dir, None)
    entries = dataset.documents[0].entries

    assert len(entries) == sum(EXPECTED_COUNTS.values())
    for entry in entries:
        assert entry.raw_text
        assert "medscape.com" not in entry.raw_text
        assert "5:17 PM" not in entry.raw_text
        assert entry.span_start <= entry.span_end
        assert entry.span_end < 1005  # stops before Adverse Effects (line 1005)

    by_name = {(e.source_category, e.interacting_name): e for e in entries}
    # Page-break joins preserve the full span across removed chrome lines.
    assert (by_name[("monitor_closely", "levofloxacin")].span_start) == 442
    assert (by_name[("monitor_closely", "levofloxacin")].span_end) == 453
    assert (by_name[("minor", "oxandrolone")].span_start) == 916
    assert (by_name[("minor", "oxandrolone")].span_end) == 925
    monitor_ofloxacin = by_name[("monitor_closely", "ofloxacin")]
    assert (monitor_ofloxacin.span_start, monitor_ofloxacin.span_end) == (525, 529)
    minor_ofloxacin = by_name[("minor", "ofloxacin")]
    assert (minor_ofloxacin.span_start, minor_ofloxacin.span_end) == (912, 914)


# --- Slice 3: real interaction section, dual assertions, count failure ---


def test_build_uses_actual_interaction_section(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    entries = dataset.documents[0].entries
    names = {e.interacting_name for e in entries}
    # Earlier navigation/summary headings carry no counts and yield no entries.
    assert "Significant - Monitor Closely" not in names
    assert "No Interactions Found" not in names
    assert "All Interactions Sort By: Severity" not in names
    # Serious ethanol keeps its section category, not its inline "Contraindicated".
    ethanol = next(e for e in entries if e.interacting_name == "ethanol")
    assert ethanol.source_category == "serious"
    assert "Contraindicated" in ethanol.raw_text
    # Same-category repeats stay one entry with both assertions preserved.
    clonidine = [e for e in entries if e.interacting_name == "clonidine"]
    assert len(clonidine) == 1
    assert "clonidine decreases effects" in clonidine[0].raw_text
    assert "clonidine, sitagliptin. Other (see comment)" in clonidine[0].raw_text


def test_ofloxacin_keeps_both_category_assertions(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)

    dataset, _ = build(source_dir, None)
    ofloxacin = [e for e in dataset.documents[0].entries if e.interacting_name == "ofloxacin"]

    assert [(e.source_category, e.span_start, e.span_end) for e in ofloxacin] == [
        ("monitor_closely", 525, 529),
        ("minor", 912, 914),
    ]
    assert "pharmacodynamic synergism" in ofloxacin[0].raw_text
    assert "Potential dysglycemia" in ofloxacin[1].raw_text


def test_removed_entry_fails_counts_but_keeps_anomaly_report(tmp_path: Path) -> None:
    source_dir = _stage_sitagliptin(tmp_path)
    lines = (source_dir / SOURCE_FILENAME).read_text(encoding="utf-8-sig").splitlines()
    # Delete the Minor ofloxacin entry (lines 912-914) plus its preceding blank.
    del lines[910:914]
    (source_dir / SOURCE_FILENAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    dataset, report = build(source_dir, None)

    assert not report.passed
    (check,) = report.documents
    assert check.parsed_counts["minor"] == 69
    assert check.expected_counts["minor"] == 70
    assert any("minor expected 70 parsed 69" in a for a in check.anomalies)
    # The anomaly report still carries provenance of the bytes actually built.
    mutated = (source_dir / SOURCE_FILENAME).read_bytes()
    assert check.checksum == hashlib.sha256(mutated).hexdigest()
    assert check.checksum != SOURCE_SHA256
    assert len(dataset.documents[0].entries) == sum(EXPECTED_COUNTS.values()) - 1


# --- Slice 3b: BOM tolerance + Warnings stop marker (synthetic, public seam only) ---


def _write_minimal_monograph(path: Path, *, encoding: str, trailing: str = "") -> None:
    body = (
        "Interactions\n"
        "\n"
        "Contraindicated (0)\n"
        "\n"
        "Serious (1)\n"
        "\n"
        "testdrug will increase the level of sitagliptin by something. "
        "Avoid combination.\n"
        "\n"
        "Monitor Closely (0)\n"
        "\n"
        "Minor (0)\n"
    )
    path.write_text(body + trailing, encoding=encoding)


def test_build_handles_bom_prefixed_monograph(tmp_path: Path) -> None:
    _write_minimal_monograph(tmp_path / "Bom.txt", encoding="utf-8-sig")

    # Missing terminology path is tolerated, never fatal (S15 interface).
    dataset, report = build(tmp_path, tmp_path / "missing-aliases.json")

    assert report.passed
    (check,) = report.documents
    assert (
        check.parsed_counts
        == check.expected_counts
        == {
            "contraindicated": 0,
            "serious": 1,
            "monitor_closely": 0,
            "minor": 0,
        }
    )
    (entry,) = dataset.documents[0].entries
    assert (entry.interacting_name, entry.source_category) == ("testdrug", "serious")


def test_build_stops_before_warnings(tmp_path: Path) -> None:
    _write_minimal_monograph(
        tmp_path / "Warn.txt",
        encoding="utf-8",
        trailing="\nWarnings\n\nThis trailing text must not become an entry.\n",
    )

    dataset, report = build(tmp_path, None)

    assert report.passed
    entries = dataset.documents[0].entries
    assert [e.interacting_name for e in entries] == ["testdrug"]
    assert all("trailing text" not in e.raw_text for e in entries)
    assert all(e.interacting_name != "Warnings" for e in entries)


# --- Slice 4: CLI agrees with the library ---

_CLI_COUNTS = {"contraindicated": 0, "serious": 4, "monitor_closely": 92, "minor": 70}


def _run_cli(source_dir: Path, staging: Path) -> object:
    import subprocess
    import sys

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "x_insight.ddi",
            "build",
            "--sources",
            str(source_dir),
            "--terminology",
            str(source_dir / "aliases.json"),
            "--output",
            str(staging),
        ],
        capture_output=True,
        text=True,
        cwd=_repo_root() / "backend",
    )


def test_cli_build_agrees_with_library(tmp_path: Path) -> None:
    import json

    source_dir = _stage_sitagliptin(tmp_path)
    staging = tmp_path / "staging"

    completed = _run_cli(source_dir, staging)

    assert completed.returncode == 0, completed.stderr
    report_json = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    dataset_json = json.loads((staging / "candidate_dataset.json").read_text(encoding="utf-8"))
    assert report_json["passed"] is True
    assert report_json["documents"][0]["parsed_counts"] == _CLI_COUNTS
    assert report_json["documents"][0]["checksum"] == SOURCE_SHA256
    dataset, report = build(source_dir, None)
    assert report_json["documents"][0]["parsed_counts"] == report.documents[0].parsed_counts
    assert len(dataset_json["documents"][0]["entries"]) == len(dataset.documents[0].entries)


def test_cli_count_failure_still_writes_anomaly_report(tmp_path: Path) -> None:
    import json

    source_dir = _stage_sitagliptin(tmp_path)
    lines = (source_dir / SOURCE_FILENAME).read_text(encoding="utf-8-sig").splitlines()
    del lines[101:106]  # remove the Serious erdafitinib entry (lines 102-105 + blank)
    (source_dir / SOURCE_FILENAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    staging = tmp_path / "staging"

    completed = _run_cli(source_dir, staging)

    assert completed.returncode != 0
    report_json = json.loads((staging / "report.json").read_text(encoding="utf-8"))
    assert report_json["passed"] is False
    assert any("serious expected 4 parsed 3" in a for a in report_json["documents"][0]["anomalies"])


# --- S16.a: real source formats (public build seam only; S15 tests above untouched) ---
#
# slice 1 — category heading glued to the nav summary line (no blank between
# "All Interactions Sort By: Severity" and "Contraindicated (0)"):
# representative: Cardiovascular Furosemide (declared 0/11/177/127).

FUROSEMIDE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/"
    "Cardiovascular and Antihypertensive Agents/Furosemide.txt"
)
FUROSEMIDE_SHA256 = "97f1f0967826ece6054053378117073bb1f2735be72e81bec875cea3fe6e3e15"
FUROSEMIDE_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 11,
    "monitor_closely": 177,
    "minor": 127,
}


def _stage_repo_file(relative: str, sha256: str, tmp_path: Path) -> Path:
    """Copy an untouched original into an isolated source dir, verifying hash."""
    original = _repo_root() / relative
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest == sha256, f"original moved: {digest}"
    staged = tmp_path / Path(relative).name
    shutil.copyfile(original, staged)
    return tmp_path


def test_build_handles_glued_category_heading(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(FUROSEMIDE_RELATIVE, FUROSEMIDE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == FUROSEMIDE_EXPECTED_COUNTS
    assert check.parsed_counts == FUROSEMIDE_EXPECTED_COUNTS
    serious = {
        e.interacting_name for e in dataset.documents[0].entries if e.source_category == "serious"
    }
    assert "amikacin" in serious


# slice 2 — subject-led continuation paragraphs stay one entry (no spurious
# entry named after the monograph's own drug; bidirectional assertions kept):
# representative: Analgesics Acetaminophen (declared 3/24/48 + Contra 0).

ACETAMINOPHEN_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Analgesics and NSAIDs/Acetaminophen.txt"
)
ACETAMINOPHEN_SHA256 = "1307eb381ba3b9399d30cf821f205d843e8603674de451201940a10ec2303117"
ACETAMINOPHEN_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 3,
    "monitor_closely": 24,
    "minor": 48,
}


def test_build_keeps_subject_led_continuation_in_one_entry(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(ACETAMINOPHEN_RELATIVE, ACETAMINOPHEN_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == ACETAMINOPHEN_EXPECTED_COUNTS
    assert check.parsed_counts == ACETAMINOPHEN_EXPECTED_COUNTS
    entries = dataset.documents[0].entries
    # No entry is named after the monograph subject itself.
    assert all(e.interacting_name.lower() != "acetaminophen" for e in entries)
    by_name = {(e.source_category, e.interacting_name): e for e in entries}
    # Bidirectional assertions about one pair survive in a single entry.
    levonorgestrel = by_name[
        ("monitor_closely", "levonorgestrel oral/ethinylestradiol/ferrous bisglycinate")
    ]
    assert "will decrease the level" in levonorgestrel.raw_text
    assert "acetaminophen increases levels of levonorgestrel" in levonorgestrel.raw_text
    # Repeated pair across categories survives (cf. S15 ofloxacin).
    assert "pharmacodynamic synergism" not in by_name[("minor", "isoniazid")].raw_text
    assert "unknown mechanism" in by_name[("minor", "isoniazid")].raw_text
    assert "CYP2E1" in by_name[("monitor_closely", "isoniazid")].raw_text


# slice 3 — page-break chrome variants (split URL/page-number lines,
# form-feed timestamp, standalone page title) never become entries; the
# wrapped entry keeps complete text and its original span: representative:
# Anticholinergics Atropine (declared 0/10/103/24).

ATROPINE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/"
    "Anticholinergics & Parkinsonism Agents/Atropine.txt"
)
ATROPINE_SHA256 = "cb0945149c56b143bedd2dfa229fb5eb0a9f5e723edc0cc7ca604f3595469735"
ATROPINE_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 10,
    "monitor_closely": 103,
    "minor": 24,
}


def test_build_bridges_page_break_chrome_variants(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(ATROPINE_RELATIVE, ATROPINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == ATROPINE_EXPECTED_COUNTS
    assert check.parsed_counts == ATROPINE_EXPECTED_COUNTS
    entries = dataset.documents[0].entries
    by_name = {(e.source_category, e.interacting_name): e for e in entries}
    # Wrapped heading's content crosses the page break with the full span.
    glucagon = by_name[("serious", "glucagon intranasal")]
    assert (glucagon.span_start, glucagon.span_end) == (68, 81)
    assert "glucagon intranasal increases effects" in glucagon.raw_text
    assert "glucagon increase the risk" in glucagon.raw_text
    for entry in entries:
        assert "medscape.com" not in entry.raw_text
        assert "6:24 AM" not in entry.raw_text
        assert "and more" not in entry.raw_text
        assert not entry.interacting_name.startswith("Atreza")
        assert not entry.interacting_name.startswith("https://")
    # Orphan header reunited with subject-led content across the break.
    aclidinium = by_name[("monitor_closely", "aclidinium")]
    assert "atropine and aclidinium both decrease" in aclidinium.raw_text


# slice 4 — repeated pair assertions counted once by the monograph stay one
# entry with both texts preserved (contradictory directions kept verbatim,
# no direction invented): representative: Mood Stabilizers Gabapentin
# (declared 1/30/211/15).

GABAPENTIN_RELATIVE = (
    "project-documents/medical-documents/DDI-text/"
    "Mood Stabilizers and Anticonvulsants/Gabapentin.txt"
)
GABAPENTIN_SHA256 = "8669ff812304052d12833150155d5e637823a209601577695cb3f34c797e17bf"
GABAPENTIN_EXPECTED_COUNTS = {
    "contraindicated": 1,
    "serious": 30,
    "monitor_closely": 211,
    "minor": 15,
}


def test_build_keeps_repeated_pair_assertions_in_one_entry(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(GABAPENTIN_RELATIVE, GABAPENTIN_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == GABAPENTIN_EXPECTED_COUNTS
    assert check.parsed_counts == GABAPENTIN_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    alprazolam = by_name[("monitor_closely", "alprazolam")]
    assert (alprazolam.span_start, alprazolam.span_end) == (408, 414)
    assert "Either increases effects of the other" in alprazolam.raw_text
    assert "both increase sedation" in alprazolam.raw_text


# slice 5 — a comma inside running prose is not an entry boundary (the
# page-break continuation rejoins its entry): representative: Antidiabetic
# Empagliflozin (declared 0/0/40/1).

EMPAGLIFLOZIN_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Antidiabetic Agents/Empagliflozin.txt"
)
EMPAGLIFLOZIN_SHA256 = "a1c3e703b2dadb4559a3d1b2a4e16a2e0332e23ac38497c846cd850eeeb4de30"
EMPAGLIFLOZIN_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 0,
    "monitor_closely": 40,
    "minor": 1,
}


def test_build_rejoins_prose_continuation_across_page_break(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(EMPAGLIFLOZIN_RELATIVE, EMPAGLIFLOZIN_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == EMPAGLIFLOZIN_EXPECTED_COUNTS
    assert check.parsed_counts == EMPAGLIFLOZIN_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    lonapegsomatropin = by_name[("monitor_closely", "lonapegsomatropin")]
    assert "Growth hormone (GH) analogs may" in lonapegsomatropin.raw_text
    assert "require dose adjustment after initiating growth hormone" in lonapegsomatropin.raw_text
