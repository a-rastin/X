import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from x_insight.cases.follow_up_content import load_content
from x_insight.ddi.lexicomp import drug_id
from x_insight.models.oral_antipsychotic import PARENTS

ROOT = Path(__file__).resolve().parents[3]


def test_follow_up_sources(tmp_path, monkeypatch):
    content = tmp_path / "content"
    for directory in ("follow-up/v1", "new-case/v1", "ddi/lexicomp/v1"):
        shutil.copytree(ROOT / "content" / directory, content / directory)
    monkeypatch.setenv("X_INSIGHT_CONTENT_DIR", str(content))
    monkeypatch.chdir(tmp_path)
    loaded = load_content()
    manifest, assets = loaded["manifest"], loaded["assets"]
    assert len(loaded["hash"]) == 64
    assert list(assets["follow-up/v1/factors.json"]["sources"]) == list(PARENTS)
    assert not {"approval", "review", "attestation"} & manifest.keys()

    graph = (
        ROOT / "project-documents/changes/follow-up-2026-11-12/decision-flow.original.mmd"
    ).read_bytes()
    assert hashlib.sha256(graph).hexdigest() == manifest["source_graph_sha256"]
    text = graph.decode()
    nodes = set(re.findall(r"^\s*(\w+)[\[(]", text, re.MULTILINE))
    outgoing = set(re.findall(r"^\s*(\w+)\s*-->", text, re.MULTILINE))
    terminals = nodes - outgoing
    assert nodes == set(assets["follow-up/v1/rules.json"]["graph_nodes"])
    assert terminals == set(assets["follow-up/v1/rules.json"]["graph_terminals"])

    inventory = {
        item["id"]: item
        for item in assets["new-case/v1/antipsychotic-formulations.json"]["entries"]
    }
    supported = {
        (item, "lai:" + item.removeprefix("oral:"))
        for item in inventory
        if item.startswith("oral:") and "lai:" + item.removeprefix("oral:") in inventory
    }
    assert supported == {
        (pair["oral"], pair["lai"])
        for pair in assets["follow-up/v1/lai-equivalences.json"]["equivalences"]
    }
    with (content / "ddi/lexicomp/v1/Lexicomp-cleaned.csv").open(
        encoding="utf-8-sig", newline=""
    ) as source:
        names = {
            drug_id(row[column])
            for row in csv.DictReader(source)
            for column in ("Drug_1", "Drug_2")
        }
    for pair in assets["follow-up/v1/lai-equivalences.json"]["equivalences"]:
        oral, lai = inventory[pair["oral"]], inventory[pair["lai"]]
        assert oral["catalog_drug_id"] in names
        assert oral["name"].casefold() == lai["name"].casefold()
        assert lai.get("catalog_drug_id", oral["catalog_drug_id"]) in names

    rules = content / "follow-up/v1/rules.json"
    rules.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        load_content()
    manifest_path = content / "follow-up/v1/manifest.json"
    modified = json.loads(manifest_path.read_text())
    modified["files"]["follow-up/v1/rules.json"] = hashlib.sha256(rules.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(modified))
    with pytest.raises((KeyError, ValueError)):
        load_content()
