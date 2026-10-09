"""Immutable F6 correction: validate actual reference XML and its new release pins."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from math import prod
from pathlib import Path
from typing import Any

import pytest

from x_insight.models.admission import check_semantics
from x_insight.models.bundles import FOLLOWUP_ORDER, validate_bundle
from x_insight.models.question_package import load_question_package
from x_insight.models.validation import validate

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / "content/questions/continue_or_adjust"
NEW = OLD / "s38-v2"


def _read(path: Path) -> tuple[dict[str, Any], Any, tuple[str, ...]]:
    package = {
        name: json.loads((path / f"{name}.json").read_text())
        for name in ("manifest", "template", "examples", "review")
    }
    package["prompt"] = {
        "version": package["manifest"]["prompt_version"],
        "text": (path / "prompt.txt").read_text(),
    }
    return (
        package,
        validate((path / "network.xml").read_bytes()),
        tuple(package["manifest"]["applicability"]["required_fields"]),
    )


def test_reference_tables_have_exact_dimensions_and_normalized_preserved_rows() -> None:
    old_package, old, paths = _read(OLD)
    package, document, _ = _read(NEW)
    assert load_question_package(old_package, old, known_source_prefixes=paths).package_hash == (
        "199c61814ceea876d16b2547ecd996ad90ba7fc16be26c4a5b7cc334fa794899"
    )
    errors = check_semantics(old).errors
    assert len(errors) == 21
    assert all(e.code == "wrong_dimensions" for e in errors)
    semantic = check_semantics(document)
    assert semantic.valid, semantic.errors
    before, after = old.networks[0], document.networks[0]
    assert before.variables == after.variables
    cards = {v.name: len(v.states) for v in after.variables}
    for original, corrected in zip(before.definitions, after.definitions, strict=True):
        assert corrected.for_node == original.for_node
        assert corrected.parents == original.parents
        card = cards[corrected.for_node]
        assert len(corrected.table) == card * prod(cards[p] for p in corrected.parents)
        assert len(original.table) == len(corrected.table) * card
        assert corrected.table == original.table[: len(corrected.table)]
        for start in range(0, len(corrected.table), card):
            row = corrected.table[start : start + card]
            assert sum(Decimal(value) for value in row) == 1
            assert row == original.table[:card]
    assert sum(len(d.table) for d in after.definitions) == 314
    assert package["manifest"]["execution_evidence"] == {}
    assert package["manifest"]["review_status"] == "awaiting_review"
    assert package["review"]["decision"] == "awaiting_review"
    assert package["review"]["reference_table_audit"]["clinical_probability_claim"] is False


def test_new_followup_release_pins_corrected_package_and_still_requires_review() -> None:
    release = json.loads((ROOT / "content/bundles/release.s39-v2.json").read_text())
    bundle_ref = release["bundles"]["followup"]
    bundle_path = ROOT / bundle_ref["file"]
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == bundle_ref["file_sha256"]
    bundle = json.loads(bundle_path.read_text())
    assert bundle["version"] == release["version"] == "s39-v2"
    packages, documents, paths = {}, {}, set()
    for key in FOLLOWUP_ORDER:
        directory = NEW if key == "continue_or_adjust" else ROOT / f"content/questions/{key}"
        package, document, prefixes = _read(directory)
        packages[key], documents[key] = package, document
        paths.update(prefixes)
        loaded = load_question_package(package, document, known_source_prefixes=prefixes)
        entry = next(e for e in bundle["questions"] if e["question_key"] == key)
        assert entry["package_hash"] == loaded.package_hash
        assert entry["network_hash"] == document.source_sha256
        assert entry["version"] == package["manifest"]["version"]
    report = validate_bundle(bundle, packages, documents, known_source_prefixes=tuple(paths))
    assert not report.valid
    assert {e.code for e in report.errors} == {"unreviewed_bundle", "unreviewed_question"}
    f6 = next(p for p in release["packages"] if p["question_key"] == "continue_or_adjust")
    assert f6["package_directory"] == str(NEW.relative_to(ROOT))
    assert f6["admission"]["semantic_valid"] is True
    assert f6["admission"]["semantic_codes"] == []
    assert f6["admission"]["xml_bytes"] == len((NEW / "network.xml").read_bytes())
    assert release["activation"]["status"] == "blocked"
    # Recompute the public loader's hash without approving the real content:
    # this hash covers identity/pins, independent of review status.
    core = {
        "schema_version": bundle["schema_version"],
        "workflow": bundle["workflow"],
        "version": bundle["version"],
        "questions": [
            {k: e[k] for k in ("question_key", "version", "network_hash", "package_hash")}
            for e in bundle["questions"]
        ],
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == bundle_ref["bundle_hash"]
    assert f6["network_hash"] == documents["continue_or_adjust"].source_sha256
    assert f6["package_hash"] == bundle["questions"][-1]["package_hash"]
    assert (
        release["bundles"]["registration"]
        == json.loads((ROOT / "content/bundles/release.json").read_text())["bundles"][
            "registration"
        ]
    )


@pytest.mark.parametrize("node", ("SchizophreniaEstablished", "SameAgentDiscussion"))
def test_extra_reference_row_is_rejected(node: str) -> None:
    source = (NEW / "network.xml").read_bytes()
    from lxml import etree

    tree = etree.fromstring(source)
    definition = next(d for d in tree.findall("NETWORK/DEFINITION") if d.findtext("FOR") == node)
    table = definition.find("TABLE")
    assert table is not None and table.text
    table.text += " " + table.text.split()[0]
    semantic = check_semantics(validate(etree.tostring(tree)))
    assert [(e.code, e.node) for e in semantic.errors] == [("wrong_dimensions", node)]
