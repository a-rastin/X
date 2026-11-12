"""Pure follow-up decisions; medication exclusions are applied by package E."""

from collections.abc import Mapping
from typing import Any

LABS = [
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
]


def decision(
    state: dict[str, Any], equivalences: list[dict[str, str]], formulations: list[dict[str, Any]]
) -> dict[str, Any]:
    fu = state["follow_up"]
    treatment, effects, c, answers = (
        fu[k] for k in ("treatment", "effects", "considerations", "branches")
    )
    main = treatment["main_catalog_drug_id"]
    ids = {e["catalog_drug_id"] for e in state["medications"]["entries"]}
    oral = next(
        (
            e["id"]
            for e in formulations
            if e.get("catalog_drug_id") == main and e["group"] == "oral"
        ),
        None,
    )
    lai = next((e["lai"] for e in equivalences if e["oral"] == oral), None)
    reached: list[str] = []
    trace: list[str] = []
    adjuncts: list[str] = []
    replacements = [
        name
        for name in ("parkinsonism", "akathisia", "orthostatic_hypotension", "anticholinergic")
        if effects[name]
    ]
    if effects["sedation"] == "intervention":
        replacements.append("sedation")
    if effects["tardive_dyskinesia"]:
        adjuncts.append("vmat2_inhibitor")
        trace.append("n21_n22")
    valid_oral = main is not None and main in ids and treatment["formulation"] == "oral"
    if effects["acute_dystonia"]:
        adjuncts.append("anticholinergic")
        trace.append("n15_n17")
        if valid_oral and c["recovery"] is not None:
            reached.extend(("dystonia_resolved", "dose_reduction_possible"))
            if answers["dose_reduction_possible"] is True:
                adjuncts.append("reduce_dose_if_possible")
            if answers["dystonia_resolved"] is False:
                replacements.append("acute_dystonia")
    if not main or main not in ids:
        terminal, reason = "physician_review", "no_main"
    elif treatment["formulation"] == "lai":
        terminal, reason = "physician_review", "current_lai"
    elif treatment["formulation"] != "oral" or c["recovery"] is None:
        terminal, reason = None, "incomplete"
    elif effects["acute_dystonia"] and answers["dystonia_resolved"] is None:
        terminal, reason = None, "dystonia_response_required"
    elif replacements:
        terminal, reason = "oral_replacement", "effect_replacement"
        trace.extend("effect_" + name for name in replacements)
    elif c["recovery"] == "controlled" and adjuncts:
        terminal, reason = "adjunct_only", "controlled_effect_terminal"
    elif c["recovery"] == "controlled":
        reached.append("switch_needed")
        if answers["switch_needed"] is None:
            terminal, reason = None, "switch_needed_required"
        elif answers["switch_needed"]:
            terminal = "same_drug_lai" if c["lai_preference"] and lai else "oral_replacement"
            reason = "n4_n8_n11" if c["lai_preference"] else "n4_n8"
        else:
            reached.append("dose_reduction_possible")
            terminal = (
                ("reduce_dose" if answers["dose_reduction_possible"] else "continue_same")
                if answers["dose_reduction_possible"] is not None
                else None
            )
            reason = "n4_n5"
    else:
        resistant = answers["treatment_resistant"]
        if c["recovery"] == "none_or_worse":
            reached.append("treatment_resistant")
        if c["recovery"] == "none_or_worse" and resistant is None:
            terminal, reason = None, "treatment_resistant_required"
        elif c["recovery"] == "none_or_worse" and resistant:
            terminal, reason = "clozapine", "n23_n24"
        elif not c["adherence"]:
            terminal, reason = ("same_drug_lai" if lai else "oral_replacement"), "n25_n11_2"
        else:
            reached.append("persistent_harm_risk")
            risk = answers["persistent_harm_risk"]
            terminal, reason = (
                ("clozapine" if risk else "oral_replacement") if risk is not None else None,
                "n25_n26",
            )
    trace.append(reason)
    return {
        "terminal": terminal,
        "reason": reason,
        "trace": trace,
        "reached_questions": sorted(set(reached)),
        "adjuncts": adjuncts,
        "effect_replacements": replacements,
        "superseded": ["symptom_adherence_pathway"] if reason == "effect_replacement" else [],
        "lai_equivalent": lai,
    }


def model_factors(evaluated: Mapping[str, Any]) -> dict[str, Any]:
    fu = evaluated["raw"]["follow_up"]
    h, e, c = (fu[k] for k in ("history", "effects", "considerations"))
    flags = evaluated["flags"]
    values = {
        "Smoking": (h["tobacco"], "history.tobacco"),
        "Marijuana_Smoking": (
            None
            if h["substances"]["present"] is None
            else h["substances"]["present"] and h["substances"]["marijuana"],
            "history.substances",
        ),
        "Hepatic_Failure": (
            flags["hepatic_failure"]["effective"]
            if flags["hepatic_failure"]["established"]
            else None,
            "flags.hepatic_failure",
        ),
        "Renal_Failure": (
            flags["renal_failure"]["effective"] if flags["renal_failure"]["established"] else None,
            "flags.renal_failure",
        ),
        "Sedative_Effects_Concern": (e["sedation"] == "intervention", "effects.sedation"),
        "Seizure_Risk": (c["seizure_risk"], "considerations.seizure_risk"),
        "Glucose_Abnormalities": (
            flags["glucose_abnormality"]["effective"]
            if flags["glucose_abnormality"]["established"]
            else None,
            "flags.glucose_abnormality",
        ),
        "Hyperlipidemia": (
            flags["hyperlipidemia"]["effective"]
            if flags["hyperlipidemia"]["established"]
            else None,
            "flags.hyperlipidemia",
        ),
        "Weight_Gain_Risk": (
            None
            if evaluated["bmi_category"] is None
            else evaluated["bmi_category"]
            in ("overweight", "obesity_i", "obesity_ii", "obesity_iii"),
            "bmi_category",
        ),
        "QTc_Prolongation_Risk": (c["qtc_risk"], "considerations.qtc_risk"),
        "Orthostatic_Hypotension_Risk": (
            e["orthostatic_hypotension"],
            "effects.orthostatic_hypotension",
        ),
        "Hyperprolactinemia": (
            flags["hyperprolactinemia"]["effective"]
            if flags["hyperprolactinemia"]["established"]
            else None,
            "flags.hyperprolactinemia",
        ),
        "Tardive_Dyskinesia": (e["tardive_dyskinesia"], "effects.tardive_dyskinesia"),
        "Dystonia": (e["acute_dystonia"], "effects.acute_dystonia"),
        "Parkinsonism": (e["parkinsonism"], "effects.parkinsonism"),
        "Akathisia": (e["akathisia"], "effects.akathisia"),
    }
    return {
        name: {
            "value": value,
            "source": source,
            "provenance": flags[source.removeprefix("flags.")]
            if source.startswith("flags.")
            else fu["baseline"].get("inherited", {}).get(source),
        }
        for name, (value, source) in values.items()
    }


def sections(evaluated: Mapping[str, Any], sex: str, result: dict[str, Any]) -> dict[str, Any]:
    state = evaluated["raw"]
    fu = state["follow_up"]
    h, c = fu["history"], fu["considerations"]
    reasons = [
        name
        for name, present in (
            ("self_harm", fu["harm"]["self"]),
            ("other_harm", fu["harm"]["others"]),
            ("cssrs_4", state["cssrs"]["answers"].get("css_i4_recent") == "yes"),
            ("cssrs_5", state["cssrs"]["answers"].get("css_i5_recent") == "yes"),
            ("basic_needs_unmet", not c["basic_needs_met"]),
            ("observation", c["needs_observation"]),
        )
        if present
    ]
    female = sex == "F"
    pregnant = evaluated["flags"]["pregnant"]["effective"] if female else False
    breastfeeding = female and h["breastfeeding"] is True
    reproductive_age = evaluated["age"] <= 55
    terminal = result["terminal"]
    timing = "physician_determines"
    if terminal in ("continue_same", "reduce_dose", "adjunct_only", "clozapine"):
        timing = "2_to_4_weeks" if terminal == "clozapine" else "four_weeks"
    elif terminal == "oral_replacement":
        timing = "2_to_4_weeks"
    elif terminal == "same_drug_lai" or (
        terminal == "physician_review" and result["reason"] == "current_lai"
    ):
        timing = "next_injection"
    return {
        "care_setting": {"setting": "inpatient" if reasons else "outpatient", "reasons": reasons},
        "antidepressant": bool(
            h["psychiatric"]["present"]
            and set(h["psychiatric"]["conditions"]) & {"depression", "ptsd"}
            and h["current_antidepressant"] is False
        ),
        "smoking_cessation": h["tobacco"] is True,
        "routine_labs": LABS
        + (["Beta-HCG"] if female and not pregnant and reproductive_age else []),
        "imaging": {
            "mri": any(h["neurological"].values()),
            "do_not_delay_treatment": any(h["neurological"].values()),
        },
        "breastfeeding": breastfeeding,
        "pregnancy": pregnant,
        "contraception": {
            "visible": female,
            "counsel": female and not pregnant and not breastfeeding and reproductive_age,
            "prior": fu["baseline"].get("prior_contraception", []) if female else [],
        },
        "next_visit": timing,
    }
