"""DDI offline CLI (S15; plan.md §6.2).

``uv run python -m x_insight.ddi build --sources <dir> --terminology
<aliases> --output <staging-dir>`` from ``backend/``. Exit 0 when every
document passes validation, nonzero for structural/count failures — the
anomaly report is still written in the failure case.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from x_insight.ddi.ingestion import build


def _cmd_build(args: argparse.Namespace) -> int:
    output = Path(args.output)
    try:
        dataset, report = build(args.sources, args.terminology)
    except Exception as exc:  # noqa: BLE001 - CLI must still report, then fail
        output.mkdir(parents=True, exist_ok=True)
        anomaly = f"sources {args.sources}: build failed: {exc}"
        (output / "report.json").write_text(
            json.dumps(
                {
                    "parser_version": "ddi-ingestion/0.1.0",
                    "documents": [],
                    "passed": False,
                    "anomalies": [anomaly],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(anomaly, file=sys.stderr)
        return 1
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidate_dataset.json").write_text(
        json.dumps(asdict(dataset), indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )
    total = sum(len(doc.entries) for doc in dataset.documents)
    status = "PASS" if report.passed else "FAIL"
    print(f"{status}: {len(dataset.documents)} document(s), {total} entries -> {output}")
    for check in report.documents:
        for anomaly in check.anomalies:
            print(f"anomaly: {anomaly}", file=sys.stderr)
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x_insight.ddi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="build candidate dataset + report")
    build_parser.add_argument("--sources", required=True, help="directory of .txt monographs")
    build_parser.add_argument(
        "--terminology",
        default=None,
        help="controlled terminology JSON; supplied file must exist and validate",
    )
    build_parser.add_argument("--output", required=True, help="staging directory for outputs")
    args = parser.parse_args(argv)
    if args.command == "build":
        return _cmd_build(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
