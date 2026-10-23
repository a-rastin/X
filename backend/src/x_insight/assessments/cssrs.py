"""C-SSRS page state: live preview over the shared autosave body (S11).

The live instrument is recent ideation levels 1-5 (past month) from
``cssrs.v2``. Each item is yes, no, or unknown. Unknown is an explicit
answer. Absent answers become unknown unless the section is skipped.
A higher-level yes never fills lower levels. Severity is the highest
level answered yes, otherwise 0. There is no composite score. Skip is
``answers {"__skipped": true}`` and evaluates to ``not_assessed``.
Retired stored ids are stripped and are not scored. No new clinical
approval is recorded.

Storage: C-SSRS answers live in ``encounters.draft_data["cssrs"]`` through
the shared S07 autosave contract. Author draft reads, draft saves, and
the draft copy frozen at signing canonicalize the cssrs answers slice
only. Already stored signed snapshots are not rewritten. No new table.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from x_insight.assessments.engine import evaluate, normalize_cssrs_answers
from x_insight.assessments.released import get_released_definition
from x_insight.cases import encounters as encounters_service

CSSRS_KEY = "cssrs"
ANSWERS_KEY = "answers"


def get_cssrs_section(draft_data: Any) -> dict[str, Any]:
    """C-SSRS sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {"answers": {}}
    section = draft_data.get(CSSRS_KEY)
    if not isinstance(section, dict):
        return {"answers": {}}
    answers = section.get(ANSWERS_KEY)
    return {"answers": dict(answers) if isinstance(answers, dict) else {}}


def get_answers(draft_data: Any) -> dict[str, Any]:
    """Live C-SSRS answers: retired ids removed, absent recent items unknown."""
    return normalize_cssrs_answers(get_cssrs_section(draft_data)["answers"])


def canonicalize_draft(draft_data: Any) -> Any:
    """Canonicalize the cssrs answers slice only.

    Other slices are not copied into a new shape. A non-dict answers object
    is left unchanged so a corrupt slice is not rewritten as a completed
    screen. Already stored signed snapshots are not rewritten.
    """
    if not isinstance(draft_data, dict):
        return draft_data
    section = draft_data.get(CSSRS_KEY)
    if not isinstance(section, dict):
        return draft_data
    answers = section.get(ANSWERS_KEY)
    if not isinstance(answers, dict):
        return draft_data
    normalized = normalize_cssrs_answers(answers)
    if normalized == answers:
        return draft_data
    updated = dict(draft_data)
    new_section = dict(section)
    new_section[ANSWERS_KEY] = normalized
    updated[CSSRS_KEY] = new_section
    return updated


def evaluate_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Live preview through the same T2 evaluator (no drift)."""
    definition = get_released_definition("cssrs")
    return evaluate(definition, answers)


def definition_version() -> str:
    """Live C-SSRS definition version. No new clinical approval."""
    return str(get_released_definition("cssrs")["version"])


def compute_cssrs_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative C-SSRS preview for one draft revision."""
    del author_id  # kept for call-site symmetry with diagnosis; C-SSRS has no gate.
    answers = get_answers(draft_data)
    version = definition_version()
    evaluation = evaluate_answers(answers)
    return {
        "answers": answers,
        "evaluation": evaluation,
        "definition_version": version,
        "revision": revision,
    }


def read_cssrs_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only C-SSRS read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_cssrs_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state
