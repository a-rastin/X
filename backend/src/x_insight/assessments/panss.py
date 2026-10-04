"""PANSS page state: live preview over the shared autosave body (S10).

Plan.md §§2.2, 5 (FR-12, FR-20): PANSS is 30 items P1-P7/N1-N7/G1-G16 on
``scale_1_7`` with ``all_required`` completeness (S08 released
``panss.v1`` — no owner review). There is no acknowledgment, no bypass, no
owner review, no awaiting_review, no treatment gate from score bands, no
default ``1`` values, and no hidden zero: a fresh form is 30 unanswered
items with null scores, Skip is ``answers {"__skipped": true}`` which
evaluates to ``not_assessed``, the total is reported only when all 30 items
are valid (a subscale only when its own items are complete), and total bands
are ``informational_only`` + ``not_a_treatment_gate``.

Storage: PANSS answers live in ``encounters.draft_data["panss"]`` through
the shared S07 autosave contract (opaque ``draft_data`` object + revision +
``If-Match``/``412`` + ``Idempotency-Key``). Answers travel via the existing
``PATCH /encounters/{id}`` path; this module only reads page state. No new
table, no new persistence mechanism — no migration.

Live preview uses the same T2 ``evaluate()`` as S08 (no drift): invalid,
out-of-range, or non-integer values are rejected server-side in the
evaluation (``item_errors`` + aggregates suppressed to null, never zero).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from x_insight.assessments.engine import evaluate
from x_insight.assessments.released import get_released_definition
from x_insight.cases import encounters as encounters_service

PANSS_KEY = "panss"
ANSWERS_KEY = "answers"


def get_panss_section(draft_data: Any) -> dict[str, Any]:
    """PANSS sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {"answers": {}}
    section = draft_data.get(PANSS_KEY)
    if not isinstance(section, dict):
        return {"answers": {}}
    answers = section.get(ANSWERS_KEY)
    return {"answers": dict(answers) if isinstance(answers, dict) else {}}


def get_answers(draft_data: Any) -> dict[str, Any]:
    """Current PANSS item answers ({} when unanswered)."""
    return get_panss_section(draft_data)["answers"]


def evaluate_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Live preview through the same T2 evaluator (no drift)."""
    definition = get_released_definition("panss")
    return evaluate(definition, answers)


def definition_version() -> str:
    """Released PANSS definition version (S08, no owner review)."""
    return str(get_released_definition("panss")["version"])


def compute_panss_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative PANSS preview for one draft revision."""
    del author_id  # kept for call-site symmetry with diagnosis; PANSS has no gate.
    answers = get_answers(draft_data)
    version = definition_version()
    evaluation = evaluate_answers(answers)
    return {
        "answers": answers,
        "evaluation": evaluation,
        "definition_version": version,
        "revision": revision,
    }


def read_panss_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only PANSS read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_panss_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state
