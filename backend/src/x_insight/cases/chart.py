"""Shared chart read + follow-up baseline preview (S14+S49, plan.md §§2.2-2.3; FR-20-23).

Chart (``GET /patients/{id}/chart``): demographics plus the signed chart for
any active authenticated user (physician or administrator — plan §2.1 reads
directory/demographics/signed charts for both roles, same as S06
``GET /patients``/``GET /patients/{id}``). Anonymous is 401 via the router's
session gate; unknown patients are 404. The chart carries no draft clinical
content, no author oracle, and no revision — just slot occupancy
(``open_draft: {exists}``) for directory badging, so stranger-draft content
never leaks through chart/search/report shapes (transitive privacy, like S07).

S49 signed reads: ``signed_snapshots`` carries the immutable frozen record
per signed encounter (original/final CPTs/results, plan, inputs/versions,
attribution, hash) plus ``addenda`` (append-only corrections); ordinary
draft routes stay draft-only (404 for signed) so live draft bodies never
leak here.

Follow-up baseline (``GET /encounters/{id}/followup-baseline``): author-only
preview of the copied baseline + reconciliation + historical scores (404
missing/released, 403 strangers/admin without content). Prior scores display
with ``historical: True`` — never as current answers.

S48d/S51 hook (documented, not implemented here): relevant shared demographic
changes marking affected follow-up results stale belongs to S48d/S51. The hook
point is the chart read — when patient demographics change, future sessions
compare the follow-up's pinned baseline demographics against current patient
truth and mark affected results stale. This module does no inference
staleness; it only exposes the shared demographics + baseline shapes that
hook will consume.

All persistence uses the caller's transaction; this module never commits (reads
only, no new tables, no migration, no bypass-sign production route).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import patients as patients_service


def read_chart(session: Session, patient_id: uuid.UUID, user: dict[str, Any]) -> dict[str, Any]:
    """Shared chart: demographics + signed chart + occupancy badge (no content)."""
    _ = user  # any active session may read (401 handled by router gate).
    patient = patients_service.get_patient(session, patient_id)
    if patient is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    occupying = encounters_service.get_open_draft_for_patient(session, patient_id)
    signed = encounters_service.list_signed_for_patient(session, patient_id)
    references = [encounters_service.safe_signed_reference(row) for row in signed]
    # S49 signed reads (shared, no drafts): frozen snapshots + addenda per
    # signed encounter. Draft clinical bodies never appear here by
    # construction (only immutable snapshots + shared demographics).
    from x_insight.cases import signing as signing_service

    snapshots: list[dict[str, Any]] = []
    all_addenda: list[dict[str, Any]] = []
    for row in signed:
        stored = signing_service.get_snapshot(session, row["id"])
        if stored is not None:
            snapshots.append(signing_service.safe_snapshot(stored))
            for entry in signing_service.list_addenda(session, row["id"]):
                all_addenda.append(signing_service.safe_addendum(entry))
    return {
        "patient": patients_service.safe_patient(patient),
        "signed_encounters": references,
        "signed_snapshots": snapshots,
        "addenda": all_addenda,
        "open_draft": {"exists": occupying is not None},
        "chronology": references,
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
