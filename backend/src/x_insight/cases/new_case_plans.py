"""Deterministic new-case proposals, freshness and atomic v4 confirmation.

All writes belong to the caller's transaction. No provider, worker or network IO.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.cases import encounters, new_case, patients, signing, tables
from x_insight.cases.new_case_candidates import calculate_candidates
from x_insight.identity import tables as identity_tables
from x_insight.operations.audit import record_audit


def context(
    session: Session, author: dict[str, Any], encounter_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    row = (
        session.execute(
            select(tables.encounters)
            .where(tables.encounters.c.id == encounter_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Encounter not found.")
    encounter = dict(row)
    encounters.require_author(encounter, author)
    if encounter["workflow_version"] != new_case.WORKFLOW:
        raise contracts.ContractError(409, "WORKFLOW_MISMATCH", "New-case workflow required.")
    patient = dict(
        session.execute(
            select(tables.patients)
            .where(tables.patients.c.id == encounter["patient_id"])
            .with_for_update()
        )
        .mappings()
        .one()
    )
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
    if patient["archived"]:
        raise contracts.ContractError(409, "PATIENT_ARCHIVED", "Patient is archived.")
    return encounter, patient, user


def inputs(
    session: Session, encounter: dict[str, Any], patient: dict[str, Any], user: dict[str, Any]
) -> tuple[new_case.NewCaseState, dict[str, Any], str, dict[str, Any]]:
    content = new_case.clinical_content()
    new_case.validate_pins(session, encounter["draft_data"], content)
    state = new_case.evaluate_new_case(patient, encounter, encounter["draft_data"], content)
    demographic = {
        "birth_year": patient["birth_year"],
        "age": state["age"],
        "sex": patient["sex"],
        "clinical_status": patient["clinical_status"],
        "registration_year": encounter["draft_data"]["new_case"]["registration_year"],
    }
    fingerprint = {
        "demographics": demographic,
        "draft": state["raw"],
        "effective_inputs": state["effective_inputs"],
        "evidence": state["evidence"],
        "availability": {
            "revision": user["revision"],
            "selected_ids": sorted(user["unavailable_antipsychotics"]),
        },
        "content_hash": content["hash"],
    }
    return state, content, contracts.canonical_hash(fingerprint), fingerprint


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


def require_complete(state: new_case.NewCaseState) -> None:
    if not state["can_complete"]:
        raise contracts.ContractError(
            422,
            "NEW_CASE_INCOMPLETE",
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
    state, content, digest, fingerprint = inputs(session, encounter, patient, user)
    require_complete(state)
    existing = (
        session.execute(
            select(tables.new_case_proposals).where(
                tables.new_case_proposals.c.encounter_id == encounter_id,
                tables.new_case_proposals.c.input_hash == digest,
                tables.new_case_proposals.c.content_hash == content["hash"],
            )
        )
        .mappings()
        .first()
    )
    if existing:
        return safe_proposal(existing)
    raw = state["raw"]
    pins = raw["new_case"]["content_pins"]
    lai = raw["new_case"]["considerations"]["lai"]
    model = None
    if not lai:
        model = session.execute(
            text("SELECT source_bytes FROM model_network_versions WHERE id=:id"),
            {"id": uuid.UUID(pins["model_version_id"])},
        ).scalar_one()
    try:
        ranking = calculate_candidates(
            session.get_bind(),
            lai=lai,
            evidence=state["evidence"],
            model_bytes=bytes(model) if model is not None else None,
            model_sha256=pins["model_sha256"],
            formulations=content["antipsychotic-formulations.json"]["entries"],
            unavailable=frozenset(user["unavailable_antipsychotics"]),
            medications=[e["catalog_drug_id"] for e in raw["medications"]["entries"]],
            dataset_version=pins["lexicomp_version"],
        )
    except (ValueError, KeyError) as exc:
        raise contracts.ContractError(
            409,
            "NEW_CASE_MODEL_UNAVAILABLE",
            "The pinned oral model cannot be used. Retry after content repair.",
        ) from exc
    moment = contracts.utcnow()
    payload = {
        "schema_version": "new-case-proposal-v1",
        "workflow_version": new_case.WORKFLOW,
        "input_hash": digest,
        "fingerprint_payload": fingerprint,
        "versions": pins,
        "demographics_at_encounter": {
            "age": state["age"],
            "sex": patient["sex"],
            "birth_year": patient["birth_year"],
            "registration_year": raw["new_case"]["registration_year"],
        },
        "raw_inputs": raw,
        "effective_inputs": state["effective_inputs"],
        "flags": state["flags"],
        "lab_warnings": state["lab_warnings"],
        "bn_evidence": state["evidence"],
        "evidence_sources": state["evidence_sources"],
        "ranking": ranking,
        "sections": {
            **state["sections"],
            "drug_interactions": {
                "status": "available" if ranking["interactions"] is not None else "no_candidates"
            },
            "antipsychotic_candidates": {
                "status": ranking["status"],
                "candidates": ranking["candidates"],
            },
        },
        "candidate_interactions": ranking["interactions"],
        "availability_snapshot": {
            "revision": user["revision"],
            "selected_ids": sorted(user["unavailable_antipsychotics"]),
        },
        "content_snapshot": {
            **{k: v for k, v in content.items() if k != "hash"},
            "factor_mapping": state["evidence_sources"],
            "recommendation_rules": {
                "version": content["manifest"]["recommendation_rule_version"],
                "section_ids": list(state["sections"]),
            },
        },
        "created_at": contracts.serialize_utc(moment),
        "author_id": str(author["id"]),
    }
    row = (
        session.execute(
            insert(tables.new_case_proposals)
            .values(
                id=uuid.uuid4(),
                encounter_id=encounter_id,
                author_id=author["id"],
                source_encounter_revision=expected_revision,
                input_hash=digest,
                content_hash=content["hash"],
                model_version_id=None if lai else uuid.UUID(pins["model_version_id"]),
                ddi_release_id=uuid.UUID(pins["lexicomp_release_id"]),
                payload=payload,
                created_at=moment,
            )
            .returning(tables.new_case_proposals)
        )
        .mappings()
        .one()
    )
    record_audit(
        session,
        operation="new_case.proposal.success",
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
    if encounter["workflow_version"] != new_case.WORKFLOW:
        raise contracts.ContractError(409, "WORKFLOW_MISMATCH", "New-case workflow required.")
    query = select(tables.new_case_proposals).where(
        tables.new_case_proposals.c.encounter_id == encounter_id
    )
    if proposal_id:
        query = query.where(tables.new_case_proposals.c.id == proposal_id)
    row = (
        session.execute(query.order_by(tables.new_case_proposals.c.created_at.desc()).limit(1))
        .mappings()
        .first()
    )
    if row is None:
        if proposal_id:
            raise contracts.ContractError(404, "NOT_FOUND", "Proposal not found in this encounter.")
        return None
    result = safe_proposal(row)
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
        _, content, digest, _ = inputs(session, encounter, patient, user)
        result["stale"] = row["input_hash"] != digest or row["content_hash"] != content["hash"]
    else:
        result["stale"] = False
    return result


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
            and existing.get("new_case_proposal_id") == proposal_id
            and existing["secondary_plan_revision"] == expected_plan_revision
            and existing["encounter_revision"] == expected_revision
        ):
            return signing.safe_snapshot(existing)
        raise contracts.ContractError(409, "ALREADY_SIGNED", "Encounter is already signed.")
    encounters.require_draft_lifecycle(encounter)
    if encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    state, content, digest, fingerprint = inputs(session, encounter, patient, user)
    require_complete(state)
    proposal = (
        session.execute(
            select(tables.new_case_proposals).where(
                tables.new_case_proposals.c.id == proposal_id,
                tables.new_case_proposals.c.encounter_id == encounter_id,
                tables.new_case_proposals.c.author_id == author["id"],
            )
        )
        .mappings()
        .first()
    )
    if (
        proposal is None
        or proposal["input_hash"] != digest
        or proposal["content_hash"] != content["hash"]
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
        "schema_version": "signed-encounter-v4",
        "workflow_version": new_case.WORKFLOW,
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
                new_case_proposal_id=proposal_id,
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
        operation="new_case.sign.success",
        actor=user["username"],
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "snapshot_id": str(row["id"])},
    )
    return signing.safe_snapshot(dict(row))
