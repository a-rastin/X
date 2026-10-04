"""Diagnosis page state: threshold preview, warning acknowledgment, bypass (S09).

Plan.md §§2.2, 5 (FR-11, FR-16): diagnosis is distinct from a calculated
claim — unanswered, partial, complete, or bypassed. Live threshold display
follows the S08 released criteria via the same T2 ``evaluate()`` (no drift):
a source-derived qualifying case satisfies every required criterion while a
symptom-count-only case does not; partial work has no completed threshold
result (never ``below_threshold``); unknown stays unknown, missing never
zero.

Storage: diagnosis answers + acknowledgment + bypass live in
``encounters.draft_data["diagnosis"]`` through the shared S07 autosave
contract (opaque ``draft_data`` object + revision + ``If-Match``/``412`` +
``Idempotency-Key``). No new table, no new persistence mechanism — later
wizard pages extend ``draft_data`` keys the same way.

Attribution: acknowledgment and bypass records carry server-derived
actor/time/status plus the assessed revision, answers hash, and definition
version. Only the draft author can read or mutate them (403 strangers,
404 missing/released). Later *relevant* edits invalidate the acknowledgment:
validity is derived on every read by comparing the stored answers hash (and
definition version, and that the current evaluation is still complete
below-threshold) — not by revision equality alone, so unrelated wizard
saves that bump the encounter revision do not invalidate it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.engine import evaluate
from x_insight.assessments.released import get_released_definition
from x_insight.cases import encounters as encounters_service
from x_insight.cases import tables as cases_tables
from x_insight.operations import audit as audit_module

DIAGNOSIS_KEY = "diagnosis"
ANSWERS_KEY = "answers"
ACK_KEY = "acknowledgment"
BYPASS_KEY = "bypass"

DIAGNOSIS_ACK_OPERATION = "diagnosis.acknowledge"
DIAGNOSIS_BYPASS_OPERATION = "diagnosis.bypass"

ACK_STATUS = "acknowledged"
BYPASS_STATUS = "bypassed"


def get_diagnosis_section(draft_data: Any) -> dict[str, Any]:
    """Diagnosis sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {"answers": {}, "acknowledgment": None, "bypass": None}
    section = draft_data.get(DIAGNOSIS_KEY)
    if not isinstance(section, dict):
        return {"answers": {}, "acknowledgment": None, "bypass": None}
    answers = section.get(ANSWERS_KEY)
    return {
        "answers": dict(answers) if isinstance(answers, dict) else {},
        "acknowledgment": section.get(ACK_KEY),
        "bypass": section.get(BYPASS_KEY),
    }


def get_answers(draft_data: Any) -> dict[str, Any]:
    """Current diagnosis item answers ({} when unanswered)."""
    return get_diagnosis_section(draft_data)["answers"]


def answers_hash(answers: dict[str, Any]) -> str:
    """Canonical hash of the answers (relevant-edit detector)."""
    return contracts.canonical_hash(answers)


def evaluate_answers(answers: dict[str, Any]) -> dict[str, Any]:
    """Live preview through the same T2 evaluator (no drift)."""
    definition = get_released_definition("diagnosis")
    return evaluate(definition, answers)


def definition_version() -> str:
    """Released diagnosis definition version (S08, no owner review)."""
    return str(get_released_definition("diagnosis")["version"])


def _parse_utc_or_none(text: Any) -> bool:
    if not isinstance(text, str) or not text:
        return False
    try:
        contracts.parse_utc(text)
    except Exception:
        return False
    return True


def is_acknowledgment_valid(
    ack: Any,
    current_answers: dict[str, Any],
    *,
    author_id: str,
    definition_version_str: str,
    current_evaluation: dict[str, Any] | None = None,
) -> bool:
    """A stored acknowledgment counts only when it still describes now.

    Valid iff status/actor/time/revision are present, the actor is the draft
    author, the timestamp parses as UTC, the definition version matches, the
    stored answers hash equals the current answers hash (any relevant edit
    invalidates), and the current evaluation is still complete
    below-threshold (so a forged ack for partial/satisfied work never counts
    and an edit that resolves the threshold invalidates).
    """
    if not isinstance(ack, dict):
        return False
    if ack.get("status") != ACK_STATUS:
        return False
    if str(ack.get("actor_id") or "") != author_id:
        return False
    if not _parse_utc_or_none(ack.get("acknowledged_at")):
        return False
    revision = ack.get("revision")
    if not isinstance(revision, int) or revision < 1:
        return False
    if str(ack.get("definition_version") or "") != definition_version_str:
        return False
    if str(ack.get("answers_hash") or "") != answers_hash(current_answers):
        return False
    evaluation = current_evaluation
    if evaluation is None:
        try:
            evaluation = evaluate_answers(current_answers)
        except Exception:
            return False
    if evaluation.get("status") != "complete":
        return False
    if evaluation.get("findings", {}).get("overall") != "below_threshold":
        return False
    return True


def is_bypass_valid(bypass: Any, current_answers: dict[str, Any], *, author_id: str) -> bool:
    """A stored bypass counts only with server attribution + empty answers.

    Any later item answer (relevant edit) invalidates it because bypass must
    not accompany item answers (engine ``__bypass`` constraint); unrelated
    wizard keys do not live in ``answers`` and so do not invalidate.
    """
    if not isinstance(bypass, dict):
        return False
    if bypass.get("status") != BYPASS_STATUS:
        return False
    if str(bypass.get("actor_id") or "") != author_id:
        return False
    if not _parse_utc_or_none(bypass.get("bypassed_at")):
        return False
    revision = bypass.get("revision")
    if not isinstance(revision, int) or revision < 1:
        return False
    if current_answers:
        return False
    return True


def compute_diagnosis_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative diagnosis preview + gate for one draft revision."""
    section = get_diagnosis_section(draft_data)
    answers = section["answers"]
    version = definition_version()
    evaluation = evaluate_answers(answers)
    ack = section["acknowledgment"]
    bypass_rec = section["bypass"]
    ack_valid = is_acknowledgment_valid(
        ack,
        answers,
        author_id=author_id,
        definition_version_str=version,
        current_evaluation=evaluation,
    )
    bypass_valid = is_bypass_valid(bypass_rec, answers, author_id=author_id)
    overall = None
    if evaluation.get("status") == "complete":
        overall = evaluation.get("findings", {}).get("overall")
    requires_ack = (
        evaluation.get("status") == "complete"
        and overall == "below_threshold"
        and not ack_valid
        and not bypass_valid
    )
    can_proceed = False
    proceed_via: str | None = None
    if bypass_valid:
        can_proceed = True
        proceed_via = "bypass"
    elif evaluation.get("status") == "complete" and overall == "criteria_satisfied":
        can_proceed = True
        proceed_via = "criteria_satisfied"
    elif evaluation.get("status") == "complete" and overall == "below_threshold" and ack_valid:
        can_proceed = True
        proceed_via = "acknowledged_below_threshold"
    return {
        "answers": answers,
        "evaluation": evaluation,
        "definition_version": version,
        "revision": revision,
        "acknowledgment": ack if isinstance(ack, dict) else None,
        "acknowledgment_valid": ack_valid,
        "bypass": bypass_rec if isinstance(bypass_rec, dict) else None,
        "bypass_valid": bypass_valid,
        "can_proceed": can_proceed,
        "requires_acknowledgment": requires_ack,
        "proceed_via": proceed_via,
    }


def _with_diagnosis_section(draft_data: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    updated = dict(draft_data)
    updated[DIAGNOSIS_KEY] = section
    return updated


def read_diagnosis_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only diagnosis read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_diagnosis_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state


def acknowledge_below_threshold(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Record the below-threshold warning acknowledgment (server-attributed).

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404/403), then the revision fence (412), then the state precondition
    (409 unless the current answers are complete below-threshold and not
    bypassed). Success stores ``{actor_id, acknowledged_at, status,
    revision, answers_hash, definition_version}``, clears any bypass (the two
    are mutually exclusive), bumps ``revision = expected + 1``, and audits.
    """
    row = (
        session.execute(
            select(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    encounters_service.require_draft_lifecycle(encounter)
    encounters_service.require_author(encounter, author)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    draft_data = dict(encounter.get("draft_data") or {})
    section = get_diagnosis_section(draft_data)
    answers = section["answers"]
    version = definition_version()
    evaluation = evaluate_answers(answers)
    author_id = str(encounter["author_id"])
    if is_bypass_valid(section["bypass"], answers, author_id=author_id):
        raise contracts.ContractError(
            409,
            "DIAGNOSIS_STATE_CONFLICT",
            "Diagnosis is already bypassed; acknowledgment does not apply.",
        )
    if evaluation.get("status") != "complete":
        raise contracts.ContractError(
            409,
            "DIAGNOSIS_STATE_CONFLICT",
            "Only a completed below-threshold diagnosis can be acknowledged.",
            {"diagnosis": ["Complete every required item before acknowledging."]},
        )
    if evaluation.get("findings", {}).get("overall") != "below_threshold":
        raise contracts.ContractError(
            409,
            "DIAGNOSIS_STATE_CONFLICT",
            "Only a below-threshold diagnosis needs acknowledgment.",
            {"diagnosis": ["Acknowledgment applies to below-threshold results only."]},
        )
    moment = contracts.utcnow()
    ack = {
        "actor_id": author_id,
        "acknowledged_at": contracts.serialize_utc(moment),
        "status": ACK_STATUS,
        "revision": expected_revision,
        "answers_hash": answers_hash(answers),
        "definition_version": version,
    }
    updated_section = {"answers": answers, "acknowledgment": ack, "bypass": None}
    updated_draft = _with_diagnosis_section(draft_data, updated_section)
    encounters_service.validate_draft_data(updated_draft)
    server_stamp = contracts.serialize_utc(moment)
    session.execute(
        update(cases_tables.encounters)
        .where(cases_tables.encounters.c.id == encounter_id)
        .values(
            draft_data=updated_draft,
            revision=expected_revision + 1,
            updated_at=moment,
        )
    )
    updated = encounters_service.get_encounter(session, encounter_id)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="diagnosis.acknowledge.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": expected_revision + 1,
            "definition_version": version,
            "overall": "below_threshold",
        },
    )
    state = compute_diagnosis_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
    )
    return updated, state, server_stamp


def bypass_diagnosis(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Record the diagnosis bypass without any reason field (server-attributed).

    Allowed from any diagnosis state; success clears item answers (bypass must
    not accompany answers) and any acknowledgment, stores ``{actor_id,
    bypassed_at, status, revision, definition_version}``, bumps the revision,
    and audits. The record survives resume via the same ``draft_data`` body.
    """
    row = (
        session.execute(
            select(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    encounters_service.require_draft_lifecycle(encounter)
    encounters_service.require_author(encounter, author)
    if int(encounter["revision"]) != expected_revision:
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    draft_data = dict(encounter.get("draft_data") or {})
    version = definition_version()
    author_id = str(encounter["author_id"])
    moment = contracts.utcnow()
    bypass = {
        "actor_id": author_id,
        "bypassed_at": contracts.serialize_utc(moment),
        "status": BYPASS_STATUS,
        "revision": expected_revision,
        "definition_version": version,
    }
    updated_section: dict[str, Any] = {
        "answers": {},
        "acknowledgment": None,
        "bypass": bypass,
    }
    updated_draft = _with_diagnosis_section(draft_data, updated_section)
    encounters_service.validate_draft_data(updated_draft)
    server_stamp = contracts.serialize_utc(moment)
    session.execute(
        update(cases_tables.encounters)
        .where(cases_tables.encounters.c.id == encounter_id)
        .values(
            draft_data=updated_draft,
            revision=expected_revision + 1,
            updated_at=moment,
        )
    )
    updated = encounters_service.get_encounter(session, encounter_id)
    assert updated is not None
    audit_module.record_audit(
        session,
        operation="diagnosis.bypass.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": expected_revision + 1,
            "definition_version": version,
        },
    )
    state = compute_diagnosis_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
    )
    return updated, state, server_stamp


def idempotency_request_hash(target_id: uuid.UUID, expected_revision: int) -> str:
    """Canonical hash of a diagnosis command (empty body + target + revision)."""
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision}
    )
