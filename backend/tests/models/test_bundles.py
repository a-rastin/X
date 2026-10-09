"""S39 workflow bundles through public T5 seams only.

Seams T5: validate_question_package / load_question_package + XML
validation.validate + admission.check_semantics. Never DB/HTTP/provider/MCP.
Expected values are worked literals or on-disk hashes, never implementation
output. Synthetic fixtures for RED slices; on-disk content for inventory.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _two_node_xml(name: str = "TwoNode") -> bytes:
    return (
        f'<BIF VERSION="0.3"><NETWORK><NAME>{name}</NAME>'.encode()
        + b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        + b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        + b"<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        + b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.9 0.1 0.3 0.7</TABLE></DEFINITION>"
        + b"</NETWORK></BIF>"
    )


def _missing_definition_xml() -> bytes:
    # XSD-valid shape with a non-normalized row: S25 content still fits
    # (nodes/states/parents match) but S22 semantics is nonexecutable.
    return (
        b'<BIF VERSION="0.3"><NETWORK><NAME>Missing</NAME>'
        b"<VARIABLE><NAME>A</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<VARIABLE><NAME>B</NAME><OUTCOME>no</OUTCOME><OUTCOME>yes</OUTCOME></VARIABLE>"
        b"<DEFINITION><FOR>A</FOR><TABLE>0.8 0.2</TABLE></DEFINITION>"
        b"<DEFINITION><FOR>B</FOR><GIVEN>A</GIVEN><TABLE>0.9 0.2 0.3 0.7</TABLE></DEFINITION>"
        b"</NETWORK></BIF>"
    )


def _synthetic_package(key: str, workflow: str = "registration") -> dict[str, Any]:
    return {
        "manifest": {
            "schema_version": "question-package-v1",
            "question_key": key,
            "title": f"{key} synthetic",
            "workflow": workflow,
            "version": "s39-test-v1",
            "review_status": "approved",
            "network_file": "network.xml",
            "network_hash": "REPLACE",
            "source_refs": ["synthetic/source.md"],
            "declared_node_order": ["A", "B"],
            "variables": [
                {
                    "node_id": "A",
                    "kind": "nature",
                    "patient_value_type": "categorical",
                    "states": ["no", "yes"],
                    "ordered_parents": [],
                },
                {
                    "node_id": "B",
                    "kind": "nature",
                    "patient_value_type": "categorical",
                    "states": ["no", "yes"],
                    "ordered_parents": ["A"],
                },
            ],
            "patient_mappings": [
                {
                    "node_id": "A",
                    "allowed_source_paths": ["candidate/history/x/h_a"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
                {
                    "node_id": "B",
                    "allowed_source_paths": ["candidate/history/x/h_b"],
                    "transform": "copy",
                    "time_window": "current_encounter",
                    "usage": "cpt_context",
                    "missing_policy": "needs_clarification",
                },
            ],
            "applicability": {
                "expression": "true",
                "required_fields": [
                    "candidate/history/x/h_a",
                    "candidate/history/x/h_b",
                ],
                "unknown_policy": "needs_clarification",
            },
            "cpt_contract": {
                "nodes": [
                    {"node_id": "A", "parent_ids": [], "states": ["no", "yes"]},
                    {"node_id": "B", "parent_ids": ["A"], "states": ["no", "yes"]},
                ]
            },
            "query_nodes": ["B"],
            "execution_evidence": {},
            "prompt_version": "s39-test-v1",
            "template_version": "s39-test-v1",
            "history_definition": "candidate/history/x.json",
        },
        "prompt": {
            "version": "s39-test-v1",
            "text": "Estimate every CPT in percentage units from supplied inputs.",
        },
        "template": {
            "version": "s39-test-v1",
            "branches": [
                {"when": {"node": "B", "state": "no", "operator": "=="}, "text": "B is {B}."},
                {"when": {"node": "B", "state": "yes", "operator": "=="}, "text": "B is {B}."},
            ],
        },
        "examples": {
            "numerical": [{"id": "n1", "expected": {"B": {"no": 0.5}}}],
            "clinical": [{"id": "c1", "expected_for_review": {"status": "execute"}}],
        },
        "review": {
            "reviewer": "owner",
            "decision": "approved",
            "date": "2026-10-09",
            "source_hashes": {"network.xml": "REPLACE"},
            "assumptions": "synthetic",
            "source_comparison": "synthetic",
            "explicit_graph": "synthetic",
            "reference_table_provenance": "synthetic uniform, no clinical claim",
            "estimation_instructions": "synthetic",
            "result_mapping": "synthetic",
            "numerical_examples": "synthetic",
            "clinical_examples": "synthetic",
            "admission_measurements": "synthetic",
            "open_assumptions": "synthetic",
        },
    }


def _bundle_for(keys: list[str], workflow: str = "registration") -> dict[str, Any]:
    return {
        "schema_version": "workflow-bundle-v1",
        "workflow": workflow,
        "version": "s39-test-v1",
        "assessment_refs": ["content/assessments/diagnosis.v1.json"],
        "ddi_ref": "content/ddi/unreleased",
        "questions": [
            {
                "question_key": k,
                "version": "s39-test-v1",
                "network_hash": "REPLACE",
                "package_hash": "REPLACE",
                "prompt_version": "s39-test-v1",
                "template_version": "s39-test-v1",
                "history_definition": "candidate/history/x.json",
            }
            for k in keys
        ],
        "review": {"reviewer": "owner", "decision": "approved", "date": "2026-10-09"},
    }


def _sealed_pair(
    key: str, workflow: str = "followup"
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    from x_insight.models.question_package import load_question_package
    from x_insight.models.validation import validate

    pkg = _synthetic_package(key, workflow=workflow)
    doc = validate(_two_node_xml(key))
    pkg["manifest"]["network_hash"] = doc.source_sha256
    pkg["review"]["source_hashes"] = {"network.xml": doc.source_sha256}
    loaded = load_question_package(pkg, doc, known_source_prefixes=("candidate/history/",))
    entry = {
        "question_key": key,
        "version": "s39-test-v1",
        "network_hash": doc.source_sha256,
        "package_hash": loaded.package_hash,
        "prompt_version": "s39-test-v1",
        "template_version": "s39-test-v1",
        "history_definition": "candidate/history/x.json",
    }
    return pkg, doc, entry


def test_bundle_rejects_missing_question() -> None:
    from x_insight.models import bundles as bundles_module
    from x_insight.models.validation import validate

    pkg = _synthetic_package("q_one")
    doc = validate(_two_node_xml())
    pkg["manifest"]["network_hash"] = doc.source_sha256
    pkg["review"]["source_hashes"] = {"network.xml": doc.source_sha256}
    bundle = _bundle_for(["q_one", "q_two"])
    # Only q_one supplied; q_two is missing.
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one", "q_two"),
    )
    assert not report.valid
    assert any(e.code == "missing_question" for e in report.errors)


def test_bundle_rejects_duplicate_key() -> None:
    from x_insight.models import bundles as bundles_module
    from x_insight.models.validation import validate

    pkg = _synthetic_package("q_one")
    doc = validate(_two_node_xml())
    pkg["manifest"]["network_hash"] = doc.source_sha256
    pkg["review"]["source_hashes"] = {"network.xml": doc.source_sha256}
    bundle = _bundle_for(["q_one", "q_one"], workflow="followup")
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one", "q_two"),
    )
    assert not report.valid
    assert any(e.code == "duplicate_key" for e in report.errors)


def test_bundle_rejects_unreviewed_question_and_bundle() -> None:
    from x_insight.models import bundles as bundles_module

    pkg, doc, entry = _sealed_pair("q_one")
    # Flip package to awaiting_review (shape still valid, cannot activate).
    pkg["manifest"]["review_status"] = "awaiting_review"
    pkg["review"]["decision"] = "awaiting_review"
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [entry]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code == "unreviewed_question" for e in report.errors)
    # Unreviewed bundle review alone also blocks.
    pkg2, doc2, entry2 = _sealed_pair("q_two")
    bad_bundle = _bundle_for(["q_two"], workflow="followup")
    bad_bundle["questions"] = [entry2]
    bad_bundle["review"] = {"reviewer": "", "decision": "draft", "date": ""}
    report2 = bundles_module.validate_bundle(
        bad_bundle,
        packages={"q_two": pkg2},
        documents={"q_two": doc2},
        expected_order=("q_two",),
    )
    assert not report2.valid
    assert any(e.code == "unreviewed_bundle" for e in report2.errors)


def test_bundle_rejects_incompatible_mapping_content() -> None:
    from x_insight.models import bundles as bundles_module

    pkg, doc, entry = _sealed_pair("q_one")
    pkg["manifest"]["patient_mappings"][0]["usage"] = "evidence"
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [entry]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code == "incompatible_mapping" for e in report.errors)


def test_bundle_rejects_chained_result_mapping() -> None:
    from x_insight.models import bundles as bundles_module

    pkg, doc, entry = _sealed_pair("q_one")
    pkg["manifest"]["patient_mappings"][0]["allowed_source_paths"] = [
        "candidate/history/posterior_B"
    ]
    pkg["manifest"]["applicability"]["required_fields"] = ["candidate/history/posterior_B"]
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [entry]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code in ("incompatible_mapping", "missing_pinned_ref") for e in report.errors)


def test_bundle_rejects_unresolved_query_template() -> None:
    from x_insight.models import bundles as bundles_module

    pkg, doc, entry = _sealed_pair("q_one")
    pkg["manifest"]["query_nodes"] = ["ZZZ"]
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [entry]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code == "unresolved_query_template" for e in report.errors)


def test_bundle_rejects_nonexecutable_network() -> None:
    from x_insight.models import bundles as bundles_module
    from x_insight.models.validation import validate

    pkg = _synthetic_package("q_one", workflow="followup")
    doc = validate(_missing_definition_xml())
    # Manifest hash tracks the broken document so the failure is semantic,
    # not a hash mismatch.
    pkg["manifest"]["network_hash"] = doc.source_sha256
    pkg["review"]["source_hashes"] = {"network.xml": doc.source_sha256}
    from x_insight.models.question_package import load_question_package

    loaded = load_question_package(pkg, doc, known_source_prefixes=("candidate/history/",))
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [
        {
            "question_key": "q_one",
            "version": "s39-test-v1",
            "network_hash": doc.source_sha256,
            "package_hash": loaded.package_hash,
            "prompt_version": "s39-test-v1",
            "template_version": "s39-test-v1",
            "history_definition": "candidate/history/x.json",
        }
    ]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code == "nonexecutable_network" for e in report.errors)


def test_bundle_rejects_hash_and_pinned_ref_mismatch() -> None:
    from x_insight.models import bundles as bundles_module

    pkg, doc, entry = _sealed_pair("q_one")
    bad_entry = dict(entry)
    bad_entry["network_hash"] = "0" * 64
    bundle = _bundle_for(["q_one"], workflow="followup")
    bundle["questions"] = [bad_entry]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg},
        documents={"q_one": doc},
        expected_order=("q_one",),
    )
    assert not report.valid
    assert any(e.code == "incompatible_content" for e in report.errors)
    # Missing pinned history ref is its own code.
    pkg2, doc2, entry2 = _sealed_pair("q_two")
    bad_entry2 = dict(entry2)
    bad_entry2["history_definition"] = ""
    bundle2 = _bundle_for(["q_two"], workflow="followup")
    bundle2["questions"] = [bad_entry2]
    report2 = bundles_module.validate_bundle(
        bundle2,
        packages={"q_two": pkg2},
        documents={"q_two": doc2},
        expected_order=("q_two",),
    )
    assert not report2.valid
    assert any(e.code == "missing_pinned_ref" for e in report2.errors)


def test_valid_synthetic_bundle_loads_stable_hash() -> None:
    from x_insight.models import bundles as bundles_module

    pkg1, doc1, entry1 = _sealed_pair("q_one")
    pkg2, doc2, entry2 = _sealed_pair("q_two")
    bundle = _bundle_for(["q_one", "q_two"], workflow="followup")
    bundle["questions"] = [entry1, entry2]
    report = bundles_module.validate_bundle(
        bundle,
        packages={"q_one": pkg1, "q_two": pkg2},
        documents={"q_one": doc1, "q_two": doc2},
        expected_order=("q_one", "q_two"),
    )
    assert report.valid, report.errors
    first = bundles_module.load_bundle(
        bundle,
        packages={"q_one": pkg1, "q_two": pkg2},
        documents={"q_one": doc1, "q_two": doc2},
        expected_order=("q_one", "q_two"),
    )
    second = bundles_module.load_bundle(
        copy.deepcopy(bundle),
        packages={"q_one": pkg1, "q_two": pkg2},
        documents={"q_one": doc1, "q_two": doc2},
        expected_order=("q_one", "q_two"),
    )
    assert first.bundle_hash == second.bundle_hash
    assert len(first.bundle_hash) == 64
    assert first.question_keys == ("q_one", "q_two")


def test_canonical_registration_bundle_holds_single_lai_network() -> None:
    from x_insight.models import bundles as bundles_module
    from x_insight.models.bundles import FOLLOWUP_ORDER, REGISTRATION_ORDER

    assert tuple(REGISTRATION_ORDER) == (
        "pharmacotherapy",
        "high_suicide_clozapine",
        "lai_indication_choice",
        "aggression_clozapine",
        "established_case_clozapine",
    )
    assert tuple(FOLLOWUP_ORDER) == (
        "tardive_dyskinesia",
        "akathisia",
        "parkinsonism",
        "acute_dystonia",
        "no_improvement_clozapine",
        "continue_or_adjust",
    )
    # Exactly one LAI entry in registration, none in followup.
    assert REGISTRATION_ORDER.count("lai_indication_choice") == 1
    assert "lai_indication_choice" not in FOLLOWUP_ORDER
    # Full synthetic registration with canonical keys validates (LAI single).
    packages: dict[str, Any] = {}
    documents: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    for key in REGISTRATION_ORDER:
        pkg, doc, entry = _sealed_pair(key, workflow="registration")
        # Per-package synthetic manifest workflow must match bundle workflow
        # for the canonical-order check; sealed helper defaults to followup
        # so fix the workflow here for this registration slice.
        pkg["manifest"]["workflow"] = "registration"
        packages[key] = pkg
        documents[key] = doc
        entries.append(entry)
    bundle = _bundle_for(list(REGISTRATION_ORDER), workflow="registration")
    bundle["questions"] = entries
    report = bundles_module.validate_bundle(bundle, packages, documents)
    assert report.valid, report.errors


def _read_on_disk_package(key: str) -> tuple[dict[str, Any], bytes, tuple[str, ...]]:
    package = {
        name: json.loads((ROOT / f"content/questions/{key}/{name}.json").read_text())
        for name in ("manifest", "template", "examples", "review")
    }
    package["prompt"] = {
        "version": package["manifest"]["prompt_version"],
        "text": (ROOT / f"content/questions/{key}/prompt.txt").read_text(),
    }
    source = (ROOT / f"content/questions/{key}/network.xml").read_bytes()
    manifest = package["manifest"]
    paths = set()
    for mapping in manifest.get("patient_mappings", []):
        for path in mapping.get("allowed_source_paths", []):
            paths.add(str(path))
    for field in manifest.get("applicability", {}).get("required_fields", []):
        paths.add(str(field))
    return package, source, tuple(sorted(paths))


def test_real_bundles_are_blocked_pending_owner_review() -> None:
    from x_insight.models import bundles as bundles_module
    from x_insight.models.bundles import FOLLOWUP_ORDER, REGISTRATION_ORDER
    from x_insight.models.question_package import load_question_package
    from x_insight.models.validation import validate

    reg_bundle = json.loads((ROOT / "content/bundles/registration.json").read_text())
    fol_bundle = json.loads((ROOT / "content/bundles/followup.json").read_text())
    assert [q["question_key"] for q in reg_bundle["questions"]] == list(REGISTRATION_ORDER)
    assert [q["question_key"] for q in fol_bundle["questions"]] == list(FOLLOWUP_ORDER)
    # One LAI discussion/review network in registration, none in followup.
    assert [q["question_key"] for q in reg_bundle["questions"]].count("lai_indication_choice") == 1
    assert "lai_indication_choice" not in [q["question_key"] for q in fol_bundle["questions"]]
    # Pinned refs present, no implicit chaining in bundle entries.
    for bundle in (reg_bundle, fol_bundle):
        assert bundle["schema_version"] == "workflow-bundle-v1"
        assert bundle["assessment_refs"]
        assert bundle["ddi_ref"].strip()
        for entry in bundle["questions"]:
            for field in (
                "version",
                "network_hash",
                "package_hash",
                "prompt_version",
                "template_version",
                "history_definition",
            ):
                assert isinstance(entry[field], str) and entry[field].strip()

    packages: dict[str, Any] = {}
    documents: dict[str, Any] = {}
    for key in (*REGISTRATION_ORDER, *FOLLOWUP_ORDER):
        package, source, prefixes = _read_on_disk_package(key)
        document = validate(source)
        # Exact network hash pin.
        assert package["manifest"]["network_hash"] == document.source_sha256
        loaded = load_question_package(package, document, known_source_prefixes=prefixes)
        # Exact canonical package hash pin vs bundle entry.
        bundle = reg_bundle if key in REGISTRATION_ORDER else fol_bundle
        entry = next(q for q in bundle["questions"] if q["question_key"] == key)
        assert entry["network_hash"] == document.source_sha256
        assert entry["package_hash"] == loaded.package_hash
        assert entry["version"] == package["manifest"]["version"]
        packages[key] = package
        documents[key] = document

    reg_report = bundles_module.validate_bundle(
        reg_bundle,
        {k: packages[k] for k in REGISTRATION_ORDER},
        {k: documents[k] for k in REGISTRATION_ORDER},
    )
    assert not reg_report.valid
    assert any(e.code == "unreviewed_question" for e in reg_report.errors)

    fol_report = bundles_module.validate_bundle(
        fol_bundle,
        {k: packages[k] for k in FOLLOWUP_ORDER},
        {k: documents[k] for k in FOLLOWUP_ORDER},
    )
    assert not fol_report.valid
    codes = {e.code for e in fol_report.errors}
    assert "unreviewed_question" in codes
    # F6 placeholder TABLEs are dimension-invalid by design; activation must
    # also report nonexecutable, never silently pass on review alone.
    assert "nonexecutable_network" in codes


def test_release_manifest_pins_hashes_gates_and_provenance() -> None:
    release = json.loads((ROOT / "content/bundles/release.json").read_text())
    assert release["version"] == "s39-v1"
    assert release["review"]["decision"] == "awaiting_review"
    assert "author" in release["approval_basis"].lower()
    by_key = {p["question_key"]: p for p in release["packages"]}
    assert len(by_key) == 11
    for key in (
        "pharmacotherapy",
        "high_suicide_clozapine",
        "lai_indication_choice",
        "aggression_clozapine",
        "established_case_clozapine",
        "tardive_dyskinesia",
        "akathisia",
        "parkinsonism",
        "acute_dystonia",
        "no_improvement_clozapine",
        "continue_or_adjust",
    ):
        entry = by_key[key]
        package, source, prefixes = _read_on_disk_package(key)
        from x_insight.models.question_package import load_question_package
        from x_insight.models.validation import validate

        document = validate(source)
        loaded = load_question_package(package, document, known_source_prefixes=prefixes)
        assert entry["network_hash"] == document.source_sha256
        assert entry["package_hash"] == loaded.package_hash
        # Gate coverage: true/false/unknown/missing/conflict must be explicit.
        assert entry["gate"]["expression"].strip()
        assert entry["gate"]["required_fields"] > 0
        assert entry["gate"]["unknown_policy"] == "needs_clarification"
        assert len(entry["gate"]["clinical_ids"]) >= 6
        # Reference tables: provenance reviewed, never a clinical claim.
        assert isinstance(entry["reference_table"]["provenance"], str)
        assert entry["reference_table"]["provenance"].strip()
        assert entry["reference_table"]["clinical_claim"] is False
        # Admission: XSD valid; semantic recorded honestly (F6 is False).
        assert entry["admission"]["xsd_valid"] is True
        assert isinstance(entry["admission"]["semantic_valid"], bool)
    # Continue_or_adjust S38 prose fixes are pinned old->new.
    fixes = release["s38_prose_fixes"]["continue_or_adjust"]
    assert (
        fixes["old_package_hash"]
        == "f75cd60091b5c3a1d83a37473b18ba74d048a11422f263b380dca904150fe569"
    )
    assert fixes["new_package_hash"] == by_key["continue_or_adjust"]["package_hash"]
    assert fixes["new_package_hash"] != fixes["old_package_hash"]
    # Activation stays blocked through the registry command only.
    assert release["activation"]["status"] == "blocked"
    assert "registry.activate_bundle" in release["activation"]["method"]
