"""Typed implementation of the reviewed manifests' applicability truth tables.

The caller first requires every declared input to be known and valid. A false
gate never turns unknown context into a negative finding. No model output is
used as evidence for another question.
"""

from __future__ import annotations

from typing import Any


def history_gate_requirements(key: str, paths: dict[str, Any]) -> list[tuple[str, str]]:
    """Known answers that still cannot satisfy the reviewed follow-up gate."""
    values = {path.rsplit("/", 1)[-1]: value for path, value in paths.items()}
    blockers: list[tuple[str, str]] = []
    if key in ("continue_or_adjust", "no_improvement_clozapine"):
        for field, expected in (
            ("h_baseline_encounter_ref", "documented_current"),
            ("h_baseline_window_validity", "current"),
        ):
            if values.get(field) != expected:
                blockers.append((field, "A current baseline is required."))
    if key == "no_improvement_clozapine":
        for field, expected in (
            ("h_clozapine_treatment_status", "CurrentTreatment"),
            ("h_panss_baseline_status", "completed"),
            ("h_panss_followup_status", "completed"),
            ("h_clozapine_trial_adequate", "Yes"),
        ):
            if values.get(field) != expected:
                blockers.append(
                    (
                        field,
                        "Current clozapine, completed PANSS and adequate trial context "
                        "are required.",
                    )
                )
    return blockers


def evaluate_history_gate(key: str, paths: dict[str, Any]) -> tuple[str, str]:
    values = {path.rsplit("/", 1)[-1]: value for path, value in paths.items()}
    false = False
    if key in ("high_suicide_clozapine", "aggression_clozapine"):
        risk = (
            "h_substantial_suicide_risk"
            if key.startswith("high_")
            else "h_substantial_aggression_risk"
        )
        false = any(
            values.get(field) == "No"
            for field in ("h_schizophrenia_established", risk, "h_persistence_despite_treatment")
        )
    elif key in ("lai_indication_choice", "established_case_clozapine"):
        false = values.get("h_schizophrenia_established") == "No"
    elif key in ("tardive_dyskinesia", "akathisia", "parkinsonism"):
        field = {
            "tardive_dyskinesia": "h_td_assessment",
            "akathisia": "h_ak_assessment",
            "parkinsonism": "h_p_assessment",
        }[key]
        false = values.get(field) == "Absent"
    elif key == "acute_dystonia":
        false = values.get("h_ad_episode_phase") == "NoEpisode"
    elif key not in ("pharmacotherapy", "continue_or_adjust", "no_improvement_clozapine"):
        return "needs_clarification", "No reviewed history gate is registered for this question."
    requirements = history_gate_requirements(key, paths)
    if requirements:
        return "needs_clarification", requirements[0][1]
    if key == "no_improvement_clozapine":
        false = values.get("h_residual_symptoms") == "No"
    if false:
        return (
            "not_applicable",
            "Reviewed applicability gate is false; all required context is known.",
        )
    return "ready", "Reviewed applicability gate is true; all required context is known."
