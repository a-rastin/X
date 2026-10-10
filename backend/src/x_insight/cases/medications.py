"""Medication draft storage + versioned DDI report reference (S20).

Plan.md 2.2, 6.3 (FR-14-16, FR-20): medications hold bundled catalog
references only; dose, unit, route, frequency, active/stopped, and free-text
entries stay excluded even if forged. Unknown catalog identifiers are rejected,
never accepted as free text.

Storage: medications live in ``encounters.draft_data["medications"]`` through
the shared S07 autosave contract (opaque ``draft_data`` object + revision +
``If-Match``/``412`` + ``Idempotency-Key``). Item entries travel via the
existing ``PATCH /encounters/{id}`` path; this module owns one strict
``POST .../medications`` command (revision-fenced, catalog-validated via the
S19 checker, report reference stored) plus author-only previews. No new table
-- no migration.

Shape: ``draft_data["medications"] = {entries: [{catalog_drug_id}],
provenance: {catalog_drug_id: {source, author_id, recorded_at,
baseline_encounter_id?}}, reconciliation: {status, baseline_encounter_id?} |
None, dataset_version, catalog_version, medication_fingerprint, generated_at}``.
The four report-reference fields pin the current report for later run
snapshots (S40). ``GET .../ddi-report`` recomputes live via the S19 checker
and returns the pinned report shape only when the stored reference matches
the live fingerprint and reconciliation is settled; otherwise it returns an
explicit pending/error flag with no rows presented as current.
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

MEDICATIONS_KEY = "medications"
ENTRIES_KEY = "entries"
PROVENANCE_KEY = "provenance"
RECONCILIATION_KEY = "reconciliation"
DATASET_VERSION_KEY = "dataset_version"
CATALOG_VERSION_KEY = "catalog_version"
FINGERPRINT_KEY = "medication_fingerprint"
GENERATED_AT_KEY = "generated_at"

MEDICATIONS_SAVE_OPERATION = "medications.save"

RECONCILIATION_STATUSES = ("not_required", "pending", "reconciled")
PROVENANCE_SOURCES = ("clinician_entry", "copied_baseline")

MAX_MEDICATIONS = 100
MAX_CATALOG_ID_CHARS = 200


def get_medications_section(draft_data: Any) -> dict[str, Any]:
    """Medications sub-object inside the opaque autosave body (never None)."""
    if not isinstance(draft_data, dict):
        return {
            "entries": [],
            "provenance": {},
            "reconciliation": None,
            "dataset_version": None,
            "catalog_version": None,
            "medication_fingerprint": None,
            "generated_at": None,
        }
    section = draft_data.get(MEDICATIONS_KEY)
    if not isinstance(section, dict):
        return {
            "entries": [],
            "provenance": {},
            "reconciliation": None,
            "dataset_version": None,
            "catalog_version": None,
            "medication_fingerprint": None,
            "generated_at": None,
        }
    entries = section.get(ENTRIES_KEY)
    provenance = section.get(PROVENANCE_KEY)
    return {
        "entries": list(entries) if isinstance(entries, list) else [],
        "provenance": dict(provenance) if isinstance(provenance, dict) else {},
        "reconciliation": section.get(RECONCILIATION_KEY),
        "dataset_version": section.get(DATASET_VERSION_KEY),
        "catalog_version": section.get(CATALOG_VERSION_KEY),
        "medication_fingerprint": section.get(FINGERPRINT_KEY),
        "generated_at": section.get(GENERATED_AT_KEY),
    }


def validate_entries_structure(entries: Any) -> list[dict[str, Any]]:
    """Structural validation for medication entries (no catalog lookup).

    Each entry must be exactly ``{catalog_drug_id}`` with no other keys.
    Duplicates collapse (first-seen order kept); they never become self-pairs.
    Raises 422 on non-list, oversize, malformed, or forged extra keys.
    """
    if not isinstance(entries, list):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Medications must be a list.",
            {"medications": ["Must be a list."]},
        )
    if len(entries) > MAX_MEDICATIONS:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Too many medications.",
            {"medications": [f"Must have at most {MAX_MEDICATIONS} entries."]},
        )
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: dict[str, list[str]] = {}
    for index, entry in enumerate(entries):
        field = f"medications.{index}"
        if not isinstance(entry, dict):
            errors.setdefault(field, []).append("Must be an object with catalog_drug_id.")
            continue
        extra = set(entry) - {"catalog_drug_id"}
        if extra:
            for key in sorted(extra):
                errors.setdefault(f"{field}.{key}", []).append("This field is excluded.")
            continue
        catalog_id = entry.get("catalog_drug_id")
        if (
            not isinstance(catalog_id, str)
            or not catalog_id.strip()
            or len(catalog_id) > MAX_CATALOG_ID_CHARS
        ):
            errors.setdefault(field, []).append("Must carry a non-empty catalog_drug_id.")
            continue
        if catalog_id in seen:
            continue
        seen.add(catalog_id)
        cleaned.append({"catalog_drug_id": catalog_id})
    if errors:
        raise contracts.ContractError(422, "VALIDATION_FAILED", "Medications are invalid.", errors)
    return cleaned


def validate_reconciliation(value: Any) -> dict[str, Any] | None:
    """Validate the reconciliation state (None allowed when absent)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation state is invalid.",
            {"medications.reconciliation": ["Must be an object or null."]},
        )
    status = value.get("status", "pending")
    if status not in RECONCILIATION_STATUSES:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation status is invalid.",
            {
                "medications.reconciliation.status": [
                    f"Must be one of {list(RECONCILIATION_STATUSES)}."
                ]
            },
        )
    baseline_id = value.get("baseline_encounter_id")
    if baseline_id is not None and not isinstance(baseline_id, str):
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Reconciliation baseline is invalid.",
            {"medications.reconciliation.baseline_encounter_id": ["Must be text or null."]},
        )
    return {"status": status, "baseline_encounter_id": baseline_id}


def evaluate_entries(entries: Any) -> dict[str, Any]:
    """Non-raising preview evaluation (structural only, never catalog lookup).

    Forged extra keys and malformed entries appear as item_errors; missing
    entries are simply absent (empty list is valid and unanswered, never an
    error). Catalog existence is enforced on strict writes and on the
    versioned report, not here.
    """
    if not isinstance(entries, list):
        return {
            "status": "invalid",
            "item_errors": {"medications": ["Must be a list."]},
        }
    item_errors: dict[str, list[str]] = {}
    seen: set[str] = set()
    valid_count = 0
    for index, entry in enumerate(entries):
        field = f"medications.{index}"
        if not isinstance(entry, dict):
            item_errors.setdefault(field, []).append("Must be an object.")
            continue
        extra = set(entry) - {"catalog_drug_id"}
        if extra:
            for key in sorted(extra):
                item_errors.setdefault(f"{field}.{key}", []).append("This field is excluded.")
            continue
        catalog_id = entry.get("catalog_drug_id")
        if not isinstance(catalog_id, str) or not catalog_id.strip():
            item_errors.setdefault(field, []).append("Must carry a non-empty catalog_drug_id.")
            continue
        if catalog_id in seen:
            continue
        seen.add(catalog_id)
        valid_count += 1
    if item_errors:
        return {"status": "invalid", "item_errors": item_errors}
    if valid_count == 0:
        return {"status": "unanswered", "item_errors": {}}
    return {"status": "valid", "item_errors": {}}


def compute_medications_state(draft_data: Any, *, author_id: str, revision: int) -> dict[str, Any]:
    """Server-authoritative medications preview for one draft revision."""
    del author_id  # preview needs no gate; attribution lives on writes.
    section = get_medications_section(draft_data)
    evaluation = evaluate_entries(section["entries"])
    return {
        "entries": section["entries"],
        "provenance": section["provenance"],
        "reconciliation": section["reconciliation"],
        "dataset_version": section["dataset_version"],
        "catalog_version": section["catalog_version"],
        "medication_fingerprint": section["medication_fingerprint"],
        "generated_at": section["generated_at"],
        "evaluation": evaluation,
        "revision": revision,
    }


def read_medications_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only medications read: 404 missing/released, 403 strangers."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    state = compute_medications_state(
        encounter.get("draft_data") or {},
        author_id=str(encounter["author_id"]),
        revision=int(encounter["revision"]),
    )
    return encounter, state


def _resolve_dataset_version(requested: Any, stored: Any, engine: Any) -> str:
    """Pinned version resolution: explicit > stored > latest (never silent)."""
    from x_insight.ddi import checker as checker_module

    if requested is not None:
        if not isinstance(requested, str) or not requested.strip():
            raise contracts.ContractError(
                422,
                "INVALID_DATASET_VERSION",
                "dataset_version must be text.",
                {"dataset_version": ["Must be text."]},
            )
        return requested
    if isinstance(stored, str) and stored.strip():
        return stored
    latest = checker_module.latest_release_version(engine)
    if latest is None:
        raise contracts.ContractError(503, "DATASET_UNAVAILABLE", "DDI dataset is unavailable.")
    return latest


def save_medications(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    medications: Any,
    dataset_version: Any = None,
    reconciliation: Any = None,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Strict drug-only save with catalog validation + report reference.

    Row-locked read-modify-write in the caller's transaction: privacy first
    (404/403), then the revision fence (412), then structural validation (422
    for forged regimen/free-text keys), then catalog validation via the S19
    checker against the pinned dataset (422 for unknown identifiers, 404 for
    unknown versions, 503 when unavailable). Success stores entries with
    server-stamped provenance, the settled reconciliation state, and the pinned
    report reference (dataset/catalog/fingerprint/generated_at), bumps
    ``revision = expected + 1``, and audits without identifiers.
    """
    from x_insight import db as db_module
    from x_insight.ddi import checker as checker_module

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
    cleaned = validate_entries_structure(medications)
    draft_data = dict(encounter.get("draft_data") or {})
    prior_section = get_medications_section(draft_data)
    if reconciliation is None:
        existing_recon = prior_section["reconciliation"]
        if isinstance(existing_recon, dict) and existing_recon.get("status") in (
            RECONCILIATION_STATUSES
        ):
            validated_recon: dict[str, Any] | None = {
                "status": existing_recon.get("status", "not_required"),
                "baseline_encounter_id": existing_recon.get("baseline_encounter_id"),
            }
        else:
            validated_recon = {"status": "not_required", "baseline_encounter_id": None}
    else:
        validated_recon = validate_reconciliation(reconciliation)
        if validated_recon is None:
            validated_recon = {"status": "not_required", "baseline_encounter_id": None}
    engine = db_module.get_engine()
    pinned_version = _resolve_dataset_version(
        dataset_version, prior_section["dataset_version"], engine
    )
    catalog_ids = [entry["catalog_drug_id"] for entry in cleaned]
    try:
        report = checker_module.check(engine, catalog_ids, pinned_version)
    except checker_module.CheckError as exc:
        raise contracts.ContractError(
            exc.status_code, exc.code, exc.message, exc.field_errors
        ) from exc
    moment = contracts.utcnow()
    stamp = contracts.serialize_utc(moment)
    author_id = str(encounter["author_id"])
    prior_provenance = prior_section["provenance"]
    provenance: dict[str, Any] = {}
    for entry in cleaned:
        catalog_id = entry["catalog_drug_id"]
        prior = prior_provenance.get(catalog_id)
        if (
            isinstance(prior, dict)
            and prior.get("source") == "copied_baseline"
            and isinstance(prior.get("baseline_encounter_id"), str)
        ):
            provenance[catalog_id] = {
                "source": "copied_baseline",
                "author_id": author_id,
                "recorded_at": stamp,
                "baseline_encounter_id": prior["baseline_encounter_id"],
            }
        else:
            provenance[catalog_id] = {
                "source": "clinician_entry",
                "author_id": author_id,
                "recorded_at": stamp,
            }
    updated_section: dict[str, Any] = {
        ENTRIES_KEY: cleaned,
        PROVENANCE_KEY: provenance,
        RECONCILIATION_KEY: validated_recon,
        DATASET_VERSION_KEY: report["dataset_version"],
        CATALOG_VERSION_KEY: report["catalog_version"],
        FINGERPRINT_KEY: report["medication_fingerprint"],
        GENERATED_AT_KEY: report["generated_at"],
    }
    updated_draft = dict(draft_data)
    updated_draft[MEDICATIONS_KEY] = updated_section
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
        operation="medications.save.success",
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": expected_revision + 1,
            "dataset_version": report["dataset_version"],
            "medication_fingerprint": report["medication_fingerprint"],
            "medication_count": len(cleaned),
            "reconciliation_status": (validated_recon or {}).get("status"),
        },
    )
    state = compute_medications_state(
        updated.get("draft_data") or {},
        author_id=author_id,
        revision=int(updated["revision"]),
    )
    return updated, state, stamp


def read_ddi_report_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only versioned DDI report with stale fencing (never stale rows).

    Returns (encounter, payload) where payload carries ``report_status``
    ``current`` with the live S19 report, or ``pending``/``error`` with
    ``report`` None plus an explicit reason. A fingerprint mismatch, a pending
    reconciliation, a missing pin, or an unknown/unavailable dataset never
    returns old rows as current.
    """
    from x_insight import db as db_module
    from x_insight.ddi import checker as checker_module

    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    section = get_medications_section(encounter.get("draft_data") or {})
    entries = section["entries"]
    # Structural entries for the live check (already deduped on strict writes;
    # PATCH-forged rows are filtered here so the checker sees drug-only input).
    catalog_ids: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("catalog_drug_id"), str):
            catalog_id = entry["catalog_drug_id"]
            if catalog_id and catalog_id not in catalog_ids:
                catalog_ids.append(catalog_id)
    reconciliation = section["reconciliation"]
    recon_status = reconciliation.get("status") if isinstance(reconciliation, dict) else None
    stored_fingerprint = section["medication_fingerprint"]
    pinned_version = section["dataset_version"]
    revision = int(encounter["revision"])

    def _payload(
        status: str,
        reason: str | None,
        report: dict[str, Any] | None,
        live_fingerprint: str | None,
    ) -> dict[str, Any]:
        return {
            "report_status": status,
            "reason": reason,
            "report": report,
            "medication_fingerprint": live_fingerprint,
            "stored_fingerprint": stored_fingerprint,
            "dataset_version": pinned_version,
            "catalog_version": section["catalog_version"],
            "generated_at": section["generated_at"],
            "reconciliation": reconciliation,
            "revision": revision,
        }

    if recon_status == "pending":
        return encounter, _payload("pending", "reconciliation_required", None, None)
    if not isinstance(pinned_version, str) or not pinned_version.strip():
        return encounter, _payload("pending", "dataset_not_pinned", None, None)
    engine = db_module.get_engine()
    try:
        live = checker_module.check(engine, catalog_ids, pinned_version)
    except checker_module.CheckError as exc:
        if exc.status_code in (404, 503):
            return encounter, _payload("error", "dataset_unavailable", None, None)
        return encounter, _payload("error", "invalid_medications", None, None)
    live_fingerprint = live.get("medication_fingerprint")
    if stored_fingerprint is None or live_fingerprint != stored_fingerprint:
        return encounter, _payload("pending", "stale_fingerprint", None, live_fingerprint)
    return encounter, _payload("current", None, live, live_fingerprint)


def idempotency_request_hash(
    target_id: uuid.UUID, expected_revision: int, body: dict[str, Any]
) -> str:
    """Canonical hash of a medications command (target + revision + body)."""
    return contracts.canonical_hash(
        {"target_id": str(target_id), "expected_revision": expected_revision, "body": body}
    )
