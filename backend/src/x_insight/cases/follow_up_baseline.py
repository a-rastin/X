"""Signed-source resolution and frozen baseline projection for ``follow-up-v1``.

Package C (plan §6.1-3, specification §2). The latest signed encounter is
selected across *all* supported signature variants under the caller's patient
``FOR UPDATE`` lock, ordered by ``signed_at`` descending with a stable ID
tie-break. Only semantically equivalent structured fields are carried through
explicit per-variant adapters; unknown fields, unavailable prior data and
unmatched historical catalog IDs stay visible as unresolved rather than being
guessed, reinterpreted or silently dropped. No prior free-text plan, historical
effect/assessment answer or retired BN context is ever copied into current
editable state.

The projection is bounded (lists are capped) so it stays inside the existing
``draft_data`` node/byte limits. The full historical chart stays in signed
snapshots; this holds only a bounded summary.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import tables

BASELINE_SCHEMA_VERSION = "follow-up-baseline-v1"
LEGACY_SCHEMAS = frozenset({"signed-encounter-v1", "signed-encounter-v2", "signed-encounter-v3"})
NEW_CASE_SCHEMA = "signed-encounter-v4"
FOLLOW_UP_SCHEMA = "signed-encounter-v5"

MAX_BASELINE_MEDICATIONS = 100
MAX_PRIOR_LABS = 64
MAX_SUMMARY_ITEMS = 40
MAX_SUMMARY_TEXT = 4000


def classify_variant(row: dict[str, Any]) -> str:
    """Classify a signed snapshot by its reference variant + schema (fail closed)."""
    snapshot = row.get("snapshot") or {}
    schema = snapshot.get("schema_version")
    if schema == FOLLOW_UP_SCHEMA and row.get("follow_up_proposal_id"):
        return "follow-up-v5"
    if schema == NEW_CASE_SCHEMA and row.get("new_case_proposal_id"):
        return "new-case-v4"
    if schema in LEGACY_SCHEMAS and row.get("batch_id") and row.get("proposal_id"):
        return "legacy"
    return "unsupported"


def latest_signed_source(session: Session, patient_id: uuid.UUID) -> dict[str, Any] | None:
    """Latest signed snapshot for the patient, stable tie-break, plus ordinal.

    Caller holds the patient row ``FOR UPDATE`` (create_open_draft), so a
    sign/create race cannot select a stale source. ``ordinal`` is the number of
    prior signed application encounters plus one (discarded/open drafts and
    self-reported hospitalizations are not visits). Returns ``None`` when the
    patient has no signed record at all — a valid empty-history case.
    """
    rows = (
        session.execute(
            select(tables.signed_encounter_snapshots)
            .where(tables.signed_encounter_snapshots.c.patient_id == patient_id)
            .order_by(
                tables.signed_encounter_snapshots.c.signed_at.desc(),
                tables.signed_encounter_snapshots.c.id.desc(),
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    return {"row": dict(rows[0]), "ordinal": len(rows) + 1, "signed_count": len(rows)}


def _source_block(row: dict[str, Any], variant: str, ordinal: int) -> dict[str, Any]:
    snapshot = row.get("snapshot") or {}
    encounter = snapshot.get("encounter") or {}
    return {
        "encounter_id": str(row.get("encounter_id")),
        "snapshot_id": str(row.get("id")),
        "snapshot_hash": str(row.get("snapshot_hash") or ""),
        "signature_schema_version": snapshot.get("schema_version"),
        "variant": variant,
        "workflow_version": snapshot.get("workflow_version")
        or encounter.get("workflow_version", "legacy-v1"),
        "kind": encounter.get("kind"),
        "visit_date": encounter.get("created_at"),
        "signed_at": contracts.serialize_utc(row["signed_at"]),
        "ordinal": ordinal,
    }


def _catalog_names(session: Session, ids: list[str], version: str) -> dict[str, str]:
    if not ids:
        return {}
    try:
        rows = session.execute(
            text(
                "SELECT c.catalog_drug_id, c.canonical_name FROM ddi_concepts c "
                "JOIN ddi_dataset_releases r ON r.id=c.release_id "
                "WHERE r.version=:version AND c.catalog_drug_id=ANY(:ids)"
            ),
            {"version": version, "ids": ids},
        ).all()
    except Exception:
        return {}  # coverage unavailable: treat every source id as unmatched
    return {str(entry[0]): str(entry[1]) for entry in rows}


def _prior_scores(draft_data: dict[str, Any]) -> dict[str, Any]:
    from x_insight.assessments import cssrs, panss

    panss_answers = (draft_data.get("panss") or {}).get("answers") or {}
    cssrs_answers = (draft_data.get("cssrs") or {}).get("answers") or {}
    try:
        panss_result = panss.evaluate_answers(panss_answers)
        total = panss_result.get("scores", {}).get("total")
    except Exception:
        total = None
    # An empty answer set is "not recorded", not a computed zero/no-risk score.
    severity = None
    if cssrs_answers:
        try:
            cssrs_result = cssrs.evaluate_answers(cssrs_answers)
            severity = cssrs_result.get("scores", {}).get("ideation_severity_recent")
        except Exception:
            severity = None
    return {
        "panss_total": total,
        "panss_severity": (draft_data.get("panss") or {}).get("severity"),
        "cssrs_severity": severity,
        "cssrs_item_4": cssrs_answers.get("css_i4_recent"),
        "cssrs_item_5": cssrs_answers.get("css_i5_recent"),
    }


def _contraception(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Prior structured contraception recommendations with source dates."""
    payload = (snapshot.get("proposal") or {}).get("payload") or {}
    section = (payload.get("sections") or {}).get("contraception")
    if not isinstance(section, dict):
        return []
    source = snapshot.get("encounter") or {}
    return [
        {
            "value": section.get("counsel")
            if snapshot.get("schema_version") == FOLLOW_UP_SCHEMA
            else section.get("value"),
            "reasons": list(section.get("reasons") or [])[:MAX_SUMMARY_ITEMS],
            "visit_date": source.get("created_at"),
            "encounter_id": source.get("id"),
        }
    ]


def _provenance(field: str, source_field: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_field": source_field,
        "source_encounter_id": source["encounter_id"],
        "snapshot_hash": source["snapshot_hash"],
        "visit_date": source["visit_date"],
        "signed_at": source["signed_at"],
        "value_from": field,
    }


def _carry_conditions(answers: dict[str, Any]) -> dict[str, bool | None]:
    """Historical explicit answers become this visit's inherited values."""
    result: dict[str, bool | None] = {}
    for name in (
        "hepatic_failure",
        "renal_failure",
        "glucose_abnormality",
        "hyperlipidemia",
        "hyperprolactinemia",
        "pregnant",
    ):
        value = answers.get(name)
        if isinstance(value, dict):
            value = value.get("manual")
        result[name] = value if isinstance(value, bool) else None
    return result


def _effective_inherited(answer: Any) -> bool | None:
    """Recorded (explicit or previously inherited) answer; never a lab guess."""
    if not isinstance(answer, dict):
        return None
    manual = answer.get("manual")
    if isinstance(manual, bool):
        return manual
    inherited = answer.get("inherited")
    return inherited if isinstance(inherited, bool) else None


def _adapt_new_case(
    draft_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """New case v4: carry the semantically equivalent structured fields."""
    nc = draft_data.get("new_case") or {}
    considerations = nc.get("considerations") or {}
    psychiatric = nc.get("psychiatric") or {}
    substances = nc.get("substances") or {}
    bmi = nc.get("bmi") or {}
    neurological = {
        "focal_neurological_deficit": nc.get("focal_neurological_deficit"),
        "suspected_brain_lesion": nc.get("suspected_brain_lesion"),
        "suspected_autoimmune_encephalitis": nc.get("suspected_autoimmune_encephalitis"),
    }
    conditions = _carry_conditions(nc.get("conditions") or {})
    carry: dict[str, Any] = {
        "heart_failure": nc.get("heart_failure"),
        "tobacco": nc.get("tobacco"),
        "pregnant": conditions["pregnant"],
        "breastfeeding": nc.get("breastfeeding"),
        "psychiatric": {
            "present": psychiatric.get("present"),
            "conditions": list(psychiatric.get("conditions") or []),
            "other_text": psychiatric.get("other_text") or "",
        },
        # New Case's "Other" free text is retained, never reclassified into the
        # newly named opioid/methamphetamine categories.
        "substances": {
            "present": substances.get("present"),
            "marijuana": bool(substances.get("marijuana")),
            "opioids": False,
            "methamphetamine": False,
            "other": bool(substances.get("other")),
            "other_text": substances.get("other_text") or "",
        },
        "bmi": {
            "height_cm": bmi.get("height_cm"),
            "weight_kg": bmi.get("weight_kg"),
            "source": bmi.get("source"),
            "manual_category": bmi.get("manual_category"),
        },
        "neurological": neurological,
        "conditions": conditions,
        "seizure_risk": considerations.get("seizure_risk"),
        "qtc_risk": considerations.get("qtc_risk"),
        "hospitalization_count": None,  # no structured count in v4
    }
    inherited = {
        "new_case.heart_failure": "heart_failure",
        "new_case.tobacco": "tobacco",
        "new_case.psychiatric": "psychiatric",
        "new_case.substances": "substances",
        "new_case.bmi": "bmi",
        "new_case.considerations.seizure_risk": "seizure_risk",
        "new_case.considerations.qtc_risk": "qtc_risk",
        **{f"new_case.conditions.{name}": f"conditions.{name}" for name in conditions},
    }
    prior_labs = _labs_from_values(nc.get("labs") or {})
    return carry, inherited, prior_labs


def _adapt_follow_up(
    draft_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Follow-up v5: v5-after-v5 carry-forward (fresh labs/assessments/effects)."""
    prev = draft_data.get("follow_up") or {}
    history = prev.get("history") or {}
    considerations = prev.get("considerations") or {}
    carry: dict[str, Any] = {}
    for field in (
        "heart_failure",
        "tobacco",
        "pregnant",
        "breastfeeding",
        "hospitalization_count",
    ):
        carry[field] = history.get(field)
    for section in ("psychiatric", "substances", "bmi", "neurological"):
        if isinstance(history.get(section), dict):
            carry[section] = dict(history[section])
    conditions = {
        name: _effective_inherited((history.get("conditions") or {}).get(name))
        for name in (
            "hepatic_failure",
            "renal_failure",
            "glucose_abnormality",
            "hyperlipidemia",
            "hyperprolactinemia",
            "pregnant",
        )
    }
    carry["conditions"] = conditions
    carry["seizure_risk"] = considerations.get("seizure_risk")
    carry["qtc_risk"] = considerations.get("qtc_risk")
    treatment = prev.get("treatment") or {}
    carry["main_catalog_drug_id"] = treatment.get("main_catalog_drug_id")
    carry["formulation"] = treatment.get("formulation")
    inherited = {
        f"follow_up.history.{field}": field for field in carry if field not in ("conditions",)
    }
    inherited.update(
        {f"follow_up.history.conditions.{name}": f"conditions.{name}" for name in conditions}
    )
    prior_labs = _labs_from_values(history.get("labs") or {})
    return carry, inherited, prior_labs


def _adapt_legacy(
    draft_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Legacy v1-v3: only medications + prior scores have clean equivalents.

    Legacy history values are workflow-question tristates (Present/Absent/
    not_assessed) tied to retired BN context, not clean clinical booleans.
    Mapping them would guess values from a different clinical concept, so they
    stay unset and must be completed by the physician (spec §2).
    """
    return {}, {}, []


def _labs_from_values(labs: dict[str, Any]) -> list[dict[str, Any]]:
    values = labs.get("values") if isinstance(labs, dict) else None
    if not isinstance(values, dict):
        return []
    from x_insight.cases import follow_up

    fields = follow_up.lab_fields()
    return [
        {
            "field": name,
            "value": value,
            "unit": fields[name]["unit"] if name in fields else None,
        }
        for name, value in list(values.items())[:MAX_PRIOR_LABS]
        if value is not None
    ]


def project_baseline(
    session: Session,
    source: dict[str, Any] | None,
    patient: dict[str, Any],
    *,
    lexicomp_version: str,
) -> dict[str, Any]:
    """Freeze the latest signed source into one bounded baseline projection."""
    from x_insight.cases import follow_up

    empty: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "has_source": False,
        "source_status": "no_prior_signed_visit",
        "source": None,
        "summary": {},
        "inherited": {},
        "carry": {},
        "unmatched_medications": [],
        "prior_labs": [],
        "prior_contraception": [],
        "prior_scores": {},
    }
    if source is None:
        return empty
    row = source["row"]
    variant = classify_variant(row)
    source_block = _source_block(row, variant, source["ordinal"])
    snapshot = row.get("snapshot") or {}
    draft_data = snapshot.get("draft_data") or {}
    baseline: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "has_source": True,
        "source_status": "available" if variant != "unsupported" else "unsupported_variant",
        "source": source_block,
        "summary": {},
        "inherited": {},
        "carry": {},
        "unmatched_medications": [],
        "prior_labs": [],
        "prior_contraception": [],
        "prior_scores": _prior_scores(draft_data),
    }
    if variant == "unsupported":
        # Fail closed: never silently fall back to an older signed record.
        baseline["summary"] = {
            "visit_ordinal": source["ordinal"],
            "note": "unsupported_signed_variant",
        }
        return baseline
    if variant == "new-case-v4":
        carry, inherited_map, prior_labs = _adapt_new_case(draft_data)
    elif variant == "follow-up-v5":
        carry, inherited_map, prior_labs = _adapt_follow_up(draft_data)
    else:
        carry, inherited_map, prior_labs = _adapt_legacy(draft_data)

    # Resolve historical catalog IDs against the current pinned release: only
    # valid IDs enter the current list; unmatched ones stay pending/unresolved.
    medications_section = draft_data.get("medications") or {}
    raw_entries = medications_section.get("entries") or []
    source_ids = [
        str(entry["catalog_drug_id"])
        for entry in raw_entries[:MAX_BASELINE_MEDICATIONS]
        if isinstance(entry, dict) and isinstance(entry.get("catalog_drug_id"), str)
    ]
    names = _catalog_names(session, source_ids, lexicomp_version)
    valid_ids = set(names)
    carry["medications"] = [
        {"catalog_drug_id": entry_id}
        for entry_id in dict.fromkeys(source_ids)
        if entry_id in valid_ids
    ]
    carry["selection_dataset_version"] = lexicomp_version
    baseline["unmatched_medications"] = [
        {
            "source_id": entry_id,
            "name": None,
            "status": "pending",
            "source_encounter_id": source_block["encounter_id"],
            "visit_date": source_block["visit_date"],
        }
        for entry_id in dict.fromkeys(source_ids)
        if entry_id not in valid_ids
    ]
    # A carried main selection survives only if it still identifies a valid
    # current entry and formulation.
    main = carry.get("main_catalog_drug_id")
    if main not in valid_ids or carry.get("formulation") not in ("oral", "lai"):
        carry["main_catalog_drug_id"] = None
        carry["formulation"] = None

    lab_field_specs = follow_up.lab_fields()
    for entry in prior_labs:
        entry["visit_date"] = source_block["visit_date"]
        entry["source_encounter_id"] = source_block["encounter_id"]
        entry["snapshot_hash"] = source_block["snapshot_hash"]
        entry["unit"] = lab_field_specs.get(entry["field"], {}).get("unit")
    baseline["prior_labs"] = prior_labs[:MAX_PRIOR_LABS]
    baseline["prior_contraception"] = _contraception(snapshot)
    for field, source_field in inherited_map.items():
        baseline["inherited"][field] = _provenance(field, source_field, source_block)
    baseline["carry"] = carry

    comorbidities = [
        name.replace("_", " ")
        for name in (
            "heart_failure",
            "tobacco",
            "hepatic_failure",
            "renal_failure",
            "glucose_abnormality",
            "hyperlipidemia",
            "hyperprolactinemia",
        )
        if carry.get(name) is True or (carry.get("conditions") or {}).get(name) is True
    ][:MAX_SUMMARY_ITEMS]
    prior_scores = baseline["prior_scores"]
    parts = [
        f"Recorded visit {source['ordinal']}.",
        f"Last visit {source_block['visit_date'] or 'not recorded'}.",
        f"Sex {patient.get('sex')}.",
    ]
    if comorbidities:
        parts.append("Recorded comorbidities: " + ", ".join(comorbidities) + ".")
    else:
        parts.append("No structured comorbidities recorded.")
    medication_names = [
        names[entry_id] for entry_id in dict.fromkeys(source_ids) if entry_id in names
    ]
    parts.append(
        "Current medications: " + ", ".join(medication_names[:MAX_SUMMARY_ITEMS]) + "."
        if medication_names
        else "No recorded current medications."
    )
    if prior_scores.get("panss_total") is not None:
        parts.append(f"Prior PANSS total {prior_scores['panss_total']} (informational).")
    else:
        parts.append("Prior PANSS not recorded.")
    if carry.get("hospitalization_count") is not None:
        parts.append(f"Prior hospitalizations recorded: {carry['hospitalization_count']}.")
    baseline["summary"] = {
        "visit_ordinal": source["ordinal"],
        "sex": patient.get("sex"),
        "last_visit_date": source_block["visit_date"],
        "signed_at": source_block["signed_at"],
        "comorbidities": comorbidities,
        "medication_names": medication_names[:MAX_SUMMARY_ITEMS],
        "hospitalization_count": carry.get("hospitalization_count"),
        "prior_scores": prior_scores,
        "tobacco": carry.get("tobacco"),
        "substances": carry.get("substances"),
        "harm": (draft_data.get("follow_up") or draft_data.get("new_case") or {}).get(
            "harm", {"self": None, "others": None}
        ),
        "schizophrenia_context": patient.get("clinical_status"),
        "note": "recorded_visits_not_lifetime_count",
        "text": " ".join(parts)[:MAX_SUMMARY_TEXT],
    }
    return baseline
