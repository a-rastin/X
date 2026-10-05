"""Structured history page state: persistence with provenance (S12).

Plan.md §§2.2, 5 (FR-14, FR-20): history is structured, with only expressly
defined analysis-visible fields. Medications contain bundled catalog
references only; dose, unit, route, frequency, active/stopped, and free-text
entries stay excluded even if forged. Unknown and not-assessed are distinct
from false (no). Undeclared or excluded field ids fail validation (422 on
strict writes; item_errors on previews).

Storage: history lives in ``encounters.draft_data["history"]`` through the
shared S07 autosave contract (opaque ``draft_data`` object + revision +
``If-Match``/``412`` + ``Idempotency-Key``). Item answers travel via the
existing ``PATCH /encounters/{id}`` path; this module only reads page state
for previews plus one strict ``POST .../history`` command that validates and
stamps provenance. No new table, no new persistence mechanism — no migration.

Shape: ``draft_data["history"] = {values: {field_id: value},
provenance: {field_id: {source, author_id, recorded_at, baseline...}},
reconciliation: {status, ...}, phone_update: str | None}``. All history
values are analysis-visible (analysis_visible true); page notes (S13) are a
separate analysis_visible false channel enforced by serialization allowlists.

Content: versioned draft ``content/history/history.v1.json`` (awaiting owner
review, not approved). The backend mirrors its 12 field ids, permitted
values, periods, and excluded list without inventing thresholds.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import tables as cases_tables
from x_insight.operations import audit as audit_module

HISTORY_KEY = "history"
VALUES_KEY = "values"
PROVENANCE_KEY = "provenance"
RECONCILIATION_KEY = "reconciliation"
PHONE_UPDATE_KEY = "phone_update"

HISTORY_SAVE_OPERATION = "history.save"

HISTORY_VERSION = "v1"

TRISTATE_NOT_ASSESSED = ("yes", "no", "unknown", "not_assessed")

ALLOWED_FIELDS: dict[str, dict[str, Any]] = {
    "h_exposure_dopamine_blocker": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "exposure",
    },
    "h_exposure_duration_3m": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "duration",
    },
    "h_exposure_1m_if_60plus": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "duration",
    },
    "h_movement_persistence_4w": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "duration",
    },
    "h_onset_timing": {
        "permitted": [
            "during_exposure",
            "within_4w_oral_withdrawal",
            "within_8w_lai_withdrawal",
            "unknown",
            "not_assessed",
        ],
        "category": "exposure",
    },
    "h_trial_adequacy_prior": {
        "permitted": ["adequate", "inadequate", "unknown", "not_assessed"],
        "category": "trial_adequacy",
    },
    "h_prior_response": {
        "permitted": ["response", "no_response", "unknown", "not_assessed"],
        "category": "prior_response",
    },
    "h_monitoring_baseline": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "monitoring",
    },
    "h_alternative_cause_considered": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "exposure",
    },
    "h_functional_impact": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "monitoring",
    },
    "h_falls_or_limitation": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "monitoring",
    },
    "h_medication_timeline_documented": {
        "permitted": list(TRISTATE_NOT_ASSESSED),
        "category": "exposure",
    },
}

FIELD_IDS = tuple(ALLOWED_FIELDS)

EXCLUDED_FIELDS = frozenset(
    {
        "dose",
        "dose_unit",
        "unit",
        "route",
        "frequency",
        "active_stopped",
        "active",
        "stopped",
        "free_text_medication",
        "medication_free_text",
        "free_text_history",
    }
)

RECONCILIATION_STATUSES = ("not_required", "pending", "reconciled")
PROVENANCE_SOURCES = ("clinician_entry", "copied_baseline")


def definition_version() -> str:
    """History inventory version (draft v1, awaiting owner review)."""
    return HISTORY_VERSION


def get_history_section(draft_data: Any) -> dict[str, Any]:
    """History sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {"values": {}, "provenance": {}, "reconciliation": None, "phone_update": None}
    section = draft_data.get(HISTORY_KEY)
    if not isinstance(section, dict):
        return {"values": {}, "provenance": {}, "reconciliation": None, "phone_update": None}
    values = section.get(VALUES_KEY)
    provenance = section.get(PROVENANCE_KEY)
    return {
        "values": dict(values) if isinstance(values, dict) else {},
        "provenance": dict(provenance) if isinstance(provenance, dict) else {},
        "reconciliation": section.get(RECONCILIATION_KEY),
        "phone_update": section.get(PHONE_UPDATE_KEY),
    }


def get_values(draft_data: Any) -> dict[str, Any]:
    """Current history values ({} when unanswered)."""
    return get_history_section(draft_data)["values"]


def _field_error(field: str, message: str) -> dict[str, list[str]]:
    return {field: [message]}


def validate_history_values(values: Any) -> dict[str, Any]:
    """Strict validation for history values (422 on undeclared/excluded/invalid).

    Unknown and not_assessed are distinct permitted values, never coerced to
    false. Excluded FR-14 regimen fields are rejected even when they look
    well-formed. Returns the values unchanged when valid.
    """
    if not isinstance(values, dict):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "History values must be a JSON object.",
            {"history.values": ["Must be a JSON object."]},
        )
    errors: dict[str, list[str]] = {}
    for key, value in values.items():
        if key in EXCLUDED_FIELDS:
            errors.update(_field_error(key, "This field is excluded and cannot be stored."))
            continue
        spec = ALLOWED_FIELDS.get(key)
        if spec is None:
            errors.update(_field_error(key, "Unknown history field."))
            continue
        if value not in spec["permitted"]:
            errors.update(
                _field_error(key, f"Must be one of {spec['permitted']}."),
            )
    if errors:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "History values are invalid.", errors
        )
    return values


def evaluate_history(values: Any) -> dict[str, Any]:
    """Non-raising preview evaluation (mirrors engine result shape).

    Undeclared ids and excluded ids appear as item_errors with aggregates
    suppressed; missing declared ids appear as missing_item_ids. Never
    zero-fills: history has no scores, only completeness.
    """
    if not isinstance(values, dict):
        values = {}
    item_errors: dict[str, list[str]] = {}
    for key, value in values.items():
        if key in EXCLUDED_FIELDS:
            item_errors.setdefault(key, []).append("This field is excluded.")
            continue
        spec = ALLOWED_FIELDS.get(key)
        if spec is None:
            item_errors.setdefault(key, []).append("Unknown history field.")
        elif value not in spec["permitted"]:
            item_errors.setdefault(key, []).append(f"Invalid value for {key}.")
    valid = {
        key: value
        for key, value in values.items()
        if key in ALLOWED_FIELDS and key not in item_errors
    }
    missing = [field_id for field_id in FIELD_IDS if field_id not in valid]
    if item_errors or missing:
        status = "unanswered" if (not valid and not item_errors) else "partial"
        return {
            "status": status,
            "missing_item_ids": missing,
            "item_errors": item_errors,
            "definition_version": definition_version(),
        }
    return {
        "status": "complete",
        "missing_item_ids": [],
        "item_errors": {},
        "definition_version": definition_version(),
    }


def validate_reconciliation(value: Any) -> dict[str, Any] | None:
    """Validate the minimal reconciliation state (None allowed when absent)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation state is invalid.",
            {"history.reconciliation": ["Must be an object or null."]},
        )
    status = value.get("status", "pending")
    if status not in RECONCILIATION_STATUSES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation status is invalid.",
            {"history.reconciliation.status": [f"Must be one of {list(RECONCILIATION_STATUSES)}."]},
        )
    baseline_id = value.get("baseline_encounter_id")
    if baseline_id is not None and not isinstance(baseline_id, str):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation baseline is invalid.",
            {"history.reconciliation.baseline_encounter_id": ["Must be text or null."]},
        )
    return {"status": status, "baseline_encounter_id": baseline_id}


def validate_phone_update(value: Any) -> str | None:
    """Optional free-text phone update (no country validation, like S06)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Phone update must be text.",
            {"history.phone_update": ["Must be text or null."]},
        )
    return value


def compute_history_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative history preview for one draft revision."""
    del author_id  # provenance carries attribution; preview needs no gate.
    section = get_history_section(draft_data)
    values = section["values"]
    evaluation = evaluate_history(values)
    return {
        "values": values,
        "provenance": section["provenance"],
        "reconciliation": section["reconciliation"],
        "phone_update": section["phone_update"],
        "evaluation": evaluation,
        "definition_version": definition_version(),
        "revision": revision,
        "analysis_visible": True,
        "analysis_visible_label": "analysis_visible",
    }


def read_history_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only history read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_history_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state


def save_history(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    values: Any,
    reconciliation: Any = None,
    phone_update: Any = None,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Strict history save with server-stamped provenance (422 on invalid).

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404/403), then the revision fence (412), then strict validation (422 for
    undeclared/excluded/invalid values, bad reconciliation, or non-text
    phone). Success stamps per-field provenance
    ``{source, author_id, recorded_at}`` (preserving ``copied_baseline``
    entries and their baseline ids), stores the minimal reconciliation state
    and optional phone update, bumps ``revision = expected + 1``, and audits.
    Questionnaire answers stay on the PATCH path; this command owns values,
    provenance, reconciliation, and phone_update.
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
    validated_values = validate_history_values(values)
    validated_reconciliation = validate_reconciliation(reconciliation)
    validated_phone = validate_phone_update(phone_update)
    moment = contracts.utcnow()
    stamp = contracts.serialize_utc(moment)
    author_id = str(encounter["author_id"])
    draft_data = dict(encounter.get("draft_data") or {})
    prior_section = get_history_section(draft_data)
    prior_provenance = prior_section["provenance"]
    provenance: dict[str, Any] = {}
    for field_id in validated_values:
        prior = prior_provenance.get(field_id)
        if (
            isinstance(prior, dict)
            and prior.get("source") == "copied_baseline"
            and isinstance(prior.get("baseline_encounter_id"), str)
        ):
            provenance[field_id] = {
                "source": "copied_baseline",
                "author_id": author_id,
                "recorded_at": stamp,
                "baseline_encounter_id": prior["baseline_encounter_id"],
            }
        else:
            provenance[field_id] = {
                "source": "clinician_entry",
                "author_id": author_id,
                "recorded_at": stamp,
            }
    updated_section: dict[str, Any] = {
        VALUES_KEY: dict(validated_values),
        PROVENANCE_KEY: provenance,
        RECONCILIATION_KEY: validated_reconciliation,
        PHONE_UPDATE_KEY: validated_phone,
    }
    updated_draft = dict(draft_data)
    updated_draft[HISTORY_KEY] = updated_section
    encounters_service.validate_draft_data(updated_draft)
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
        operation="history.save.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": expected_revision + 1,
            "definition_version": definition_version(),
        },
    )
    state = compute_history_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
    )
    return updated, state, stamp


def idempotency_request_hash(
    target_id: uuid.UUID, expected_revision: int, body: dict[str, Any]
) -> str:
    """Canonical hash of a history command (target + revision + body)."""
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision, "body": body}
    )
