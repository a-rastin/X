"""Read-only comparison of a stored proposal with one transient source scenario."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict

from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.engine import evaluate
from x_insight.assessments.released import get_released_snapshot
from x_insight.cases import (
    follow_up,
    follow_up_candidates,
    follow_up_plans,
    new_case,
    new_case_candidates,
    new_case_plans,
    reasoning_paths,
)
from x_insight.models import oral_antipsychotic

SimulationValue = bool | int | str | None
_BOOLEAN = (False, True)
_AUTOMATIC = (*_BOOLEAN, None)
_HISTORY_CONTROLS = {
    "substances.present": _BOOLEAN,
    "substances.marijuana": _BOOLEAN,
    "substances.other": _BOOLEAN,
    "bmi.source": ("calculated", "manual", None),
    "bmi.manual_category": (*new_case.BMI_CATEGORIES, None),
    "breastfeeding": _BOOLEAN,
    "psychiatric.present": _BOOLEAN,
    **{f"conditions.{name}.manual": _AUTOMATIC for name in new_case.CONDITIONS},
}
_COMMON_CONSIDERATIONS = ("basic_needs_met", "needs_observation", "seizure_risk", "qtc_risk")
_CONTROLS = {
    new_case.WORKFLOW: {
        **{f"new_case.{name}": values for name, values in _HISTORY_CONTROLS.items()},
        **{
            f"new_case.considerations.{name}": _BOOLEAN
            for name in (*_COMMON_CONSIDERATIONS, "lai", "avoid_sedation")
        },
        "new_case.considerations.orthostatic_risk.manual": _AUTOMATIC,
        "new_case.tobacco": _BOOLEAN,
        "new_case.harm.self": _BOOLEAN,
        "new_case.harm.others": _BOOLEAN,
    },
    follow_up.WORKFLOW: {
        **{f"follow_up.history.{name}": values for name, values in _HISTORY_CONTROLS.items()},
        "follow_up.history.tobacco": _AUTOMATIC,
        "follow_up.history.substances.present": _AUTOMATIC,
        "follow_up.history.substances.opioids": _BOOLEAN,
        "follow_up.history.substances.methamphetamine": _BOOLEAN,
        "follow_up.harm.self": _BOOLEAN,
        "follow_up.harm.others": _BOOLEAN,
        **{
            f"follow_up.considerations.{name}": _BOOLEAN
            for name in (*_COMMON_CONSIDERATIONS, "adherence", "lai_preference")
        },
        "follow_up.considerations.recovery": ("controlled", "partial", "none_or_worse", None),
        **{f"follow_up.branches.{name}": _AUTOMATIC for name in follow_up.Branches.model_fields},
        **{f"follow_up.effects.{name}": _BOOLEAN for name in follow_up.Effects.model_fields},
        "follow_up.effects.sedation": ("none", "tolerable", "intervention"),
    },
}
_ORAL_IDS = frozenset(f"oral:{outcome.lower()}" for outcome in oral_antipsychotic.OUTCOMES)


@dataclass(frozen=True)
class _Sources:
    workflow: str
    payload: dict[str, Any]
    content: dict[str, Any]
    definitions: dict[str, dict[str, Any]]
    assessment_hashes: dict[str, str]
    model_bytes: bytes
    model_sha256: str
    ddi_release_id: str
    formulations: list[dict[str, Any]]


class Side(TypedDict):
    state: dict[str, Any]
    ranking: dict[str, Any] | None
    assessments: dict[str, Any]
    factors: dict[str, Any]
    sections: dict[str, Any]
    status: Literal["complete", "partial", "non_model"]
    issues: list[dict[str, str]]
    active_questions: list[str]


class Exploration(TypedDict):
    patient: Side
    scenario: Side
    diff: dict[str, Any]
    active_graph: reasoning_paths.ActiveGraph
    selected_attribution: dict[str, Any] | None
    identity: dict[str, Any]


def _unavailable(reason: str) -> contracts.ContractError:
    return contracts.ContractError(409, "REASONING_BASELINE_UNAVAILABLE", reason)


def _resolve(session: Session, proposal: dict[str, Any]) -> _Sources:
    payload = proposal["payload"]
    workflow = payload["workflow_version"]
    if workflow not in _CONTROLS:
        raise _unavailable("The proposal workflow does not support reasoning exploration.")
    fingerprint = payload["fingerprint_payload"]
    new_registration = workflow == new_case.WORKFLOW
    key = "new_case" if new_registration else "follow_up"
    draft = payload["raw_inputs"]
    pins = draft[key]["content_pins"]
    if (
        pins != payload["versions"]
        or proposal["input_hash"] != payload["input_hash"]
        or contracts.canonical_hash(fingerprint) != proposal["input_hash"]
        or draft != fingerprint["draft" if new_registration else "raw_inputs"]
        or payload["availability_snapshot"] != fingerprint["availability"]
    ):
        raise _unavailable("The frozen proposal identities are inconsistent.")
    if new_registration:
        content = new_case.clinical_content()
        snapshots = {name: get_released_snapshot(name) for name in ("diagnosis", "panss", "cssrs")}
        definitions = {name: definition for name, (definition, _) in snapshots.items()}
        hashes = {name: digest for name, (_, digest) in snapshots.items()}
        new_case.validate_pins(session, draft, content, definitions=definitions)
        model = new_case_plans.load_pinned_model(session, pins["model_version_id"])
        if hashlib.sha256(model).hexdigest() != pins["model_sha256"]:
            raise _unavailable("The pinned model bytes differ from the proposal.")
        ddi_id = pins["lexicomp_release_id"]
        formulations = content["antipsychotic-formulations.json"]["entries"]
    else:
        content = follow_up_plans.resolve_pinned_content(session, draft)
        definitions = content["definitions"]
        hashes = pins["assessment_hashes"]
        model = content["model_bytes"]
        ddi_id = content["ddi_release_id"]
        formulations = content["assets"]["new-case/v1/antipsychotic-formulations.json"]["entries"]
        if (
            fingerprint["installed_content"]
            != {
                "model_version_id": content["model_version_id"],
                "ddi_release_id": ddi_id,
            }
            or fingerprint["assessment_definitions"] != definitions
        ):
            raise _unavailable("The installed follow-up identities differ from the proposal.")
    if content["hash"] != proposal["content_hash"]:
        raise _unavailable("The proposal's pinned workflow content is unavailable.")
    return _Sources(
        workflow,
        payload,
        content,
        definitions,
        hashes,
        model,
        pins["model_sha256"],
        ddi_id,
        formulations,
    )


def _apply_overrides(
    source: _Sources, overrides: Mapping[str, SimulationValue]
) -> tuple[dict[str, Any], dict[str, SimulationValue]]:
    draft = deepcopy(source.payload["raw_inputs"])
    allowed: dict[str, tuple[SimulationValue, ...]] = {**_CONTROLS[source.workflow]}
    for instrument, values in (("panss", tuple(range(1, 8))), ("cssrs", ("yes", "no", *_BOOLEAN))):
        allowed.update(
            {
                f"{instrument}.answers.{item['id']}": values
                for item in source.definitions[instrument]["items"]
            }
        )

    applied = {}
    for path, value in sorted(overrides.items()):
        if not any(
            type(value) is type(option) and value == option for option in allowed.get(path, ())
        ):
            raise contracts.ContractError(
                422,
                "VALIDATION_FAILED",
                "Unsupported reasoning override.",
                {path: ["Unsupported source or value."]},
            )
        if path.startswith("cssrs.") and type(value) is bool:
            value = "yes" if value else "no"
        *parts, field = path.split(".")
        parent = draft
        for part in parts:
            parent = parent[part]
        if value == parent.get(field):
            continue
        parent[field] = value
        if field == "manual":
            parent.update(manual_actor_id=None, manual_recorded_at=None)
        applied[path] = value
    return draft, applied


def _evaluate(
    session: Session,
    source: _Sources,
    raw: dict[str, Any],
) -> Side:
    payload = source.payload
    patient = payload["demographics_at_encounter"]
    candidate_options = {
        "model_bytes": source.model_bytes,
        "model_sha256": source.model_sha256,
        "formulations": source.formulations,
        "unavailable": frozenset(payload["availability_snapshot"]["selected_ids"]),
        "dataset_version": payload["versions"]["lexicomp_version"],
    }
    ranking: dict[str, Any] | None = None
    state: dict[str, Any]
    factors: dict[str, dict[str, Any]]
    active_questions: list[str] = []
    if source.workflow == new_case.WORKFLOW:
        registration = new_case.evaluate_frozen_inputs(
            patient, payload["author_id"], raw, source.content, definitions=source.definitions
        )
        state = dict(registration)
        lai = registration["raw"]["new_case"]["considerations"]["lai"]
        factors = {
            name: {
                "value": None
                if name == "Weight_Gain_Risk" and registration["bmi_category"] is None
                else value,
                "source": registration["evidence_sources"][name],
            }
            for name, value in registration["evidence"].items()
        }
        if lai or registration["bmi_category"] is not None:
            ranking = new_case_candidates.calculate_candidates(
                session.get_bind(),
                lai=lai,
                evidence=registration["evidence"],
                medications=[
                    entry["catalog_drug_id"]
                    for entry in registration["raw"]["medications"]["entries"]
                ],
                **candidate_options,
            )
        sections = (
            new_case_plans.proposal_sections(registration, ranking)
            if ranking is not None
            else registration["sections"]
        )
        model_expected = not lai
    else:
        follow = follow_up.evaluate_state(
            raw, patient, assets=source.content["assets"], definitions=source.definitions
        )
        state = dict(follow)
        decision = follow["decision"]
        factors = {name: dict(value) for name, value in follow["model_factors"].items()}
        active_questions = follow["reached_questions"]
        missing_branch = any(
            follow["raw"]["follow_up"]["branches"][name] is None for name in active_questions
        )
        model_expected = decision["terminal"] == "oral_replacement"
        if (
            decision["terminal"] is not None
            and not missing_branch
            and (
                not model_expected
                or all(factor["value"] is not None for factor in factors.values())
            )
        ):
            ranking = follow_up_candidates.calculate_candidates(
                session.get_bind(),
                evaluated=state,
                decision=decision,
                sex=patient["sex"],
                **candidate_options,
            )
        sections = ranking["sections"] if ranking is not None else follow["sections"]

    for factor in factors.values():
        flag = factor["source"].removeprefix("flags.").removesuffix(".effective")
        if factor["source"].startswith("flags.") and flag in state["flags"]:
            factor["provenance"] = state["flags"][flag]
    status: Literal["complete", "partial", "non_model"]
    status = "partial" if ranking is None else "complete" if model_expected else "non_model"
    return {
        "state": state,
        "ranking": ranking,
        "factors": factors,
        "sections": sections,
        "assessments": {
            name: evaluate(source.definitions[name], state["raw"][name]["answers"])
            for name in ("panss", "cssrs")
        },
        "issues": [dict(issue) for errors in state["errors_by_step"].values() for issue in errors],
        "active_questions": active_questions,
        "status": status,
    }


def _mark_simulated(side: Side, workflow: str, overrides: Mapping[str, SimulationValue]) -> None:
    for name, factor in side["factors"].items():
        paths, manual_path = reasoning_paths.factor_source_paths(
            side["state"], workflow, name, factor["source"]
        )
        simulated = sorted(overrides.keys() & set((*paths, manual_path)))
        if simulated:
            factor["simulated_override"] = simulated


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["drug_id"]: row for row in rows}


def _check_patient(source: _Sources, patient: Side) -> None:
    frozen = source.payload
    ranking = patient["ranking"]
    if ranking is None:
        raise _unavailable("The frozen proposal no longer evaluates completely.")
    original_ranking = frozen["ranking"]
    if (
        _by_id(ranking["distribution"]) != _by_id(original_ranking["distribution"])
        or ranking["candidates"] != original_ranking["candidates"]
        or any(
            ranking.get(key) != original_ranking.get(key)
            for key in ("status", "direct", "original_decision", "final_decision")
        )
        or contracts.canonical_json(patient["sections"])
        != contracts.canonical_json(frozen["sections"])
        or patient["state"]["age"] != frozen["demographics_at_encounter"]["age"]
    ):
        raise _unavailable("The recalculated result differs from the frozen proposal.")
    if source.workflow == new_case.WORKFLOW:
        valid = patient["state"]["evidence"] == frozen["bn_evidence"]
    else:
        valid = contracts.canonical_json(
            patient["state"]["model_factors"]
        ) == contracts.canonical_json(frozen["model_factors"])
    if not valid:
        raise _unavailable("The recalculated evidence differs from the frozen proposal.")


def _changes(patient: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: {"patient": patient.get(key), "scenario": scenario.get(key)}
        for key in sorted(patient.keys() | scenario.keys())
        if patient.get(key) != scenario.get(key)
    }


def _selection(side: Side) -> dict[str, Any]:
    ranking = side["ranking"] or {}
    rows = ranking.get("candidates", [])
    top = rows[0] if rows else None
    return {
        "top": top,
        "tied_eligible_ids": [
            row["drug_id"]
            for row in ranking.get("distribution", [])
            if top and row["probability"] == top["probability"] and not row["removed_reason"]
        ],
        "candidate_ids": [row["drug_id"] for row in rows],
        "original_decision": ranking.get("original_decision", side["state"].get("decision")),
        "final_decision": ranking.get("final_decision"),
        "direct": ranking.get("direct"),
        "status": side["status"],
    }


def _raw_changes(
    patient: Side, scenario: Side, overrides: Mapping[str, SimulationValue]
) -> dict[str, dict[str, SimulationValue]]:
    raw = {}
    for path in overrides:
        *parents, key = path.split(".")
        previous, current = patient["state"]["raw"], scenario["state"]["raw"]
        for parent in parents:
            previous, current = previous[parent], current[parent]
        if previous.get(key) != current.get(key):
            raw[path] = {"patient": previous.get(key), "scenario": current.get(key)}
    return raw


def _diff(
    patient: Side, scenario: Side, raw_changes: dict[str, dict[str, SimulationValue]]
) -> dict[str, Any]:
    patient_rows = _by_id((patient["ranking"] or {}).get("distribution", []))
    scenario_rows = _by_id((scenario["ranking"] or {}).get("distribution", []))
    return {
        "raw_inputs": raw_changes,
        "factors": _changes(
            {name: factor["value"] for name, factor in patient["factors"].items()},
            {name: factor["value"] for name, factor in scenario["factors"].items()},
        ),
        "assessments": _changes(patient["assessments"], scenario["assessments"]),
        "sections": _changes(patient["sections"], scenario["sections"]),
        "probabilities": _changes(
            {key: row["probability"] for key, row in patient_rows.items()},
            {key: row["probability"] for key, row in scenario_rows.items()},
        ),
        "exclusions": _changes(
            {
                key: row.get("removal_reasons")
                or ([row["removed_reason"]] if row["removed_reason"] else [])
                for key, row in patient_rows.items()
            },
            {
                key: row.get("removal_reasons")
                or ([row["removed_reason"]] if row["removed_reason"] else [])
                for key, row in scenario_rows.items()
            },
        ),
        "selection": _changes(_selection(patient), _selection(scenario)),
    }


def _target(patient: Side, scenario: Side, requested: str | None) -> str | None:
    if requested is not None:
        if requested not in _ORAL_IDS:
            raise contracts.ContractError(422, "VALIDATION_FAILED", "Unknown oral treatment.")
        return requested
    for side in (patient, scenario):
        ranking = side["ranking"] or {}
        rows = ranking.get("candidates") or ranking.get("distribution")
        if rows:
            return str(rows[0]["drug_id"])
    return None


def _attribution(source: _Sources, side: Side, treatment_id: str) -> dict[str, Any] | None:
    if not (side["ranking"] or {}).get("distribution"):
        return None
    result = oral_antipsychotic.explain_oral(
        source.model_bytes,
        source.model_sha256,
        {name: factor["value"] for name, factor in side["factors"].items()},
        treatment_id,
    )
    return asdict(result)


def explore_proposal(
    session: Session,
    proposal: dict[str, Any],
    *,
    overrides: Mapping[str, SimulationValue] | None = None,
    selected_treatment_id: str | None = None,
) -> Exploration:
    """Recompute both sides of a stored, already access-checked proposal."""
    try:
        source = _resolve(session, proposal)
        scenario_raw, applied = _apply_overrides(source, overrides or {})
        patient = _evaluate(session, source, source.payload["raw_inputs"])
        _check_patient(source, patient)
        scenario = _evaluate(session, source, scenario_raw) if applied else patient
        raw_changes = _raw_changes(patient, scenario, applied)
        canonical_overrides = {path: values["scenario"] for path, values in raw_changes.items()}
        if canonical_overrides:
            _mark_simulated(scenario, source.workflow, canonical_overrides)
        else:
            scenario = patient
        target = _target(patient, scenario, selected_treatment_id)
        attribution = None
        if target is not None:
            patient_attribution = _attribution(source, patient, target)
            scenario_attribution = (
                _attribution(source, scenario, target)
                if canonical_overrides
                else patient_attribution
            )
            if patient_attribution is not None or scenario_attribution is not None:
                attribution = {
                    "treatment_id": target,
                    "patient": patient_attribution,
                    "scenario": scenario_attribution,
                }
        graph = reasoning_paths.build_active_graph(
            workflow=source.workflow,
            patient_state=patient["state"],
            scenario_state=scenario["state"],
            patient_ranking=patient["ranking"],
            scenario_ranking=scenario["ranking"],
            selected_treatment_id=target,
            model_bytes=source.model_bytes,
            model_sha256=source.model_sha256,
            overrides=canonical_overrides,
            patient_assessments=patient["assessments"],
            scenario_assessments=scenario["assessments"],
        )
        return {
            "patient": patient,
            "scenario": scenario,
            "diff": _diff(patient, scenario, raw_changes),
            "active_graph": graph,
            "selected_attribution": attribution,
            "identity": {
                "workflow": source.workflow,
                "proposal_id": proposal["id"],
                "input_hash": proposal["input_hash"],
                "payload_hash": contracts.canonical_hash(source.payload),
                "content_hash": proposal["content_hash"],
                "model_sha256": source.model_sha256,
                "ddi_release_id": source.ddi_release_id,
                "assessment_hashes": source.assessment_hashes,
                "assessment_hash_origin": "historical_byte_pin"
                if source.workflow == follow_up.WORKFLOW
                else "resolved_from_pinned_versions",
            },
        }
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise _unavailable("The proposal's pinned calculation inputs are unavailable.") from exc
