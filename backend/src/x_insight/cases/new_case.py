"""Typed new-case state, content pins and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Literal, NoReturn, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments import diagnosis
from x_insight.assessments.engine import evaluate
from x_insight.assessments.released import get_released_definition
from x_insight.cases.new_case_demographics import age_at_registration
from x_insight.cases.new_case_rules import PlanInputs, bmi_category, override, plan_sections
from x_insight.ddi.lexicomp import PARSER, content_directory, current_content_hash, current_version

WORKFLOW: Literal["new-case-v1"] = "new-case-v1"
CONDITIONS = (
    "hepatic_failure",
    "renal_failure",
    "glucose_abnormality",
    "hyperlipidemia",
    "hyperprolactinemia",
    "pregnant",
)
BMI_CATEGORIES = ("underweight", "normal", "overweight", "obesity_i", "obesity_ii", "obesity_iii")
STEPS = ("diagnosis", "panss", "harm", "history", "medications", "considerations")


class FieldError(TypedDict):
    field: str
    code: str
    message_key: str


class NewCaseState(TypedDict):
    raw: dict[str, Any]
    age: int
    calculated_bmi: str | None
    bmi_category: str | None
    flags: dict[str, Any]
    lab_warnings: list[dict[str, Any]]
    effective_inputs: dict[str, Any]
    errors_by_step: dict[str, list[FieldError]]
    can_complete_step: dict[str, bool]
    can_complete: bool
    evidence: dict[str, bool]
    evidence_sources: dict[str, str]
    sections: dict[str, Any]
    proposal: NotRequired[dict[str, Any] | None]
    revision: NotRequired[int]
    can_navigate: NotRequired[bool]
    navigation_errors: NotRequired[list[FieldError]]


def fail(field: str, message: str) -> NoReturn:
    raise contracts.ContractError(
        422, "VALIDATION_FAILED", "New-case inputs are invalid.", {field: [message]}
    )


@lru_cache(maxsize=4)
def _content(root: str) -> dict[str, Any]:
    from pathlib import Path

    directory = Path(root) / "new-case/v1"
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest["workflow_version"] != WORKFLOW
        or manifest["lexicomp_version"] != current_version()
        or manifest["lexicomp_content_hash"] != current_content_hash()
        or manifest["lexicomp_parser_version"] != PARSER
    ):
        raise ValueError("New-case manifest identity mismatch")
    if set(manifest["files"]) != {
        "labs.json",
        "blood-pressure-lowering-drugs.json",
        "antipsychotic-formulations.json",
        "oral-antipsychotic.xml",
        "oral-model-manifest.json",
    }:
        raise ValueError("Incomplete new-case manifest")
    assets = {}
    for name, expected in manifest["files"].items():
        if name not in {
            "labs.json",
            "blood-pressure-lowering-drugs.json",
            "antipsychotic-formulations.json",
            "oral-antipsychotic.xml",
            "oral-model-manifest.json",
        }:
            raise ValueError("Unexpected new-case content asset")
        raw = (directory / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("New-case content checksum mismatch")
        if name.endswith(".json"):
            assets[name] = json.loads(raw)
    if set(assets) != {
        "labs.json",
        "blood-pressure-lowering-drugs.json",
        "antipsychotic-formulations.json",
        "oral-model-manifest.json",
    }:
        raise ValueError("Incomplete new-case content")
    return {"manifest": manifest, "hash": contracts.canonical_hash(manifest), **assets}


def clinical_content() -> dict[str, Any]:
    return deepcopy(_content(str(content_directory())))


def resolve_pins(session: Session, content: dict[str, Any]) -> dict[str, Any]:
    manifest = content["manifest"]
    model = (
        session.execute(
            text(
                "SELECT v.id, v.source_hash, v.review FROM model_network_versions v "
                "JOIN model_networks n "
                "ON v.network_id=n.id WHERE n.name='new-case-oral-antipsychotic-v1' "
                "AND v.version_number=1"
            )
        )
        .mappings()
        .first()
    )
    release = (
        session.execute(
            text(
                "SELECT id, version, content_hash, terminology_checksum, parser_version "
                "FROM ddi_dataset_releases WHERE version=:version"
            ),
            {"version": manifest["lexicomp_version"]},
        )
        .mappings()
        .first()
    )
    if (
        model is None
        or release is None
        or model["source_hash"] != manifest["files"]["oral-antipsychotic.xml"]
        or model["review"].get("execution_profile") != "fully-observed-oral-row-lookup/1"
        or model["review"].get("manifest_hash")
        != contracts.canonical_hash(content["oral-model-manifest.json"])
        or release["content_hash"] != current_content_hash()
        or release["terminology_checksum"] != manifest["lexicomp_sha256"]
        or release["parser_version"] != PARSER
    ):
        raise contracts.ContractError(
            409,
            "NEW_CASE_CONTENT_UNAVAILABLE",
            "Install the pinned new-case model and Lexicomp release.",
        )
    versions = {
        name: get_released_definition(name)["version"] for name in ("diagnosis", "panss", "cssrs")
    }
    if versions != manifest["assessment_versions"]:
        raise contracts.ContractError(
            409, "NEW_CASE_CONTENT_UNAVAILABLE", "Assessment definitions do not match the workflow."
        )
    return {
        "workflow_manifest_hash": content["hash"],
        "model_version_id": str(model["id"]),
        "model_sha256": model["source_hash"],
        "lexicomp_release_id": str(release["id"]),
        "lexicomp_version": release["version"],
        "lexicomp_content_hash": release["content_hash"],
        "assessment_versions": versions,
        "lab_content_hash": manifest["files"]["labs.json"],
        "bp_classification_hash": manifest["files"]["blood-pressure-lowering-drugs.json"],
        "formulation_content_hash": manifest["files"]["antipsychotic-formulations.json"],
    }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Manual(StrictModel):
    manual: bool | None = None
    manual_actor_id: str | None = None
    manual_recorded_at: str | None = None


class Harm(StrictModel):
    self: bool = False
    others: bool = False


class Psychiatric(StrictModel):
    present: bool = False
    conditions: list[Literal["depression", "ptsd", "adhd", "other"]] = Field(default_factory=list)
    other_text: str = Field(default="", max_length=2000)


def decimal_text(value: Any, *, positive: bool = False, percentage: bool = False) -> str | None:
    if value is None or value == "":
        return None
    if type(value) not in (str, int, float, Decimal):
        raise ValueError("Expected a finite decimal measurement")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal measurement") from exc
    if (
        not number.is_finite()
        or number < 0
        or (positive and number <= 0)
        or (percentage and number > 100)
    ):
        raise ValueError("Measurement is outside its input bounds")
    exponent = number.as_tuple().exponent
    if number.adjusted() > 100 or (isinstance(exponent, int) and exponent < -100):
        raise ValueError("Measurement precision is too large")
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


class Labs(StrictModel):
    present: bool = False
    values: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("values", mode="before")
    @classmethod
    def measurements(cls, value: Any) -> dict[str, str | None]:
        if not isinstance(value, dict):
            raise ValueError("Lab values must be an object")
        fields = clinical_content()["labs.json"]["fields"]
        if set(value) - set(fields):
            raise ValueError("Unknown laboratory field")
        return {
            name: decimal_text(number, percentage=name.endswith("_pct"))
            for name, number in value.items()
        }


class Conditions(StrictModel):
    hepatic_failure: Manual = Field(default_factory=Manual)
    renal_failure: Manual = Field(default_factory=Manual)
    glucose_abnormality: Manual = Field(default_factory=Manual)
    hyperlipidemia: Manual = Field(default_factory=Manual)
    hyperprolactinemia: Manual = Field(default_factory=Manual)
    pregnant: Manual = Field(default_factory=Manual)


class Substances(StrictModel):
    present: bool = False
    marijuana: bool = False
    other: bool = False
    other_text: str = Field(default="", max_length=2000)


class BMI(StrictModel):
    height_cm: str | None = None
    weight_kg: str | None = None
    source: Literal["calculated", "manual"] | None = None
    manual_category: (
        Literal["underweight", "normal", "overweight", "obesity_i", "obesity_ii", "obesity_iii"]
        | None
    ) = None

    @field_validator("height_cm", "weight_kg", mode="before")
    @classmethod
    def measurements(cls, value: Any) -> str | None:
        return decimal_text(value, positive=True)


class Considerations(StrictModel):
    basic_needs_met: bool = True
    needs_observation: bool = False
    lai: bool = False
    seizure_risk: bool = False
    avoid_sedation: bool = False
    qtc_risk: bool = False
    orthostatic_risk: Manual = Field(default_factory=Manual)


class NewCase(StrictModel):
    schema_version: Literal["new-case-v1"] = WORKFLOW
    registration_year: int
    content_pins: dict[str, Any]
    harm: Harm = Field(default_factory=Harm)
    psychiatric: Psychiatric = Field(default_factory=Psychiatric)
    labs: Labs = Field(default_factory=Labs)
    conditions: Conditions = Field(default_factory=Conditions)
    heart_failure: bool = False
    tobacco: bool = False
    substances: Substances = Field(default_factory=Substances)
    bmi: BMI = Field(default_factory=BMI)
    breastfeeding: bool = False
    focal_neurological_deficit: bool = False
    suspected_brain_lesion: bool = False
    suspected_autoimmune_encephalitis: bool = False
    considerations: Considerations = Field(default_factory=Considerations)


def fresh_draft(registration_year: int, pins: dict[str, Any]) -> dict[str, Any]:
    return {
        "new_case": NewCase(registration_year=registration_year, content_pins=pins).model_dump(),
        "diagnosis": {"answers": {}, "new_case_assertion": None},
        "panss": {"answers": {}},
        "cssrs": {"answers": {f"css_i{index}_recent": "no" for index in range(1, 6)}},
        "medications": {"entries": [], "selection_dataset_version": pins["lexicomp_version"]},
    }


def structural_draft(draft: dict[str, Any]) -> dict[str, Any]:
    try:
        state = NewCase.model_validate(draft.get("new_case"))
    except ValidationError as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid new-case state.",
            {
                "new_case." + ".".join(map(str, error["loc"])): [error["msg"]]
                for error in exc.errors()
            },
        ) from exc
    _require_complete_shape(draft["new_case"], state.model_dump(), "new_case")
    result = deepcopy(draft)
    result["new_case"] = state.model_dump()
    bmi = result["new_case"]["bmi"]
    if bmi["source"] is None and bmi["height_cm"] is not None and bmi["weight_kg"] is not None:
        bmi["source"] = "calculated"
    for name in ("diagnosis", "panss", "cssrs"):
        section = result.get(name)
        allowed = {"answers", "new_case_assertion"} if name == "diagnosis" else {"answers"}
        if (
            not isinstance(section, dict)
            or set(section) - allowed
            or not isinstance(section.get("answers"), dict)
        ):
            fail(name, "Invalid assessment section")
        answers = section["answers"]
        definition = get_released_definition(name)
        item_ids = {item["id"] for item in definition["items"]}
        if set(answers) - item_ids:
            fail(name, "Unknown assessment answer")
        if name == "cssrs" and (
            set(answers) != item_ids
            or any(answer not in ("yes", "no") for answer in answers.values())
        ):
            fail(name, "All five recent C-SSRS answers must be yes or no")
        if evaluate(definition, answers).get("item_errors"):
            fail(name, "Invalid assessment answer value")
    medications = result.get("medications")
    if not isinstance(medications, dict) or set(medications) != {
        "entries",
        "selection_dataset_version",
    }:
        fail("medications", "Invalid medication selection")
    from x_insight.cases.medications import validate_entries_structure

    medications["entries"] = validate_entries_structure(medications["entries"])
    return result


def _require_complete_shape(raw: dict[str, Any], normalized: dict[str, Any], path: str) -> None:
    for key, value in normalized.items():
        if key in ("manual_actor_id", "manual_recorded_at"):
            continue
        if key not in raw:
            fail(path + "." + key, "Full new-case state is required")
        if isinstance(value, dict) and key not in ("values", "content_pins"):
            _require_complete_shape(raw[key], value, path + "." + key)


def prepare_autosave(
    stored: dict[str, Any], incoming: dict[str, Any], *, actor_id: str, recorded_at: str
) -> dict[str, Any]:
    candidate = structural_draft(incoming)
    old = stored["new_case"]
    new = candidate["new_case"]
    for name in ("schema_version", "registration_year", "content_pins"):
        if new[name] != old[name]:
            fail("new_case." + name, "Server-controlled metadata cannot be changed")
    old_assertion = stored["diagnosis"].get("new_case_assertion")
    if candidate["diagnosis"].get("new_case_assertion") != old_assertion:
        fail(
            "diagnosis.new_case_assertion",
            "Only the explicit server assertion command can change this record",
        )
    if candidate["diagnosis"]["answers"] != stored["diagnosis"]["answers"]:
        candidate["diagnosis"]["new_case_assertion"] = None
    for name in (*CONDITIONS, "orthostatic_risk"):
        old_answer = (
            old["considerations"][name] if name == "orthostatic_risk" else old["conditions"][name]
        )
        new_answer = (
            new["considerations"][name] if name == "orthostatic_risk" else new["conditions"][name]
        )
        for field in ("manual_actor_id", "manual_recorded_at"):
            incoming_answer = (
                incoming["new_case"]["considerations"][name]
                if name == "orthostatic_risk"
                else incoming["new_case"]["conditions"][name]
            )
            if field in incoming_answer and incoming_answer[field] != old_answer.get(field):
                fail("new_case." + name + "." + field, "Override attribution is server-controlled")
            new_answer[field] = old_answer.get(field)
        if new_answer["manual"] != old_answer["manual"]:
            new_answer["manual_actor_id"] = actor_id if new_answer["manual"] is not None else None
            new_answer["manual_recorded_at"] = (
                recorded_at if new_answer["manual"] is not None else None
            )
    return candidate


def validate_pins(session: Session, draft: dict[str, Any], content: dict[str, Any]) -> None:
    if draft["new_case"]["content_pins"] != resolve_pins(session, content):
        fail(
            "new_case.content_pins",
            "Pinned content identity does not match installed immutable content",
        )
    medication = draft["medications"]
    if (
        medication["selection_dataset_version"]
        != draft["new_case"]["content_pins"]["lexicomp_version"]
    ):
        fail("medications.selection_dataset_version", "Medication release must match workflow pin")
    ids = [entry["catalog_drug_id"] for entry in medication["entries"]]
    if ids:
        found = set(
            session.execute(
                text(
                    "SELECT catalog_drug_id FROM ddi_concepts WHERE release_id=:release "
                    "AND catalog_drug_id=ANY(:ids)"
                ),
                {"release": draft["new_case"]["content_pins"]["lexicomp_release_id"], "ids": ids},
            ).scalars()
        )
        if found != set(ids):
            fail("medications.entries", "Unknown catalog drug in pinned release")


def preview(
    session: Session,
    encounter_id: uuid.UUID,
    user: dict[str, Any],
    *,
    expected_revision: int | None = None,
    destination: str | None = None,
) -> NewCaseState:
    from x_insight.cases import encounters, patients

    encounter = encounters.read_draft_for_author(session, encounter_id, user)
    if encounter.get("workflow_version") != WORKFLOW:
        raise contracts.ContractError(409, "WRONG_WORKFLOW", "This is not a new-case registration.")
    if expected_revision is not None and encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    content = clinical_content()
    validate_pins(session, encounter["draft_data"], content)
    patient = patients.get_patient(session, encounter["patient_id"])
    assert patient is not None
    state = evaluate_new_case(patient, encounter, encounter["draft_data"], content)
    state["revision"] = encounter["revision"]
    if destination is not None:
        if destination not in (*STEPS, "proposal"):
            fail("destination", "Unknown new-case destination")
        prerequisites = STEPS[: STEPS.index(destination)] if destination in STEPS else STEPS
        blocked = [error for step in prerequisites for error in state["errors_by_step"][step]]
        state["can_navigate"] = not blocked
        state["navigation_errors"] = blocked
    return state


def assert_diagnosis(
    session: Session,
    encounter_id: uuid.UUID,
    author: dict[str, Any],
    expected_revision: int,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    from x_insight.cases import encounters, patients, tables
    from x_insight.operations.audit import record_audit

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
    encounters.require_draft_lifecycle(encounter)
    encounters.require_author(encounter, author)
    patients.require_encounter_mutable(session, encounter, author)
    if encounter.get("workflow_version") != WORKFLOW:
        raise contracts.ContractError(409, "WRONG_WORKFLOW", "This is not a new-case registration.")
    if encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    draft = structural_draft(encounter["draft_data"])
    validate_pins(session, draft, clinical_content())
    result = diagnosis.evaluate_answers(draft["diagnosis"]["answers"])
    positive = (
        result.get("status") == "complete"
        and result.get("findings", {}).get("overall") == "criteria_satisfied"
    )
    if not positive:
        draft["diagnosis"]["answers"] = {}
    stamp = contracts.serialize_utc(contracts.utcnow())
    draft["diagnosis"]["new_case_assertion"] = {
        "status": "definite_diagnosis",
        "actor_id": str(author["id"]),
        "recorded_at": stamp,
        "revision": expected_revision,
        "definition_version": diagnosis.definition_version(),
        "answers_hash": diagnosis.answers_hash(draft["diagnosis"]["answers"]),
    }
    encounters.validate_draft_data(draft)
    session.execute(
        update(tables.encounters)
        .where(tables.encounters.c.id == encounter_id)
        .values(
            draft_data=draft, revision=expected_revision + 1, updated_at=contracts.parse_utc(stamp)
        )
    )
    record_audit(
        session,
        operation="new_case.diagnosis_assertion.success",
        actor=str(author["username"]),
        request_id=request_id,
        details={"encounter_id": str(encounter_id), "revision": expected_revision + 1},
    )
    updated = encounters.get_encounter(session, encounter_id)
    assert updated is not None
    return updated, stamp


def _outside(value: Decimal, bounds: dict[str, Any]) -> bool:
    low, high = bounds.get("min"), bounds.get("max")
    return bool(
        (
            low is not None
            and (
                value < Decimal(low) if bounds.get("min_inclusive", True) else value <= Decimal(low)
            )
        )
        or (
            high is not None
            and (
                value > Decimal(high)
                if bounds.get("max_inclusive", True)
                else value >= Decimal(high)
            )
        )
    )


def _critical(value: Decimal, bounds: dict[str, Any]) -> bool:
    low, high = bounds.get("low"), bounds.get("high")
    return bool(
        (
            low is not None
            and (value <= Decimal(low) if bounds.get("low_inclusive") else value < Decimal(low))
        )
        or (
            high is not None
            and (value >= Decimal(high) if bounds.get("high_inclusive") else value > Decimal(high))
        )
    )


def assertion_valid(assertion: Any, answers: dict[str, Any], author_id: str) -> bool:
    if not isinstance(assertion, dict) or set(assertion) != {
        "status",
        "actor_id",
        "recorded_at",
        "revision",
        "definition_version",
        "answers_hash",
    }:
        return False
    if (
        assertion["status"] != "definite_diagnosis"
        or assertion["actor_id"] != author_id
        or assertion["answers_hash"] != diagnosis.answers_hash(answers)
        or assertion["definition_version"] != diagnosis.definition_version()
        or type(assertion["revision"]) is not int
        or assertion["revision"] < 1
        or not isinstance(assertion["recorded_at"], str)
    ):
        return False
    try:
        contracts.parse_utc(assertion["recorded_at"])
    except (TypeError, ValueError):
        return False
    return True


def evaluate_new_case(
    patient: dict[str, Any],
    encounter: dict[str, Any],
    draft_data: dict[str, Any],
    clinical_content: dict[str, Any],
) -> NewCaseState:
    if encounter.get("workflow_version") != WORKFLOW or encounter.get("kind") != "registration":
        raise contracts.ContractError(
            409, "WRONG_WORKFLOW", "This encounter is not a new-case registration."
        )
    draft = structural_draft(draft_data)
    raw = draft["new_case"]
    sex = patient["sex"]
    age = age_at_registration(patient.get("birth_year"), raw["registration_year"])
    errors: dict[str, list[FieldError]] = {name: [] for name in STEPS}

    def required(step: str, field: str, code: str) -> None:
        errors[step].append(
            {"field": field, "code": code, "message_key": "new_case.errors." + code}
        )

    diagnosis_evaluation = diagnosis.evaluate_answers(draft["diagnosis"]["answers"])
    positive = (
        diagnosis_evaluation.get("status") == "complete"
        and diagnosis_evaluation.get("findings", {}).get("overall") == "criteria_satisfied"
    )
    assertion = draft["diagnosis"].get("new_case_assertion")
    asserted = assertion_valid(
        assertion, draft["diagnosis"]["answers"], str(encounter["author_id"])
    )
    if not positive and not asserted:
        required("diagnosis", "diagnosis.answers", "diagnosis_not_positive")
    if raw["psychiatric"]["present"] and not raw["psychiatric"]["conditions"]:
        required("history", "new_case.psychiatric.conditions", "psychiatric_selection_required")
    if raw["substances"]["present"] and not (
        raw["substances"]["marijuana"] or raw["substances"]["other"]
    ):
        required("history", "new_case.substances", "substance_selection_required")
    measurements = {
        name: Decimal(value)
        for name, value in raw["labs"]["values"].items()
        if value is not None and raw["labs"]["present"] and (name != "beta_hcg" or sex == "F")
    }
    if raw["labs"]["present"] and not measurements:
        required("history", "new_case.labs.values", "lab_measurement_required")
    findings: dict[str, list[str]] = {name: [] for name in (*CONDITIONS, "orthostatic_risk")}
    warnings = []
    for name, value in measurements.items():
        spec = clinical_content["labs.json"]["fields"][name]
        reference = spec["reference"].get(sex, spec["reference"])
        abnormal = _outside(value, reference)
        if abnormal:
            warnings.append(
                {"field": name, "value": str(value), "code": "abnormal", "source": spec["source"]}
            )
        for bounds_name in ("critical", "outpatient_critical"):
            if _critical(value, spec.get(bounds_name, {})):
                warnings.append(
                    {"field": name, "value": str(value), "code": bounds_name, "source": "critical"}
                )
        if name == "beta_hcg" and not _outside(value, spec["indeterminate"]):
            warnings.append(
                {
                    "field": name,
                    "value": str(value),
                    "code": "indeterminate",
                    "source": spec["source"],
                }
            )
        suggestion = spec.get("suggestion")
        if suggestion:
            threshold = suggestion["threshold"]
            threshold = threshold[sex] if isinstance(threshold, dict) else threshold
            if value > Decimal(threshold) or (
                suggestion["operator"] == ">=" and value == Decimal(threshold)
            ):
                findings[suggestion["condition"]].append(name)
    if raw["heart_failure"]:
        findings["orthostatic_risk"].append("heart_failure")
    medication_ids = {entry["catalog_drug_id"] for entry in draft["medications"]["entries"]}
    for entry in clinical_content["blood-pressure-lowering-drugs.json"]["entries"]:
        if entry["catalog_drug_id"] in medication_ids:
            findings["orthostatic_risk"].append(entry["catalog_drug_id"])
    flags = {}
    for name, reasons in findings.items():
        answer = (
            raw["considerations"][name] if name == "orthostatic_risk" else raw["conditions"][name]
        )
        flag = asdict(override(tuple(reasons), answer["manual"]))
        flag.update(
            {
                "mode": "automatic" if answer["manual"] is None else "manual",
                "manual_actor_id": answer["manual_actor_id"],
                "manual_recorded_at": answer["manual_recorded_at"],
            }
        )
        triggering_findings = []
        for reason in reasons:
            if reason in measurements:
                spec = clinical_content["labs.json"]["fields"][reason]
                triggering_findings.append(
                    {
                        "field": reason,
                        "value": str(measurements[reason]),
                        "unit": spec["unit"],
                        "source": clinical_content["labs.json"]["sources"][spec["source"]],
                    }
                )
            elif reason == "heart_failure":
                triggering_findings.append({"field": "heart_failure", "value": True})
            else:
                triggering_findings.extend(
                    entry
                    for entry in clinical_content["blood-pressure-lowering-drugs.json"]["entries"]
                    if entry["catalog_drug_id"] == reason
                )
        flag["triggering_findings"] = triggering_findings
        if name == "pregnant" and sex != "F":
            flag.update(suggested=False, effective=False, findings=(), triggering_findings=[])
        flags[name] = flag
    bmi = raw["bmi"]
    calculated = None
    calculated_category = None
    if bmi["height_cm"] and bmi["weight_kg"]:
        calculated, calculated_category = bmi_category(
            Decimal(bmi["height_cm"]), Decimal(bmi["weight_kg"])
        )
    category = (
        bmi["manual_category"]
        if bmi["source"] == "manual"
        else calculated_category
        if bmi["source"] == "calculated"
        else None
    )
    if category is None:
        required("history", "new_case.bmi", "bmi_category_required")
    considerations = raw["considerations"]
    evidence = {
        "Smoking": raw["tobacco"],
        "Marijuana_Smoking": raw["substances"]["present"] and raw["substances"]["marijuana"],
        "Hepatic_Failure": flags["hepatic_failure"]["effective"],
        "Renal_Failure": flags["renal_failure"]["effective"],
        "Sedative_Effects_Concern": considerations["avoid_sedation"],
        "Seizure_Risk": considerations["seizure_risk"],
        "Glucose_Abnormalities": flags["glucose_abnormality"]["effective"],
        "Hyperlipidemia": flags["hyperlipidemia"]["effective"],
        "Weight_Gain_Risk": category in BMI_CATEGORIES[2:],
        "QTc_Prolongation_Risk": considerations["qtc_risk"],
        "Orthostatic_Hypotension_Risk": flags["orthostatic_risk"]["effective"],
        "Hyperprolactinemia": flags["hyperprolactinemia"]["effective"],
        **{name: False for name in ("Tardive_Dyskinesia", "Dystonia", "Parkinsonism", "Akathisia")},
    }
    inputs = PlanInputs(
        sex="female" if sex == "F" else "male",
        age=age,
        self_harm=raw["harm"]["self"],
        other_harm=raw["harm"]["others"],
        cssrs_item_4=draft["cssrs"]["answers"]["css_i4_recent"] == "yes",
        cssrs_item_5=draft["cssrs"]["answers"]["css_i5_recent"] == "yes",
        basic_needs_met=considerations["basic_needs_met"],
        needs_observation=considerations["needs_observation"],
        lai=considerations["lai"],
        psychiatric_present=raw["psychiatric"]["present"],
        psychiatric_conditions=frozenset(raw["psychiatric"]["conditions"]),
        pregnant=flags["pregnant"]["effective"],
        breastfeeding=sex == "F" and raw["breastfeeding"],
        tobacco=raw["tobacco"],
        focal_neurological_deficit=raw["focal_neurological_deficit"],
        suspected_brain_lesion=raw["suspected_brain_lesion"],
        suspected_autoimmune_encephalitis=raw["suspected_autoimmune_encephalitis"],
    )
    sections = {
        name: {**asdict(section), "message_key": "new_case.sections." + name}
        for name, section in plan_sections(inputs).items()
    }
    sections["brain_imaging"]["must_not_delay_treatment"] = True
    sections["pregnancy"]["actions"] = (
        ["obstetric_consultation", "neonatal_consultation_after_birth", "lowest_effective_dose"]
        if inputs.pregnant
        else []
    )
    sections["breastfeeding"]["actions"] = (
        ["discontinue_breastfeeding", "pediatric_consultation_for_formula"]
        if inputs.breastfeeding
        else []
    )
    sections["antipsychotic_candidates"] = {
        "status": "model_unavailable" if inputs.lai else "not_generated",
        "candidates": [],
        "message_key": "new_case.sections.antipsychotic_candidates",
    }
    sections["drug_interactions"] = {
        "status": "not_generated",
        "message_key": "new_case.sections.drug_interactions",
    }
    effective_inputs = asdict(inputs)
    effective_inputs["psychiatric_conditions"] = (
        sorted(inputs.psychiatric_conditions) if inputs.psychiatric_present else []
    )
    return {
        "raw": draft,
        "age": age,
        "calculated_bmi": str(calculated) if calculated is not None else None,
        "bmi_category": category,
        "flags": flags,
        "lab_warnings": [
            warning
            for warning in warnings
            if warning["code"] != "outpatient_critical"
            or sections["care_setting"]["value"] == "outpatient"
        ],
        "effective_inputs": effective_inputs,
        "errors_by_step": errors,
        "can_complete_step": {name: not values for name, values in errors.items()},
        "can_complete": not any(errors.values()),
        "evidence": evidence,
        "evidence_sources": {
            "Smoking": "new_case.tobacco",
            "Marijuana_Smoking": "new_case.substances.present AND marijuana",
            "Hepatic_Failure": "flags.hepatic_failure.effective",
            "Renal_Failure": "flags.renal_failure.effective",
            "Sedative_Effects_Concern": "new_case.considerations.avoid_sedation",
            "Seizure_Risk": "new_case.considerations.seizure_risk",
            "Glucose_Abnormalities": "flags.glucose_abnormality.effective",
            "Hyperlipidemia": "flags.hyperlipidemia.effective",
            "Weight_Gain_Risk": "effective BMI category overweight or obesity",
            "QTc_Prolongation_Risk": "new_case.considerations.qtc_risk",
            "Orthostatic_Hypotension_Risk": "flags.orthostatic_risk.effective",
            "Hyperprolactinemia": "flags.hyperprolactinemia.effective",
            **{
                name: "workflow_default"
                for name in ("Tardive_Dyskinesia", "Dystonia", "Parkinsonism", "Akathisia")
            },
        },
        "sections": sections,
    }
