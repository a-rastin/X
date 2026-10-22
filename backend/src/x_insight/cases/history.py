"""Structured history page state: persistence with provenance (S12).

Plan.md §§2.2, 5 (FR-14, FR-20): history is structured, with only expressly
defined analysis-visible fields. Medications contain bundled catalog
references only; dose, unit, route, frequency, active/stopped, and free-text
entries stay excluded even if forged. Unknown is distinct from false (no).
Core History and revised pharmacotherapy controls omit not_assessed; other
workflow controls retain it where explicitly permitted. Undeclared or excluded
field ids fail validation (422 on strict writes; item_errors on previews).

Storage: history lives in ``encounters.draft_data["history"]`` through the
shared S07 autosave contract (opaque ``draft_data`` object + revision +
``If-Match``/``412`` + ``Idempotency-Key``). Item answers travel via the
existing ``PATCH /encounters/{id}`` path; this module only reads page state
for previews plus one strict ``POST .../history`` command that validates and
stamps provenance. No new table, no new persistence mechanism — no migration.

Shape: ``draft_data["history"] = {values: {field_id: value},
provenance: {field_id: {source, author_id, recorded_at, baseline...}},
reconciliation: {status, ...}, phone_update: str | None}``. All history
values are analysis-visible (analysis_visible true); working notes and legacy
note keys stay excluded by serialization allowlists.

Content: core fields follow the current conditional History inventory. The server
validates the owner-confirmed workflow release, then derives the versioned
application input contract and exposes its permitted workflow controls. Original
clinical review is retained as provenance; the derived input contract records no
new clinical approval.
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

HISTORY_VERSION = "v2"

TRISTATE = ("yes", "no", "unknown")

ALLOWED_FIELDS: dict[str, dict[str, Any]] = {
    "h_exposure_dopamine_blocker": {
        "permitted": ["yes", "no", "unknown"],
        "category": "exposure",
    },
    "h_movement_persistence_4w": {
        "permitted": list(TRISTATE),
        "category": "duration",
    },
    "h_onset_timing": {
        "permitted": [
            "during_exposure",
            "within_4w_oral_withdrawal",
            "within_8w_lai_withdrawal",
            "other",
            "unknown",
        ],
        "category": "exposure",
    },
    "h_trial_adequacy_prior": {
        "permitted": ["adequate", "inadequate", "unknown"],
        "category": "trial_adequacy",
    },
    "h_prior_response": {
        "permitted": ["response", "no_response", "unknown"],
        "category": "prior_response",
    },
    "h_alternative_cause_considered": {
        "permitted": list(TRISTATE),
        "category": "exposure",
    },
    "h_falls_or_limitation": {
        "permitted": list(TRISTATE),
        "category": "monitoring",
    },
}

FIELD_IDS = tuple(ALLOWED_FIELDS)

# Read compatibility only: these ids cannot be introduced by a new write.
RETIRED_FIELD_IDS = frozenset(
    {
        "h_exposure_duration_3m",
        "h_exposure_1m_if_60plus",
        "h_medication_timeline_documented",
        "h_monitoring_baseline",
        "h_functional_impact",
        *(
            f"candidate/history/pharmacotherapy.v2/{field}"
            for field in (
                "h_prior_treatment_difficulty",
                "h_preference_barrier",
                "h_exposure_modifier",
                "h_trial_duration_review",
                "h_urgent_safety_concern",
                "h_monitoring_gap",
            )
        ),
    }
)
# Stable application cleanup also applies during content outages.
LAI_PREFIX = "candidate/history/lai_indication_choice.v1/"
AGGRESSION_PREFIX = "candidate/history/aggression_clozapine.v1/"
SUICIDE_PREFIX = "candidate/history/high_suicide_clozapine.v1/"
RETIRED_FIELD_IDS = RETIRED_FIELD_IDS | frozenset(
    {
        *(
            LAI_PREFIX + field
            for field in (
                "h_schizophrenia_established",
                "h_oral_experience",
                "h_product_plan",
                "h_lai_treatment_status",
            )
        ),
        *(
            AGGRESSION_PREFIX + field
            for field in (
                "h_schizophrenia_established",
                "h_persistence_despite_treatment",
                "h_urgent_safety_concern",
                "h_adherence_concern",
                "h_modifiable_targets",
            )
        ),
        *(
            SUICIDE_PREFIX + field
            for field in (
                "h_schizophrenia_established",
                "h_substantial_suicide_risk",
                "h_persistence_despite_treatment",
                "h_urgent_safety_concern",
                "h_adherence_concern",
                "h_modifiable_targets",
                "h_clozapine_treatment_status",
                "h_willing_to_discuss",
                "h_monitoring_plan",
                "h_medical_review",
            )
        ),
    }
)
REVISED_CONTEXT_FIELDS = {
    **{
        LAI_PREFIX + field: {"not_assessed": "unknown"}
        for field in (
            "h_lai_preference",
            "h_adherence_history",
            "h_discussion_context",
            "h_delivery_barriers",
        )
    },
    AGGRESSION_PREFIX + "h_substantial_aggression_risk": {"not_assessed": "unknown"},
}
REVISED_CONTEXT_FIELDS[LAI_PREFIX + "h_adherence_history"]["Uncertain"] = "unknown"
NMS_FIELD = LAI_PREFIX + "h_prior_nms"

DEFAULT_UNKNOWN_FIELD_IDS = frozenset(set(ALLOWED_FIELDS))

# Application-level cleanup must work even when generation content is unavailable.
# These stable form rules mirror the pinned pharmacotherapy.v3 input contract;
# permitted model categories remain validated through the strict content inventory.
PHARMACOTHERAPY_PREFIX = "candidate/history/pharmacotherapy.v2/"
TREATMENT_CONTEXT_FIELD = f"{PHARMACOTHERAPY_PREFIX}h_antipsychotic_treatment_context"
PHARMACOTHERAPY_LEGACY_VALUES = {
    f"{PHARMACOTHERAPY_PREFIX}h_physical_vulnerability": {
        "Present": "yes",
        "Absent": "no",
        "not_assessed": "unknown",
    },
    f"{PHARMACOTHERAPY_PREFIX}h_substance_contribution": {
        "Present": "yes",
        "Absent": "no",
        "not_assessed": "unknown",
    },
    TREATMENT_CONTEXT_FIELD: {
        "not_assessed": "unknown",
        "NoEstablishedTreatment": "unknown",
    },
}

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
    """Current History inventory version (awaiting owner review)."""
    return HISTORY_VERSION


def applicable_field_ids(values: dict[str, Any]) -> tuple[str, ...]:
    """Only visible core questions contribute to History completeness."""
    hidden = set()
    if values.get("h_exposure_dopamine_blocker") != "yes":
        hidden.update({"h_onset_timing", "h_trial_adequacy_prior", "h_prior_response"})
    elif values.get("h_trial_adequacy_prior") != "inadequate":
        hidden.add("h_prior_response")
    if values.get("h_movement_persistence_4w") != "yes":
        hidden.update({"h_alternative_cause_considered", "h_falls_or_limitation"})
    return tuple(field_id for field_id in FIELD_IDS if field_id not in hidden)


def normalize_history_values(values: Any) -> dict[str, Any]:
    """Read old drafts without reviving retired or currently hidden answers.

    Unknown defaults carry no attribution. Other invalid/undeclared values stay
    available to preview validation; this is not a replacement for strict writes.
    """
    normalized = dict(values) if isinstance(values, dict) else {}
    for field_id in RETIRED_FIELD_IDS:
        normalized.pop(field_id, None)
    for field_id in DEFAULT_UNKNOWN_FIELD_IDS:
        if normalized.get(field_id) == "not_assessed":
            normalized[field_id] = "unknown"
    normalized.setdefault("h_exposure_dopamine_blocker", "unknown")
    normalized.setdefault("h_movement_persistence_4w", "unknown")
    applicable = applicable_field_ids(normalized)
    for field_id in DEFAULT_UNKNOWN_FIELD_IDS - {"h_exposure_dopamine_blocker"}:
        if field_id in applicable:
            normalized.setdefault(field_id, "unknown")
        else:
            normalized.pop(field_id, None)
    for path, legacy_mapping in PHARMACOTHERAPY_LEGACY_VALUES.items():
        if path == TREATMENT_CONTEXT_FIELD and normalized["h_exposure_dopamine_blocker"] != "yes":
            normalized.pop(path, None)
            continue
        raw = normalized.get(path)
        if isinstance(raw, str) and raw in legacy_mapping:
            normalized[path] = legacy_mapping[raw]
        normalized.setdefault(path, "unknown")
    for path, legacy in REVISED_CONTEXT_FIELDS.items():
        raw = normalized.get(path)
        if isinstance(raw, str) and raw in legacy:
            normalized[path] = legacy[raw]
        normalized.setdefault(path, "unknown")
    if normalized["h_exposure_dopamine_blocker"] != "yes":
        normalized.pop(NMS_FIELD, None)
    else:
        normalized.setdefault(NMS_FIELD, "unknown")
    return normalized


def normalize_draft_history(draft_data: Any) -> Any:
    """Normalize mutable draft History while preserving unrelated page state."""
    if not isinstance(draft_data, dict) or not isinstance(draft_data.get(HISTORY_KEY), dict):
        return draft_data
    updated = dict(draft_data)
    section = dict(draft_data[HISTORY_KEY])
    if VALUES_KEY not in section and any(
        key in ALLOWED_FIELDS or key in RETIRED_FIELD_IDS or key.startswith("candidate/history/")
        for key in section
    ):
        # Retain the engineering client's historical direct-value shape.
        metadata = {PROVENANCE_KEY, RECONCILIATION_KEY, PHONE_UPDATE_KEY}
        raw = {key: value for key, value in section.items() if key not in metadata}
        normalized = normalize_history_values(raw)
        section = {key: value for key, value in section.items() if key in metadata}
        provenance = section.get(PROVENANCE_KEY)
        if isinstance(provenance, dict):
            section[PROVENANCE_KEY] = {
                key: value
                for key, value in provenance.items()
                if key in normalized
                and key in raw
                and not (normalized[key] == "unknown" and raw[key] != "unknown")
            }
        section.update(normalized)
        updated[HISTORY_KEY] = section
        return updated
    if VALUES_KEY not in section:
        return updated
    section.update(get_history_section(draft_data))
    updated[HISTORY_KEY] = section
    return updated


def history_for_autosave(previous: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    """Clean legacy round trips, reject newly supplied retired/invalid choices.

    The general autosave remains permissive for other pages and History fields;
    the revised questions enforce their current contract on new answers.
    """
    section = incoming.get(HISTORY_KEY)
    if not isinstance(section, dict):
        return normalize_draft_history(incoming)
    values = section.get(VALUES_KEY, section)
    if not isinstance(values, dict):
        return normalize_draft_history(incoming)
    prior_section = previous.get(HISTORY_KEY) if isinstance(previous, dict) else None
    prior = (
        prior_section.get(VALUES_KEY, prior_section) if isinstance(prior_section, dict) else None
    )
    prior = prior if isinstance(prior, dict) else {}
    normalized_prior = normalize_history_values(prior)
    errors = {}
    workflow_changes = {}
    for field_id, value in values.items():
        unchanged = (field_id in prior and value == prior[field_id]) or (
            field_id in normalized_prior and value == normalized_prior[field_id]
        )
        if field_id in RETIRED_FIELD_IDS and not unchanged:
            errors[field_id] = ["This history field has been removed."]
        elif (
            field_id in ALLOWED_FIELDS
            and value not in ALLOWED_FIELDS[field_id]["permitted"]
            and not unchanged
        ):
            errors[field_id] = [f"Must be one of {ALLOWED_FIELDS[field_id]['permitted']}."]
        elif field_id.startswith("candidate/history/") and not unchanged:
            workflow_changes[field_id] = value
    if errors:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "History values are invalid.", errors
        )
    if workflow_changes:
        # A full autosave body may merely echo normalized History while editing
        # another page. Only new workflow answers require available content.
        from x_insight.models.workflow_release import history_fields

        fields = history_fields()
        for field_id, value in workflow_changes.items():
            spec = fields.get(field_id, {})
            if "default_value" in spec and value not in spec["permitted"]:
                errors[field_id] = [f"Must be one of {spec['permitted']}."]
        if errors:
            raise contracts.ContractError(
                422, "VALIDATION_FAILED", "History values are invalid.", errors
            )
    return normalize_draft_history(incoming)


def get_history_section(draft_data: Any) -> dict[str, Any]:
    """History sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        draft_data = {}
    section = draft_data.get(HISTORY_KEY)
    if not isinstance(section, dict):
        section = {}
    raw_values = section.get(VALUES_KEY)
    raw_values = raw_values if isinstance(raw_values, dict) else {}
    values = normalize_history_values(raw_values)
    provenance = section.get(PROVENANCE_KEY)
    return {
        "values": values,
        "provenance": {
            key: value
            for key, value in provenance.items()
            if key in values
            and key in raw_values
            and not (values[key] == "unknown" and raw_values[key] != "unknown")
        }
        if isinstance(provenance, dict)
        else {},
        "reconciliation": section.get(RECONCILIATION_KEY),
        "phone_update": section.get(PHONE_UPDATE_KEY),
    }


def get_values(draft_data: Any) -> dict[str, Any]:
    """Current history values including applicable Unknown defaults."""
    return get_history_section(draft_data)["values"]


def _field_error(field: str, message: str) -> dict[str, list[str]]:
    return {field: [message]}


def validate_history_values(values: Any) -> dict[str, Any]:
    """Strict validation for history values (422 on undeclared/excluded/invalid).

    Unknown never becomes false. Core and revised pharmacotherapy controls
    reject not_assessed; other workflow controls permit it only when declared.
    Excluded FR-14 regimen fields are rejected even when they look well-formed.
    Valid values receive defaults and conditional cleanup.
    """
    if not isinstance(values, dict):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "History values must be a JSON object.",
            {"history.values": ["Must be a JSON object."]},
        )
    from x_insight.models.workflow_release import history_fields

    allowed = {**ALLOWED_FIELDS, **history_fields()}
    errors: dict[str, list[str]] = {}
    for key, value in values.items():
        if key in EXCLUDED_FIELDS:
            errors.update(_field_error(key, "This field is excluded and cannot be stored."))
            continue
        spec = allowed.get(key)
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
    return normalize_history_values(values)


def evaluate_history(values: Any) -> dict[str, Any]:
    """Non-raising preview evaluation (mirrors engine result shape).

    Undeclared ids and excluded ids appear as item_errors with aggregates
    suppressed; missing declared ids appear as missing_item_ids. Never
    zero-fills: history has no scores, only completeness.
    """
    values = normalize_history_values(values)
    from x_insight.models.workflow_release import history_fields

    allowed = {**ALLOWED_FIELDS, **history_fields()}
    item_errors: dict[str, list[str]] = {}
    for key, value in values.items():
        if key in EXCLUDED_FIELDS:
            item_errors.setdefault(key, []).append("This field is excluded.")
            continue
        spec = allowed.get(key)
        if spec is None:
            item_errors.setdefault(key, []).append("Unknown history field.")
        elif value not in spec["permitted"]:
            item_errors.setdefault(key, []).append(f"Invalid value for {key}.")
    valid = {
        key: value for key, value in values.items() if key in allowed and key not in item_errors
    }
    missing = [field_id for field_id in applicable_field_ids(values) if field_id not in valid]
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


def compute_history_state(
    draft_data: Any, *, author_id: str, revision: int, workflow: str = "registration"
) -> dict[str, Any]:
    """Server-authoritative history preview for one draft revision."""
    del author_id  # provenance carries attribution; preview needs no gate.
    from x_insight.models.workflow_release import history_fields, workflow_history_fields
    from x_insight.reasoning.readiness import compute_proposal_readiness

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
        "workflow_fields": workflow_history_fields(workflow),
        "proposal_readiness": compute_proposal_readiness(draft_data, revision, workflow),
        "allowed_field_ids": [*FIELD_IDS, *history_fields()],
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
        workflow="followup" if encounter["kind"] == "follow_up" else "registration",
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
    from x_insight.cases import patients as _patients

    _patients.require_encounter_mutable(session, encounter, author)
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
        if field_id not in values:
            continue  # Defaults express uncertainty; they are not clinician entries.
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
            "patient_id": str(updated.get("patient_id")),
            "encounter_id": str(encounter_id),
            "actor_id": str(author.get("id")),
            "actor_display": str(author.get("username", "")),
            "revision": expected_revision + 1,
            "definition_version": definition_version(),
        },
    )
    state = compute_history_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
        workflow="followup" if updated["kind"] == "follow_up" else "registration",
    )
    return updated, state, stamp


def idempotency_request_hash(
    target_id: uuid.UUID, expected_revision: int, body: dict[str, Any]
) -> str:
    """Canonical hash of a history command (target + revision + body)."""
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision, "body": body}
    )
