"""C-SSRS page state: live preview over the shared autosave body (S11).

Plan.md §§2.2, 5 (FR-13, FR-20): C-SSRS is ideation 1-5 in two windows,
five intensity dimensions, five behavior categories in two windows, and
lethality codings (S08 released ``cssrs.v1`` — no owner review). Ideation
severity, intensity, behavior, and lethality remain separate with no
composite score; a higher-level yes never auto-fills lower levels; ``{}``
is unanswered distinct from explicit negatives; Skip is
``answers {"__skipped": true}`` which evaluates to ``not_assessed``;
complete explicit negatives give the S08-defined no-ideation result
(severity 0); incomplete required branches report ``missing_item_ids``,
never guessed negatives. NSSI contributes to ``clinical_review`` but never
triggers ``high_risk_alert`` by itself.

Storage: C-SSRS answers live in ``encounters.draft_data["cssrs"]`` through
the shared S07 autosave contract (opaque ``draft_data`` object + revision +
``If-Match``/``412`` + ``Idempotency-Key``). Answers travel via the existing
``PATCH /encounters/{id}`` path; this module only reads page state. No new
table, no new persistence mechanism — no migration. No ack/bypass POSTs, no
treatment gate.

Live preview uses the same T2 ``evaluate()`` as S08 (no drift): invalid
values are rejected server-side in the evaluation (``item_errors`` +
aggregates suppressed to null, never zero).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from x_insight.assessments.engine import evaluate
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
    """Current C-SSRS item answers ({} when unanswered)."""
    return get_cssrs_section(draft_data)["answers"]


def evaluate_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Live preview through the same T2 evaluator (no drift)."""
    definition = get_released_definition("cssrs")
    return evaluate(definition, answers)


def definition_version() -> str:
    """Released C-SSRS definition version (S08, no owner review)."""
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
