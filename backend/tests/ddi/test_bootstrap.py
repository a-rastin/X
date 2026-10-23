"""Source-only setup at the public bootstrap CLI/publication/migration seams.

Approvals and drug names in these compact fixtures are synthetic, never a
replacement for the committed owner-reviewed DDInter release.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.ddi.test_ddinter_release import fixture_approval, stage
from x_insight import db
from x_insight.ddi import checker, publish
from x_insight.ddi.__main__ import main
from x_insight.ddi.bootstrap import bootstrap_release

pytest_plugins = ["tests.ddi.test_publish"]


def source_only_bundle(tmp_path):
    staged, _, _ = stage(tmp_path / "build")
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    shutil.copytree(staged / "sources", bundle / "sources")
    shutil.copyfile(staged / "candidate_dataset.json", bundle / "candidate_dataset.json")
    shutil.copyfile(fixture_approval(staged), bundle / "owner-reviewed-manifest.json")
    return bundle


@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch):
    monkeypatch.delenv("DDI_DEFAULT_DATASET_VERSION", raising=False)
    monkeypatch.delenv("X_INSIGHT_CONTENT_DIR", raising=False)


def test_source_only_bootstrap_matches_approved_publication(tmp_path, clean_ddi, capsys):
    bundle = source_only_bundle(tmp_path)
    code = main(
        [
            "bootstrap",
            "--bundle",
            str(bundle),
            "--database-url",
            clean_ddi.url.render_as_string(hide_password=False),
        ]
    )
    assert code == 0
    assert "READY: ddi-" in capsys.readouterr().out
    version = checker.latest_release_version(clean_ddi)
    release = publish.get_release(clean_ddi, version)
    assert release["status"] == "released_limited"
    assert release["evidence_count"] == 5
    assert len(release["documents"]) == 9
    assert release["manifest"] == json.loads((bundle / "owner-reviewed-manifest.json").read_text())
    # Publishing the original reviewed staging must resolve to the exact same release.
    approved = publish.publish_release(
        tmp_path / "build/candidate",
        tmp_path / "build/candidate/synthetic-test-approval.json",
        clean_ddi,
    )
    assert approved["version"] == version
    assert approved["reused"]
    report = checker.check(clean_ddi, ["DDInter1", "DDInter2"], version)
    assert report["pairs"][0]["highest_known_severity"] == "Major"


def test_repeat_setup_reuses_release_without_source_rebuild(tmp_path, clean_ddi):
    bundle = source_only_bundle(tmp_path)
    first = bootstrap_release(clean_ddi, bundle)
    before = publish.get_release(clean_ddi, first["version"])
    # A repeated setup must not depend on the expensive source rebuild.
    shutil.rmtree(bundle / "sources")
    assert bootstrap_release(clean_ddi, bundle) == {
        "version": first["version"],
        "content_hash": first["content_hash"],
        "reused": True,
        "skipped": False,
    }
    assert len(publish.list_releases(clean_ddi)) == 1
    assert publish.get_release(clean_ddi, first["version"]) == before


@pytest.mark.parametrize("damage", ["source", "candidate", "unapproved", "review_inventory"])
def test_invalid_bundle_cli_fails_without_partial_publication(tmp_path, clean_ddi, capsys, damage):
    bundle = source_only_bundle(tmp_path)
    if damage == "source":
        with (bundle / "sources/ddinter_downloads_code_A.csv").open("a") as handle:
            handle.write("DDInter90,Changed,DDInter91,Drug,Minor\n")
    else:
        path = bundle / (
            "candidate_dataset.json" if damage == "candidate" else "owner-reviewed-manifest.json"
        )
        value = json.loads(path.read_text())
        if damage == "candidate":
            value["candidate_hash"] = "0" * 64
        elif damage == "unapproved":
            value["decision"] = "awaiting_review"
        else:
            value["reviewed_evidence"].pop()
        path.write_text(json.dumps(value))
    assert (
        main(
            [
                "bootstrap",
                "--bundle",
                str(bundle),
                "--database-url",
                clean_ddi.url.render_as_string(hide_password=False),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "DDI bootstrap FAILED:" in captured.err
    assert "READY:" not in captured.out
    assert publish.list_releases(clean_ddi) == []
    assert checker.latest_release_version(clean_ddi) is None


def test_existing_custom_release_preserved_unless_bundle_explicitly_configured(
    tmp_path,
    clean_ddi,
    monkeypatch,
    capsys,
):
    bundle = source_only_bundle(tmp_path)
    original = bootstrap_release(clean_ddi, bundle)
    approval = bundle / "owner-reviewed-manifest.json"
    changed = json.loads(approval.read_text())
    changed["record"] += "; a separately reviewed synthetic release"
    approval.write_text(json.dumps(changed))
    assert (
        main(
            [
                "bootstrap",
                "--bundle",
                str(bundle),
                "--database-url",
                clean_ddi.url.render_as_string(hide_password=False),
            ]
        )
        == 0
    )
    assert "SKIPPED: existing DDI release preserved" in capsys.readouterr().out
    assert checker.latest_release_version(clean_ddi) == original["version"]
    assert len(publish.list_releases(clean_ddi)) == 1
    # Public publication supplies an independent release identity for the explicit pin.
    staged = tmp_path / "build/candidate"
    expected = publish.publish_release(staged, approval, clean_ddi)
    with clean_ddi.begin() as connection:
        # Fixture reset: restore the pre-upgrade installation, keeping its original release.
        connection.execute(
            text(
                "DELETE FROM ddi_interaction_evidence WHERE release_id = "
                "(SELECT id FROM ddi_dataset_releases WHERE version = :version)"
            ),
            {"version": expected["version"]},
        )
        connection.execute(
            text(
                "DELETE FROM ddi_concepts WHERE release_id = "
                "(SELECT id FROM ddi_dataset_releases WHERE version = :version)"
            ),
            {"version": expected["version"]},
        )
        connection.execute(
            text(
                "DELETE FROM ddi_source_documents WHERE release_id = "
                "(SELECT id FROM ddi_dataset_releases WHERE version = :version)"
            ),
            {"version": expected["version"]},
        )
        connection.execute(
            text("DELETE FROM ddi_dataset_releases WHERE version = :version"),
            {"version": expected["version"]},
        )
    monkeypatch.setenv("DDI_DEFAULT_DATASET_VERSION", expected["version"])
    added = bootstrap_release(clean_ddi, bundle)
    assert added["version"] == expected["version"]
    assert not added["skipped"] and not added["reused"]
    assert checker.latest_release_version(clean_ddi) == expected["version"]
    assert len(publish.list_releases(clean_ddi)) == 2
    assert publish.get_release(clean_ddi, original["version"])["evidence_count"] == 5


def test_missing_bundle_cli_reports_failure(tmp_path, clean_ddi, capsys):
    assert (
        main(
            [
                "bootstrap",
                "--bundle",
                str(tmp_path / "absent"),
                "--database-url",
                clean_ddi.url.render_as_string(hide_password=False),
            ]
        )
        == 1
    )
    assert "DDI bootstrap FAILED:" in capsys.readouterr().err
    assert publish.list_releases(clean_ddi) == []


def test_explicit_migration_provisions_blank_database_then_reuses(tmp_path, clean_ddi):
    # Like the standard Compose test database owner, this fixture role needs CREATEDB.
    bundle = source_only_bundle(tmp_path)
    content = tmp_path / "container/content"
    target = content / "ddi/ddinter/2026-10-21"
    target.parent.mkdir(parents=True)
    shutil.copytree(bundle, target)
    database = "ddi_bootstrap_" + uuid.uuid4().hex
    admin = db.build_engine(clean_ddi.url)
    fresh_url = clean_ddi.url.set(database=database)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(
            text(f"CREATE DATABASE \"{database}\" TEMPLATE template0 ENCODING 'UTF8'")
        )
    engine = db.build_engine(fresh_url)
    try:
        env = {
            **os.environ,
            "DATABASE_URL": fresh_url.render_as_string(hide_password=False),
            "X_INSIGHT_CONTENT_DIR": str(content),
        }
        backend = Path(__file__).resolve().parents[2]
        command = [sys.executable, "-m", "x_insight.operations.migrate"]
        first = subprocess.run(
            command, cwd=backend, env=env, capture_output=True, text=True, timeout=60
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert "reused=False" in first.stdout
        assert db.check_readiness(engine).ok
        version = checker.latest_release_version(engine)
        assert publish.get_release(engine, version)["evidence_count"] == 5
        second = subprocess.run(
            command, cwd=backend, env=env, capture_output=True, text=True, timeout=60
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert "reused=True" in second.stdout
        assert len(publish.list_releases(engine)) == 1
        # A broken source-only deployment must fail setup, even after valid schema migration.
        (target / "owner-reviewed-manifest.json").unlink()
        failed = subprocess.run(
            command, cwd=backend, env=env, capture_output=True, text=True, timeout=60
        )
        assert failed.returncode == 1
        assert "DDI bootstrap FAILED:" in failed.stderr
        assert publish.get_release(engine, version)["evidence_count"] == 5
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()
