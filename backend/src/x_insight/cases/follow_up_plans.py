"""Immutable follow-up proposals, freshness and atomic v5 confirmation."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.released import get_released_definition
from x_insight.cases import encounters, follow_up, follow_up_content, patients, signing, tables
from x_insight.cases.follow_up_candidates import calculate_candidates
from x_insight.identity import tables as identity_tables
from x_insight.operations.audit import record_audit

SUPPORTED_STATUSES = frozenset(
    {
        "available",
        "non_model",
        "direct_available",
        "no_eligible_oral_candidate",
        "unsupported_direct_mapping",
        "direct_excluded",
    }
)
SUPPORTED_TERMINALS = frozenset(
    {
        "physician_review",
        "oral_replacement",
        "continue_same",
        "reduce_dose",
        "adjunct_only",
        "clozapine",
        "same_drug_lai",
    }
)


def verified_inputs(
    session: Session, draft: dict[str, Any], patient: dict[str, Any], user: dict[str, Any]
) -> tuple[follow_up.FollowUpState, dict[str, Any], str]:
    """Resolve exact installed content and hash the saved clinical inputs.

    Unlike the autosave cache, proposal generation and signing must verify the
    asset bytes on every operation. The draft's original manifest is authoritative.
    """
    try:
        content = follow_up_content.load_content()
        pins = draft["follow_up"]["content_pins"]
        manifest = content["manifest"]
        expected = follow_up.content_pins(content)
        versions = {name: get_released_definition(name)["version"] for name in ("panss", "cssrs")}
        if pins != expected or versions != pins["assessment_versions"]:
            raise ValueError("Content or assessment identity does not match the draft")
        model = (
            session.execute(
                text(
                    "SELECT id, source_hash, source_bytes, review FROM model_network_versions "
                    "WHERE source_hash=:hash"
                ),
                {"hash": pins["model_sha256"]},
            )
            .mappings()
            .all()
        )
        model = [
            row
            for row in model
            if row["review"].get("execution_profile") == "fully-observed-oral-row-lookup/1"
            and row["review"].get("manifest_hash")
            == contracts.canonical_hash(content["assets"]["new-case/v1/oral-model-manifest.json"])
        ]
        release = (
            session.execute(
                text(
                    "SELECT id, content_hash, terminology_checksum, parser_version FROM "
                    "ddi_dataset_releases WHERE version=:version"
                ),
                {"version": pins["lexicomp_version"]},
            )
            .mappings()
            .first()
        )
        if (
            len(model) != 1
            or hashlib.sha256(bytes(model[0]["source_bytes"])).hexdigest() != pins["model_sha256"]
            or release is None
            or release["content_hash"] != pins["lexicomp_content_hash"]
            or release["terminology_checksum"]
            != manifest["files"]["ddi/lexicomp/v1/Lexicomp-cleaned.csv"]
            or release["parser_version"] != manifest["lexicomp_parser_version"]
        ):
            raise ValueError("Pinned model or interaction release unavailable")
        medication = draft["medications"]
        if medication["selection_dataset_version"] not in (None, pins["lexicomp_version"]):
            raise ValueError("Medication release differs from draft")
        ids = {entry["catalog_drug_id"] for entry in medication["entries"]}
        if ids:
            found = set(
                session.execute(
                    text(
                        "SELECT catalog_drug_id FROM ddi_concepts WHERE release_id=:release "
                        "AND catalog_drug_id=ANY(:ids)"
                    ),
                    {"release": release["id"], "ids": list(ids)},
                ).scalars()
            )
            if found != ids:
                raise ValueError("Medication is absent from the pinned release")
        state = follow_up.evaluate_state(draft, patient)
        identity = {"model_version_id": str(model[0]["id"]), "ddi_release_id": str(release["id"])}
        fingerprint = {
            "raw_inputs": state["raw"],
            "demographics": {
                key: patient.get(key) for key in ("birth_year", "age", "sex", "clinical_status")
            },
            "effective_inputs": {
                key: state[key]
                for key in (
                    "age",
                    "flags",
                    "calculated_bmi",
                    "bmi_category",
                    "model_factors",
                    "decision",
                    "sections",
                )
            },
            "content_pins": pins,
            "installed_content": identity,
            "assessment_definitions": {
                name: get_released_definition(name) for name in ("panss", "cssrs")
            },
            "availability": {
                "revision": user["revision"],
                "selected_ids": sorted(user["unavailable_antipsychotics"]),
            },
        }
        return (
            state,
            {**content, **identity, "fingerprint": fingerprint},
            contracts.canonical_hash(fingerprint),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise contracts.ContractError(
            409, "FOLLOW_UP_CONTENT_UNAVAILABLE", "The pinned follow-up content is unavailable."
        ) from exc


def context(
    session: Session, author: dict[str, Any], encounter_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    initial = encounters.get_encounter(session, encounter_id)
    if initial is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounters.require_author(initial, author)
    # Deactivation holds the account lock before discarding its drafts.
    # Take it first too, then share creation's patient → encounter order.
    user = dict(
        session.execute(
            select(identity_tables.users)
            .where(identity_tables.users.c.id == author["id"])
            .with_for_update()
        )
        .mappings()
        .one()
    )
    if not user["active"] or user["role"] != "physician":
        raise contracts.ContractError(403, "FORBIDDEN", "Active physician required.")
    patient = dict(
        session.execute(
            select(tables.patients)
            .where(tables.patients.c.id == initial["patient_id"])
            .with_for_update()
        )
        .mappings()
        .one()
    )
    encounter = dict(
        session.execute(
            select(tables.encounters)
            .where(tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )
    encounters.require_author(encounter, author)
    if encounter["workflow_version"] != follow_up.WORKFLOW:
        raise contracts.ContractError(409, "WORKFLOW_MISMATCH", "Follow-up workflow required.")
    if patient["archived"]:
        raise contracts.ContractError(409, "PATIENT_ARCHIVED", "Patient is archived.")
    return encounter, patient, user


def safe_proposal(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "encounter_id": str(row["encounter_id"]),
        "source_encounter_revision": row["source_encounter_revision"],
        "input_hash": row["input_hash"],
        "content_hash": row["content_hash"],
        "payload": row["payload"],
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def require_complete(state: follow_up.FollowUpState) -> None:
    if not state["can_complete"]:
        raise contracts.ContractError(
            422,
            "FOLLOW_UP_INCOMPLETE",
            "Complete the required inputs.",
            {
                e["field"]: [e["message_key"]]
                for errors in state["errors_by_step"].values()
                for e in errors
            },
        )


def generate_proposal(
    session: Session,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    request_id: str,
) -> dict[str, Any]:
    encounter, patient, user = context(session, author, encounter_id)
    encounters.require_draft_lifecycle(encounter)
    if encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    state, content, digest = verified_inputs(session, encounter["draft_data"], patient, user)
    require_complete(state)
    existing = (
        session.execute(
            select(tables.follow_up_proposals).where(
                tables.follow_up_proposals.c.encounter_id == encounter_id,
                tables.follow_up_proposals.c.input_hash == digest,
                tables.follow_up_proposals.c.content_hash == content["hash"],
            )
        )
        .mappings()
        .first()
    )
    if existing:
        return safe_proposal(existing)
    pins = state["raw"]["follow_up"]["content_pins"]
    model_run = state["decision"]["terminal"] == "oral_replacement"
    model = (
        session.execute(
            text("SELECT source_bytes FROM model_network_versions WHERE id=:id"),
            {"id": uuid.UUID(content["model_version_id"])},
        ).scalar_one()
        if model_run
        else None
    )
    try:
        ranking = calculate_candidates(
            session.get_bind(),
            evaluated=dict(state),
            decision=state["decision"],
            sex=patient["sex"],
            model_bytes=bytes(model) if model is not None else None,
            model_sha256=pins["model_sha256"],
            formulations=content["assets"]["new-case/v1/antipsychotic-formulations.json"][
                "entries"
            ],
            unavailable=frozenset(user["unavailable_antipsychotics"]),
            dataset_version=pins["lexicomp_version"],
        )
    except (ValueError, KeyError) as exc:
        raise contracts.ContractError(
            409,
            "FOLLOW_UP_CONTENT_UNAVAILABLE",
            "The pinned model or interaction lookup cannot be used.",
        ) from exc
    moment = contracts.utcnow()
    payload = {
        "schema_version": "follow-up-proposal-v1",
        "workflow_version": follow_up.WORKFLOW,
        "input_hash": digest,
        "fingerprint_payload": content["fingerprint"],
        "versions": pins,
        "raw_inputs": state["raw"],
        "effective_inputs": content["fingerprint"]["effective_inputs"],
        "demographics_at_encounter": {
            "age": state["age"],
            "sex": patient["sex"],
            "birth_year": patient["birth_year"],
            "visit_year": state["visit_year"],
        },
        "baseline": state["baseline"],
        "flags": state["flags"],
        "lab_warnings": state["lab_warnings"],
        "model_factors": state["model_factors"],
        "model_not_run_reason": None if model_run else state["decision"]["terminal"],
        "ranking": ranking,
        "sections": ranking["sections"],
        "availability_snapshot": content["fingerprint"]["availability"],
        "content_snapshot": {"manifest": content["manifest"], "assets": content["assets"]},
        "created_at": contracts.serialize_utc(moment),
        "author_id": str(author["id"]),
    }
    row = (
        session.execute(
            insert(tables.follow_up_proposals)
            .values(
                id=uuid.uuid4(),
                encounter_id=encounter_id,
                author_id=author["id"],
                source_encounter_revision=expected_revision,
                input_hash=digest,
                content_hash=content["hash"],
                model_version_id=uuid.UUID(content["model_version_id"]) if model_run else None,
                ddi_release_id=uuid.UUID(content["ddi_release_id"]),
                payload=payload,
                created_at=moment,
            )
            .returning(tables.follow_up_proposals)
        )
        .mappings()
        .one()
    )
    record_audit(
        session,
        operation="follow_up.proposal.success",
        actor=user["username"],
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "proposal_id": str(row["id"])},
    )
    return safe_proposal(row)


def read_proposal(
    session: Session,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    proposal_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    encounter = encounters.get_encounter(session, encounter_id)
    if encounter is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounters.require_author(encounter, author)
    if encounter["workflow_version"] != follow_up.WORKFLOW:
        raise contracts.ContractError(409, "WORKFLOW_MISMATCH", "Follow-up workflow required.")
    digest = None
    if encounter["lifecycle"] == "draft":
        patient = patients.get_patient(session, encounter["patient_id"])
        user = dict(
            session.execute(
                select(identity_tables.users).where(identity_tables.users.c.id == author["id"])
            )
            .mappings()
            .one()
        )
        assert patient is not None
        _, _, digest = verified_inputs(session, encounter["draft_data"], patient, user)
    query = select(tables.follow_up_proposals).where(
        tables.follow_up_proposals.c.encounter_id == encounter_id
    )
    if proposal_id:
        query = query.where(tables.follow_up_proposals.c.id == proposal_id)
    if digest:
        query = query.order_by((tables.follow_up_proposals.c.input_hash == digest).desc())
    row = (
        session.execute(
            query.order_by(
                tables.follow_up_proposals.c.created_at.desc(),
                tables.follow_up_proposals.c.id.desc(),
            ).limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        if proposal_id:
            raise contracts.ContractError(404, "NOT_FOUND", "Proposal not found in this encounter.")
        return None
    return {
        **safe_proposal(row),
        "stale": digest is not None and row["input_hash"] != digest,
        "signed_patient_id": str(encounter["patient_id"])
        if encounter["lifecycle"] == "signed"
        else None,
    }


def sign(
    session: Session,
    author: dict[str, Any],
    encounter_id: uuid.UUID,
    expected_revision: int,
    expected_plan_revision: int,
    proposal_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    encounter, patient, user = context(session, author, encounter_id)
    if encounter["lifecycle"] == "signed":
        existing = signing.get_snapshot(session, encounter_id)
        if (
            existing
            and existing.get("follow_up_proposal_id") == proposal_id
            and existing["secondary_plan_revision"] == expected_plan_revision
            and existing["encounter_revision"] == expected_revision
        ):
            return signing.safe_snapshot(existing)
        raise contracts.ContractError(409, "ALREADY_SIGNED", "Encounter is already signed.")
    encounters.require_draft_lifecycle(encounter)
    if encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    state, content, digest = verified_inputs(session, encounter["draft_data"], patient, user)
    require_complete(state)
    proposal = (
        session.execute(
            select(tables.follow_up_proposals).where(
                tables.follow_up_proposals.c.id == proposal_id,
                tables.follow_up_proposals.c.encounter_id == encounter_id,
                tables.follow_up_proposals.c.author_id == author["id"],
            )
        )
        .mappings()
        .first()
    )
    if (
        proposal is None
        or proposal["input_hash"] != digest
        or proposal["content_hash"] != content["hash"]
        or proposal["payload"].get("schema_version") != "follow-up-proposal-v1"
        or proposal["payload"].get("raw_inputs") != state["raw"]
        or contracts.canonical_hash(proposal["payload"].get("fingerprint_payload")) != digest
        or proposal["payload"].get("ranking", {}).get("status") not in SUPPORTED_STATUSES
        or proposal["payload"].get("ranking", {}).get("final_decision", {}).get("terminal")
        not in SUPPORTED_TERMINALS
    ):
        raise contracts.ContractError(
            409, "STALE_INPUTS", "Regenerate the proposal before confirmation."
        )
    plan = (
        session.execute(
            select(tables.secondary_plans)
            .where(tables.secondary_plans.c.encounter_id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if plan is None or plan["revision"] != expected_plan_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the final plan.")
    if not plan["text"].strip():
        raise contracts.ContractError(409, "PLAN_NOT_READY", "Save a nonblank final plan.")
    moment = contracts.utcnow()
    payload = {
        "schema_version": "signed-encounter-v5",
        "workflow_version": follow_up.WORKFLOW,
        "encounter": encounters.safe_encounter_reference(encounter),
        "patient": patients.safe_patient(patient),
        "proposal": safe_proposal(proposal),
        "draft_data": encounter["draft_data"],
        "secondary_plan": {"revision": plan["revision"], "text": plan["text"]},
        "signer": {
            "signer_id": str(user["id"]),
            "signer_username": user["username"],
            "name_fa": user["name_fa"],
            "name_en": user["name_en"],
            "signed_at": contracts.serialize_utc(moment),
        },
        "encounter_revision": expected_revision,
    }
    row = (
        session.execute(
            insert(tables.signed_encounter_snapshots)
            .values(
                id=uuid.uuid4(),
                encounter_id=encounter_id,
                patient_id=encounter["patient_id"],
                batch_id=None,
                proposal_id=None,
                new_case_proposal_id=None,
                follow_up_proposal_id=proposal_id,
                secondary_plan_revision=plan["revision"],
                secondary_plan_text=plan["text"],
                snapshot=payload,
                snapshot_hash=contracts.canonical_hash(payload),
                signer_id=user["id"],
                signer_username=user["username"],
                signed_at=moment,
                encounter_revision=expected_revision,
                created_at=moment,
            )
            .returning(tables.signed_encounter_snapshots)
        )
        .mappings()
        .one()
    )
    session.execute(
        update(tables.encounters)
        .where(tables.encounters.c.id == encounter_id)
        .values(lifecycle="signed", revision=expected_revision + 1, updated_at=moment)
    )
    record_audit(
        session,
        operation="follow_up.sign.success",
        actor=user["username"],
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "snapshot_id": str(row["id"])},
    )
    return signing.safe_snapshot(dict(row))
