import csv
import hashlib
import json
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CSV_DIR = ROOT / "content/ddi/lexicomp/v1"
MODEL = ROOT / "content/new-case/v1/oral-antipsychotic.xml"


def normalized(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def test_new_case_bundled_source_inventory() -> None:
    manifest = json.loads((CSV_DIR / "manifest.json").read_text())
    csv_path = CSV_DIR / manifest["source_file"]
    assert csv_path.stat().st_size == manifest["bytes"]
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == manifest["sha256"]
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest() == (
        "f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a"
    )

    names: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    ratings: Counter[str] = Counter()
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == manifest["columns"]
        for row in reader:
            assert None not in row and all(row.values())
            first, second = normalized(row["Drug_1"]), normalized(row["Drug_2"])
            assert first and second and first != second
            pair = tuple(sorted((first, second)))
            assert pair not in pairs
            pairs.add(pair)
            names.update(pair)
            ratings[row["Lexicomp_level"]] += 1
    assert len(pairs) == manifest["counts"]["rows"]
    assert len(names) == manifest["counts"]["catalog_names"]
    assert ratings == manifest["counts"]["ratings"]

    root = ET.parse(MODEL).getroot()
    targets = [node for node in root.findall(".//VARIABLE") if node.findtext("NAME") == "Antipsychotic"]
    assert len(targets) == 1
    outcomes = [node.text for node in targets[0].findall("OUTCOME")]
    assert len(outcomes) == 22
    assert all(outcome is not None and normalized(outcome) in names for outcome in outcomes)
