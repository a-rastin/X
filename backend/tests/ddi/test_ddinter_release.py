"""DDInter public build/publish/check seams; approvals here are synthetic fixtures."""

import csv
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event

from x_insight.ddi import checker, publish
from x_insight.ddi.ddinter import CSV_FILES, HEADERS, build_ddinter

pytest_plugins = ["tests.ddi.test_publish"]


def stage(tmp_path, rows=None):
    source = tmp_path / "sources"
    source.mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "docs/explanation.txt").write_text(
        "6. Criteria for severity annotation\nMajor: fixture definition\n7. Statistic\n"
    )
    for name in CSV_FILES:
        with (source / name).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            if name == CSV_FILES[0]:
                writer.writerows(
                    rows
                    or [
                        ["DDInter1", "Alpha", "DDInter2", "Beta", "Major"],
                        ["DDInter2", "Beta", "DDInter1", "Alpha", "Major"],
                        ["DDInter1", "Alpha", "DDInter3", "Gamma", "Moderate"],
                        ["DDInter2", "Beta", "DDInter3", "Gamma", "Minor"],
                        ["DDInter3", "Gamma", "DDInter10", "Delta", "Unknown"],
                    ]
                )
    root = tmp_path / "candidate"
    report = build_ddinter(source, root)
    return root, source, report


def fixture_approval(root):
    manifest = json.loads((root / "review-manifest.json").read_text())
    manifest.update(
        decision="approved_limited",
        date="2026-10-21",
        record="SYNTHETIC TEST FIXTURE ONLY; no clinical owner review",
        accept_severity_only=True,
        accept_unavailable_pair_coverage=True,
        reviewed_evidence=[
            json.loads(line)
            for line in (root / "major-review-inventory.jsonl").read_text().splitlines()
        ],
    )
    path = root / "synthetic-test-approval.json"
    path.write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize(
    "row,reason",
    [
        (["DDInter0", "Alpha", "DDInter2", "Beta", "Major"], "malformed DDInter ID"),
        (["DDInter1", "Alpha", "DDInter2", "Beta", "Severe"], "unexpected severity"),
        (["DDInter1", "Alpha", "DDInter1", "Alpha", "Minor"], "self-pair"),
        (["DDInter1", "", "DDInter2", "Beta", "Major"], "five nonempty fields"),
    ],
)
def test_invalid_record_prevents_release_eligibility(tmp_path, row, reason):
    root, _, report = stage(tmp_path, [row])
    assert not report["passed"]
    assert any(reason in item["reason"] for item in report["anomalies"])
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(root, fixture_approval(root), None)


@pytest.mark.parametrize(
    "second,reason",
    [
        (["DDInter1", "Other", "DDInter3", "Gamma", "Major"], "ID/name disagreement"),
        (["DDInter3", "alpha", "DDInter2", "Beta", "Major"], "shared by different IDs"),
        (["DDInter2", "Beta", "DDInter1", "Alpha", "Minor"], "conflicting severities"),
    ],
)
def test_identity_and_severity_conflicts_are_reported(tmp_path, second, reason):
    _, _, report = stage(
        tmp_path,
        [
            ["DDInter1", "Alpha", "DDInter2", "Beta", "Major"],
            second,
        ],
    )
    assert not report["passed"]
    assert any(reason in item["reason"] for item in report["anomalies"])


def test_missing_files_and_wrong_headers_fail(tmp_path):
    root, source, _ = stage(tmp_path)
    (source / CSV_FILES[0]).write_text("Drug_A,Drug_B\nAlpha,Beta\n")
    assert not build_ddinter(source, root)["passed"]
    (source / CSV_FILES[1]).unlink()
    with pytest.raises(ValueError, match="missing required sources"):
        build_ddinter(source, root)


def test_changed_input_changes_report_and_candidate_identity(tmp_path):
    root, source, first = stage(tmp_path)
    first_candidate = json.loads((root / "candidate_dataset.json").read_text())
    with (source / CSV_FILES[-1]).open("a", newline="") as handle:
        csv.writer(handle).writerow(["DDInter1001", "Later drug", "DDInter1", "Alpha", "Minor"])
    second = build_ddinter(source, root)
    second_candidate = json.loads((root / "candidate_dataset.json").read_text())
    assert second["passed"]
    assert (first["evidence_count"], second["evidence_count"], second["catalog_count"]) == (5, 6, 5)
    assert second_candidate["candidate_hash"] != first_candidate["candidate_hash"]
    assert second["sources"] != first["sources"]
    concepts = json.loads((root / "catalog.json").read_text())
    assert next(row for row in concepts if row["catalog_drug_id"] == "DDInter1001")["id"] == (
        "ddinter:DDInter1001"
    )


@pytest.mark.parametrize(
    "artifact",
    [
        "evidence.jsonl",
        "catalog.json",
        "report.json",
        "major-review-inventory.jsonl",
        "duplicates.jsonl",
        "sources/docs/explanation.txt",
        "sources/" + CSV_FILES[0],
    ],
)
def test_changed_staged_artifacts_cannot_publish(tmp_path, artifact):
    root, _, _ = stage(tmp_path)
    approval = fixture_approval(root)
    with (root / artifact).open("a") as handle:
        handle.write("\n")
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(root, approval, None)


@pytest.mark.parametrize("change", ["major", "coverage", "inventory", "acceptance"])
def test_incomplete_or_changed_review_cannot_publish(tmp_path, change):
    root, _, _ = stage(tmp_path)
    approval = fixture_approval(root)
    manifest = json.loads(approval.read_text())
    if change == "major":
        manifest["reviewed_evidence"].pop()
    elif change == "coverage":
        manifest["coverage"]["missing_pair_policy"] = "covered_no_listed_interaction"
    elif change == "inventory":
        manifest["sources"][0]["bytes"] += 1
    else:
        manifest["accept_severity_only"] = False
    approval.write_text(json.dumps(manifest))
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(root, approval, None)


@pytest.mark.parametrize("record", [True, {}, [], 123, "   "])
def test_review_record_must_be_meaningful_text(tmp_path, record):
    root, _, _ = stage(tmp_path)
    approval = fixture_approval(root)
    manifest = json.loads(approval.read_text())
    manifest["record"] = record
    approval.write_text(json.dumps(manifest))
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(root, approval, None)


@pytest.mark.parametrize("limitation", [None, {}, "", "  "])
def test_review_limitations_reject_malformed_values(tmp_path, limitation):
    root, _, _ = stage(tmp_path)
    approval = fixture_approval(root)
    manifest = json.loads(approval.read_text())
    manifest["limitations"].append(limitation)
    approval.write_text(json.dumps(manifest))
    with pytest.raises(publish.PublishRejected):
        publish.publish_release(root, approval, None)


def test_native_severities_missing_pairs_dedup_and_bounded_queries(
    tmp_path, clean_ddi, monkeypatch
):
    root, _, _ = stage(tmp_path)
    version = publish.publish_release(root, fixture_approval(root), clean_ddi)["version"]
    statements = []

    def observed(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(clean_ddi, "before_cursor_execute", observed)
    import socket

    connect = socket.socket.connect

    def database_only(sock, address):
        assert address == (clean_ddi.url.host, clean_ddi.url.port), (
            "DDI attempted non-database network"
        )
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", database_only)
    monkeypatch.delenv("X_INSIGHT_PROVIDER_ENDPOINT", raising=False)
    monkeypatch.delenv("X_INSIGHT_PROVIDER_MODEL", raising=False)
    try:
        report = checker.check(
            clean_ddi, ["DDInter1", "DDInter2", "DDInter3", "DDInter10", "DDInter1"], version
        )
    finally:
        event.remove(clean_ddi, "before_cursor_execute", observed)
    assert len(report["pairs"]) == 6
    assert len(report["resolved_medications"]) == 4
    found = [pair for pair in report["pairs"] if pair["status"] == "interaction_found"]
    assert {p["highest_known_severity"] for p in found} == {"Major", "Moderate", "Minor", "Unknown"}
    assert all(not p["conflicts"] for p in found)
    assert sum(p["status"] == "coverage_unavailable" for p in report["pairs"]) == 2
    assert len([sql for sql in statements if "FROM ddi_interaction_evidence" in sql]) == 1
    catalog_sql = [sql for sql in statements if "FROM ddi_concepts" in sql]
    assert len(catalog_sql) == 1 and "catalog_drug_id IN" in catalog_sql[0]
    reverse = checker.check(clean_ddi, ["DDInter10", "DDInter3", "DDInter2", "DDInter1"], version)
    assert reverse["pairs"] == report["pairs"]
    assert reverse["medication_fingerprint"] == report["medication_fingerprint"]
    for ids in ([], ["DDInter1"]):
        empty = checker.check(clean_ddi, ids, version)
        assert empty["pairs"] == [] and empty["limitations"]
    rid = checker.get_release_row(clean_ddi, version)["id"]
    exact, total = checker.search_concepts(clean_ddi, rid, "DDInter1", 25, 0)
    assert total == 1 and exact[0]["catalog_drug_id"] == "DDInter1"
    pages = [checker.search_concepts(clean_ddi, rid, None, 1, offset)[0][0] for offset in range(4)]
    assert [row["canonical_name"] for row in pages] == ["Alpha", "Beta", "Delta", "Gamma"]


def test_default_selection_and_concurrent_idempotent_publication(tmp_path, clean_ddi, monkeypatch):
    root, _, _ = stage(tmp_path)
    approval = fixture_approval(root)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: publish.publish_release(root, approval, clean_ddi), range(2))
        )
    assert {result["reused"] for result in results} == {True, False}
    version = results[0]["version"]
    assert len(publish.list_releases(clean_ddi)) == 1
    assert publish.get_release(clean_ddi, version)["evidence_count"] == 5
    assert publish.publish_release(root, approval, clean_ddi)["reused"]
    changed = json.loads(approval.read_text())
    changed["record"] += "; second simulated review fixture"
    approval.write_text(json.dumps(changed))
    second_version = publish.publish_release(root, approval, clean_ddi)["version"]
    assert second_version != version
    monkeypatch.delenv("DDI_DEFAULT_DATASET_VERSION", raising=False)
    assert checker.latest_release_version(clean_ddi) == second_version
    monkeypatch.setenv("DDI_DEFAULT_DATASET_VERSION", version)
    assert checker.latest_release_version(clean_ddi) == version
    monkeypatch.setenv("DDI_DEFAULT_DATASET_VERSION", "ddi-000000000000")
    with pytest.raises(checker.CheckError) as error:
        checker.latest_release_version(clean_ddi)
    assert error.value.code == "INVALID_DEFAULT_DATASET"
    # An explicit historical pin remains usable even with an invalid configured default.
    assert checker.check(clean_ddi, ["DDInter1"], version)["dataset_version"] == version


def test_failed_insert_rolls_back_without_hiding_prior_release(tmp_path, clean_ddi):
    root, _, _ = stage(tmp_path / "first")
    original = publish.publish_release(root, fixture_approval(root), clean_ddi)
    changed, _, _ = stage(tmp_path / "changed", [["DDInter5", "Five", "DDInter6", "Six", "Minor"]])

    def fail_insert(conn, cursor, statement, params, context, many):
        if "INSERT INTO ddi_interaction_evidence" in statement:
            raise RuntimeError("injected database write failure")

    event.listen(clean_ddi, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            publish.publish_release(changed, fixture_approval(changed), clean_ddi)
    finally:
        event.remove(clean_ddi, "before_cursor_execute", fail_insert)
    assert len(publish.list_releases(clean_ddi)) == 1
    assert (
        checker.check(clean_ddi, ["DDInter1", "DDInter2"], original["version"])["pairs"][0][
            "highest_known_severity"
        ]
        == "Major"
    )
