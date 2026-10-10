"""Atomic plan signing + immutable snapshots + addenda (S49, seam T1).

Plan.md §§4.1, 9.2 (FR-15, FR-22, FR-57, FR-42, NFR-04):

- Secondary plan: separately revisioned physician edit with its own
  If-Match fence (``secondary_plans`` revision, not the encounter
  revision). Never in the analysis fingerprint by construction — this
  module never reads ``patients`` for projections and ``snapshots`` only
  reads allowlisted draft keys, so plan-text edits leave per-question
  freshness/acceptance intact (S48d §4 reuse).
- Sign: one transaction locking patient/encounter/secondary-plan. Checks
  active author, draft lifecycle, expected encounter/plan/review revisions,
  a complete current original proposal/DDI report, and exact current
  per-question acceptances with successful matching results. Freshness is
  recomputed INSIDE the locked transaction via S48d per-question hash
  semantics (``question_input_hash`` + ``build_question_projection`` +
  ``evaluate_gate`` with the stored ``source_revision``), never the batch
  hash; client flags never grant authority. Freezes the full record
  (patient/encounter, batch/proposal/DDI, per-question original + final
  CPTs/results/acceptances, secondary plan, notes, inputs/versions), moves
  lifecycle draft→signed (releasing the single-draft slot via the partial
  unique index), and audits atomically. ``UNIQUE(encounter_id)`` is the
  backstop for concurrent signs; idempotent repeats return the original.
  Failures create no partial rows (single transaction, row locks).
- Addenda: any active physician may append server-derived attributed dated
  text to any signed encounter (append-only, idempotent, no snapshot
  mutation). Original signer/content stay intact.
- Signed chart read (``cases.chart``) shares snapshot content without draft
  leakage; ordinary draft routes stay draft-only (404 for signed).

S51 hooks (documented, not built): concurrent sign/save, sign/slider/reset,
sign/demographic-edit, sign/deactivation races beyond single-transaction
fencing + UNIQUE; the shared demographics-edit HTTP route that stales
without granting draft access (S48d §1 / S51). This slice proves snapshot
immutability against live patient-row changes (setup) + HTTP chart reads;
the shared-edit route itself belongs to S51.

All persistence uses the caller's ``session_scope`` transaction; this module
never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters as encounters_service
from x_insight.cases import tables as cases_tables
from x_insight.operations import audit as audit_module

SECONDARY_PLAN_SAVE_OPERATION = "secondary_plan.save"
SIGN_OPERATION = "encounters.sign"
ADDENDUM_OPERATION = "encounters.addendum"

SECONDARY_PLAN_AUDIT_OPERATION = "secondary_plan.save.success"
SIGN_AUDIT_OPERATION = "encounters.sign.success"
ADDENDUM_AUDIT_OPERATION = "encounters.addendum.success"

MAX_SECONDARY_PLAN_CHARS = 10_000
MAX_ADDENDUM_CHARS = 2000
SNAPSHOT_SCHEMA_VERSION = "signed-encounter-v1"


def _fail(status: int, code: str, message: str, field: str) -> contracts.ContractError:
    return contracts.ContractError(status, code, message, {field: [message]})


def _parse_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            f"{field} must be a UUID.",
            {field: ["Must be a UUID."]},
        ) from exc


def validate_secondary_text(value: Any) -> str:
    """Secondary plan text: verbatim string, at most 10k chars (422 otherwise).

    Empty string is allowed on PATCH (explicit clear); signing requires
    non-empty (409 PLAN_NOT_READY) so an acknowledged save must exist.
    """
    if not isinstance(value, str):
        raise _fail(422, "VALIDATION_FAILED", "Secondary plan must be text.", "text")
    if len(value) > MAX_SECONDARY_PLAN_CHARS:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            f"Secondary plan must be at most {MAX_SECONDARY_PLAN_CHARS} characters.",
            "text",
        )
    return value


def validate_addendum_text(value: Any) -> str:
    if not isinstance(value, str) or value == "":
        raise _fail(422, "VALIDATION_FAILED", "Addendum text must be non-empty.", "text")
    if len(value) > MAX_ADDENDUM_CHARS:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            f"Addendum must be at most {MAX_ADDENDUM_CHARS} characters.",
            "text",
        )
    return value


def secondary_plan_request_hash(encounter_id: uuid.UUID, expected_revision: int, text: str) -> str:
    return contracts.canonical_hash(
        {
            "target_id": str(encounter_id),
            "expected_revision": int(expected_revision),
            "text": str(text),
        }
    )


def sign_request_hash(
    encounter_id: uuid.UUID,
    expected_encounter_revision: int,
    expected_plan_revision: int,
    batch_id: uuid.UUID,
    acceptances: list[dict[str, Any]],
) -> str:
    ordered = sorted(
        [
            {
                "question_run_id": str(e.get("question_run_id")),
                "acceptance_id": str(e.get("acceptance_id")),
                "expected_review_revision": int(e.get("expected_review_revision", 0)),
            }
            for e in acceptances
        ],
        key=lambda e: str(e["question_run_id"]),
    )
    return contracts.canonical_hash(
        {
            "target_id": str(encounter_id),
            "expected_encounter_revision": int(expected_encounter_revision),
            "expected_plan_revision": int(expected_plan_revision),
            "batch_id": str(batch_id),
            "acceptances": ordered,
        }
    )


def addendum_request_hash(encounter_id: uuid.UUID, expected_revision: int, text: str) -> str:
    return contracts.canonical_hash(
        {
            "target_id": str(encounter_id),
            "expected_revision": int(expected_revision),
            "text": str(text),
        }
    )


def safe_secondary_plan(plan: Mapping[str, Any], encounter_id: Any) -> dict[str, Any]:
    return {
        "encounter_id": str(encounter_id),
        "revision": int(plan.get("revision", 1)),
        "text": str(plan.get("text", "")),
        "updated_at": contracts.serialize_utc(plan["updated_at"])
        if plan.get("updated_at") is not None
        else None,
    }


def safe_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "encounter_id": str(row.get("encounter_id")),
        "patient_id": str(row.get("patient_id")),
        "batch_id": str(row.get("batch_id")),
        "proposal_id": str(row.get("proposal_id")),
        "secondary_plan_revision": int(row.get("secondary_plan_revision", 1)),
        "secondary_plan_text": str(row.get("secondary_plan_text", "")),
        "snapshot": dict(row.get("snapshot") or {}),
        "snapshot_hash": str(row.get("snapshot_hash", "")),
        "signer_id": str(row.get("signer_id")),
        "signer_username": str(row.get("signer_username", "")),
        "signed_at": contracts.serialize_utc(row["signed_at"]),
        "encounter_revision": int(row.get("encounter_revision", 1)),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def safe_addendum(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "encounter_id": str(row.get("encounter_id")),
        "author_id": str(row.get("author_id")),
        "author_display": str(row.get("author_display", "")),
        "created_at": contracts.serialize_utc(row["created_at"]),
        "text": str(row.get("text", "")),
    }


def _get_plan_row(session: Session, encounter_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(cases_tables.secondary_plans).where(
                cases_tables.secondary_plans.c.encounter_id == encounter_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _default_plan(encounter: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "encounter_id": encounter["id"],
        "revision": 1,
        "text": "",
        "updated_at": encounter.get("updated_at"),
    }


def read_secondary_plan_for_author(
    session: Session, encounter_id: uuid.UUID, user: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Author-only secondary-plan read (draft only: 404/403, no content leak)."""
    encounter = encounters_service.read_draft_for_author(session, encounter_id, user)
    plan = _get_plan_row(session, encounter_id)
    if plan is None:
        plan = _default_plan(encounter)
    return encounter, plan


def save_secondary_plan(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_plan_revision: Any,
    text: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Author-only secondary-plan save with its own revision fence (412 stale).

    Row-locked on the encounter (serializes concurrent plan edits per
    encounter without touching the encounter revision — separate fences, no
    interference). Missing rows start at revision 1 empty; success writes
    revision expected+1 and audits. Returns (plan, encounter, timestamp).
    """
    if not isinstance(expected_plan_revision, int) or expected_plan_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_plan_revision must be a positive integer.",
            {"expected_plan_revision": ["Must be a positive integer."]},
        )
    expected = int(expected_plan_revision)
    validated = validate_secondary_text(text)
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
    plan = _get_plan_row(session, encounter_id)
    current = int(plan["revision"]) if plan is not None else 1
    if int(expected) != int(current):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The secondary plan changed. Reload and reconcile your edits.",
            {"expected_plan_revision": ["Stale plan revision."]},
        )
    moment = contracts.utcnow()
    if plan is None:
        session.execute(
            insert(cases_tables.secondary_plans).values(
                encounter_id=encounter_id,
                text=validated,
                revision=expected + 1,
                created_at=moment,
                updated_at=moment,
            )
        )
    else:
        session.execute(
            update(cases_tables.secondary_plans)
            .where(cases_tables.secondary_plans.c.encounter_id == encounter_id)
            .values(text=validated, revision=expected + 1, updated_at=moment)
        )
    session.flush()
    stored = _get_plan_row(session, encounter_id)
    assert stored is not None
    audit_module.record_audit(
        session,
        operation=SECONDARY_PLAN_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "revision": int(expected) + 1,
        },
    )
    updated = encounters_service.get_encounter(session, encounter_id)
    assert updated is not None
    return stored, updated, contracts.serialize_utc(moment)


def get_snapshot(session: Session, encounter_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(cases_tables.signed_encounter_snapshots).where(
                cases_tables.signed_encounter_snapshots.c.encounter_id == encounter_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_addenda(session: Session, encounter_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(cases_tables.encounter_addenda)
            .where(cases_tables.encounter_addenda.c.encounter_id == encounter_id)
            .order_by(
                cases_tables.encounter_addenda.c.created_at,
                cases_tables.encounter_addenda.c.id,
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _ordered_runs(session: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    from x_insight.reasoning import tables as reasoning_tables

    ordered = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
                reasoning_tables.question_runs.c.position.asc(),
                reasoning_tables.question_runs.c.question_key.asc(),
                reasoning_tables.question_runs.c.created_at.asc(),
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in ordered]


def sign_encounter(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_encounter_revision: Any,
    expected_plan_revision: Any,
    batch_id: Any,
    acceptances: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Atomic sign: freeze, release slot, audit (single transaction, row locks).

    Validates author/lifecycle/revisions, a complete current original
    proposal (stored proposal row, valid DDI by construction), and exact
    current per-question acceptances with successful matching results.
    Freshness is recomputed inside this locked transaction from current
    draft facts (S48d per-question semantics). Failures raise ContractError
    with no partial writes (caller rolls back).
    """
    from x_insight.cases import patients as patients_service
    from x_insight.probability_review import service as review_service
    from x_insight.reasoning import coordinator as coordinator_module
    from x_insight.reasoning import snapshots as snapshots_service
    from x_insight.reasoning import tables as reasoning_tables

    if not isinstance(expected_encounter_revision, int) or expected_encounter_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_encounter_revision must be a positive integer.",
            {"expected_encounter_revision": ["Must be a positive integer."]},
        )
    if not isinstance(expected_plan_revision, int) or expected_plan_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_plan_revision must be a positive integer.",
            {"expected_plan_revision": ["Must be a positive integer."]},
        )
    want_batch = _parse_uuid(batch_id, "batch_id")
    if not isinstance(acceptances, list):
        raise _fail(422, "VALIDATION_FAILED", "acceptances must be a list.", "acceptances")
    parsed_acceptances: list[dict[str, Any]] = []
    for entry in acceptances:
        if not isinstance(entry, Mapping):
            raise _fail(422, "VALIDATION_FAILED", "acceptances entries must map.", "acceptances")
        run_id = _parse_uuid(entry.get("question_run_id"), "question_run_id")
        acc_id = _parse_uuid(entry.get("acceptance_id"), "acceptance_id")
        rev = entry.get("expected_review_revision")
        if not isinstance(rev, int) or rev < 1:
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "expected_review_revision must be a positive integer.",
                {"expected_review_revision": ["Must be a positive integer."]},
            )
        parsed_acceptances.append(
            {
                "question_run_id": run_id,
                "acceptance_id": acc_id,
                "expected_review_revision": int(rev),
            }
        )

    # Lock encounter + patient + author (single-transaction fencing; UNIQUE
    # backstop for concurrent signs; S51 race hardening).
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
    if str(encounter.get("lifecycle")) == "signed":
        raise contracts.ContractError(409, "ALREADY_SIGNED", "Encounter is already signed.")
    encounters_service.require_draft_lifecycle(encounter)
    encounters_service.require_author(encounter, author)
    if int(encounter["revision"]) != int(expected_encounter_revision):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The draft changed. Reload and reconcile your edits.",
        )
    # S51: patient lock + archived + author-active inside the tx.
    patient_row = (
        session.execute(
            select(cases_tables.patients)
            .where(cases_tables.patients.c.id == encounter["patient_id"])
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if patient_row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Patient not found.")
    patient = dict(patient_row)
    if bool(patient.get("archived", False)):
        raise contracts.ContractError(
            409, "PATIENT_ARCHIVED", "Archived patients cannot be signed."
        )
    from x_insight.identity import service as _identity

    _author_row = _identity.get_user_by_id(session, author["id"])
    if _author_row is None or not bool(_author_row.get("active", False)):
        raise contracts.ContractError(403, "FORBIDDEN", "Author is inactive.")
    # Lock the author user row to serialize sign vs deactivation.
    session.execute(
        select(_identity.tables.users)
        .where(_identity.tables.users.c.id == author["id"])
        .with_for_update()
    ).mappings().first()

    plan = _get_plan_row(session, encounter_id)
    current_plan_revision = int(plan["revision"]) if plan is not None else 1
    current_plan_text = str(plan["text"]) if plan is not None else ""
    if int(expected_plan_revision) != int(current_plan_revision):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The secondary plan changed. Reload and reconcile your edits.",
            {"expected_plan_revision": ["Stale plan revision."]},
        )
    if not current_plan_text.strip():
        raise contracts.ContractError(
            409,
            "PLAN_NOT_READY",
            "Save a secondary plan before signing.",
            {"secondary_plan": ["Secondary plan is empty."]},
        )

    batch_row = (
        session.execute(
            select(reasoning_tables.generation_batches).where(
                reasoning_tables.generation_batches.c.id == want_batch
            )
        )
        .mappings()
        .first()
    )
    if batch_row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Generation batch not found.")
    batch = dict(batch_row)
    if str(batch.get("encounter_id")) != str(encounter_id):
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "Batch does not belong to this encounter.",
            {"batch_id": ["Does not match this encounter."]},
        )
    if str(batch.get("author_id")) != str(author.get("id")):
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "Batch does not belong to this author.",
            {"batch_id": ["Does not match this author."]},
        )
    # S51 §2: relevant demographics (age/sex/status) inside the locked tx;
    # phone/name do not stale. Stored hash lives in pinned_bundle.
    try:
        _pinned = batch.get("pinned_bundle") or {}
        _stored_hash = _pinned.get("patient_relevant_hash") if isinstance(_pinned, dict) else None
        if isinstance(_stored_hash, str) and _stored_hash:
            _current_hash = patients_service.relevant_demographics_hash(patient)
            if _current_hash != str(_stored_hash):
                raise contracts.ContractError(
                    409,
                    "STALE_INPUTS",
                    "Patient inputs changed. Regenerate before signing.",
                    {"patient_id": ["Stale patient inputs."]},
                )
    except contracts.ContractError:
        raise
    except Exception:
        pass
    proposal = coordinator_module.get_proposal(session, want_batch)
    if proposal is None:
        raise contracts.ContractError(
            409,
            "PROPOSAL_INCOMPLETE",
            "No complete original proposal exists for this batch.",
            {"batch_id": ["Proposal is incomplete."]},
        )

    runs = _ordered_runs(session, want_batch)
    if not runs:
        raise contracts.ContractError(409, "PROPOSAL_INCOMPLETE", "Batch holds no questions.")
    ready_runs = [r for r in runs if str(r.get("status")) == "ready"]
    for r in runs:
        status = str(r.get("status"))
        if status not in ("ready", "not_applicable"):
            raise contracts.ContractError(
                409,
                "PROPOSAL_INCOMPLETE",
                "Proposal is not complete for signing.",
                {"batch_id": [f"Question {r.get('question_key')} is {status}."]},
            )
    by_run = {str(e["question_run_id"]): e for e in parsed_acceptances}
    if len(by_run) != len(parsed_acceptances):
        raise _fail(422, "VALIDATION_FAILED", "Duplicate acceptance references.", "acceptances")
    if set(by_run) != {str(r["id"]) for r in ready_runs}:
        raise contracts.ContractError(
            409,
            "REFERENCE_MISMATCH",
            "Acceptances must cover exactly the ready questions.",
            {"acceptances": ["Must reference exactly the ready question runs."]},
        )
    for r in runs:
        if str(r.get("status")) == "not_applicable" and str(r["id"]) in by_run:
            raise contracts.ContractError(
                409,
                "REFERENCE_MISMATCH",
                "Skipped questions carry no acceptance.",
                {"acceptances": ["Skipped question must not be accepted."]},
            )

    draft_data = encounter.get("draft_data") or {}
    acceptance_rows: dict[str, dict[str, Any]] = {}
    for run in runs:
        if str(run.get("status")) != "ready":
            continue
        supplied = by_run[str(run["id"])]
        # Review pointer fence (412 stale, never merged).
        state = review_service.get_review_state(session, run["id"])
        current_review = (
            int(state["review_revision"])
            if state is not None
            else review_service.INITIAL_REVIEW_REVISION
        )
        if int(supplied["expected_review_revision"]) != int(current_review):
            raise contracts.ContractError(
                412,
                "STALE_REVISION",
                "The probability review changed. Reload and reconcile your edits.",
                {"expected_review_revision": ["Stale review revision."]},
            )
        # Freshness INSIDE the locked transaction (S48d per-question hash,
        # stored source_revision so bumps alone never stale; S51 binds relevant
        # demographics via patient-aware hash, phone/name excluded).
        full = snapshots_service.compute_question_freshness(run, batch, draft_data)
        if bool(full.get("stale", True)):
            raise contracts.ContractError(
                409,
                "STALE_INPUTS",
                "Patient inputs changed. Regenerate before signing.",
                {"question_run_id": ["Stale patient inputs."]},
            )
        current_fp = str(full.get("current_fingerprint", ""))
        # S51: bind relevant demographics inside the tx (stored vs current).
        try:
            _pinned = batch.get("pinned_bundle") or {}
            _stored_patient = (
                _pinned.get("patient_relevant_hash") if isinstance(_pinned, dict) else None
            )
            if isinstance(_stored_patient, str) and _stored_patient:
                _current_patient = patients_service.relevant_demographics_hash(patient)
                if _current_patient != str(_stored_patient):
                    raise contracts.ContractError(
                        409,
                        "STALE_INPUTS",
                        "Patient inputs changed. Regenerate before signing.",
                        {"question_run_id": ["Stale patient inputs."]},
                    )
                current_fp = snapshots_service.patient_aware_input_hash(
                    current_fp, _current_patient
                )
        except contracts.ContractError:
            raise
        except Exception:
            pass
        baseline = coordinator_module.get_baseline(session, run["id"])
        live = review_service._live_acceptance_point(session, run=run, baseline=baseline)
        if not live.get("ok"):
            raise contracts.ContractError(
                409,
                str(live.get("code", "NO_SUCCESSFUL_RESULT")),
                str(live.get("message", "No successful result exists.")),
                {"question_run_id": [str(live.get("message", "No success."))]},
            )
        acc_row = (
            session.execute(
                select(review_service.review_tables.probability_acceptances).where(
                    review_service.review_tables.probability_acceptances.c.id
                    == supplied["acceptance_id"]
                )
            )
            .mappings()
            .first()
        )
        if acc_row is None:
            raise contracts.ContractError(
                409,
                "REFERENCE_MISMATCH",
                "Acceptance does not exist.",
                {"acceptance_id": ["Unknown acceptance."]},
            )
        acc = dict(acc_row)
        if str(acc.get("question_run_id")) != str(run["id"]):
            raise contracts.ContractError(
                409,
                "REFERENCE_MISMATCH",
                "Acceptance does not match this question run.",
                {"acceptance_id": ["Run mismatch."]},
            )
        if str(acc.get("batch_id")) != str(batch["id"]):
            raise contracts.ContractError(
                409,
                "REFERENCE_MISMATCH",
                "Acceptance does not match this batch.",
                {"acceptance_id": ["Batch mismatch."]},
            )
        if str(acc.get("encounter_id")) != str(encounter_id):
            raise contracts.ContractError(
                409,
                "REFERENCE_MISMATCH",
                "Acceptance does not match this encounter.",
                {"acceptance_id": ["Encounter mismatch."]},
            )
        if str(acc.get("baseline_id")) != str(live.get("baseline_id")):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance baseline is outdated."
            )
        want_rev = acc.get("cpt_revision_id")
        live_rev = live.get("cpt_revision_id")
        if (str(want_rev) if want_rev is not None else None) != live_rev:
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance revision is outdated."
            )
        if str(acc.get("cpt_hash")) != str(live.get("cpt_hash")):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance CPT hash is outdated."
            )
        if str(acc.get("result_id")) != str(live.get("result_id")):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance result is outdated."
            )
        if str(acc.get("result_kind")) != str(live.get("result_kind")):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance result kind is outdated."
            )
        if str(acc.get("input_hash")) != str(current_fp):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance inputs are outdated."
            )
        if str(acc.get("projection_hash")) != str(run.get("projection_hash", "")):
            raise contracts.ContractError(
                409, "REFERENCE_MISMATCH", "Acceptance projection is outdated."
            )
        acceptance_rows[str(run["id"])] = acc

    # Build the frozen snapshot (persisted data only, no secrets).
    moment = contracts.utcnow()
    notes_rows = (
        session.execute(
            select(cases_tables.notes)
            .where(cases_tables.notes.c.encounter_id == encounter_id)
            .order_by(cases_tables.notes.c.created_at, cases_tables.notes.c.id)
        )
        .mappings()
        .all()
    )
    frozen_notes = [
        {
            "id": str(dict(r).get("id")),
            "page": str(dict(r).get("page")),
            "author_id": str(dict(r).get("author_id")),
            "author_display": str(dict(r).get("author_display", "")),
            "created_at": contracts.serialize_utc(dict(r)["created_at"]),
            "text": str(dict(r).get("text", "")),
        }
        for r in notes_rows
    ]
    frozen_per_question: list[dict[str, Any]] = []
    for run in runs:
        key = str(run.get("question_key", ""))
        baseline = coordinator_module.get_baseline(session, run["id"])
        safe_base = coordinator_module.safe_baseline(baseline) if baseline is not None else None
        latest = review_service.get_latest_revision(session, run["id"])
        current_tables = review_service.current_tables_for_run(baseline, latest)
        state = review_service.get_review_state(session, run["id"])
        review_rev = (
            int(state["review_revision"])
            if state is not None
            else review_service.INITIAL_REVIEW_REVISION
        )
        if str(run.get("status")) != "ready":
            frozen_per_question.append(
                {
                    "question_key": key,
                    "question_run_id": str(run["id"]),
                    "status": str(run.get("status")),
                    "gate_reason": str(run.get("gate_reason", "")),
                    "projection": dict(run.get("projection") or {}),
                    "projection_hash": str(run.get("projection_hash", "")),
                    "pinned_versions": dict(run.get("pinned_versions") or {}),
                    "original_baseline": safe_base,
                    "current_tables": current_tables,
                    "current_cpt_revision_id": str(latest["id"]) if latest is not None else None,
                    "cpt_hash": str(latest["cpt_hash"]) if latest is not None else None,
                    "current_result": None,
                    "result_kind": None,
                    "result_id": None,
                    "acceptance": None,
                    "review_revision": int(review_rev),
                    "calculation_state": str(run.get("status")),
                }
            )
            continue
        acc = acceptance_rows[str(run["id"])]
        result_kind = str(acc.get("result_kind", ""))
        result_id = str(acc.get("result_id"))
        current_result: dict[str, Any] | None = None
        if result_kind == review_service.RESULT_KIND_CALCULATION:
            calc = review_service.get_calculation_result_for_revision(
                session, acc.get("cpt_revision_id")
            )
            # Fallback: direct id lookup for reset-reuse rows.
            if calc is None:
                from sqlalchemy import select as _select

                cand = (
                    session.execute(
                        _select(review_service.review_tables.calculation_results).where(
                            review_service.review_tables.calculation_results.c.id
                            == acc.get("result_id")
                        )
                    )
                    .mappings()
                    .first()
                )
                calc = dict(cand) if cand is not None else None
            current_result = (
                review_service.safe_calculation_result(calc) if calc is not None else None
            )
        # Unchanged originals: the baseline is the current result.
        calc_state = (
            "unchanged" if acc.get("cpt_revision_id") is None else "successfully_recalculated"
        )
        frozen_per_question.append(
            {
                "question_key": key,
                "question_run_id": str(run["id"]),
                "status": str(run.get("status")),
                "gate_reason": str(run.get("gate_reason", "")),
                "projection": dict(run.get("projection") or {}),
                "projection_hash": str(run.get("projection_hash", "")),
                "pinned_versions": dict(run.get("pinned_versions") or {}),
                "original_baseline": safe_base,
                "current_tables": current_tables,
                "current_cpt_revision_id": str(acc.get("cpt_revision_id"))
                if acc.get("cpt_revision_id") is not None
                else None,
                "cpt_hash": str(acc.get("cpt_hash", "")),
                "current_result": current_result,
                "result_kind": result_kind,
                "result_id": result_id,
                "acceptance": review_service.safe_acceptance(acc, question_key=key),
                "review_revision": int(review_rev),
                "calculation_state": calc_state,
            }
        )

    from x_insight.reasoning import snapshots as snapshots_service

    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "encounter": encounters_service.safe_encounter_reference(encounter),
        "patient": patients_service.safe_patient(patient),
        "batch": snapshots_service.safe_batch(batch),
        "proposal": coordinator_module.safe_proposal(proposal),
        "secondary_plan": {
            "revision": int(current_plan_revision),
            "text": str(current_plan_text),
        },
        "questions": frozen_per_question,
        "notes": frozen_notes,
        "draft_data": dict(draft_data) if isinstance(draft_data, dict) else {},
        "fingerprint_payload": dict(batch.get("fingerprint_payload") or {}),
        "pinned_bundle": dict(batch.get("pinned_bundle") or {}),
        "signer": {
            "signer_id": str(author.get("id")),
            "signer_username": str(author.get("username", "")),
            "signed_at": contracts.serialize_utc(moment),
        },
        "encounter_revision": int(expected_encounter_revision),
    }
    snapshot_hash = contracts.canonical_hash(snapshot)
    snapshot_id = uuid.uuid4()
    try:
        session.execute(
            insert(cases_tables.signed_encounter_snapshots).values(
                id=snapshot_id,
                encounter_id=encounter_id,
                patient_id=encounter["patient_id"],
                batch_id=want_batch,
                proposal_id=proposal["id"],
                secondary_plan_revision=int(current_plan_revision),
                secondary_plan_text=str(current_plan_text),
                snapshot=dict(snapshot),
                snapshot_hash=str(snapshot_hash),
                signer_id=author["id"],
                signer_username=str(author.get("username", "")),
                signed_at=moment,
                encounter_revision=int(expected_encounter_revision),
                created_at=moment,
            )
        )
        session.execute(
            update(cases_tables.encounters)
            .where(cases_tables.encounters.c.id == encounter_id)
            .values(
                lifecycle="signed",
                revision=int(expected_encounter_revision) + 1,
                updated_at=moment,
            )
        )
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise contracts.ContractError(
            409, "ALREADY_SIGNED", "Encounter is already signed."
        ) from exc
    audit_module.record_audit(
        session,
        operation=SIGN_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "patient_id": str(encounter.get("patient_id")),
            "batch_id": str(want_batch),
            "proposal_id": str(proposal.get("id")),
            "snapshot_id": str(snapshot_id),
            "snapshot_hash": str(snapshot_hash),
            "secondary_plan_revision": int(current_plan_revision),
            "question_keys": [str(r.get("question_key", "")) for r in runs],
            "acceptance_ids": [str(acceptance_rows[k].get("id")) for k in sorted(acceptance_rows)],
            "encounter_revision": int(expected_encounter_revision) + 1,
        },
    )
    stored = get_snapshot(session, encounter_id)
    assert stored is not None
    updated = encounters_service.get_encounter(session, encounter_id)
    assert updated is not None
    return stored, updated, contracts.serialize_utc(moment)


def create_addendum(
    session: Session,
    *,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_encounter_revision: Any,
    text: Any,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Any-active-physician append to a signed encounter (no snapshot mutation).

    Row-locked read of the signed encounter (404 missing/draft/discarded?
    draft/discarded are 409 ENCOUNTER_NOT_SIGNED so the signed-only rule is
    explicit). Revision fence is 412; text is 422. Server derives
    author/time; the encounter row is never bumped (snapshot stays byte-
    identical). Returns (addendum, encounter, timestamp).
    """
    if not isinstance(expected_encounter_revision, int) or expected_encounter_revision < 1:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "expected_encounter_revision must be a positive integer.",
            {"expected_encounter_revision": ["Must be a positive integer."]},
        )
    validated = validate_addendum_text(text)
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
    if str(encounter.get("lifecycle")) != "signed":
        raise contracts.ContractError(
            409, "ENCOUNTER_NOT_SIGNED", "Addenda apply to signed encounters only."
        )
    # S51 §1 provisional: archived blocks addenda (409, no leak).

    _patient_row = (
        session.execute(
            select(cases_tables.patients)
            .where(cases_tables.patients.c.id == encounter.get("patient_id"))
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if _patient_row is not None and bool(dict(_patient_row).get("archived", False)):
        raise contracts.ContractError(
            409,
            "PATIENT_ARCHIVED",
            "Patient is archived and read-only.",
            {"patient_id": ["Patient is archived."]},
        )
    if int(encounter["revision"]) != int(expected_encounter_revision):
        raise contracts.ContractError(
            412,
            "STALE_REVISION",
            "The signed encounter changed. Reload before appending.",
        )
    moment = contracts.utcnow()
    addendum_id = uuid.uuid4()
    session.execute(
        insert(cases_tables.encounter_addenda).values(
            id=addendum_id,
            encounter_id=encounter_id,
            author_id=author["id"],
            author_display=str(author.get("username", "")),
            created_at=moment,
            text=validated,
        )
    )
    session.flush()
    stored_row = (
        session.execute(
            select(cases_tables.encounter_addenda).where(
                cases_tables.encounter_addenda.c.id == addendum_id
            )
        )
        .mappings()
        .first()
    )
    assert stored_row is not None
    stored = dict(stored_row)
    audit_module.record_audit(
        session,
        operation=ADDENDUM_AUDIT_OPERATION,
        actor=str(author.get("username")),
        request_id=request_id,
        details={
            "encounter_id": str(encounter_id),
            "addendum_id": str(addendum_id),
        },
    )
    return stored, encounter, contracts.serialize_utc(moment)
