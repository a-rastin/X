"""CSV build seam: exact provenance, deterministic identity, honest review gate."""

import csv
import json
from pathlib import Path

from x_insight.ddi.ddinter import CSV_FILES, HEADERS, build_ddinter
from x_insight.ddi.publish import PublishRejected, publish_release


def test_build_csv_record_provenance_and_pending_review(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "docs").mkdir()
    (source / "docs/explanation.txt").write_text(
        "6. Criteria for severity annotation\nMajor: supplied definition\n7. Statistic\n"
    )
    for name in CSV_FILES:
        with (source / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            if name == CSV_FILES[0]:
                writer.writerow(["DDInter1", "Alpha, α\nmultiline", "DDInter2", "Beta", "Major"])
            elif name == CSV_FILES[1]:
                writer.writerow(["DDInter2", "Beta", "DDInter1", "Alpha, α\nmultiline", "Major"])
    output = tmp_path / "candidate"
    report = build_ddinter(source, output)
    assert report["passed"]
    assert (report["catalog_count"], report["evidence_count"], report["unique_pair_count"]) == (
        2,
        2,
        1,
    )
    assert report["repeated_occurrences"] == 1
    assert report["major_occurrences"] == 2
    rows = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    assert (rows[0]["span_start"], rows[0]["span_end"], rows[0]["source_record_number"]) == (
        2,
        3,
        1,
    )
    assert rows[0]["source_record"]["Drug_A"] == "Alpha, α\nmultiline"
    assert rows[0]["direction"] == "unknown"
    assert rows[0]["pair_key"] == rows[1]["pair_key"]
    manifest = json.loads((output / "review-manifest.json").read_text())
    assert manifest["decision"] == "awaiting_review"
    assert manifest["reviewed_evidence"] == []
    candidate = (output / "candidate_dataset.json").read_bytes()
    build_ddinter(source, tmp_path / "another-location")
    assert candidate == (tmp_path / "another-location/candidate_dataset.json").read_bytes()
    try:
        publish_release(output, output / "review-manifest.json", None)
    except PublishRejected as exc:
        assert exc.code == "awaiting_review"
    else:
        raise AssertionError("pending owner review must never publish")
