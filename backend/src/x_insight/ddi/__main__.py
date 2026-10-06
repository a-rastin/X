"""DDI offline CLI (S15 build; S18 publish; plan.md §6.2).

``uv run python -m x_insight.ddi build --sources <dir> --terminology
<aliases> --output <staging-dir>`` from ``backend/``. Exit 0 when every
document passes validation, nonzero for structural/count failures — the
anomaly report is still written in the failure case.

``uv run python -m x_insight.ddi publish --staging <staging-dir> --manifest
<reviewed-manifest>`` validates owner approvals and imports one immutable
release in a single transaction. Rejections exit nonzero with no partial
import; the prior released dataset stays readable.
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


def _cmd_publish(args: argparse.Namespace) -> int:
    from x_insight import db as db_module
    from x_insight.ddi import publish as publish_module

    url = args.database_url or db_module.get_database_url()
    engine = db_module.build_engine(url)
    try:
        try:
            result = publish_module.publish_release(args.staging, args.manifest, engine)
        except publish_module.PublishRejected as exc:
            print(f"REJECTED: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - CLI must fail safe, never half-import
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        print(
            f"PUBLISHED: {result['version']} "
            f"(content {result['content_hash'][:12]}…, reused={result['reused']})"
        )
        return 0
    finally:
        engine.dispose()


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
    publish_parser = sub.add_parser("publish", help="validate approvals and import a release")
    publish_parser.add_argument(
        "--staging", required=True, help="staging directory holding candidate_dataset.json"
    )
    publish_parser.add_argument("--manifest", required=True, help="owner-reviewed manifest JSON")
    publish_parser.add_argument(
        "--database-url",
        default=None,
        help="database URL override (default DATABASE_URL env)",
    )
    args = parser.parse_args(argv)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "publish":
        return _cmd_publish(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
