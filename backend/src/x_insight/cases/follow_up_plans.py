"""Follow-up proposal input identity and content verification.

Proposal persistence and signing are deliberately not exposed until their complete
transaction and workflow-guard contracts are implemented.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.released import get_released_definition
from x_insight.cases import follow_up, follow_up_content


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
        expected = {
            "workflow_manifest_hash": content["hash"],
            "lexicomp_version": manifest["lexicomp_version"],
            "lexicomp_content_hash": manifest["lexicomp_content_hash"],
            "assessment_versions": manifest["assessment_versions"],
            "model_sha256": manifest["files"]["new-case/v1/oral-antipsychotic.xml"],
            "lab_content_hash": manifest["files"]["new-case/v1/labs.json"],
            "formulation_content_hash": manifest["files"][
                "new-case/v1/antipsychotic-formulations.json"
            ],
        }
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
            "availability": {
                "revision": user["revision"],
                "selected_ids": sorted(user["unavailable_antipsychotics"]),
            },
        }
        return state, {**content, **identity}, contracts.canonical_hash(fingerprint)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise contracts.ContractError(
            409, "FOLLOW_UP_CONTENT_UNAVAILABLE", "The pinned follow-up content is unavailable."
        ) from exc
