import hashlib
from copy import deepcopy

from x_insight.cases import new_case
from x_insight.ddi.lexicomp import current_content_hash
from x_insight.operations.restore import _check_model_artifacts, _check_new_case_state


def staged_tables():
    content = new_case.clinical_content()
    files = content["manifest"]["files"]
    pins = {
        "workflow_manifest_hash": content["hash"],
        "model_version_id": "model",
        "model_sha256": files["oral-antipsychotic.xml"],
        "lexicomp_release_id": "release",
        "lexicomp_version": content["manifest"]["lexicomp_version"],
        "lexicomp_content_hash": current_content_hash(),
        "assessment_versions": content["manifest"]["assessment_versions"],
        "lab_content_hash": files["labs.json"],
        "bp_classification_hash": files["blood-pressure-lowering-drugs.json"],
        "formulation_content_hash": files["antipsychotic-formulations.json"],
    }
    return {
        "patients": [{"id": "patient", "birth_year": 1996}],
        "encounters": [
            {
                "id": "encounter",
                "patient_id": "patient",
                "author_id": "author",
                "kind": "registration",
                "workflow_version": "new-case-v1",
                "created_at": "2026-11-09T00:00:00Z",
                "draft_data": new_case.fresh_draft(2026, pins),
            }
        ],
        "model_network_versions": [{"id": "model", "source_hash": pins["model_sha256"]}],
        "ddi_dataset_releases": [
            {
                "id": "release",
                "version": pins["lexicomp_version"],
                "content_hash": current_content_hash(),
                "terminology_checksum": content["manifest"]["lexicomp_sha256"],
            }
        ],
    }


def test_staged_new_case_identity_and_legacy_defaults():
    tables = staged_tables()
    assert _check_new_case_state(tables, "0030") is None
    assert _check_new_case_state(tables, "0029") is not None
    old = deepcopy(tables)
    old["encounters"][0].pop("workflow_version")
    old["encounters"][0]["draft_data"] = {}
    assert _check_new_case_state(old, "0029") is None
    assert _check_new_case_state(old, "0030") is not None


def test_staging_rejects_forged_pin_override_and_assertion():
    tables = staged_tables()
    raw = tables["encounters"][0]["draft_data"]["new_case"]
    raw["content_pins"]["lab_content_hash"] = "forged"
    assert _check_new_case_state(tables, "0030") is not None
    tables = staged_tables()
    tables["encounters"][0]["draft_data"]["new_case"]["conditions"]["renal_failure"].update(
        manual=True, manual_actor_id="stranger", manual_recorded_at="2026-11-09T00:00:00Z"
    )
    assert _check_new_case_state(tables, "0030") is not None
    tables = staged_tables()
    tables["encounters"][0]["draft_data"]["diagnosis"]["new_case_assertion"] = {
        "status": "definite_diagnosis"
    }
    assert _check_new_case_state(tables, "0030") is not None


def test_only_exact_trusted_model_bypasses_generic_admission(monkeypatch):
    from x_insight.ddi.lexicomp import content_directory
    from x_insight.models import validation

    calls = []

    def reject(raw):
        calls.append(raw)
        raise validation.XmlValidationError("generic_rejected", "Not the trusted model")

    monkeypatch.setattr(validation, "validate", reject)
    raw = (content_directory() / "new-case/v1/oral-antipsychotic.xml").read_bytes()
    row = {"id": "model", "source_hash": hashlib.sha256(raw).hexdigest(), "source_bytes": raw}
    assert (
        _check_model_artifacts({"model_network_versions": [row]}, {"networks/model.xml": raw})
        is None
    )
    assert calls == []
    fake = b"<arbitrary/>"
    row.update(
        source_hash=hashlib.sha256(fake).hexdigest(),
        source_bytes=fake,
        review={"execution_profile": "fully-observed-oral-row-lookup/1"},
    )
    assert (
        _check_model_artifacts({"model_network_versions": [row]}, {"networks/model.xml": fake})
        is not None
    )
    assert calls == [fake]


def test_follow_up_archives_fail_closed_until_their_contract_is_validated():
    from x_insight.operations.restore import _check_new_case_state, _check_references

    tables = {"users": [], "patients": [], "encounters": [], "follow_up_proposals": [{"id": "p"}]}
    assert _check_references(tables) == "Follow-up proposals are not yet supported by restore."
    tables["follow_up_proposals"] = []
    tables["encounters"] = [
        {"workflow_version": "follow-up-v1", "kind": "follow_up", "draft_data": {}}
    ]
    assert _check_new_case_state(tables, "0032") == "Unknown encounter workflow identity."
