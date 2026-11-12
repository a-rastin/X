"""Typed ``follow-up-v1`` state: defaults, strict autosave and read previews.

Package C (see ``project-documents/dev/follow-up-implementation-plan.md`` §6).
The state lives under ``draft_data.follow_up``; the shared ``panss``/``cssrs``/
``medications`` sections keep their existing shapes. This module owns the raw/
effective state contract that packages B (restore validation) and F (proposal
fingerprint) must agree with. Pure decision rules and independent sections are
in ``follow_up_rules``; medication ranking, proposals and signing belong to E/F. ``preview`` and
``validate-step`` resolve inputs and decisions without creating a proposal.

Reuse: finite-decimal parsing, BMI categories, lab bounds and the override
primitive come from ``new_case`` / ``new_case_rules``; the registration-only
evaluator (``evaluate_new_case`` / ``plan_sections``) is never called.
"""

from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal, NoReturn, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.assessments.engine import evaluate
from x_insight.assessments.released import get_released_definition
from x_insight.cases import follow_up_content, follow_up_rules
from x_insight.cases.medications import validate_entries_structure
from x_insight.cases.new_case import _critical, _outside, decimal_text
from x_insight.cases.new_case_rules import bmi_category, override
from x_insight.ddi.lexicomp import content_directory

LABS_PATH = "new-case/v1/labs.json"


def _content_fingerprint() -> tuple[str, str]:
    """Cheap invalidation key: small JSON bytes + large-asset size/mtime.

    ponytail: large immutable assets (Lexicomp CSV, oral XML) are fingerprinted
    by size+mtime, not re-hashed on the autosave hot path. Full SHA-256
    verification still runs once per distinct fingerprint via ``load_content``;
    packaged startup re-verifies them in package H.
    """
    root = content_directory()
    digest = hashlib.sha256()
    for path in sorted(follow_up_content.FILES):
        file = root / path
        if path.endswith(".json"):
            digest.update(file.read_bytes())
        else:
            stat = file.stat()
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return str(root), digest.hexdigest()


@lru_cache(maxsize=4)
def _verified_content_cached(root: str, fingerprint: str) -> dict[str, Any]:
    return follow_up_content.load_content()


def verified_content() -> dict[str, Any]:
    """Hash-checked follow-up content, cached away from the autosave hot path."""
    return _verified_content_cached(*_content_fingerprint())


def lab_fields() -> dict[str, Any]:
    return verified_content()["assets"][LABS_PATH]["fields"]


WORKFLOW: Literal["follow-up-v1"] = "follow-up-v1"
STATE_SCHEMA_VERSION: Literal["follow-up-state-v1"] = "follow-up-state-v1"

STEPS = ("panss", "harm", "history", "medications", "effects", "considerations", "proposal")
CONDITIONS = (
    "hepatic_failure",
    "renal_failure",
    "glucose_abnormality",
    "hyperlipidemia",
    "hyperprolactinemia",
    "pregnant",
)
EFFECT_BOOLEANS = (
    "orthostatic_hypotension",
    "akathisia",
    "parkinsonism",
    "acute_dystonia",
    "tardive_dyskinesia",
    "anticholinergic",
)
PSYCHIATRIC_CONDITIONS = ("depression", "ptsd", "adhd", "other")
SUBSTANCE_CATEGORIES = ("marijuana", "opioids", "methamphetamine", "other")
BMI_CATEGORIES = (
    "underweight",
    "normal",
    "overweight",
    "obesity_i",
    "obesity_ii",
    "obesity_iii",
)
RECOVERY = ("controlled", "partial", "none_or_worse")
FORMULATIONS = ("oral", "lai")
SEDATION = ("none", "tolerable", "intervention")
BRANCH_ANSWERS = (
    "switch_needed",
    "dose_reduction_possible",
    "treatment_resistant",
    "persistent_harm_risk",
    "dystonia_resolved",
)
DRAFT_SECTIONS = frozenset({"follow_up", "panss", "cssrs", "medications"})


class FieldError(TypedDict):
    field: str
    code: str
    message_key: str


class FollowUpState(TypedDict):
    raw: dict[str, Any]
    age: int
    visit_year: int
    baseline: dict[str, Any]
    calculated_bmi: str | None
    bmi_category: str | None
    flags: dict[str, Any]
    lab_warnings: list[dict[str, Any]]
    errors_by_step: dict[str, list[FieldError]]
    can_complete_step: dict[str, bool]
    can_complete: bool
    reached_questions: list[str]
    steps: list[str]
    proposal: None
    decision: dict[str, Any]
    model_factors: dict[str, Any]
    sections: dict[str, Any]
    revision: NotRequired[int]
    can_navigate: NotRequired[bool]
    navigation_errors: NotRequired[list[FieldError]]


def fail(field: str, message: str) -> NoReturn:
    raise contracts.ContractError(
        422, "VALIDATION_FAILED", "Follow-up inputs are invalid.", {field: [message]}
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Harm(StrictModel):
    self: bool = False
    others: bool = False


class Psychiatric(StrictModel):
    present: bool | None = None
    conditions: list[Literal["depression", "ptsd", "adhd", "other"]] = Field(default_factory=list)
    other_text: str = Field(default="", max_length=2000)


class Labs(StrictModel):
    present: bool = False
    values: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("values", mode="before")
    @classmethod
    def measurements(cls, value: Any) -> dict[str, str | None]:
        if not isinstance(value, dict):
            raise ValueError("Lab values must be an object")
        fields = lab_fields()
        if set(value) - set(fields):
            raise ValueError("Unknown laboratory field")
        return {
            name: decimal_text(number, percentage=name.endswith("_pct"))
            for name, number in value.items()
        }


class ConditionAnswer(StrictModel):
    # Server-owned historical answer (never downgraded to "No" when missing).
    inherited: bool | None = None
    # Current physician override; None means automatic (suggestion > inherited).
    manual: bool | None = None
    manual_actor_id: str | None = None
    manual_recorded_at: str | None = None


class Conditions(StrictModel):
    hepatic_failure: ConditionAnswer = Field(default_factory=ConditionAnswer)
    renal_failure: ConditionAnswer = Field(default_factory=ConditionAnswer)
    glucose_abnormality: ConditionAnswer = Field(default_factory=ConditionAnswer)
    hyperlipidemia: ConditionAnswer = Field(default_factory=ConditionAnswer)
    hyperprolactinemia: ConditionAnswer = Field(default_factory=ConditionAnswer)
    pregnant: ConditionAnswer = Field(default_factory=ConditionAnswer)


class Substances(StrictModel):
    present: bool | None = None
    marijuana: bool = False
    opioids: bool = False
    methamphetamine: bool = False
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


class Neurological(StrictModel):
    focal_neurological_deficit: bool | None = None
    suspected_brain_lesion: bool | None = None
    suspected_autoimmune_encephalitis: bool | None = None


class History(StrictModel):
    psychiatric: Psychiatric = Field(default_factory=Psychiatric)
    labs: Labs = Field(default_factory=Labs)
    conditions: Conditions = Field(default_factory=Conditions)
    heart_failure: bool | None = None
    tobacco: bool | None = None
    substances: Substances = Field(default_factory=Substances)
    pregnant: bool | None = None
    breastfeeding: bool | None = None
    bmi: BMI = Field(default_factory=BMI)
    neurological: Neurological = Field(default_factory=Neurological)
    hospitalization_count: int | None = Field(default=None, ge=0, le=10000)
    current_antidepressant: bool | None = None


class Effects(StrictModel):
    sedation: Literal["none", "tolerable", "intervention"] = "none"
    orthostatic_hypotension: bool = False
    akathisia: bool = False
    parkinsonism: bool = False
    acute_dystonia: bool = False
    tardive_dyskinesia: bool = False
    anticholinergic: bool = False


class Treatment(StrictModel):
    main_catalog_drug_id: str | None = Field(default=None, max_length=200)
    formulation: Literal["oral", "lai"] | None = None


class Considerations(StrictModel):
    recovery: Literal["controlled", "partial", "none_or_worse"] | None = None
    adherence: bool = True
    basic_needs_met: bool = True
    needs_observation: bool = False
    lai_preference: bool = False
    seizure_risk: bool | None = None
    qtc_risk: bool | None = None


class Branches(StrictModel):
    switch_needed: bool | None = None
    dose_reduction_possible: bool | None = None
    treatment_resistant: bool | None = None
    persistent_harm_risk: bool | None = None
    dystonia_resolved: bool | None = None


class PendingMedication(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    resolution: Literal["pending", "replaced", "removed"] = "pending"
    replacement_catalog_drug_id: str | None = Field(default=None, max_length=200)


class FollowUp(StrictModel):
    schema_version: Literal["follow-up-state-v1"] = STATE_SCHEMA_VERSION
    visit_year: int
    # Server-owned: exact content/assessment/Lexicomp/model pins.
    content_pins: dict[str, Any] = Field(default_factory=dict)
    # Server-owned frozen signed-source projection (see follow_up_baseline).
    baseline: dict[str, Any] = Field(default_factory=dict)
    harm: Harm = Field(default_factory=Harm)
    history: History = Field(default_factory=History)
    effects: Effects = Field(default_factory=Effects)
    treatment: Treatment = Field(default_factory=Treatment)
    considerations: Considerations = Field(default_factory=Considerations)
    branches: Branches = Field(default_factory=Branches)


def fresh_draft(
    visit_year: int, content_pins: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Build a fresh typed follow-up draft from a frozen baseline projection.

    ``baseline['carry']`` (produced by ``follow_up_baseline.project_baseline``)
    seeds only semantically equivalent carried values. Everything else starts
    at the exact defaults: fresh PANSS, five explicit C-SSRS "no" answers,
    seven simple effects, required recovery/branch nulls, fresh considerations.
    No prior free-text plan, assessment answer or effect is ever copied.
    """
    carry = baseline.get("carry") or {}
    # ``carry`` is a one-shot seeding view, not part of the frozen record.
    stored_baseline = {key: value for key, value in baseline.items() if key != "carry"}
    state = FollowUp(visit_year=visit_year, content_pins=content_pins, baseline=stored_baseline)
    dumped = state.model_dump()
    history = dumped["history"]
    for field in (
        "heart_failure",
        "tobacco",
        "pregnant",
        "breastfeeding",
        "hospitalization_count",
        "current_antidepressant",
    ):
        if field in carry:
            history[field] = carry[field]
    for section in ("psychiatric", "substances", "bmi", "neurological"):
        if section in carry:
            history[section].update(carry[section])
    for name in CONDITIONS:
        if name in carry.get("conditions", {}):
            history["conditions"][name]["inherited"] = carry["conditions"][name]
    if "seizure_risk" in carry:
        dumped["considerations"]["seizure_risk"] = carry["seizure_risk"]
    if "qtc_risk" in carry:
        dumped["considerations"]["qtc_risk"] = carry["qtc_risk"]
    if "main_catalog_drug_id" in carry:
        dumped["treatment"]["main_catalog_drug_id"] = carry["main_catalog_drug_id"]
    if "formulation" in carry:
        dumped["treatment"]["formulation"] = carry["formulation"]
    unmatched = baseline.get("unmatched_medications") or []
    pending = [
        {
            "source_id": str(entry["source_id"]),
            "resolution": "pending",
            "replacement_catalog_drug_id": None,
        }
        for entry in unmatched
    ]
    return structural_draft(
        {
            "follow_up": dumped,
            "panss": {"answers": {}},
            "cssrs": {"answers": {f"css_i{index}_recent": "no" for index in range(1, 6)}},
            "medications": {
                "entries": [dict(entry) for entry in (carry.get("medications") or [])],
                "selection_dataset_version": carry.get("selection_dataset_version"),
                "pending": pending,
            },
        }
    )


def _require_complete_shape(raw: dict[str, Any], normalized: dict[str, Any], path: str) -> None:
    """Refuse partial state: pydantic fills defaults, clients must send the full shape."""
    for key, value in normalized.items():
        if key in ("manual_actor_id", "manual_recorded_at"):
            continue
        if key not in raw:
            fail(path + "." + key, "Full follow-up state is required")
        if key in ("baseline", "content_pins"):
            continue  # opaque server-owned dicts
        if isinstance(value, dict) and key != "values" and isinstance(raw.get(key), dict):
            _require_complete_shape(raw[key], value, path + "." + key)


def structural_draft(draft: dict[str, Any]) -> dict[str, Any]:
    """Normalize and strictly validate a full typed follow-up draft (422)."""
    if not isinstance(draft, dict) or set(draft) - DRAFT_SECTIONS or "follow_up" not in draft:
        fail("draft_data", "Invalid follow-up draft sections")
    try:
        state = FollowUp.model_validate(draft.get("follow_up"))
    except ValidationError as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid follow-up state.",
            {
                "follow_up." + ".".join(map(str, error["loc"])): [error["msg"]]
                for error in exc.errors()
            },
        ) from exc
    _require_complete_shape(draft["follow_up"], state.model_dump(), "follow_up")
    result = deepcopy(draft)
    result["follow_up"] = state.model_dump()
    bmi = result["follow_up"]["history"]["bmi"]
    if bmi["source"] is None and bmi["height_cm"] is not None and bmi["weight_kg"] is not None:
        bmi["source"] = "calculated"

    for name in ("panss", "cssrs"):
        section = result.get(name)
        if (
            not isinstance(section, dict)
            or set(section) != {"answers"}
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
    if not isinstance(medications, dict) or set(medications) - {
        "entries",
        "selection_dataset_version",
        "pending",
    }:
        fail("medications", "Invalid medication selection")
    medications["entries"] = validate_entries_structure(medications.get("entries"))
    if medications.get("selection_dataset_version") is not None and not isinstance(
        medications["selection_dataset_version"], str
    ):
        fail("medications.selection_dataset_version", "Must be text or null")
    raw_pending = medications.get("pending")
    if not isinstance(raw_pending, list):
        fail("medications.pending", "Must be a list")
    unmatched = result["follow_up"]["baseline"].get("unmatched_medications", [])
    if not isinstance(unmatched, list):
        fail("follow_up.baseline.unmatched_medications", "Invalid frozen baseline")
    source_ids = [
        str(entry["source_id"])
        for entry in unmatched
        if isinstance(entry, dict) and isinstance(entry.get("source_id"), str)
    ]
    try:
        pending = [PendingMedication.model_validate(entry).model_dump() for entry in raw_pending]
    except ValidationError as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "Invalid pending medication resolution.",
            {"medications.pending": [error["msg"] for error in exc.errors()]},
        ) from exc
    if sorted(entry["source_id"] for entry in pending) != sorted(source_ids):
        fail("medications.pending", "Pending items must match the frozen baseline exactly")
    valid_ids = {entry["catalog_drug_id"] for entry in medications["entries"]}
    for entry in pending:
        if entry["resolution"] == "replaced":
            if entry["replacement_catalog_drug_id"] not in valid_ids:
                fail("medications.pending", "Replacement must be a selected catalog drug")
        elif entry["replacement_catalog_drug_id"] is not None:
            fail("medications.pending", "Only a replacement carries a catalog id")
    medications["pending"] = pending
    return result


def prepare_autosave(
    stored: dict[str, Any], incoming: dict[str, Any], *, actor_id: str, recorded_at: str
) -> dict[str, Any]:
    """Strict autosave: reject forged identity/pins/baseline/provenance/results."""
    candidate = structural_draft(incoming)
    old = stored["follow_up"]
    new = candidate["follow_up"]
    for name in ("schema_version", "visit_year", "content_pins", "baseline"):
        if new[name] != old[name]:
            fail("follow_up." + name, "Server-controlled metadata cannot be changed")
    for name in CONDITIONS:
        old_answer = old["history"]["conditions"][name]
        new_answer = new["history"]["conditions"][name]
        incoming_answer = incoming["follow_up"]["history"]["conditions"][name]
        if new_answer["inherited"] != old_answer["inherited"]:
            fail(
                "follow_up.history.conditions." + name + ".inherited",
                "Inherited answer is server-controlled",
            )
        for field in ("manual_actor_id", "manual_recorded_at"):
            if field in incoming_answer and incoming_answer[field] != old_answer.get(field):
                fail(
                    "follow_up.history.conditions." + name + "." + field,
                    "Override attribution is server-controlled",
                )
            new_answer[field] = old_answer.get(field)
        if new_answer["manual"] != old_answer["manual"]:
            new_answer["manual_actor_id"] = actor_id if new_answer["manual"] is not None else None
            new_answer["manual_recorded_at"] = (
                recorded_at if new_answer["manual"] is not None else None
            )
    return candidate


def intervention_effects(effects: dict[str, Any]) -> tuple[str, ...]:
    """Effects requiring intervention (tolerable sedation alone does not)."""
    present = [name for name in EFFECT_BOOLEANS if effects[name]]
    if effects["sedation"] == "intervention":
        present.append("sedation")
    return tuple(present)


def reached_questions(state: dict[str, Any]) -> list[str]:
    """Return the authoritative decision path's active branch questions."""
    assets = verified_content()["assets"]
    return follow_up_rules.decision(
        state,
        assets["follow-up/v1/lai-equivalences.json"]["equivalences"],
        assets["new-case/v1/antipsychotic-formulations.json"]["entries"],
    )["reached_questions"]


def evaluate_state(draft: dict[str, Any], patient: dict[str, Any]) -> FollowUpState:
    """Pure effective-state evaluation (no proposal, no model, no DB writes)."""
    state = structural_draft(draft)
    fu = state["follow_up"]
    raw = fu["history"]
    sex = patient["sex"]
    # §2: birth-year records use frozen visit year - birth year; age-only legacy
    # records keep the explicitly recorded age (never infer a birth year).
    age = (
        fu["visit_year"] - int(patient["birth_year"])
        if patient.get("birth_year") is not None
        else int(patient["age"])
    )
    errors: dict[str, list[FieldError]] = {name: [] for name in STEPS}

    def required(step: str, field: str, code: str) -> None:
        errors[step].append(
            {"field": field, "code": code, "message_key": "follow_up.errors." + code}
        )

    content = verified_content()["assets"][LABS_PATH]
    lab_field_specs = content["fields"]
    measurements = {
        name: Decimal(value)
        for name, value in raw["labs"]["values"].items()
        if value is not None and raw["labs"]["present"] and (name != "beta_hcg" or sex == "F")
    }
    findings: dict[str, list[str]] = {name: [] for name in CONDITIONS}
    warnings: list[dict[str, Any]] = []
    if raw["labs"]["present"] and not measurements:
        required("history", "follow_up.history.labs.values", "lab_measurement_required")
    for name, value in measurements.items():
        spec = lab_field_specs[name]
        reference = spec["reference"].get(sex, spec["reference"])
        if _outside(value, reference):
            warnings.append(
                {"field": name, "value": str(value), "code": "abnormal", "source": spec["source"]}
            )
        for bounds_name in ("critical", "outpatient_critical"):
            if _critical(value, spec.get(bounds_name, {})):
                warnings.append(
                    {
                        "field": name,
                        "value": str(value),
                        "code": bounds_name,
                        "source": "critical",
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
    flags: dict[str, Any] = {}
    for name in CONDITIONS:
        answer = raw["conditions"][name]
        flag = override(tuple(findings[name]), answer["manual"])
        # Priority (spec §4): explicit manual Yes/No > current abnormal
        # suggestion Yes > inherited answer. Never let an inherited Yes beat an
        # explicit current No.
        if answer["manual"] is not None:
            effective = answer["manual"]
        elif findings[name]:
            effective = True
        elif name == "pregnant" and raw["pregnant"] is not None:
            effective = raw["pregnant"]
        elif answer["inherited"] is not None:
            effective = bool(answer["inherited"])
        else:
            effective = False
        if name == "pregnant" and sex != "F":
            effective = False
        # An explicit override, a lab suggestion, an inherited answer or (for
        # prolactin only) a current normal measurement establishes the factor.
        # Absence of all four stays unresolved, never a manufactured "No".
        established = (
            answer["manual"] is not None
            or bool(findings[name])
            or answer["inherited"] is not None
            or (name == "pregnant" and raw["pregnant"] is not None)
            or (name == "hyperprolactinemia" and "prolactin" in measurements)
        )
        flags[name] = {
            "suggested": flag.suggested,
            "effective": effective,
            "established": established,
            "findings": list(flag.findings),
            "inherited": answer["inherited"],
            "manual": answer["manual"],
            "mode": "automatic" if answer["manual"] is None else "manual",
            "manual_actor_id": answer["manual_actor_id"],
            "manual_recorded_at": answer["manual_recorded_at"],
            "triggering_findings": [
                {
                    "field": reason,
                    "value": str(measurements[reason]),
                    "unit": lab_field_specs[reason]["unit"],
                }
                for reason in findings[name]
                if reason in measurements
            ],
        }
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
        required("history", "follow_up.history.bmi", "bmi_category_required")

    psychiatric = raw["psychiatric"]
    if psychiatric["present"] and not psychiatric["conditions"]:
        required(
            "history", "follow_up.history.psychiatric.conditions", "psychiatric_selection_required"
        )
    if psychiatric["present"] and {"depression", "ptsd"} & set(psychiatric["conditions"]):
        if raw["current_antidepressant"] is None:
            required(
                "history",
                "follow_up.history.current_antidepressant",
                "antidepressant_confirmation_required",
            )
    substances = raw["substances"]
    if substances["present"] and not any(substances[name] for name in SUBSTANCE_CATEGORIES):
        required("history", "follow_up.history.substances", "substance_selection_required")
    for field in ("psychiatric", "substances"):
        if raw[field]["present"] is None:
            required("history", "follow_up.history." + field + ".present", "carried_value_required")
    for field in ("heart_failure", "tobacco"):
        if raw[field] is None:
            required("history", "follow_up.history." + field, "carried_value_required")
    for field in (
        "focal_neurological_deficit",
        "suspected_brain_lesion",
        "suspected_autoimmune_encephalitis",
    ):
        if raw["neurological"][field] is None:
            required("history", "follow_up.history.neurological." + field, "carried_value_required")
    if sex == "F":
        for field in ("pregnant", "breastfeeding"):
            if raw[field] is None:
                required("history", "follow_up.history." + field, "carried_value_required")

    medications = state["medications"]
    if any(entry["resolution"] == "pending" for entry in medications["pending"]):
        required("medications", "medications.pending", "pending_medication_required")
    if (
        fu["treatment"]["main_catalog_drug_id"] is not None
        and fu["treatment"]["formulation"] is None
    ):
        required("medications", "follow_up.treatment.formulation", "formulation_required")

    considerations = fu["considerations"]
    if considerations["recovery"] is None:
        required("considerations", "follow_up.considerations.recovery", "recovery_required")
    for field in ("seizure_risk", "qtc_risk"):
        if considerations[field] is None:
            required(
                "considerations", "follow_up.considerations." + field, "carried_value_required"
            )
    reached = reached_questions(state)
    for name in reached:
        if fu["branches"][name] is None:
            required("considerations", "follow_up.branches." + name, "branch_answer_required")

    result: FollowUpState = {
        "raw": state,
        "age": age,
        "visit_year": fu["visit_year"],
        "baseline": fu["baseline"],
        "calculated_bmi": str(calculated) if calculated is not None else None,
        "bmi_category": category,
        "flags": flags,
        "lab_warnings": warnings,
        "errors_by_step": errors,
        "can_complete_step": {name: not values for name, values in errors.items()},
        "can_complete": not any(errors.values()),
        "reached_questions": reached,
        "steps": list(STEPS),
        "proposal": None,  # F owns proposals; reading never creates one.
        "decision": {},
        "model_factors": {},
        "sections": {},
    }
    assets = verified_content()["assets"]
    decision = follow_up_rules.decision(
        state,
        assets["follow-up/v1/lai-equivalences.json"]["equivalences"],
        assets["new-case/v1/antipsychotic-formulations.json"]["entries"],
    )
    factors = follow_up_rules.model_factors(result)
    if decision["terminal"] == "oral_replacement":
        for name, factor in factors.items():
            if factor["value"] is None:
                required("considerations", "model_factors." + name, "model_factor_required")
    result["decision"] = decision
    result["model_factors"] = factors
    result["sections"] = follow_up_rules.sections(result, sex, decision)
    result["can_complete_step"] = {name: not values for name, values in errors.items()}
    result["can_complete"] = not any(errors.values())
    return result


def preview(
    session: Session,
    encounter_id: uuid.UUID,
    user: dict[str, Any],
    *,
    expected_revision: int | None = None,
    destination: str | None = None,
) -> FollowUpState:
    """Author-only read preview; resolves state, never creates a proposal."""
    from x_insight.cases import encounters, patients

    encounter = encounters.read_draft_for_author(session, encounter_id, user)
    if encounter.get("workflow_version") != WORKFLOW:
        raise contracts.ContractError(409, "WRONG_WORKFLOW", "This is not a follow-up-v1 draft.")
    if expected_revision is not None and encounter["revision"] != expected_revision:
        raise contracts.ContractError(412, "STALE_REVISION", "Reload and reconcile the draft.")
    patient = patients.get_patient(session, encounter["patient_id"])
    assert patient is not None
    state = evaluate_state(encounter["draft_data"], patient)
    state["revision"] = encounter["revision"]
    if destination is not None:
        if destination not in (*STEPS,):
            fail("destination", "Unknown follow-up destination")
        prerequisites = STEPS[: STEPS.index(destination)]
        blocked = [error for step in prerequisites for error in state["errors_by_step"][step]]
        state["can_navigate"] = not blocked
        state["navigation_errors"] = blocked
    return state


def config() -> dict[str, Any]:
    """Versioned labels/definitions and hashes (no credentials, no provider)."""
    loaded = verified_content()
    assets = loaded["assets"]
    manifest = loaded["manifest"]
    labs = assets[LABS_PATH]
    formulations = assets["new-case/v1/antipsychotic-formulations.json"]
    equivalences = assets["follow-up/v1/lai-equivalences.json"]
    return {
        "workflow_version": WORKFLOW,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "steps": list(STEPS),
        "definitions": {
            "conditions": list(CONDITIONS),
            "effects": {"sedation": list(SEDATION), "boolean": list(EFFECT_BOOLEANS)},
            "psychiatric_conditions": list(PSYCHIATRIC_CONDITIONS),
            "substance_categories": list(SUBSTANCE_CATEGORIES),
            "bmi_categories": list(BMI_CATEGORIES),
            "recovery": list(RECOVERY),
            "formulations": list(FORMULATIONS),
            "branch_answers": list(BRANCH_ANSWERS),
        },
        "defaults": {
            "harm": {"self": False, "others": False},
            "cssrs_recent": "no",
            "effects": "none",
            "recovery": None,
            "adherence": True,
            "basic_needs_met": True,
            "needs_observation": False,
            "lai_preference": False,
        },
        "lab_fields": {
            name: {
                "unit": spec["unit"],
                "reference": spec["reference"],
                "source": spec["source"],
                "suggestion": spec.get("suggestion"),
            }
            for name, spec in labs["fields"].items()
        },
        "formulations": formulations["entries"],
        "lai_equivalences": equivalences["equivalences"],
        "hashes": {
            "workflow_manifest_hash": loaded["hash"],
            "factor_mapping_version": manifest["factor_mapping_version"],
            "recommendation_rule_version": manifest["recommendation_rule_version"],
            "assessment_versions": manifest["assessment_versions"],
            "lexicomp_version": manifest["lexicomp_version"],
            "lexicomp_content_hash": manifest["lexicomp_content_hash"],
            "files": manifest["files"],
        },
    }
