"""Shared chart read over B/cases (S14, plan.md §§2.2-2.3; FR-20-23).

Any active physician can read demographics plus the signed chart; the
administrator has no ordinary clinical route here (403 — plan §2.1: admins
read the directory and archive, they do not work clinical records through
ordinary routes). Anonymous callers get 401 via the router's session gate;
unknown patients are 404.

The chart carries demographics (the ``safe_patient`` shape) plus signed
references only: ``signed_encounters``/``chronology`` derive from the
encounters table's ``signed`` rows (none exist yet — signing is S49 scope),
and the open-draft badge is just ``{exists: bool}`` — no author, no content,
no revision, so directory badging never leaks stranger-draft clinical
content (transitive privacy, like S07). Until reasoning exists the chart
reports the proposal honestly as unavailable (literal
``generation_not_implemented`` — no fake successful proposal).

All persistence uses the caller's transaction; this module never commits
(reads only, no new tables, no migration).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import patients as patients_service


def require_chart_reader(user: dict[str, Any]) -> dict[str, Any]:
    """Physician-only gate for the shared chart (403 otherwise, no content)."""
    if user.get("role") != "physician" or not user.get("active", False):
        raise contracts.ContractError(403, "FORBIDDEN", "Physician access required.")
    return user


def read_chart_for_physician(
    session: Session, patient_id: uuid.UUID, user: dict[str, Any]
) -> dict[str, Any]:
    """Shared chart: demographics + signed chart + occupancy badge (no content)."""
    require_chart_reader(user)
    patient = patients_service.get_patient(session, patient_id)
    if patient is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    occupying = encounters_service.get_open_draft_for_patient(session, patient_id)
    signed = encounters_service.list_signed_for_patient(session, patient_id)
    return {
        "patient": patients_service.safe_patient(patient),
        "signed_encounters": [encounters_service.safe_signed_reference(row) for row in signed],
        "open_draft": {"exists": occupying is not None},
        "chronology": [encounters_service.safe_signed_reference(row) for row in signed],
        "proposal": {"status": "unavailable", "reason": "generation_not_implemented"},
    }


def read_followup_baseline_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only follow-up baseline: baseline + reconciliation + history.

    404 when missing/released, 403 for strangers (no content). Prior scores
    display distinctly labeled ``historical: True`` — never as current
    answers. Drafts created without a baseline report ``baseline: None`` with
    ``not_required`` reconciliation and an empty score display.
    """
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    draft_data = encounter.get("draft_data") or {}
    baseline = draft_data.get(encounters_service.FOLLOWUP_BASELINE_KEY)
    history_section = draft_data.get(encounters_service.HISTORY_STATE_KEY)
    raw_values = history_section.get("values") if isinstance(history_section, dict) else None
    current_values = dict(raw_values) if isinstance(raw_values, dict) else {}
    raw_reconciliation = (
        history_section.get("reconciliation") if isinstance(history_section, dict) else None
    )
    reconciliation = (
        dict(raw_reconciliation)
        if isinstance(raw_reconciliation, dict)
        else {"status": "not_required", "baseline_encounter_id": None}
    )
    if not isinstance(baseline, dict):
        return encounter, {
            "baseline": None,
            "reconciliation": reconciliation,
            "prior_scores_display": {},
        }
    raw_scores = baseline.get("prior_scores")
    scores = dict(raw_scores) if isinstance(raw_scores, dict) else {}
    return encounter, {
        "baseline": {
            "history_values": current_values,
            "medications": [dict(entry) for entry in (baseline.get("medications") or [])],
            "provenance_note": baseline.get("provenance_note"),
            "baseline_encounter_id": baseline.get("baseline_encounter_id"),
        },
        "reconciliation": reconciliation,
        "prior_scores_display": {
            key: {"value": value, "historical": True} for key, value in scores.items()
        },
    }
