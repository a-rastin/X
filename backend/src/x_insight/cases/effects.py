"""Adverse-effect page state: four effects with questionnaires (S12).

Plan.md §§2.2, 5 (FR-20–21): each follow-up effect accepts
``present|absent|not_assessed``. Present requires the corresponding complete
reviewed questionnaire plus reviewed severity; absent and not_assessed carry
null severity and no questionnaire-completion requirement. Changing status
must explicitly clear an obsolete severity (absent/not_assessed with a
non-null severity is 422). Missing required items suppress calculated
results to null (never zero) unless a reviewed source defines a
missing-data rule (none does here). Urgent handling is independent of
questionnaire completion; completeness is enforced only for present effects,
including the Acute Dystonia Dx Criteria form.

Storage: effects live in ``encounters.draft_data["effects"]`` through the
shared S07 autosave contract. Questionnaire item answers travel via the
existing ``PATCH /encounters/{id}`` path; effect status/severity travel via
the strict ``POST .../effects/{effect}/status`` command here (revision-fenced,
audited). No new table — no migration.

Instruments (drafts under ``content/history/``, awaiting owner review):
- akathisia: full BARS, 4 items (0-3/0-3/0-3/0-5); global is the principal
  severity; threshold global>=2 indicates present; no invented summed rule.
- parkinsonism: full SAS, 10 items 0-4; raw total 0-40, mean 0-4; no severity
  bands; reviewed severity is independent (mild|moderate|severe).
- tardive dyskinesia: AIMS complete form missing — no items invented;
  evaluation is awaiting_source with null results; present cannot clear
  completion until the source is resolved.
- acute dystonia: Acute Dystonia Dx Criteria, 5 criteria items
  (yes/no/unknown), no total score; urgent airway flag routes independently.

Unknown/not-assessed questionnaire states stay distinct from negatives; absent
versus not-assessed stays distinct; undeclared item ids are item_errors (GET)
or 422 (strict status command preserves answers verbatim and never invents).
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

EFFECTS_KEY = "effects"

EFFECT_KEYS = (
    "tardive_dyskinesia",
    "akathisia",
    "parkinsonism",
    "acute_dystonia",
)

STATUSES = ("present", "absent", "not_assessed")

EFFECT_STATUS_OPERATION_PREFIX = "effects.status"

BARS_VERSION = "v1"
SAS_VERSION = "v1"
AIMS_VERSION = "v1"
ACUTE_VERSION = "v1"

BARS_ITEMS: dict[str, tuple[int, int]] = {
    "bars_objective": (0, 3),
    "bars_awareness": (0, 3),
    "bars_distress": (0, 3),
    "bars_global": (0, 5),
}

SAS_ITEMS: dict[str, tuple[int, int]] = {
    "sas_gait": (0, 4),
    "sas_arm_dropping": (0, 4),
    "sas_shoulder_shaking": (0, 4),
    "sas_elbow_rigidity": (0, 4),
    "sas_wrist_rigidity": (0, 4),
    "sas_leg_pendulousness": (0, 4),
    "sas_head_dropping": (0, 4),
    "sas_glabellar_tap": (0, 4),
    "sas_tremor": (0, 4),
    "sas_salivation": (0, 4),
}

ACUTE_ITEMS = (
    "addx_sustained_posture",
    "addx_medication_timeline",
    "addx_distribution_persistence",
    "addx_exclusions",
    "addx_urgent_airway",
)

ACUTE_PERMITTED = ("yes", "no", "unknown")

REVIEWED_SEVERITIES = ("mild", "moderate", "severe")

# akathisia reviewed severity is the BARS global score itself (0-5); the other
# three effects use independent reviewed labels (no invented bands/totals).
PARKINSONISM_SEVERITIES = REVIEWED_SEVERITIES
TARDIVE_SEVERITIES = REVIEWED_SEVERITIES
ACUTE_SEVERITIES = REVIEWED_SEVERITIES


def definition_versions() -> dict[str, str]:
    """Questionnaire definition versions (draft v1s, awaiting review)."""
    return {
        "tardive_dyskinesia": AIMS_VERSION,
        "akathisia": BARS_VERSION,
        "parkinsonism": SAS_VERSION,
        "acute_dystonia": ACUTE_VERSION,
    }


def _is_int_in_range(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def evaluate_bars(answers: Any) -> dict[str, Any]:
    """BARS evaluation: complete only with all 4 valid items."""
    if not isinstance(answers, dict):
        answers = {}
    item_errors: dict[str, list[str]] = {}
    for key, value in answers.items():
        bounds = BARS_ITEMS.get(key)
        if bounds is None:
            item_errors.setdefault(key, []).append("Unknown BARS item.")
        elif not _is_int_in_range(value, bounds[0], bounds[1]):
            item_errors.setdefault(key, []).append(f"Must be an integer {bounds[0]}-{bounds[1]}.")
    valid = {
        key: value for key, value in answers.items() if key in BARS_ITEMS and key not in item_errors
    }
    missing = [item_id for item_id in BARS_ITEMS if item_id not in valid]
    if item_errors or missing:
        status = "unanswered" if (not valid and not item_errors) else "partial"
        return {
            "status": status,
            "missing_item_ids": missing,
            "item_errors": item_errors,
            "scores": {
                "objective": None,
                "awareness": None,
                "distress": None,
                "global": None,
                "component_total": None,
            },
            "findings": {},
            "definition_version": BARS_VERSION,
        }
    objective = int(valid["bars_objective"])
    awareness = int(valid["bars_awareness"])
    distress = int(valid["bars_distress"])
    global_score = int(valid["bars_global"])
    scores = {
        "objective": objective,
        "awareness": awareness,
        "distress": distress,
        "global": global_score,
        "component_total": objective + awareness + distress,
    }
    labels = {0: "Absent", 1: "Questionable", 2: "Mild", 3: "Moderate", 4: "Marked", 5: "Severe"}
    findings: dict[str, Any] = {
        "global_label": labels[global_score],
        "threshold_met": global_score >= 2,
        "principal_severity": global_score,
    }
    if objective > 0 and awareness == 0 and global_score == 0:
        findings["pseudoakathisia_note"] = (
            "Characteristic movements without subjective restlessness: "
            "reassess rather than assume acute medication-induced akathisia."
        )
    return {
        "status": "complete",
        "missing_item_ids": [],
        "item_errors": {},
        "scores": scores,
        "findings": findings,
        "definition_version": BARS_VERSION,
    }


def evaluate_sas(answers: Any) -> dict[str, Any]:
    """SAS evaluation: raw total 0-40 and mean 0-4 only when all 10 valid."""
    if not isinstance(answers, dict):
        answers = {}
    item_errors: dict[str, list[str]] = {}
    for key, value in answers.items():
        bounds = SAS_ITEMS.get(key)
        if bounds is None:
            item_errors.setdefault(key, []).append("Unknown SAS item.")
        elif not _is_int_in_range(value, bounds[0], bounds[1]):
            item_errors.setdefault(key, []).append("Must be an integer 0-4.")
    valid = {
        key: value for key, value in answers.items() if key in SAS_ITEMS and key not in item_errors
    }
    missing = [item_id for item_id in SAS_ITEMS if item_id not in valid]
    if item_errors or missing:
        status = "unanswered" if (not valid and not item_errors) else "partial"
        return {
            "status": status,
            "missing_item_ids": missing,
            "item_errors": item_errors,
            "scores": {"raw_total": None, "mean": None},
            "findings": {},
            "definition_version": SAS_VERSION,
        }
    raw_total = sum(int(valid[item_id]) for item_id in SAS_ITEMS)
    mean = round(raw_total / 10, 1)
    return {
        "status": "complete",
        "missing_item_ids": [],
        "item_errors": {},
        "scores": {"raw_total": raw_total, "mean": mean},
        "findings": {
            "original_normal_note": (
                "Mean up to 0.3 within normal range; greater than 0.3 abnormal. "
                "No mild/moderate/severe bands."
            ),
            "no_severity_bands": True,
        },
        "definition_version": SAS_VERSION,
    }


def evaluate_aims(answers: Any) -> dict[str, Any]:
    """AIMS gap evaluation: awaiting_source, never invented items."""
    supplied = dict(answers) if isinstance(answers, dict) else {}
    item_errors: dict[str, list[str]] = {}
    for key in supplied:
        item_errors.setdefault(key, []).append("AIMS form awaiting source; no items defined.")
    return {
        "status": "awaiting_source",
        "missing_item_ids": [],
        "item_errors": item_errors,
        "scores": None,
        "findings": {
            "awaiting_source": True,
            "research_thresholds_only": (
                "Schooler-Kane: 3+ in one region or 2+ in two regions with "
                "3-month exposure and no alternative cause "
                "(research only, not a total)."
            ),
            "no_total_score": True,
        },
        "definition_version": AIMS_VERSION,
    }


def evaluate_acute(answers: Any) -> dict[str, Any]:
    """Acute Dystonia Dx Criteria: 5 criteria, no total, urgent independent."""
    if not isinstance(answers, dict):
        answers = {}
    item_errors: dict[str, list[str]] = {}
    for key, value in answers.items():
        if key not in ACUTE_ITEMS:
            item_errors.setdefault(key, []).append("Unknown Acute Dystonia Dx Criteria item.")
        elif value not in ACUTE_PERMITTED:
            item_errors.setdefault(key, []).append("Must be yes, no, or unknown.")
    valid = {
        key: value
        for key, value in answers.items()
        if key in ACUTE_ITEMS and key not in item_errors
    }
    missing = [item_id for item_id in ACUTE_ITEMS if item_id not in valid]
    urgent_value = valid.get("addx_urgent_airway")
    urgent: bool | None = None
    if urgent_value == "yes":
        urgent = True
    elif urgent_value in ("no", "unknown"):
        urgent = False if urgent_value == "no" else None
        if urgent_value == "unknown":
            urgent = None
    if item_errors or missing:
        status = "unanswered" if (not valid and not item_errors) else "partial"
        return {
            "status": status,
            "missing_item_ids": missing,
            "item_errors": item_errors,
            "scores": None,
            "findings": {"urgent": urgent, "no_total_score": True},
            "definition_version": ACUTE_VERSION,
        }
    return {
        "status": "complete",
        "missing_item_ids": [],
        "item_errors": {},
        "scores": None,
        "findings": {"urgent": urgent, "no_total_score": True},
        "definition_version": ACUTE_VERSION,
    }


EVALUATORS = {
    "tardive_dyskinesia": evaluate_aims,
    "akathisia": evaluate_bars,
    "parkinsonism": evaluate_sas,
    "acute_dystonia": evaluate_acute,
}


def evaluate_questionnaire(effect: str, answers: Any) -> dict[str, Any]:
    """Evaluate one effect's questionnaire (awaiting_source for AIMS)."""
    evaluator = EVALUATORS.get(effect)
    if evaluator is None:
        raise contracts.ContractError(
            422, "VALIDATION_FAILED", "Unknown effect.", {"effect": ["Must be a known effect."]}
        )
    return evaluator(answers)


def get_effects_section(draft_data: Any) -> dict[str, Any]:
    """Effects sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {}
    section = draft_data.get(EFFECTS_KEY)
    if not isinstance(section, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key in EFFECT_KEYS:
        entry = section.get(key)
        if isinstance(entry, dict):
            questionnaire = entry.get("questionnaire")
            answers: dict[str, Any] = {}
            if isinstance(questionnaire, dict) and isinstance(questionnaire.get("answers"), dict):
                answers = dict(questionnaire["answers"])
            cleaned[key] = {
                "status": entry.get("status"),
                "severity": entry.get("severity"),
                "questionnaire": {"answers": answers},
            }
        else:
            cleaned[key] = {"status": None, "severity": None, "questionnaire": {"answers": {}}}
    return cleaned


def validate_effect_key(effect: Any) -> str:
    """One of the four follow-up effects (422 otherwise)."""
    if effect not in EFFECT_KEYS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Unknown effect.",
            {"effect": [f"Must be one of {list(EFFECT_KEYS)}."]},
        )
    return str(effect)


def validate_status(status: Any) -> str:
    """present|absent|not_assessed (422 otherwise)."""
    if status not in STATUSES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Effect status is invalid.",
            {"status": [f"Must be one of {list(STATUSES)}."]},
        )
    return str(status)


def validate_severity_for_status(
    effect: str, status: str, severity: Any, evaluation: dict[str, Any]
) -> Any:
    """Reviewed severity contract per effect (422 on mismatch or stale).

    Present requires a reviewed severity plus a complete questionnaire
    (except AIMS, which is awaiting_source and therefore cannot satisfy
    present). Absent/not_assessed require null severity and impose no
    questionnaire-completion requirement. A non-null severity with
    absent/not_assessed is the stale-severity case: the caller must
    explicitly clear it (send null).
    """
    if status in ("absent", "not_assessed"):
        if severity is not None:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Changing status must explicitly clear an obsolete severity.",
                {"severity": ["Must be null when status is absent or not_assessed."]},
            )
        return None
    # status == present from here.
    if effect == "tardive_dyskinesia":
        if severity not in TARDIVE_SEVERITIES:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Reviewed severity is required when present.",
                {"severity": [f"Must be one of {list(TARDIVE_SEVERITIES)}."]},
            )
        raise contracts.ContractError(
            422,
            "AWAITING_SOURCE",
            "The complete AIMS form is awaiting source; present cannot clear completion yet.",
            {"questionnaire": ["Complete AIMS source required before present."]},
        )
    if effect == "akathisia":
        if not _is_int_in_range(severity, 0, 5):
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Reviewed severity is required when present.",
                {"severity": ["Must be the BARS global score, an integer 0-5."]},
            )
        if evaluation.get("status") != "complete":
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Present requires the complete BARS questionnaire.",
                {"questionnaire": ["Complete every BARS item before marking present."]},
            )
        global_score = evaluation.get("scores", {}).get("global")
        if severity != global_score:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Severity must match the BARS global score.",
                {"severity": ["Must equal the recorded BARS global score."]},
            )
        if int(severity) < 2:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "BARS global below the present threshold.",
                {"severity": ["Global 2 or greater indicates present per BARS."]},
            )
        return severity
    if effect == "parkinsonism":
        if severity not in PARKINSONISM_SEVERITIES:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Reviewed severity is required when present.",
                {"severity": [f"Must be one of {list(PARKINSONISM_SEVERITIES)}."]},
            )
        if evaluation.get("status") != "complete":
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Present requires the complete SAS questionnaire.",
                {"questionnaire": ["Complete every SAS item before marking present."]},
            )
        return severity
    # acute_dystonia
    if severity not in ACUTE_SEVERITIES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reviewed severity is required when present.",
            {"severity": [f"Must be one of {list(ACUTE_SEVERITIES)}."]},
        )
    if evaluation.get("status") != "complete":
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Present requires the complete Acute Dystonia Dx Criteria.",
            {"questionnaire": ["Complete every criteria item before marking present."]},
        )
    return severity


def compute_effects_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative effects preview for one draft revision."""
    del author_id  # attribution lives on writes; preview needs no gate.
    section = get_effects_section(draft_data)
    versions = definition_versions()
    effects: dict[str, Any] = {}
    for key in EFFECT_KEYS:
        entry = section.get(
            key, {"status": None, "severity": None, "questionnaire": {"answers": {}}}
        )
        status = entry.get("status")
        if status not in STATUSES:
            status = None
        answers = entry.get("questionnaire", {}).get("answers", {})
        if not isinstance(answers, dict):
            answers = {}
        evaluation = evaluate_questionnaire(key, answers)
        severity = entry.get("severity")
        # Absent/not_assessed suppress severity to null in preview even when a
        # stale value lingers in storage; present keeps the stored value for
        # review (strict writes reject mismatches with 422).
        if status in ("absent", "not_assessed"):
            severity = None
        questionnaire = {
            "answers": answers,
            "definition_version": versions[key],
            "evaluation": evaluation,
        }
        urgent: bool | None = None
        if key == "acute_dystonia":
            urgent = evaluation.get("findings", {}).get("urgent")
        effects[key] = {
            "status": status,
            "severity": severity,
            "questionnaire": questionnaire,
            "urgent": urgent,
        }
    return {
        "effects": effects,
        "definition_versions": versions,
        "revision": revision,
    }


def read_effects_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only effects read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_effects_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state


def set_effect_status(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    effect: str,
    status: Any,
    severity: Any,
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Strict effect status transition (422 on stale severity or incomplete).

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404/403), then the revision fence (412), then the effect/status/severity
    contract (422). Questionnaire answers are preserved verbatim from storage
    (they travel via PATCH); this command never invents or repairs them.
    Present requires a complete questionnaire (except AIMS awaiting_source)
    plus the reviewed severity; absent/not_assessed require null severity and
    impose no completion requirement. Success bumps the revision and audits.
    """
    effect_key = validate_effect_key(effect)
    validated_status = validate_status(status)
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
    section = get_effects_section(draft_data)
    current_answers = section.get(effect_key, {}).get("questionnaire", {}).get("answers", {})
    evaluation = evaluate_questionnaire(effect_key, current_answers)
    # validate_severity_for_status raises 422 for stale severity, missing
    # severity when present, incomplete questionnaire when present, BARS
    # global mismatch/threshold, and AIMS awaiting_source.
    validated_severity = validate_severity_for_status(
        effect_key, validated_status, severity, evaluation
    )
    moment = contracts.utcnow()
    stamp = contracts.serialize_utc(moment)
    author_id = str(encounter["author_id"])
    updated_section = dict(draft_data.get(EFFECTS_KEY) or {})
    if not isinstance(updated_section, dict):
        updated_section = {}
    updated_section[effect_key] = {
        "status": validated_status,
        "severity": validated_severity,
        "questionnaire": {
            "answers": dict(current_answers),
            "definition_version": definition_versions()[effect_key],
        },
        "updated_at": stamp,
        "actor_id": author_id,
    }
    updated_draft = dict(draft_data)
    updated_draft[EFFECTS_KEY] = updated_section
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
        operation=f"{EFFECT_STATUS_OPERATION_PREFIX}.{effect_key}.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": expected_revision + 1,
            "effect": effect_key,
            "status": validated_status,
            "definition_version": definition_versions()[effect_key],
        },
    )
    state = compute_effects_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
    )
    return updated, state, stamp


def idempotency_request_hash(
    target_id: uuid.UUID, effect: str, expected_revision: int, body: dict[str, Any]
) -> str:
    """Canonical hash of an effect-status command (target + effect + rev + body)."""
    return contracts.canonical_hash(
        {
            "target_id": str(target_id),
            "effect": effect,
            "expected_revision": expected_revision,
            "body": body,
        }
    )
