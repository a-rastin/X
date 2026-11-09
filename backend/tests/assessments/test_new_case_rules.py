from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from x_insight.cases.new_case_rules import PlanInputs, bmi_category, override, plan_sections


@pytest.fixture
def inputs() -> PlanInputs:
    return PlanInputs(
        sex="female",
        age=30,
        self_harm=False,
        other_harm=False,
        cssrs_item_4=False,
        cssrs_item_5=False,
        basic_needs_met=True,
        needs_observation=False,
        lai=False,
        psychiatric_present=False,
        psychiatric_conditions=frozenset(),
        pregnant=False,
        breastfeeding=False,
        tobacco=False,
        focal_neurological_deficit=False,
        suspected_brain_lesion=False,
        suspected_autoimmune_encephalitis=False,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("self_harm", True),
        ("other_harm", True),
        ("cssrs_item_4", True),
        ("cssrs_item_5", True),
        ("basic_needs_met", False),
        ("needs_observation", True),
    ],
)
def test_independent_inpatient_reasons(inputs: PlanInputs, field: str, value: bool) -> None:
    assert plan_sections(inputs)["care_setting"].value == "outpatient"
    result = plan_sections(replace(inputs, **{field: value}))["care_setting"]
    assert result.value == "inpatient"
    assert len(result.reasons) == 1


def test_conditional_rules_and_separate_contraception(inputs: PlanInputs) -> None:
    breastfeeding = plan_sections(replace(inputs, breastfeeding=True))
    assert breastfeeding["contraception_recommendation"].value is True
    assert breastfeeding["contraception_counseling"].value is False
    older = plan_sections(replace(inputs, age=60))
    assert older["contraception_recommendation"].value is False
    assert older["contraception_counseling"].value is True
    male = plan_sections(replace(inputs, sex="male", pregnant=True, breastfeeding=True))
    assert male["pregnancy"].value is False
    assert male["breastfeeding"].value is False
    assert "Beta-HCG" not in male["baseline_tests"].value
    assert "Beta-HCG" in plan_sections(replace(inputs, age=55))["baseline_tests"].value
    assert "Beta-HCG" not in plan_sections(replace(inputs, age=56))["baseline_tests"].value
    stale = replace(inputs, psychiatric_conditions=frozenset({"depression"}))
    assert plan_sections(stale)["antidepressant"].value is False
    assert plan_sections(replace(stale, psychiatric_present=True))["antidepressant"].value is True
    assert (
        plan_sections(
            replace(inputs, psychiatric_present=True, psychiatric_conditions=frozenset({"adhd"}))
        )["antidepressant"].value
        is False
    )


def test_imaging_and_route(inputs: PlanInputs) -> None:
    state = plan_sections(
        replace(
            inputs,
            age=56,
            lai=True,
            focal_neurological_deficit=True,
            suspected_brain_lesion=True,
            suspected_autoimmune_encephalitis=True,
        )
    )
    assert len(state["brain_imaging"].reasons) == 4
    assert state["next_visit"].value == "next_injection"
    assert plan_sections(replace(inputs, age=55))["brain_imaging"].value is False


@pytest.mark.parametrize(
    "value,category",
    [
        ("18.49", "underweight"),
        ("18.5", "normal"),
        ("24.99", "normal"),
        ("25", "overweight"),
        ("30", "obesity_i"),
        ("35", "obesity_ii"),
        ("40", "obesity_iii"),
    ],
)
def test_bmi_boundaries(value: str, category: str) -> None:
    assert bmi_category(Decimal(100), Decimal(value))[1] == category


def test_override_retains_manual_no() -> None:
    assert override(("ast",), False).effective is False
    assert override(("ast",), None).effective is True
    assert override((), True).effective is True
    assert override((), None).effective is False
    invalid: Any = 1
    with pytest.raises(ValueError):
        override((), invalid)
