"""Pure, experimental new-case calculations; not a clinical validation."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

Sex = Literal["female", "male"]
BMICategory = Literal[
    "underweight", "normal", "overweight", "obesity_i", "obesity_ii", "obesity_iii"
]


@dataclass(frozen=True)
class Flag:
    suggested: bool
    effective: bool
    findings: tuple[str, ...]
    manual: bool | None


def override(findings: tuple[str, ...], manual: bool | None) -> Flag:
    if manual is not None and type(manual) is not bool:
        raise ValueError("Manual override must be a boolean or null")
    suggested = bool(findings)
    return Flag(suggested, suggested if manual is None else manual, findings, manual)


def bmi_category(height_cm: Decimal, weight_kg: Decimal) -> tuple[Decimal, BMICategory]:
    if not height_cm.is_finite() or not weight_kg.is_finite() or height_cm <= 0 or weight_kg <= 0:
        raise ValueError("Height and weight must be finite and positive")
    with localcontext() as context:
        context.prec = 512
        value = weight_kg / (height_cm / Decimal(100)) ** 2
    boundaries: tuple[tuple[str, BMICategory], ...] = (
        ("18.5", "underweight"),
        ("25", "normal"),
        ("30", "overweight"),
        ("35", "obesity_i"),
        ("40", "obesity_ii"),
    )
    for boundary, category in boundaries:
        if value < Decimal(boundary):
            return value, category
    return value, "obesity_iii"


@dataclass(frozen=True)
class PlanInputs:
    sex: Sex
    age: int
    self_harm: bool
    other_harm: bool
    cssrs_item_4: bool
    cssrs_item_5: bool
    basic_needs_met: bool
    needs_observation: bool
    lai: bool
    psychiatric_present: bool
    psychiatric_conditions: frozenset[str]
    pregnant: bool
    breastfeeding: bool
    tobacco: bool
    focal_neurological_deficit: bool
    suspected_brain_lesion: bool
    suspected_autoimmune_encephalitis: bool


@dataclass(frozen=True)
class Section:
    value: bool | str | tuple[str, ...]
    reasons: tuple[str, ...] = ()


def plan_sections(inputs: PlanInputs) -> dict[str, Section]:
    if (
        inputs.sex not in ("female", "male")
        or type(inputs.age) is not int
        or not 18 <= inputs.age <= 100
    ):
        raise ValueError("Invalid demographic inputs")
    female = inputs.sex == "female"
    pregnant = female and inputs.pregnant
    breastfeeding = female and inputs.breastfeeding
    inpatient_reasons = tuple(
        reason
        for reason, enabled in (
            ("self_harm", inputs.self_harm),
            ("other_harm", inputs.other_harm),
            ("cssrs_item_4", inputs.cssrs_item_4),
            ("cssrs_item_5", inputs.cssrs_item_5),
            ("basic_needs_unmet", not inputs.basic_needs_met),
            ("needs_observation", inputs.needs_observation),
        )
        if enabled
    )
    imaging_reasons = tuple(
        reason
        for reason, enabled in (
            ("age_over_55", inputs.age > 55),
            ("focal_neurological_deficit", inputs.focal_neurological_deficit),
            ("suspected_brain_lesion", inputs.suspected_brain_lesion),
            ("suspected_autoimmune_encephalitis", inputs.suspected_autoimmune_encephalitis),
        )
        if enabled
    )
    antidepressant_reasons = tuple(
        name
        for name in ("depression", "ptsd")
        if inputs.psychiatric_present and name in inputs.psychiatric_conditions
    )
    contraception = female and inputs.age <= 55 and not pregnant
    tests = (
        "CBC",
        "Diff",
        "Na",
        "K",
        "BUN",
        "Cr",
        "AST",
        "ALT",
        "ALP",
        "PT",
        "PTT",
        "INR",
        "TSH",
        "FBS",
        "HbA1c",
    )
    return {
        "care_setting": Section(
            "inpatient" if inpatient_reasons else "outpatient", inpatient_reasons
        ),
        "antipsychotic_route": Section("lai" if inputs.lai else "oral"),
        "antidepressant": Section(bool(antidepressant_reasons), antidepressant_reasons),
        "contraception_recommendation": Section(contraception),
        "smoking_cessation": Section(inputs.tobacco),
        "baseline_tests": Section(tests + (("Beta-HCG",) if contraception else ())),
        "brain_imaging": Section(bool(imaging_reasons), imaging_reasons),
        "breastfeeding": Section(breastfeeding),
        "pregnancy": Section(pregnant),
        "contraception_counseling": Section(female and not pregnant and not breastfeeding),
        "next_visit": Section("next_injection" if inputs.lai else "2_to_4_weeks"),
    }
