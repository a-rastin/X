"""Opt-in destructive DDInter drill, restricted to two named disposable databases.

Run from backend with DDINTER_ACCEPTANCE_SOURCES=/root/ddinter and
DDINTER_ACCEPTANCE_DATABASE_URL / DDINTER_RESTORE_DATABASE_URL set to databases
named ddinter_acceptance and ddinter_restore. No approval is given to the real
candidate: the import uses a temporary, clearly labeled simulated test fixture.
Each phase runs in a fresh process so peak RSS is attributable to that phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from x_insight import contracts, db
from x_insight.ddi import checker, publish
from x_insight.ddi.ddinter import SOURCE_FILES, build_ddinter, records
from x_insight.identity import service
from x_insight.operations import backup, restore


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def migrate(url):
    from alembic import command
    from alembic.config import Config

    os.environ["DATABASE_URL"] = url
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    return db.build_engine(url)


def actor_for(engine):
    service.ensure_default_admin(engine)
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT * FROM users WHERE role='admin'")).mappings().one())


def release_state(engine, version):
    with engine.connect() as conn:
        rid = conn.execute(
            text("SELECT id FROM ddi_dataset_releases WHERE version=:v"), {"v": version}
        ).scalar_one()
        counts = conn.execute(
            text(
                "SELECT count(*),count(DISTINCT pair_key) FROM ddi_interaction_evidence "
                "WHERE release_id=:r"
            ),
            {"r": rid},
        ).one()
        severity = dict(
            conn.execute(
                text(
                    "SELECT source_severity,count(DISTINCT pair_key) FROM "
                    "ddi_interaction_evidence WHERE release_id=:r GROUP BY source_severity"
                ),
                {"r": rid},
            ).all()
        )
        dangling = conn.execute(
            text(
                "SELECT count(*) FROM ddi_interaction_evidence e LEFT JOIN ddi_concepts a ON"
                " a.release_id=e.release_id AND a.concept_id=e.subject_concept_id LEFT JOIN "
                "ddi_concepts b ON b.release_id=e.release_id AND "
                "b.concept_id=e.interacting_concept_id WHERE e.release_id=:r AND (a.id IS "
                "NULL OR b.id IS NULL)"
            ),
            {"r": rid},
        ).scalar_one()
        sources = (
            conn.execute(
                text(
                    "SELECT source_path,checksum,source_bytes FROM ddi_source_documents WHERE "
                    "release_id=:r ORDER BY source_path"
                ),
                {"r": rid},
            )
            .mappings()
            .all()
        )
        assert {row["source_path"] for row in sources} == set(SOURCE_FILES)
        for row in sources:
            assert hashlib.sha256(bytes(row["source_bytes"])).hexdigest() == row["checksum"]
        digests, canonical_digests = {}, {}
        for table in (
            "ddi_dataset_releases",
            "ddi_source_documents",
            "ddi_concepts",
            "ddi_interaction_evidence",
        ):
            key = "id" if table == "ddi_dataset_releases" else "release_id"
            digest = hashlib.sha256()
            canonical_digest = hashlib.sha256()
            for row in conn.execution_options(yield_per=1000).execute(
                text(f"SELECT row_to_json(t)::text FROM {table} t WHERE {key}=:r ORDER BY id"),
                {"r": rid},
            ):
                digest.update(row[0].encode())
                digest.update(b"\n")
                canonical_digest.update(contracts.canonical_json(json.loads(row[0])))
                canonical_digest.update(b"\n")
            digests[table] = digest.hexdigest()
            canonical_digests[table] = canonical_digest.hexdigest()
        concepts = conn.execute(
            text(
                "SELECT count(*) FROM ddi_concepts WHERE release_id=:r AND catalog_drug_id "
                "IS NOT NULL"
            ),
            {"r": rid},
        ).scalar_one()
        database_bytes = conn.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar_one()
    assert (concepts, *counts, dangling) == (1939, 222383, 160235, 0)
    assert severity == {"Major": 26914, "Moderate": 96675, "Minor": 6833, "Unknown": 29813}
    return {
        "concepts": concepts,
        "evidence": counts[0],
        "pairs": counts[1],
        "repeated_occurrences": counts[0] - counts[1],
        "unique_severities": severity,
        "dangling_endpoints": dangling,
        "table_sha256": digests,
        "canonical_table_sha256": canonical_digests,
        "database_bytes": database_bytes,
        "source_hashes": {row["source_path"]: row["checksum"] for row in sources},
    }


def phase(name, root, source, import_url, restore_url):
    candidate = root / "candidate"
    if name == "build":
        report = build_ddinter(source, candidate)
        assert report["passed"] and not report["anomalies"]
        assert (
            report["catalog_count"],
            report["evidence_count"],
            report["unique_pair_count"],
            report["repeated_occurrences"],
            report["major_occurrences"],
            report["major_unique_pairs"],
        ) == (1939, 222383, 160235, 62148, 33896, 26914)
        return report
    if name == "import":
        engine = migrate(import_url)
        assert not publish.list_releases(engine), "acceptance database must be empty"
        manifest = json.loads((candidate / "review-manifest.json").read_text())
        assert manifest["decision"] == "awaiting_review"
        try:
            publish.publish_release(candidate, candidate / "review-manifest.json", engine)
        except publish.PublishRejected as exc:
            assert exc.code == "awaiting_review"
        else:
            raise AssertionError("pending real approval unexpectedly published")
        manifest.update(
            decision="approved_limited",
            date="2026-10-21",
            record=(
                "SIMULATED FULL-VOLUME ACCEPTANCE TEST FIXTURE ONLY. No clinical owner approval."
            ),
            accept_severity_only=True,
            accept_unavailable_pair_coverage=True,
            reviewed_evidence=[
                json.loads(line)
                for line in (candidate / "major-review-inventory.jsonl").read_text().splitlines()
            ],
        )
        with tempfile.TemporaryDirectory(prefix="ddinter-simulated-test-approval-") as temporary:
            approval = Path(temporary) / "SIMULATED-TEST-ONLY.json"
            dump(approval, manifest)
            started = time.perf_counter()
            imported = publish.publish_release(candidate, approval, engine)
            import_seconds = time.perf_counter() - started
            repeated = publish.publish_release(candidate, approval, engine)
            assert repeated["reused"] and repeated["version"] == imported["version"]
        dump(root / "release.json", imported)
        state = release_state(engine, imported["version"])
        # Every imported occurrence must match the source record and inclusive lines.
        with engine.connect() as conn:
            rid = checker.get_release_row(engine, imported["version"])["id"]
            checked = 0
            for source_path in SOURCE_FILES[:-1]:
                rows = (
                    conn.execution_options(yield_per=1000)
                    .execute(
                        text(
                            "SELECT raw_text,span_start,span_end,source_record_metadata FROM "
                            "ddi_interaction_evidence WHERE release_id=:r "
                            "AND source_path=:p ORDER BY "
                            "span_start"
                        ),
                        {"r": rid, "p": source_path},
                    )
                    .mappings()
                )
                for expected, actual in zip(
                    records(candidate / "sources" / source_path), rows, strict=True
                ):
                    number, start, end, fields, raw = expected
                    assert (actual["raw_text"], actual["span_start"], actual["span_end"]) == (
                        raw,
                        start,
                        end,
                    )
                    assert actual["source_record_metadata"]["source_record_number"] == number
                    assert (
                        list(
                            actual["source_record_metadata"]["source_record"].get(key)
                            for key in ("DDInterID_A", "Drug_A", "DDInterID_B", "Drug_B", "Level")
                        )
                        == fields
                    )
                    checked += 1
        assert checked == 222383
        catalog = json.loads((candidate / "catalog.json").read_text())
        ids = [row["catalog_drug_id"] for row in catalog[:10]]
        statements = []

        def observe(conn, cursor, statement, parameters, context, many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", observe)
        # Disable provider and prohibit non-loopback sockets while checking.
        import socket

        original_connect = socket.socket.connect

        def local_only(sock, address):
            assert address == ("127.0.0.1", make_url(import_url).port), (
                "network forbidden in offline DDI check"
            )
            return original_connect(sock, address)

        os.environ["PROVIDER_ENABLED"] = "false"
        socket.socket.connect = local_only
        started = time.perf_counter()
        try:
            report = checker.check(engine, ids, imported["version"])
        finally:
            socket.socket.connect = original_connect
            event.remove(engine, "before_cursor_execute", observe)
        elapsed = time.perf_counter() - started
        assert len([sql for sql in statements if "FROM ddi_interaction_evidence" in sql]) == 1
        assert len(report["pairs"]) == 45
        with engine.connect() as conn:
            sample = conn.execute(
                text("SELECT pair_key FROM ddi_interaction_evidence WHERE release_id=:r LIMIT 1"),
                {"r": rid},
            ).scalar_one()
            plan = conn.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT * FROM "
                    "ddi_interaction_evidence WHERE release_id=:r AND pair_key=:p"
                ),
                {"r": rid, "p": sample},
            ).scalar_one()
        assert "ix_ddi_evidence_release_pair" in json.dumps(plan)
        seen = []
        for offset in range(0, 1939, 25):
            page, total = checker.search_concepts(engine, rid, None, 25, offset)
            assert total == 1939
            seen.extend(row["catalog_drug_id"] for row in page)
        assert len(seen) == len(set(seen)) == 1939
        exact, total = checker.search_concepts(engine, rid, "DDInter1", 25, 0)
        assert total == 1 and exact[0]["catalog_drug_id"] == "DDInter1"
        assert (
            json.loads((candidate / "review-manifest.json").read_text())["decision"]
            == "awaiting_review"
        )
        return {
            "approval_status": (
                "SIMULATED TEST FIXTURE ONLY; REAL CANDIDATE REMAINS awaiting_review"
            ),
            "release": imported,
            "state": state,
            "import_seconds": import_seconds,
            "provenance_records_checked": checked,
            "lookup_seconds_10_drugs_45_pairs": elapsed,
            "lookup_sql": statements,
            "query_plan": plan,
            "catalog_pagination_ids": len(seen),
        }
    if name == "backup":
        engine = db.build_engine(import_url)
        actor = actor_for(engine)
        with Session(engine) as session:
            job, replayed, failed = backup.create_backup(
                session, engine, actor=actor, request_id="ddinter-full-volume-test"
            )
            session.commit()
        assert not failed, job
        archive = Path(job["archive_path"])
        with zipfile.ZipFile(archive) as handle:
            sizes = {
                info.filename: {"bytes": info.file_size, "compressed_bytes": info.compress_size}
                for info in handle.infolist()
            }
        dump(root / "archive.json", {"path": str(archive), "job_id": str(job["id"])})
        return {
            "archive_path": str(archive),
            "archive_bytes": archive.stat().st_size,
            "entries": sizes,
        }
    if name in ("restore", "verify"):
        engine = db.build_engine(restore_url)
        committed = None
        if name == "restore":
            engine = migrate(restore_url)
            assert not publish.list_releases(engine), "restore database must be empty"
            actor = actor_for(engine)
            archive = json.loads((root / "archive.json").read_text())
            with Session(engine) as session:
                job, failed = restore.stage_restore(
                    session,
                    engine,
                    actor=actor,
                    request_id="ddinter-full-volume-test",
                    archive_blob=Path(archive["path"]).read_bytes(),
                )
                session.commit()
            assert not failed, job
            with Session(engine) as session:
                committed, failed, rolled_back = restore.commit_restore(
                    session,
                    engine,
                    actor=actor,
                    request_id="ddinter-full-volume-test",
                    job_id=uuid.UUID(str(job["id"])),
                    confirmation_digest=job["confirmation_digest"],
                )
                session.commit()
            assert not failed and not rolled_back, committed
        version = json.loads((root / "release.json").read_text())["version"]
        state = release_state(engine, version)
        original = json.loads((root / "import.json").read_text())["result"]["state"]
        # JSON columns can change object-key order through portable backup. Compare
        # canonical semantic rows, retaining textual digests for diagnosis. Bytea,
        # raw CSV strings and content_hash remain exact values in this comparison.
        expected = original.get("canonical_table_sha256")
        if expected is None:
            expected = release_state(db.build_engine(import_url), version)["canonical_table_sha256"]
        assert state["canonical_table_sha256"] == expected
        assert state["source_hashes"] == original["source_hashes"]
        return {
            "state": state,
            "expected_canonical_table_sha256": expected,
            "restore_status": (
                committed["report"]["commit_status"]
                if committed
                else "resumed verification of already committed restore"
            ),
            "all_four_ddi_tables_exactly_preserved": True,
            "measurement_scope": (
                "full restore and verification"
                if committed
                else "resumed verification only; original restore timing unavailable"
            ),
        }
    raise ValueError(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("build", "import", "backup", "restore", "verify"))
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/ddinter-acceptance"))
    args = parser.parse_args()
    root = args.workdir
    root.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ["DDINTER_ACCEPTANCE_SOURCES"])
    import_url = os.environ["DDINTER_ACCEPTANCE_DATABASE_URL"]
    restore_url = os.environ["DDINTER_RESTORE_DATABASE_URL"]
    assert make_url(import_url).database == "ddinter_acceptance"
    assert make_url(restore_url).database == "ddinter_restore"
    assert make_url(import_url).host == make_url(restore_url).host == "127.0.0.1"
    os.environ.update(
        BACKUP_STAGING_DIR=str(root / "backups"),
        RESTORE_STAGING_DIR=str(root / "restores"),
        RESTORE_MAINTENANCE_FILE=str(root / "maintenance.lock"),
        RESTORE_OPERATOR_LOG=str(root / "operator.log"),
    )
    if args.phase:
        started = time.perf_counter()
        result = phase(args.phase, root, source, import_url, restore_url)
        measurements = {
            "phase": args.phase,
            "seconds": time.perf_counter() - started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "result": result,
        }
        dump(root / f"{args.phase}.json", measurements)
        print(json.dumps({key: value for key, value in measurements.items() if key != "result"}))
    else:
        for name in ("build", "import", "backup", "restore"):
            subprocess.run(
                [sys.executable, __file__, "--phase", name, "--workdir", str(root)], check=True
            )
        dump(
            Path("/tmp/ddinter-acceptance-results.json"),
            {
                name: json.loads((root / f"{name}.json").read_text())
                for name in ("build", "import", "backup", "restore")
            },
        )


if __name__ == "__main__":
    main()
