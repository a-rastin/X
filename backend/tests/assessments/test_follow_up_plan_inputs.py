"""Fingerprint changes and content failure at the follow-up proposal boundary."""

from copy import deepcopy
from uuid import uuid4

import pytest

from x_insight import contracts
from x_insight.cases import follow_up, follow_up_baseline, follow_up_content, follow_up_plans
from x_insight.ddi.lexicomp import content_directory


class Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def all(self):
        return self.value

    def first(self):
        return self.value


class Session:
    def __init__(self, content):
        manifest = content["manifest"]
        self.model = [
            {
                "id": uuid4(),
                "source_bytes": (
                    content_directory() / "new-case/v1/oral-antipsychotic.xml"
                ).read_bytes(),
                "review": {
                    "execution_profile": "fully-observed-oral-row-lookup/1",
                    "manifest_hash": contracts.canonical_hash(
                        content["assets"]["new-case/v1/oral-model-manifest.json"]
                    ),
                },
            }
        ]
        self.release = {
            "id": uuid4(),
            "content_hash": manifest["lexicomp_content_hash"],
            "terminology_checksum": manifest["files"]["ddi/lexicomp/v1/Lexicomp-cleaned.csv"],
            "parser_version": manifest["lexicomp_parser_version"],
        }

    def execute(self, query, params):
        return Rows(self.model if "model_network_versions" in str(query) else self.release)


def test_fingerprint_and_content_failure(monkeypatch, tmp_path):
    content = follow_up_content.load_content()
    pins = follow_up.content_pins(content)
    patient = {"sex": "F", "age": 30, "birth_year": None, "clinical_status": "established"}
    baseline = follow_up_baseline.project_baseline(
        None, None, patient, lexicomp_version=pins["lexicomp_version"]
    )
    draft = follow_up.fresh_draft(2026, pins, baseline)
    user = {"revision": 1, "unavailable_antipsychotics": []}
    session = Session(content)
    digest = follow_up_plans.verified_inputs(session, draft, patient, user)[2]
    assert digest == follow_up_plans.verified_inputs(session, draft, patient, user)[2]
    assert (
        digest != follow_up_plans.verified_inputs(session, draft, {**patient, "age": 31}, user)[2]
    )
    assert (
        digest
        != follow_up_plans.verified_inputs(session, draft, patient, {**user, "revision": 2})[2]
    )
    changed = deepcopy(draft)
    changed["follow_up"]["harm"]["self"] = True
    assert digest != follow_up_plans.verified_inputs(session, changed, patient, user)[2]
    # Even a same-version assessment byte change invalidates the frozen pins.
    from x_insight.assessments.released import assessments_dir

    for name, version in content["manifest"]["assessment_versions"].items():
        filename = f"{name}.{version}.json"
        (tmp_path / filename).write_bytes((assessments_dir() / filename).read_bytes() + b" ")
    with monkeypatch.context() as patch:
        patch.setattr(follow_up, "assessments_dir", lambda: tmp_path)
        with pytest.raises(contracts.ContractError):
            follow_up_plans.verified_inputs(session, draft, patient, user)
    session.model[0]["source_bytes"] = b"corrupt"
    with pytest.raises(contracts.ContractError, match="pinned follow-up content"):
        follow_up_plans.verified_inputs(session, draft, patient, user)
