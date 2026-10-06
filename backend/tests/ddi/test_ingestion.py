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

    # Omitted terminology preserves raw parsing; explicit missing paths fail from S17.
    dataset, report = build(tmp_path)

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


# --- S16.b(a): condensed-bullet splitting (public build seam only) ---
#
# slice a1 — bare bullet headers (`• name` + content lines, no blank between
# entries): representative: Antipsychotics Pimozide
# (declared 98/152/385/56). Each `•` header opens one entry; repeated pair
# assertions about the same pair merge into it (cf. S15 ofloxacin / S16.a
# gabapentin) with complete text and original spans preserved.

PIMOZIDE_RELATIVE = "project-documents/medical-documents/DDI-text/Antipsychotics/Pimozide.txt"
PIMOZIDE_SHA256 = "9522cb65163450f295c63568f234fbf4546c41ee41f6ad18e7cf75190cd148c9"
PIMOZIDE_EXPECTED_COUNTS = {
    "contraindicated": 98,
    "serious": 152,
    "monitor_closely": 385,
    "minor": 56,
}


def test_build_splits_bare_bullet_headers(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(PIMOZIDE_RELATIVE, PIMOZIDE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == PIMOZIDE_EXPECTED_COUNTS
    assert check.parsed_counts == PIMOZIDE_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    # Bare header reunited with its content lines; bullet never leaks into names.
    amiodarone = by_name[("contraindicated", "amiodarone")]
    assert (amiodarone.span_start, amiodarone.span_end) == (48, 49)
    assert amiodarone.raw_text == (
        "amiodarone and pimozide both increase QTc interval. Contraindicated."
    )
    assert all(not e.interacting_name.startswith("•") for e in dataset.documents[0].entries)
    # Repeated pair assertions stay one entry with both texts preserved.
    amisulpride = [e for e in dataset.documents[0].entries if e.interacting_name == "amisulpride"]
    assert len(amisulpride) == 1
    assert (amisulpride[0].span_start, amisulpride[0].span_end) == (50, 52)
    assert "amisulpride and pimozide both increase QTc interval" in amisulpride[0].raw_text
    assert "Either increases toxicity of the other" in amisulpride[0].raw_text


# slice a2 — inline bullets (`• name: description`, one or several names per
# bullet sharing one description): representative: Antipsychotics
# Trifluoperazine (declared 10/80/366/59). Each bullet splits into one entry
# per comma-separated name with shared span provenance; slash-joined
# formulation variants (`fentanyl / fentanyl intranasal / ...`) stay one
# entry — never fabricated into fragments. Enumerable sections match;
# summary-style sections keep honest count-mismatch anomalies for S18 review.

TRIFLUOPERAZINE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Antipsychotics/Trifluoperazine.txt"
)
TRIFLUOPERAZINE_SHA256 = "6e6ebf25da0483f148279df1c1417d4455abeb0d8579e61ebf9b1acb0ecac2d1"
TRIFLUOPERAZINE_EXPECTED_COUNTS = {
    "contraindicated": 10,
    "serious": 80,
    "monitor_closely": 366,
    "minor": 59,
}


def test_build_splits_inline_bullet_name_lists(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(TRIFLUOPERAZINE_RELATIVE, TRIFLUOPERAZINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    (check,) = report.documents
    assert check.expected_counts == TRIFLUOPERAZINE_EXPECTED_COUNTS
    # Fully enumerable single-name section matches exactly with provenance.
    assert check.parsed_counts["contraindicated"] == 10
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    amisulpride = by_name[("contraindicated", "amisulpride")]
    assert (amisulpride.span_start, amisulpride.span_end) == (63, 63)
    assert amisulpride.raw_text == (
        "• amisulpride: Either increases toxicity of the other. "
        "Increases risk of neuroleptic malignant syndrome."
    )
    # Multi-name bullets share one span across per-name entries.
    trio = [by_name[("serious", name)] for name in ("amiodarone", "amitriptyline", "amoxapine")]
    assert {e.span_start for e in trio} == {76}
    assert all("Both increase QTc interval" in e.raw_text for e in trio)
    # Slash-joined formulation variants stay one entry, never fragments.
    assert ("serious", "transdermal") not in by_name
    fentanyl = by_name[("serious", "fentanyl / fentanyl intranasal / transdermal / transmucosal")]
    assert (fentanyl.span_start, fentanyl.span_end) == (87, 87)
    # Remaining sections keep honest count-mismatch anomalies for S18 review:
    # the monograph declares more entries than its bullets enumerate.
    assert not report.passed
    assert check.parsed_counts["serious"] == 70
    assert check.parsed_counts["monitor_closely"] == 350
    assert check.parsed_counts["minor"] == 55
    assert any("serious expected 80 parsed 70" in a for a in check.anomalies)
    assert any("monitor_closely expected 366 parsed 350" in a for a in check.anomalies)
    assert any("minor expected 59 parsed 55" in a for a in check.anomalies)


# --- S16.b(b): uncounted / citation / hybrid / no-interaction policies ---
#
# Policy: an interaction section without countable ``Name (N)`` headings is
# reported as uncounted (passed=False, entries omitted, checksum + location
# kept in denominators) — never silently dropped, never fabricated. A file
# with no interaction section at all keeps the no-section anomaly.

ARIPIPRAZOLE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Antipsychotics/Aripiprazole.txt"
)
ARIPIPRAZOLE_SHA256 = "652cfa4e7499e41266fcb92e3fe607ecb5f18736dd8b48288f386d0eba27d30b"

LOXAPINE_RELATIVE = "project-documents/medical-documents/DDI-text/Antipsychotics/Loxapine.txt"
LOXAPINE_SHA256 = "f13d7533699181ed0550d5dbabd5ae109e43c44c9894be43d2243325d191562c"

VALPROIC_RELATIVE = "project-documents/medical-documents/DDI-text/Antipsychotics/Valproic acid.txt"
VALPROIC_SHA256 = "a5430526c02b3074ca3bf77ce30f27a818f18d36cca207c42a4fd2c4b2285672"

SIMETHICONE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Gastrointestinal Medications/Simethicone.txt"
)
SIMETHICONE_SHA256 = "c8d07a01fc06c85eaaf234058cb0d0f394335325f52773b031c7f2f9274c49b9"


def test_build_reports_citation_style_section_as_uncounted(tmp_path: Path) -> None:
    # Aripiprazole uses a `DRUG INTERACTIONS` banner with `[cite: N]`
    # markers and uncounted `Contraindicated` / `Serious (...)` headings:
    # the section is found, but no counts can be validated.
    source_dir = _stage_repo_file(ARIPIPRAZOLE_RELATIVE, ARIPIPRAZOLE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert not report.passed
    (check,) = report.documents
    assert check.checksum == ARIPIPRAZOLE_SHA256
    assert check.expected_counts == {}
    assert check.parsed_counts == {
        "contraindicated": 0,
        "serious": 0,
        "monitor_closely": 0,
        "minor": 0,
    }
    assert dataset.documents[0].entries == ()
    assert any("uncounted interaction section" in a for a in check.anomalies)
    assert not any("interaction section not found" in a for a in check.anomalies)


def test_build_reports_uncounted_headings_without_fabrication(tmp_path: Path) -> None:
    # Loxapine has a standard `Interactions` section whose severity
    # headings carry no counts (`Contraindicated`, `Serious`, ...): same
    # uncounted policy as the citation style, entries omitted by policy.
    source_dir = _stage_repo_file(LOXAPINE_RELATIVE, LOXAPINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert not report.passed
    (check,) = report.documents
    assert check.checksum == LOXAPINE_SHA256
    assert check.expected_counts == {}
    assert dataset.documents[0].entries == ()
    assert any("uncounted interaction section" in a for a in check.anomalies)
    assert not any("interaction section not found" in a for a in check.anomalies)


def test_build_supports_hybrid_heading_styles(tmp_path: Path) -> None:
    # Valproic acid mixes standard `Contraindicated (1)` / `Serious (35)`
    # headings with a `Significant - Monitor Closely (242)` alias heading
    # and an uncounted `Minor (49)` summary paragraph: enumerable sections
    # parse, the rest keep honest anomalies for S18 review.
    source_dir = _stage_repo_file(VALPROIC_RELATIVE, VALPROIC_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert not report.passed
    (check,) = report.documents
    assert check.expected_counts["contraindicated"] == 1
    assert check.expected_counts["serious"] == 35
    assert check.expected_counts["monitor_closely"] == 242
    assert check.parsed_counts["contraindicated"] == 1
    assert check.parsed_counts["serious"] == 35
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    adagrasib = by_name[("serious", "adagrasib")]
    assert (adagrasib.span_start, adagrasib.span_end) == (129, 129)
    assert adagrasib.raw_text.startswith("• adagrasib: will increase the level or effect")
    assert any("monitor_closely expected 242 parsed" in a for a in check.anomalies)
    assert any("minor expected 49 parsed" in a for a in check.anomalies)


def test_build_reports_genuinely_missing_interaction_section(tmp_path: Path) -> None:
    # Simethicone's monograph jumps from dosing to Adverse Effects (verified
    # by inspection): no interaction section exists in the source, so the
    # no-section anomaly with empty entries keeps it in denominators.
    source_dir = _stage_repo_file(SIMETHICONE_RELATIVE, SIMETHICONE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert not report.passed
    (check,) = report.documents
    assert check.checksum == SIMETHICONE_SHA256
    assert check.expected_counts == {}
    assert dataset.documents[0].entries == ()
    assert check.anomalies == (f"{Path(SIMETHICONE_RELATIVE).name}: interaction section not found",)


# --- S16.b(c): per-file residual triage (public build seam only) ---
#
# slice c1 — page-break continuation text misread as new entries:
# representative: Analgesics Tramadol (declared 7/87/322/21). Wrapped
# symptom lists (`depression, hypotension, ...`) continuing an entry across
# a page break are prose, not `Subject, Pair.` assertions; the restated
# pair sentence after the break still merges into the open entry.

TRAMADOL_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Analgesics and NSAIDs/Tramadol.txt"
)
TRAMADOL_SHA256 = "f006c2932b09da37343de94dba6b69d52bd2cdfc6cf4b478cfa7f7dfc9339598"
TRAMADOL_EXPECTED_COUNTS = {
    "contraindicated": 7,
    "serious": 87,
    "monitor_closely": 322,
    "minor": 21,
}


def test_build_keeps_page_break_continuation_text_in_open_entry(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(TRAMADOL_RELATIVE, TRAMADOL_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == TRAMADOL_EXPECTED_COUNTS
    assert check.parsed_counts == TRAMADOL_EXPECTED_COUNTS
    fentanyl = [
        e for e in dataset.documents[0].entries if e.interacting_name == "fentanyl intranasal"
    ]
    assert len(fentanyl) == 1
    # Header before the break reunited with wrapped content, continuation
    # prose, and the restated pair sentence after the break.
    assert (fentanyl[0].span_start, fentanyl[0].span_end) == (570, 586)
    assert "Either increases effects of the other" in fentanyl[0].raw_text
    assert "fentanyl intranasal and tramadol both increase sedation" in fentanyl[0].raw_text
    assert "medscape.com" not in fentanyl[0].raw_text
    # No entry is named after wrapped symptom prose.
    assert all(e.interacting_name != "depression" for e in dataset.documents[0].entries)


# slice c2 — mid-sentence wraps continue across page breaks: representative:
# Cardiovascular Aspirin (declared 3/24/264/118). An entry whose text ends
# mid-sentence (`Effect of interaction is`) before page chrome resumes in
# the next block (`not clear, use caution.`) — even though the resume line
# looks header-like on its own.

ASPIRIN_RELATIVE = (
    "project-documents/medical-documents/DDI-text/"
    "Cardiovascular and Antihypertensive Agents/Aspirin.txt"
)
ASPIRIN_SHA256 = "4b1d853065e7e84d77b899c11e4263b71ea889b25537bf69ce7b3bbb6fd09873"
ASPIRIN_EXPECTED_COUNTS = {
    "contraindicated": 3,
    "serious": 24,
    "monitor_closely": 264,
    "minor": 118,
}


def test_build_continues_mid_sentence_entry_across_page_break(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(ASPIRIN_RELATIVE, ASPIRIN_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == ASPIRIN_EXPECTED_COUNTS
    assert check.parsed_counts == ASPIRIN_EXPECTED_COUNTS
    pirbuterol = [e for e in dataset.documents[0].entries if e.interacting_name == "pirbuterol"]
    assert len(pirbuterol) == 1
    assert (pirbuterol[0].span_start, pirbuterol[0].span_end) == (1692, 1701)
    assert "Effect of interaction is not clear, use caution." in pirbuterol[0].raw_text
    assert "medscape.com" not in pirbuterol[0].raw_text
    assert all(e.interacting_name != "not clear" for e in dataset.documents[0].entries)


# slice c3 — wrapped echo lines still confirm dense headers: representative:
# Gastrointestinal Bisacodyl (declared 0/3/3/2). A header whose echoing
# content line wraps mid-name (`.../polyethylene` + `glycol. ...`) still
# opens a new entry; the wrap never fuses two pairs.

BISACODYL_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Gastrointestinal Medications/Bisacodyl.txt"
)
BISACODYL_SHA256 = "fad4052611bab6d9c60b682667f6510fc1d8dbf49f247a15a69ca2a0301debf2"
BISACODYL_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 3,
    "monitor_closely": 3,
    "minor": 2,
}


def test_build_splits_dense_header_on_wrapped_echo(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(BISACODYL_RELATIVE, BISACODYL_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == BISACODYL_EXPECTED_COUNTS
    assert check.parsed_counts == BISACODYL_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    middle = by_name[
        (
            "serious",
            "sodium sulfate/potassium chloride/magnesium sulfate/polyethylene glycol",
        )
    ]
    assert (middle.span_start, middle.span_end) == (59, 63)
    assert "Either increases toxicity of the other" in middle.raw_text


# slice c4 — same wrapped-echo mechanism in a blank-delimited file:
# representative: Gastrointestinal Famotidine (declared 0/25/44/9).

FAMOTIDINE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/Gastrointestinal Medications/Famotidine.txt"
)
FAMOTIDINE_SHA256 = "2984c0e9fe0dbde06dfbb63fd2830a00b41d54f2a352b81644f0c80ef5d0c6d2"
FAMOTIDINE_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 25,
    "monitor_closely": 44,
    "minor": 9,
}


def test_build_confirms_header_on_wrapped_content_echo(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(FAMOTIDINE_RELATIVE, FAMOTIDINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == FAMOTIDINE_EXPECTED_COUNTS
    assert check.parsed_counts == FAMOTIDINE_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    entry = by_name[("monitor_closely", "serdexmethylphenidate/dexmethylphenidate")]
    assert (entry.span_start, entry.span_end) == (380, 384)
    assert "famotidine will increase the level or effect" in entry.raw_text


# slice c5 — qualified variant headers stay separate entries:
# representative: Anticholinergics Amantadine (declared 0/11/64/20).
# `influenza virus vaccine quadrivalent` and its `, cell-cultured` /
# `, recombinant` variants are distinct listed pairs, not repeated
# assertions about one pair — an echo-confirmed bare header always opens
# a new entry instead of repeats-merging into the open one.

AMANTADINE_RELATIVE = (
    "project-documents/medical-documents/DDI-text/"
    "Anticholinergics & Parkinsonism Agents/Amantadine.txt"
)
AMANTADINE_SHA256 = "53df1c298a1ceb1cb3699c46ad8620836e2aed3ce7c1a05b6ee738ff0594653a"
AMANTADINE_EXPECTED_COUNTS = {
    "contraindicated": 0,
    "serious": 11,
    "monitor_closely": 64,
    "minor": 20,
}


def test_build_keeps_qualified_variant_headers_separate(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(AMANTADINE_RELATIVE, AMANTADINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    assert report.passed
    (check,) = report.documents
    assert check.expected_counts == AMANTADINE_EXPECTED_COUNTS
    assert check.parsed_counts == AMANTADINE_EXPECTED_COUNTS
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    base = by_name[("minor", "influenza virus vaccine quadrivalent")]
    assert (base.span_start, base.span_end) == (690, 695)
    cell = by_name[("minor", "influenza virus vaccine quadrivalent, cell-cultured")]
    assert (cell.span_start, cell.span_end) == (697, 702)
    assert "cell-cultured" in cell.raw_text
    recombinant = by_name[("minor", "influenza virus vaccine quadrivalent, recombinant")]
    assert (recombinant.span_start, recombinant.span_end) == (704, 709)


# --- S16.b(b2): markdown/citation-variant recognition (public build seam only) ---
#
# slice b2a — markdown banners/headings plus bold and inverted cite-per-item
# bullets (`* **Mechanism:** drug1 [cite], drug2 [cite], ...`):
# representative: Antipsychotics Asenapine (declared 6/142/363/44).
# `##`/`###` markers, `[cite: N]` suffixes, and per-item cites are section
# syntax only — raw names stay verbatim. Enumerable sections match;
# summary-style sections keep honest count-mismatch anomalies for S18
# review. Slash-joined variants stay one entry, never fragments.

ASENAPINE_RELATIVE = "project-documents/medical-documents/DDI-text/Antipsychotics/Asenapine.txt"
ASENAPINE_SHA256 = "15a13b1092a9fcc9debd359c1d538a9bff8bafa07fb5b373905d732daee137cc"
ASENAPINE_EXPECTED_COUNTS = {
    "contraindicated": 6,
    "serious": 142,
    "monitor_closely": 363,
    "minor": 44,
}


def test_build_supports_markdown_citation_bullets(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(ASENAPINE_RELATIVE, ASENAPINE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    (check,) = report.documents
    assert check.checksum == ASENAPINE_SHA256
    assert check.expected_counts == ASENAPINE_EXPECTED_COUNTS
    assert check.parsed_counts["contraindicated"] == 6
    assert check.parsed_counts["minor"] == 44
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    amisulpride = by_name[("contraindicated", "amisulpride")]
    assert (amisulpride.span_start, amisulpride.span_end) == (41, 41)
    assert amisulpride.raw_text.startswith("* **amisulpride:**")
    # Inverted mechanism bullets share one span across per-name entries.
    assert by_name[("serious", "adagrasib")].span_start == 46
    assert by_name[("serious", "alfuzosin")].span_start == 46
    assert "Increase QTc interval" in by_name[("serious", "adagrasib")].raw_text
    assert all(not e.interacting_name.startswith("*") for e in dataset.documents[0].entries)
    # Remaining sections keep honest count-mismatch anomalies for S18 review:
    # the monograph declares more entries than its bullets enumerate.
    assert not report.passed
    assert check.parsed_counts["serious"] == 137
    assert check.parsed_counts["monitor_closely"] == 341
    assert any("serious expected 142 parsed 137" in a for a in check.anomalies)
    assert any("monitor_closely expected 363 parsed 341" in a for a in check.anomalies)


# slice b2b — cite-suffixed plain headings plus `*` bullets without bold:
# representative: Antipsychotics Thiothixene (declared 11/46/324/8).
# `INTERACTIONS [cite]` banners, `Name (N) [cite]` headings, and trailing
# cites are syntax only. Mechanism-summary bullets without per-item cites
# stay one category entry each (Clozapine-Monitor precedent), keeping an
# honest mismatch for S18 review.

THIOTHIXENE_RELATIVE = "project-documents/medical-documents/DDI-text/Antipsychotics/Thiothixene.txt"
THIOTHIXENE_SHA256 = "b9b0c8aab7f029e7f72e99223ec61573bd33f7e4e06b69f198e11947ea20c0bc"
THIOTHIXENE_EXPECTED_COUNTS = {
    "contraindicated": 11,
    "serious": 46,
    "monitor_closely": 324,
    "minor": 8,
}


def test_build_supports_plain_star_bullets_with_cites(tmp_path: Path) -> None:
    source_dir = _stage_repo_file(THIOTHIXENE_RELATIVE, THIOTHIXENE_SHA256, tmp_path)

    dataset, report = build(source_dir, None)

    (check,) = report.documents
    assert check.checksum == THIOTHIXENE_SHA256
    assert check.expected_counts == THIOTHIXENE_EXPECTED_COUNTS
    assert check.parsed_counts["contraindicated"] == 11
    assert check.parsed_counts["minor"] == 8
    by_name = {(e.source_category, e.interacting_name): e for e in dataset.documents[0].entries}
    amisulpride = by_name[("contraindicated", "amisulpride")]
    assert (amisulpride.span_start, amisulpride.span_end) == (23, 23)
    assert amisulpride.raw_text.startswith("* amisulpride:")
    assert any("monitor_closely expected 324 parsed" in a for a in check.anomalies)
    # Remaining sections keep honest count-mismatch anomalies for S18 review.
    assert not report.passed
    assert check.parsed_counts["serious"] == 32
    assert check.parsed_counts["monitor_closely"] == 6
    assert check.parsed_counts["minor"] == 8
    assert any("serious expected 46 parsed 32" in a for a in check.anomalies)
