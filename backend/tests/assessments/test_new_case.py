from copy import deepcopy
from decimal import Decimal

import pytest

from x_insight import contracts
from x_insight.cases import new_case
from x_insight.ddi.lexicomp import drug_id
from x_insight.models.oral_antipsychotic import PARENTS


@pytest.fixture
def content():
    return new_case.clinical_content()


@pytest.fixture
def draft(content):
    return new_case.fresh_draft(2026, {"lexicomp_version": content["manifest"]["lexicomp_version"]})


def state(draft, content, sex="F"):
    return new_case.evaluate_new_case(
        {"birth_year": 1996, "sex": sex, "age": 99},
        {"workflow_version": "new-case-v1", "kind": "registration", "author_id": "author"},
        draft,
        content,
    )


def test_explicit_defaults_and_empty_assessments(draft, content):
    result = state(draft, content)
    assert result["age"] == 30
    assert result["raw"]["panss"]["answers"] == {}
    assert result["can_complete_step"]["panss"]
    assert not result["can_complete_step"]["diagnosis"]
    assert not result["can_complete_step"]["history"]
    assert tuple(result["evidence"]) == PARENTS
    assert set(result["evidence"].values()) == {False}
    assert result["sections"]["care_setting"]["value"] == "outpatient"


@pytest.mark.parametrize(
    "field,threshold,condition,strict",
    [
        ("ast", "40", "hepatic_failure", True),
        ("alt", "40", "hepatic_failure", True),
        ("alp", "120", "hepatic_failure", True),
        ("pt", "13", "hepatic_failure", True),
        ("aptt", "35", "hepatic_failure", True),
        ("bun", "20", "renal_failure", True),
        ("creatinine", "1.10", "renal_failure", True),
        ("fbs", "100", "glucose_abnormality", False),
        ("hba1c", "5.7", "glucose_abnormality", False),
        ("ldl", "100", "hyperlipidemia", False),
        ("tg", "150", "hyperlipidemia", False),
        ("cholesterol", "200", "hyperlipidemia", False),
        ("prolactin", "23.3", "hyperprolactinemia", True),
        ("beta_hcg", "25", "pregnant", False),
    ],
)
def test_each_suggestion_boundary(draft, content, field, threshold, condition, strict):
    bound = Decimal(threshold)
    draft["new_case"]["labs"]["present"] = True
    for measurement, triggered in (
        (bound - Decimal(".01"), False),
        (bound, not strict),
        (bound + Decimal(".01"), True),
    ):
        draft["new_case"]["labs"]["values"] = {field: str(measurement)}
        result = state(draft, content)
        assert result["flags"][condition]["suggested"] is triggered
        assert result["flags"][condition]["effective"] is triggered
    draft["new_case"]["conditions"][condition]["manual"] = False
    assert not state(draft, content)["flags"][condition]["effective"]
    draft["new_case"]["labs"]["present"] = False
    assert not state(draft, content)["flags"][condition]["suggested"]


def test_hidden_data_and_independent_cssrs(draft, content):
    raw = draft["new_case"]
    raw["conditions"]["pregnant"]["manual"] = True
    raw["labs"] = {"present": True, "values": {"beta_hcg": "100", "inr": "9", "hdl": "1"}}
    raw["breastfeeding"] = True
    raw["substances"]["marijuana"] = True
    raw["psychiatric"]["conditions"] = ["depression"]
    draft["cssrs"]["answers"]["css_i4_recent"] = "yes"
    result = state(draft, content, sex="M")
    assert not result["flags"]["pregnant"]["effective"]
    assert not result["flags"]["hepatic_failure"]["suggested"]
    assert not result["flags"]["hyperlipidemia"]["suggested"]
    assert not result["evidence"]["Marijuana_Smoking"]
    assert not result["sections"]["antidepressant"]["value"]
    assert not result["sections"]["breastfeeding"]["value"]
    assert result["sections"]["care_setting"]["reasons"] == ("cssrs_item_4",)
    assert all(warning["field"] != "beta_hcg" for warning in result["lab_warnings"])


@pytest.mark.parametrize("value", [True, False, "NaN", "Infinity", "-1", "1e99999", [], {}])
def test_bad_measurements_are_rejected(draft, value):
    draft["new_case"]["labs"]["values"]["fbs"] = value
    with pytest.raises(contracts.ContractError):
        new_case.structural_draft(draft)


def test_zero_is_not_blank_and_differential_is_bounded(draft, content):
    draft["new_case"]["labs"] = {"present": True, "values": {"fbs": None}}
    assert state(draft, content)["errors_by_step"]["history"]
    draft["new_case"]["labs"]["values"]["fbs"] = 0
    result = state(draft, content)
    assert not any(
        error["code"] == "lab_measurement_required" for error in result["errors_by_step"]["history"]
    )
    assert result["raw"]["new_case"]["labs"]["values"]["fbs"] == "0"
    draft["new_case"]["labs"]["values"]["bands_pct"] = "100.01"
    with pytest.raises(contracts.ContractError):
        new_case.structural_draft(draft)


def test_modes_sources_and_all_orthostatic_reasons(draft, content):
    raw = draft["new_case"]
    raw["bmi"].update(height_cm="100", weight_kg="24.999")
    assert state(draft, content)["bmi_category"] == "normal"
    raw["bmi"].update(source="manual", manual_category="obesity_iii")
    assert state(draft, content)["evidence"]["Weight_Gain_Risk"]
    raw["bmi"].update(source="calculated", height_cm=None)
    assert state(draft, content)["bmi_category"] is None
    raw["heart_failure"] = True
    draft["medications"]["entries"] = [
        {"catalog_drug_id": drug_id(name)} for name in ("Lisinopril", "Furosemide")
    ]
    result = state(draft, content)
    assert set(result["flags"]["orthostatic_risk"]["findings"]) == {
        "heart_failure",
        drug_id("Lisinopril"),
        drug_id("Furosemide"),
    }
    raw["considerations"]["orthostatic_risk"]["manual"] = False
    assert not state(draft, content)["flags"]["orthostatic_risk"]["effective"]
    raw["heart_failure"] = False
    draft["medications"]["entries"] = []
    assert not state(draft, content)["flags"]["orthostatic_risk"]["suggested"]


def test_override_attribution_and_forgery(draft):
    changed = deepcopy(draft)
    changed["new_case"]["conditions"]["renal_failure"]["manual"] = False
    saved = new_case.prepare_autosave(draft, changed, actor_id="author", recorded_at="stamp")
    answer = saved["new_case"]["conditions"]["renal_failure"]
    assert answer["manual_actor_id"] == "author"
    assert answer["manual_recorded_at"] == "stamp"
    roundtrip = deepcopy(saved)
    del roundtrip["new_case"]["conditions"]["renal_failure"]["manual_actor_id"]
    assert (
        new_case.prepare_autosave(saved, roundtrip, actor_id="author", recorded_at="later") == saved
    )
    forged = deepcopy(saved)
    forged["new_case"]["conditions"]["renal_failure"]["manual_actor_id"] = "stranger"
    with pytest.raises(contracts.ContractError):
        new_case.prepare_autosave(saved, forged, actor_id="author", recorded_at="later")
    restored = deepcopy(saved)
    restored["new_case"]["conditions"]["renal_failure"]["manual"] = None
    assert (
        new_case.prepare_autosave(saved, restored, actor_id="author", recorded_at="later")[
            "new_case"
        ]["conditions"]["renal_failure"]["manual_recorded_at"]
        is None
    )


def test_conditional_completion_and_lai(draft, content):
    raw = draft["new_case"]
    raw["bmi"].update(source="manual", manual_category="normal")
    assert state(draft, content)["can_complete_step"]["history"]
    raw["psychiatric"]["present"] = True
    raw["substances"]["present"] = True
    assert len(state(draft, content)["errors_by_step"]["history"]) == 2
    raw["psychiatric"]["conditions"] = ["other"]
    raw["substances"]["other"] = True
    raw["considerations"]["lai"] = True
    result = state(draft, content)
    assert result["can_complete_step"]["history"]
    assert result["sections"]["antipsychotic_candidates"]["status"] == "model_unavailable"
    assert result["sections"]["baseline_tests"]["value"]
    assert result["sections"]["brain_imaging"]["must_not_delay_treatment"]


@pytest.mark.parametrize(
    "field,value,critical,outpatient",
    [
        ("fbs", "50", False, False),
        ("fbs", "49.99", True, False),
        ("fbs", "450", False, False),
        ("fbs", "450.01", True, False),
        ("wbc", "1000", True, False),
        ("wbc", "1000.01", False, False),
        ("wbc", "50000", True, False),
        ("platelets", "10000", True, False),
        ("platelets", "1000000", True, False),
        ("hematocrit", "18", True, False),
        ("hematocrit", "55", True, False),
        ("hemoglobin", "6", True, True),
        ("hemoglobin", "6.5", False, True),
        ("hemoglobin", "7", False, False),
        ("hemoglobin", "22", True, False),
        ("inr", "4", True, False),
        ("aptt", "49.99", False, False),
        ("aptt", "50", False, True),
    ],
)
def test_critical_operators_and_setting(draft, content, field, value, critical, outpatient):
    draft["new_case"]["labs"] = {"present": True, "values": {field: value}}
    codes = {warning["code"] for warning in state(draft, content)["lab_warnings"]}
    assert ("critical" in codes) is critical
    assert ("outpatient_critical" in codes) is outpatient
    draft["new_case"]["harm"]["self"] = True
    assert all(
        warning["code"] != "outpatient_critical"
        for warning in state(draft, content)["lab_warnings"]
    )


@pytest.mark.parametrize(
    "field,value,female_abnormal,male_abnormal",
    [
        ("hemoglobin", "12", False, True),
        ("hemoglobin", "16.01", True, False),
        ("hematocrit", "40", False, True),
        ("hematocrit", "49", True, False),
        ("creatinine", "1.2", True, False),
        ("prolactin", "16", False, True),
        ("hdl", "45", True, False),
    ],
)
def test_sex_specific_intervals(draft, content, field, value, female_abnormal, male_abnormal):
    draft["new_case"]["labs"] = {"present": True, "values": {field: value}}
    for sex, expected in (("F", female_abnormal), ("M", male_abnormal)):
        warnings = state(draft, content, sex=sex)["lab_warnings"]
        assert any(warning["code"] == "abnormal" for warning in warnings) is expected


@pytest.mark.parametrize(
    "value,indeterminate,pregnant",
    [("4.99", False, False), ("5", True, False), ("24.99", True, False), ("25", False, True)],
)
def test_hcg_interpretation(draft, content, value, indeterminate, pregnant):
    draft["new_case"]["labs"] = {"present": True, "values": {"beta_hcg": value}}
    result = state(draft, content)
    assert result["flags"]["pregnant"]["suggested"] is pregnant
    assert (
        any(warning["code"] == "indeterminate" for warning in result["lab_warnings"])
        is indeterminate
    )


def test_metadata_and_unknown_fields_do_not_roundtrip(draft):
    for section, field, value in (("harm", "invented", True), ("considerations", "lai", "yes")):
        changed = deepcopy(draft)
        changed["new_case"][section][field] = value
        with pytest.raises(contracts.ContractError):
            new_case.prepare_autosave(draft, changed, actor_id="author", recorded_at="stamp")
    changed = deepcopy(draft)
    changed["new_case"]["conditions"] = {}
    with pytest.raises(contracts.ContractError):
        new_case.structural_draft(changed)


def test_content_checksum_rejection(tmp_path):
    import shutil

    from x_insight.ddi.lexicomp import content_directory

    copied = tmp_path / "new-case/v1"
    shutil.copytree(content_directory() / "new-case/v1", copied)
    with (copied / "labs.json").open("a") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        new_case._content(str(tmp_path))
