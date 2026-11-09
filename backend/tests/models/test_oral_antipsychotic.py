import hashlib
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text
from sqlalchemy.orm import Session

from x_insight.models import oral_antipsychotic as oral
from x_insight.models.registry import install_bundled_oral_model

pytest_plugins = ["tests.ddi.test_publish"]

MODEL = Path(__file__).resolve().parents[3] / "content/new-case/v1/oral-antipsychotic.xml"
SHA = "f2f68f970b2fd13ec9ec14bc7d6163dbf859c43d2ba379b7543563bce530b18a"


@pytest.fixture(scope="module")
def model():
    return MODEL.read_bytes()


def test_exact_rows_and_filtering(model):
    root = etree.fromstring(model, etree.XMLParser(huge_tree=True, resolve_entities=False))
    table = next(
        d.findtext("TABLE")
        for d in root.findall(".//DEFINITION")
        if d.findtext("FOR") == "Antipsychotic"
    ).split()
    for index in (0, 1, 32768, 32769, 43690, 65535):
        evidence = {
            parent: bool(index & (1 << (15 - offset))) for offset, parent in enumerate(oral.PARENTS)
        }
        result = oral.rank_oral(model, SHA, evidence)
        expected = [float(value) for value in table[index * 22 : (index + 1) * 22]]
        assert {item.name: item.probability for item in result.distribution} == dict(
            zip(oral.OUTCOMES, expected, strict=True)
        )
        assert [item.probability for item in result.distribution] == sorted(expected, reverse=True)
    evidence = dict.fromkeys(oral.PARENTS, False)
    original = oral.rank_oral(model, SHA, evidence)
    unavailable = frozenset(item.drug_id for item in original.candidates[:2])
    filtered = oral.rank_oral(model, SHA, evidence, unavailable | {"lai:haloperidol"})
    assert filtered.candidates == original.candidates[2:] + original.distribution[5:7]
    assert (
        oral.rank_oral(
            model, SHA, evidence, frozenset(item.drug_id for item in original.distribution)
        ).candidates
        == ()
    )
    assert oral.rank_oral(model, SHA, evidence, frozenset({"lai:haloperidol"})) == original


def test_rejects_invalid_inputs_and_corrupt_model(model):
    evidence = dict.fromkeys(oral.PARENTS, False)
    for invalid in (
        {},
        {**evidence, "extra": False},
        {**evidence, "Smoking": 1},
        {**evidence, "Smoking": "Unknown"},
    ):
        with pytest.raises(ValueError):
            oral.rank_oral(model, SHA, invalid)
    with pytest.raises(ValueError):
        oral.load_model(model + b" ", SHA)
    swapped = model.replace(b"<GIVEN>Smoking</GIVEN>", b"<GIVEN>Akathisia</GIVEN>", 1)
    with pytest.raises(ValueError):
        oral.load_model(swapped, hashlib.sha256(swapped).hexdigest())
    hostile = b'<!DOCTYPE BIF [<!ENTITY x "bad">]>' + model
    with pytest.raises(ValueError):
        oral.load_model(hostile, hashlib.sha256(hostile).hexdigest())


def test_registry_transaction_and_repeat_install(migrated_test_engine, model):
    connection = migrated_test_engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection) as session:
            first = install_bundled_oral_model(session)
            second = install_bundled_oral_model(session)
            assert not first["reused"] and second["reused"]
            assert first["version_id"] == second["version_id"]
            stored = (
                session.execute(
                    text(
                        "SELECT source_bytes, status, review FROM model_network_versions "
                        "WHERE id = :id"
                    ),
                    {"id": first["version_id"]},
                )
                .mappings()
                .one()
            )
            assert bytes(stored["source_bytes"]) == model
            assert stored["status"] == "awaiting_review"
            assert stored["review"]["execution_profile"] == "fully-observed-oral-row-lookup/1"
            assert (
                session.execute(text("SELECT count(*) FROM model_workflow_pointers")).scalar_one()
                == 0
            )
    finally:
        transaction.rollback()
        connection.close()
