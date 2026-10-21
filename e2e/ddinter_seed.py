"""Synthetic browser fixtures only; never imports or approves the supplied corpus."""

import csv
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from x_insight.ddi.ddinter import CSV_FILES, HEADERS, build_ddinter
from x_insight.ddi.publish import publish_release
from x_insight.identity.service import ensure_default_admin

URL = "postgresql+psycopg://nobody@127.0.0.1:55432/ddinter_browser"
root = Path("/tmp/ddinter-browser-fixtures")
root.mkdir(exist_ok=True)
engine = create_engine(URL)
ensure_default_admin(engine)


def ddinter(variant):
    sources = root / f"sources-{variant}"
    sources.mkdir(exist_ok=True)
    (sources / "docs").mkdir(exist_ok=True)
    (sources / "docs/explanation.txt").write_text(
        "6. Criteria for severity annotation\nSynthetic browser fixture only.\n7. Statistic\n"
    )

    def name(i):
        return (
            f"Drug {i:04}"
            + (", α <b>literal</b>\n<script>alert(1)</script>" if i == 1 else "")
            + (f" revised {variant}" if variant != "first" else "")
        )

    for file in CSV_FILES:
        with (sources / file).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS)
            if file == CSV_FILES[0]:
                for i in range(2, 1940):
                    writer.writerow(
                        [
                            "DDInter1",
                            name(1),
                            f"DDInter{i}",
                            name(i),
                            ["Major", "Moderate", "Minor", "Unknown"][(i - 2) % 4],
                        ]
                    )
            if file == CSV_FILES[1]:
                writer.writerow(["DDInter2", name(2), "DDInter1", name(1), "Major"])
    stage = root / f"candidate-{variant}"
    build_ddinter(sources, stage)
    manifest = json.loads((stage / "review-manifest.json").read_text())
    manifest.update(
        decision="approved_limited",
        reviewer="owner",
        date="2026-10-20",
        record="SYNTHETIC TEST ONLY",
        accept_severity_only=True,
        accept_unavailable_pair_coverage=True,
    )
    manifest["reviewed_evidence"] = [
        json.loads(row)
        for row in (stage / "major-review-inventory.jsonl").read_text().splitlines()
    ]
    path = root / f"synthetic-approval-{variant}.json"
    path.write_text(json.dumps(manifest))
    return publish_release(stage, path, engine)["version"]


def legacy():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from tests.http.test_medications import (
        _manifest_for_staging,
        _reviewed_for_staging,
        _stage,
        concept,
    )

    sources = root / "legacy"
    sources.mkdir(exist_ok=True)
    for file in sources.glob("*.txt"):
        file.unlink()
    (sources / "E2eAlpha.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (1)\n\nE2eBeta\nE2eBeta increases the level of E2eAlpha <b>e2e-bold</b> <script>alert(1)</script>. Avoid combination.\n\nMonitor Closely (0)\n\nMinor (0)\nWarnings\n"
    )
    (sources / "E2eBeta.txt").write_text(
        "Interactions\n\nContraindicated (0)\n\nSerious (0)\n\nMonitor Closely (0)\n\nMinor (1)\n\nE2eAlpha\nE2eAlpha increases the level of E2eBeta. Minor significance.\nWarnings\n"
    )
    (sources / "Gamma.txt").write_text(
        "Synthetic excluded source without interaction coverage.\n"
    )
    stage = root / "legacy-candidate"
    _stage(
        sources,
        stage,
        {
            "version": "e2e-s20/1",
            "concepts": [
                concept("alpha", "E2eAlpha", catalog="catalog_e2e_alpha"),
                concept("beta", "E2eBeta", catalog="catalog_e2e_beta"),
                concept("gamma", "E2eGamma", catalog="catalog_e2e_gamma"),
            ],
            "aliases": [],
        },
    )
    manifest = _manifest_for_staging(
        stage,
        root / "legacy-approval.json",
        decision="approved_limited",
        coverage={
            "scope": "limited",
            "excluded_sources": [
                {"path": "Gamma.txt", "reason": "Synthetic uncovered source"}
            ],
        },
        limitations=["Synthetic limited coverage unavailable"],
        reviewed_evidence=_reviewed_for_staging(stage, ["E2eAlpha.txt", "E2eBeta.txt"]),
    )
    return publish_release(stage, manifest, engine)["version"]


if __name__ == "__main__":
    if sys.argv[1:]:
        print(ddinter(sys.argv[1]))
    else:
        versions = {"legacy": legacy(), "first": ddinter("first")}
        (root / "versions.json").write_text(json.dumps(versions))
        print(json.dumps(versions))
