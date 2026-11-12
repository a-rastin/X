import pytest

from x_insight.cases import follow_up as f
from x_insight.cases import follow_up_baseline as baseline
from x_insight.cases import follow_up_rules as rules


@pytest.fixture
def case():
    patient = {"sex": "F", "age": 30, "birth_year": None, "clinical_status": "established"}
    draft = f.fresh_draft(
        2026, {}, baseline.project_baseline(None, None, patient, lexicomp_version="v1")
    )
    assets = f.verified_content()["assets"]
    forms = assets["new-case/v1/antipsychotic-formulations.json"]["entries"]
    equivalents = assets["follow-up/v1/lai-equivalences.json"]["equivalences"]
    main = next(e["catalog_drug_id"] for e in forms if e["id"] == "oral:aripiprazole")
    draft["medications"]["entries"] = [{"catalog_drug_id": main}]
    fu = draft["follow_up"]
    fu["treatment"] = {"main_catalog_drug_id": main, "formulation": "oral"}
    fu["considerations"].update(recovery="controlled", seizure_risk=False, qtc_risk=False)
    fu["branches"].update(switch_needed=False, dose_reduction_possible=False)
    return draft, forms, equivalents


def decide(case):
    draft, forms, equivalents = case
    return rules.decision(draft, equivalents, forms)


@pytest.mark.parametrize(
    "recovery,answers,adherence,preference,expected",
    [
        (
            "controlled",
            {"switch_needed": False, "dose_reduction_possible": False},
            True,
            False,
            "continue_same",
        ),
        (
            "controlled",
            {"switch_needed": False, "dose_reduction_possible": True},
            True,
            False,
            "reduce_dose",
        ),
        ("controlled", {"switch_needed": True}, True, False, "oral_replacement"),
        ("controlled", {"switch_needed": True}, True, True, "same_drug_lai"),
        ("partial", {}, False, False, "same_drug_lai"),
        ("partial", {"persistent_harm_risk": True}, True, False, "clozapine"),
        ("partial", {"persistent_harm_risk": False}, True, False, "oral_replacement"),
        ("none_or_worse", {"treatment_resistant": True}, True, False, "clozapine"),
        ("none_or_worse", {"treatment_resistant": False}, False, False, "same_drug_lai"),
        (
            "none_or_worse",
            {"treatment_resistant": False, "persistent_harm_risk": False},
            True,
            False,
            "oral_replacement",
        ),
        (
            "none_or_worse",
            {"treatment_resistant": False, "persistent_harm_risk": True},
            True,
            False,
            "clozapine",
        ),
    ],
)
def test_pathways(case, recovery, answers, adherence, preference, expected):
    fu = case[0]["follow_up"]
    fu["considerations"].update(recovery=recovery, adherence=adherence, lai_preference=preference)
    fu["branches"].update(answers)
    result = decide(case)
    assert result["terminal"] == expected and result["trace"]
    if recovery == "partial":
        assert "treatment_resistant" not in result["reached_questions"]
    if expected == "same_drug_lai":
        assert rules.decision(case[0], [], case[1])["terminal"] == "oral_replacement"


@pytest.mark.parametrize(
    "effect",
    ["parkinsonism", "akathisia", "orthostatic_hypotension", "anticholinergic", "sedation"],
)
def test_effect_replacement(case, effect):
    case[0]["follow_up"]["effects"][effect] = "intervention" if effect == "sedation" else True
    assert decide(case)["terminal"] == "oral_replacement"


def test_effect_priority_and_adjuncts(case):
    fu = case[0]["follow_up"]
    fu["effects"].update(parkinsonism=True, tardive_dyskinesia=True)
    fu["considerations"].update(recovery="none_or_worse", adherence=False)
    fu["branches"]["treatment_resistant"] = True
    result = decide(case)
    assert result["terminal"] == "oral_replacement" and result["adjuncts"] == ["vmat2_inhibitor"]
    fu["effects"].update(parkinsonism=False, acute_dystonia=True)
    fu["branches"].update(dystonia_resolved=False, dose_reduction_possible=True)
    assert decide(case)["terminal"] == "oral_replacement"
    fu["branches"]["dystonia_resolved"] = True
    fu["considerations"]["recovery"] = "controlled"
    assert decide(case)["terminal"] == "adjunct_only"
    assert set(decide(case)["adjuncts"]) == {
        "vmat2_inhibitor",
        "anticholinergic",
        "reduce_dose_if_possible",
    }
    fu["branches"]["dose_reduction_possible"] = False
    assert "reduce_dose_if_possible" not in decide(case)["adjuncts"]
    fu["effects"].update(acute_dystonia=False, tardive_dyskinesia=False, sedation="tolerable")
    assert decide(case)["terminal"] == "continue_same"


def test_review_and_inactive_answers(case):
    fu = case[0]["follow_up"]
    fu["treatment"]["formulation"] = "lai"
    assert decide(case)["reason"] == "current_lai"
    case[0]["medications"]["entries"] = []
    assert decide(case)["reason"] == "no_main"
    assert decide(case)["reached_questions"] == []


def test_independent_sections_and_factors(case):
    draft = case[0]
    fu = draft["follow_up"]
    h = fu["history"]
    h.update(tobacco=True, pregnant=False, breastfeeding=False)
    h["psychiatric"].update(present=True, conditions=["ptsd"])
    h["current_antidepressant"] = False
    h["neurological"]["focal_neurological_deficit"] = True
    fu["harm"].update(self=True, others=True)
    fu["considerations"].update(basic_needs_met=False, needs_observation=True)
    draft["cssrs"]["answers"].update(css_i4_recent="yes", css_i5_recent="yes")
    # Pure rule inputs may be incomplete; the model factor is explicitly unresolved.
    flags = {name: {"effective": False, "established": False} for name in f.CONDITIONS}
    evaluated = {"raw": draft, "flags": flags, "bmi_category": "overweight", "age": 55}
    assert rules.model_factors(evaluated)["Hyperprolactinemia"]["value"] is None
    sections = rules.sections(evaluated, "F", decide(case))
    assert len(sections["care_setting"]["reasons"]) == 6
    assert sections["antidepressant"] and sections["smoking_cessation"]
    assert sections["imaging"]["mri"] and "Beta-HCG" in sections["routine_labs"]
    assert sections["contraception"]["counsel"] and sections["next_visit"] == "four_weeks"
    evaluated["age"] = 56
    h["neurological"]["focal_neurological_deficit"] = False
    sections = rules.sections(evaluated, "F", decide(case))
    assert "Beta-HCG" not in sections["routine_labs"] and not sections["imaging"]["mri"]
    assert not sections["contraception"]["counsel"]


def test_model_factor_required_only_for_oral_replacement(case):
    draft = case[0]
    patient = {"sex": "F", "age": 30, "birth_year": None}
    fu = draft["follow_up"]
    fu["branches"]["switch_needed"] = True
    result = f.evaluate_state(draft, patient)
    assert result["decision"]["terminal"] == "oral_replacement"
    assert any(
        e["field"] == "model_factors.Hyperprolactinemia"
        for e in result["errors_by_step"]["considerations"]
    )
    fu["history"]["labs"] = {"present": True, "values": {"prolactin": "10"}}
    result = f.evaluate_state(draft, patient)
    assert result["model_factors"]["Hyperprolactinemia"]["value"] is False
    assert not any(
        e["field"] == "model_factors.Hyperprolactinemia"
        for e in result["errors_by_step"]["considerations"]
    )
    fu["history"]["labs"]["present"] = False
    fu["branches"]["switch_needed"] = False
    result = f.evaluate_state(draft, patient)
    assert not any(
        e["code"] == "model_factor_required" for e in result["errors_by_step"]["considerations"]
    )


@pytest.mark.parametrize(
    "sex,pregnant,breastfeeding,age,counsel,beta",
    [
        ("M", False, False, 30, False, False),
        ("F", False, False, 55, True, True),
        ("F", False, False, 56, False, False),
        ("F", True, False, 30, False, False),
        ("F", False, True, 30, False, True),
    ],
)
def test_reproductive_sections(case, sex, pregnant, breastfeeding, age, counsel, beta):
    draft = case[0]
    draft["follow_up"]["history"]["breastfeeding"] = breastfeeding
    flags = {name: {"effective": False, "established": True} for name in f.CONDITIONS}
    flags["pregnant"]["effective"] = pregnant
    evaluated = {"raw": draft, "flags": flags, "age": age}
    result = rules.sections(evaluated, sex, decide(case))
    assert result["contraception"]["counsel"] is counsel
    assert ("Beta-HCG" in result["routine_labs"]) is beta
    assert result["pregnancy"] is (sex == "F" and pregnant)
    assert result["breastfeeding"] is (sex == "F" and breastfeeding)


@pytest.mark.parametrize("trigger", ["self", "others", "css4", "css5", "needs", "observation"])
def test_care_setting_each_trigger(case, trigger):
    draft = case[0]
    fu = draft["follow_up"]
    if trigger in ("self", "others"):
        fu["harm"][trigger] = True
    elif trigger.startswith("css"):
        draft["cssrs"]["answers"][f"css_i{trigger[-1]}_recent"] = "yes"
    elif trigger == "needs":
        fu["considerations"]["basic_needs_met"] = False
    else:
        fu["considerations"]["needs_observation"] = True
    evaluated = {"raw": draft, "flags": {"pregnant": {"effective": False}}, "age": 30}
    care = rules.sections(evaluated, "F", decide(case))["care_setting"]
    assert care["setting"] == "inpatient" and len(care["reasons"]) == 1


@pytest.mark.parametrize(
    "terminal,reason,expected",
    [
        ("continue_same", "n6", "four_weeks"),
        ("reduce_dose", "n7", "four_weeks"),
        ("adjunct_only", "n22", "four_weeks"),
        ("oral_replacement", "n10", "2_to_4_weeks"),
        ("clozapine", "n24", "2_to_4_weeks"),
        ("same_drug_lai", "n11", "next_injection"),
        ("physician_review", "current_lai", "next_injection"),
        ("physician_review", "excluded", "physician_determines"),
        ("physician_review", "no_main", "physician_determines"),
    ],
)
def test_timing(case, terminal, reason, expected):
    evaluated = {"raw": case[0], "flags": {"pregnant": {"effective": False}}, "age": 30}
    assert (
        rules.sections(evaluated, "F", {"terminal": terminal, "reason": reason})["next_visit"]
        == expected
    )
