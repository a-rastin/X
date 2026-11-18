"""Versioned HTTP contract and public projection for proposal simulation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NotRequired, get_args
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    WithJsonSchema,
    model_validator,
)
from typing_extensions import TypedDict

from x_insight import contracts
from x_insight.cases import follow_up_rules, new_case_rules
from x_insight.cases import reasoning_exploration as exploration
from x_insight.cases.reasoning_paths import (
    EdgeKind,
    NodeKind,
    PathState,
    ScenarioState,
    factor_source_paths,
)
from x_insight.models import oral_antipsychotic as oral

Workflow = Literal["new-case-v1", "follow-up-v1"]
EvaluationStatus = Literal["complete", "partial", "non_model"]
ModelStatus = Literal[
    "available",
    "model_unavailable",
    "non_model",
    "no_eligible_oral_candidate",
    "direct_available",
    "direct_excluded",
    "unsupported_direct_mapping",
    "partial",
]
Terminal = Literal[
    "oral_replacement",
    "clozapine",
    "same_drug_lai",
    "physician_review",
    "continue_same",
    "reduce_dose",
    "adjunct_only",
    "oral",
    "lai",
]
Exclusion = Literal[
    "zero_probability",
    "unavailable",
    "current_main",
    "interaction_dx",
    "unsupported_direct_mapping",
]
Impact = Literal["assessment", "section", "model", "decision"]
Kind = Literal["panss", "cssrs", "boolean", "nullable_boolean", "choice"]
BannerKind = Literal[
    "no_change",
    "top_choice_changed",
    "top_probability_changed",
    "eligibility_changed",
    "decision_branch_changed",
    "assessment_changed",
    "section_changed",
    "inputs_changed",
]
PanssItemId = Literal[
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "N1",
    "N2",
    "N3",
    "N4",
    "N5",
    "N6",
    "N7",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G7",
    "G8",
    "G9",
    "G10",
    "G11",
    "G12",
    "G13",
    "G14",
    "G15",
    "G16",
]
CssrsItemId = Literal[
    "css_i1_recent",
    "css_i2_recent",
    "css_i3_recent",
    "css_i4_recent",
    "css_i5_recent",
]
SCHEMA_VERSION = "reasoning-simulation-v1"
_ORAL_IDS = tuple(f"oral:{outcome.lower()}" for outcome in oral.OUTCOMES)
Probability = Annotated[float, Field(ge=0, le=1)]
ControlId = Annotated[StrictStr, Field(min_length=1, max_length=96)]
ControlValue = exploration.SimulationValue


def _known_treatment(value: str) -> str:
    if value not in _ORAL_IDS:
        raise ValueError("Unknown oral treatment.")
    return value


def _known_factor(value: str) -> str:
    if value not in oral.PARENTS:
        raise ValueError("Unknown oral model factor.")
    return value


OralTreatmentId = Annotated[
    StrictStr,
    AfterValidator(_known_treatment),
    WithJsonSchema({"type": "string", "enum": _ORAL_IDS}),
]
FactorId = Annotated[
    StrictStr,
    AfterValidator(_known_factor),
    WithJsonSchema({"type": "string", "enum": oral.PARENTS}),
]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PanssChange(WireModel):
    kind: Literal["panss"]
    item_id: PanssItemId
    value: Annotated[StrictInt, Field(ge=1, le=7)]


class CssrsChange(WireModel):
    kind: Literal["cssrs"]
    item_id: CssrsItemId
    value: StrictBool


class BooleanChange(WireModel):
    kind: Literal["boolean"]
    control_id: ControlId
    value: StrictBool


class NullableBooleanChange(WireModel):
    kind: Literal["nullable_boolean"]
    control_id: ControlId
    value: StrictBool | None


class ChoiceChange(WireModel):
    kind: Literal["choice"]
    control_id: ControlId
    value: Annotated[StrictStr, Field(max_length=48)] | None


Change = Annotated[
    PanssChange | CssrsChange | BooleanChange | NullableBooleanChange | ChoiceChange,
    Field(discriminator="kind"),
]


class SimulationRequest(WireModel):
    schema_version: Literal["reasoning-simulation-v1"]
    baseline_proposal_id: UUID
    selected_treatment_id: OralTreatmentId | None
    client_request_id: Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]
    changes: Annotated[list[Change], Field(max_length=128)]

    @model_validator(mode="after")
    def unique_controls(self) -> SimulationRequest:
        keys = [
            change.item_id if isinstance(change, PanssChange | CssrsChange) else change.control_id
            for change in self.changes
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate reasoning control.")
        return self


class Identity(WireModel):
    schema_version: Literal["reasoning-simulation-v1"]
    algorithm_version: Literal["oral-root-prior-shapley-v1"]
    client_request_id: int
    workflow: Workflow
    baseline_proposal_id: UUID
    baseline_input_hash: str
    baseline_payload_hash: str
    content_hash: str
    model_sha256: str
    assessment_hashes: dict[str, str]
    assessment_hash_origin: Literal["historical_byte_pin", "resolved_from_pinned_versions"]
    ddi_release_id: str
    availability_snapshot_hash: str
    scenario_hash: str
    selected_treatment_id: OralTreatmentId | None


class Control(WireModel):
    control_id: str
    kind: Kind
    label_key: str
    source_path: str
    patient_value: ControlValue
    scenario_value: ControlValue
    choices: list[ControlValue]
    minimum: int | None
    maximum: int | None
    active: bool
    affected_outputs: list[Impact]


class ProbabilityRow(WireModel):
    treatment_id: OralTreatmentId
    probability: Probability
    raw_rank: int
    eligible_rank: int | None
    exclusion_reasons: list[Exclusion]


class Direct(WireModel):
    treatment_id: str | None
    exclusion_reasons: list[Exclusion]


class Decision(WireModel):
    terminal: Terminal | None
    reason: str | None


class Factor(WireModel):
    value: bool | None
    source: str
    simulated_override: list[str]


class Assessment(WireModel):
    status: Literal["unanswered", "partial", "complete", "not_assessed"]
    definition_version: str
    missing_item_ids: list[str]
    item_errors: dict[str, list[str]]
    scores: dict[str, float | None]
    findings: dict[str, JsonValue]


class Assessments(WireModel):
    panss: Assessment
    cssrs: Assessment


class Issue(WireModel):
    field: str
    message_key: str


class Side(WireModel):
    model_status: ModelStatus
    original_decision: Decision | None
    final_decision: Decision | None
    distribution: list[ProbabilityRow]
    candidates: list[ProbabilityRow]
    direct: Direct | None
    sections: dict[str, JsonValue]
    assessments: Assessments
    factors: dict[FactorId, Factor]


class ComparisonRow(WireModel):
    treatment_id: OralTreatmentId
    patient_probability: Probability | None
    scenario_probability: Probability | None
    patient_raw_rank: int | None
    scenario_raw_rank: int | None
    patient_eligible_rank: int | None
    scenario_eligible_rank: int | None
    patient_exclusion_reasons: list[Exclusion]
    scenario_exclusion_reasons: list[Exclusion]
    probability_delta: float | None


class FactorAttribution(WireModel):
    factor_id: FactorId
    contribution: float
    source: str


class AttributionSide(WireModel):
    posterior_probability: Probability
    contributions: list[FactorAttribution]
    reconstruction_residual: float


class Attribution(WireModel):
    treatment_id: OralTreatmentId
    model_reference_probability: Probability
    patient: AttributionSide | None
    scenario: AttributionSide | None


class Selection(WireModel):
    top: ProbabilityRow | None
    tied_eligible_ids: list[OralTreatmentId]
    candidate_ids: list[OralTreatmentId]
    original_decision: Decision | None
    final_decision: Decision | None
    direct: Direct | None


class Delta[T](WireModel):
    patient: T | None
    scenario: T | None


class SelectionPair(WireModel):
    patient: Selection
    scenario: Selection


class MessageParameters(WireModel):
    changed_control_ids: list[str]
    patient_top: ProbabilityRow | None
    scenario_top: ProbabilityRow | None


class Diff(WireModel):
    raw_inputs: dict[str, Delta[ControlValue]]
    factors: dict[FactorId, Delta[bool]]
    assessments: dict[Literal["panss", "cssrs"], Delta[Assessment]]
    sections: dict[str, Delta[JsonValue]]
    probabilities: dict[OralTreatmentId, Delta[Probability]]
    exclusions: dict[OralTreatmentId, Delta[list[Exclusion]]]
    selection: SelectionPair
    banner_kind: BannerKind
    message_parameters: MessageParameters


class Provenance(TypedDict):
    kind: Literal[
        "source", "recorded_override", "simulated_override", "workflow_default", "derived"
    ]
    path: NotRequired[str]
    source: NotRequired[str]
    paths: NotRequired[list[str]]
    overrides: NotRequired[list[str]]


class Observation(WireModel):
    value: JsonValue
    provenance: Provenance | None


class GraphNode(WireModel):
    id: str
    kind: NodeKind
    subject: str
    patient: Observation | None
    scenario: Observation | None
    path_state: PathState
    scenario_state: ScenarioState


class GraphEdge(WireModel):
    id: str
    kind: EdgeKind
    source: str
    target: str
    path_state: PathState
    scenario_state: ScenarioState


class ActiveGraph(WireModel):
    selected_treatment_id: OralTreatmentId | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    pruning_counts: dict[str, int]


class Evaluation(WireModel):
    status: EvaluationStatus
    issues: list[Issue]
    unevaluated_reason: ModelStatus | None


class EvaluationPair(WireModel):
    patient: Evaluation
    scenario: Evaluation


class SimulationResponse(WireModel):
    identity: Identity
    controls: list[Control]
    patient: Side
    scenario: Side
    comparison_rows: list[ComparisonRow]
    selected_attribution: Attribution | None
    diff: Diff
    active_graph: ActiveGraph
    evaluation: EvaluationPair


@dataclass(frozen=True)
class _SourceControl:
    path: str
    kind: Kind
    choices: tuple[ControlValue, ...]


def _control_kind(choices: tuple[ControlValue, ...]) -> Kind:
    if choices == (False, True):
        return "boolean"
    if choices == (False, True, None):
        return "nullable_boolean"
    return "choice"


def _controls(workflow: str) -> dict[str, _SourceControl]:
    return {
        path.split(".", 1)[1]
        .removeprefix("history.")
        .removeprefix("considerations."): _SourceControl(path, _control_kind(values), values)
        for path, values in exploration.source_controls(workflow).items()
    }


def _invalid_change(field: str, reason: str) -> contracts.ContractError:
    return contracts.ContractError(
        422, "VALIDATION_FAILED", "Unsupported reasoning change.", {field: [reason]}
    )


def source_overrides(request: SimulationRequest, workflow: str) -> dict[str, ControlValue]:
    allowed = _controls(workflow)
    overrides: dict[str, ControlValue] = {}
    for change in request.changes:
        if isinstance(change, PanssChange | CssrsChange):
            path = f"{change.kind}.answers.{change.item_id}"
        else:
            control = allowed.get(change.control_id)
            if (
                control is None
                or control.kind != change.kind
                or change.value not in control.choices
            ):
                raise _invalid_change(change.control_id, "Unsupported control, kind or value.")
            path = control.path
        overrides[path] = change.value
    return overrides


def _raw_value(side: exploration.Side, path: str) -> ControlValue:
    value: Any = side["state"]["raw"]
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    if path.startswith("cssrs.") and value in ("yes", "no"):
        return value == "yes"
    return value


def _affected_outputs(path: str, model_paths: set[str]) -> list[Impact]:
    if path.startswith("panss."):
        return ["assessment"]
    if path.startswith("cssrs."):
        return (
            ["assessment", "section"] if path.endswith(("4_recent", "5_recent")) else ["assessment"]
        )
    impacts: list[Impact] = []
    if path in model_paths or path.endswith((".bmi.manual_category", ".substances.marijuana")):
        impacts.append("model")
    if path.startswith(("follow_up.branches.", "follow_up.effects.")) or path.endswith(
        (".lai", ".lai_preference", ".recovery", ".adherence")
    ):
        impacts.append("decision")
    if any(
        path in paths
        for paths in (
            *new_case_rules.SECTION_SOURCES.values(),
            *follow_up_rules.SECTION_SOURCES.values(),
        )
    ) or path.endswith(
        (
            ".lai",
            ".breastfeeding",
            ".basic_needs_met",
            ".needs_observation",
            ".harm.self",
            ".harm.others",
            ".conditions.pregnant.manual",
        )
    ):
        impacts.append("section")
    return impacts


def _control_descriptors(
    workflow: str, patient: exploration.Side, scenario: exploration.Side
) -> tuple[list[Control], dict[str, str]]:
    controls = _controls(workflow)
    assessments: tuple[tuple[Kind, Any, tuple[ControlValue, ...]], ...] = (
        ("panss", PanssItemId, ()),
        ("cssrs", CssrsItemId, (False, True)),
    )
    for kind, item_type, choices in assessments:
        controls.update(
            {
                item: _SourceControl(f"{kind}.answers.{item}", kind, choices)
                for item in get_args(item_type)
            }
        )
    reached = scenario["active_questions"]
    model_paths: set[str] = set()
    for side in (patient, scenario):
        for name, factor in side["factors"].items():
            paths, manual_path = factor_source_paths(
                side["state"], workflow, name, factor["source"]
            )
            model_paths.update(paths)
            if manual_path is not None:
                model_paths.add(manual_path)
    descriptors = []
    for control_id, control in controls.items():
        path, kind = control.path, control.kind
        active = not path.startswith("follow_up.branches.") or path.rsplit(".", 1)[1] in reached
        if path.rsplit(".", 1)[-1] in ("marijuana", "other", "opioids", "methamphetamine"):
            active = _raw_value(scenario, path.rsplit(".", 1)[0] + ".present") is True
        if path.endswith(".bmi.manual_category"):
            active = (
                _raw_value(scenario, path.removesuffix(".manual_category") + ".source") == "manual"
            )
        descriptors.append(
            Control(
                control_id=control_id,
                kind=kind,
                label_key=f"reasoning.controls.{control_id}",
                source_path=path,
                patient_value=_raw_value(patient, path),
                scenario_value=_raw_value(scenario, path),
                choices=list(control.choices),
                minimum=1 if kind == "panss" else None,
                maximum=7 if kind == "panss" else None,
                active=active,
                affected_outputs=_affected_outputs(path, model_paths),
            )
        )
    return descriptors, {control.path: control_id for control_id, control in controls.items()}


def _decision(data: Mapping[str, Any] | None) -> Decision | None:
    if data is None:
        return None
    return Decision(terminal=data.get("terminal"), reason=data.get("reason"))


def _project_side(side: exploration.Side) -> Side:
    ranking = side["ranking"] or {}
    distribution = []
    eligible_rank = 0
    for row in ranking.get("distribution", ()):
        reasons = row.get("removal_reasons")
        if reasons is None:
            reasons = [row["removed_reason"]] if row["removed_reason"] else []
        if not reasons:
            eligible_rank += 1
        distribution.append(
            ProbabilityRow(
                treatment_id=row["drug_id"],
                probability=row["probability"],
                raw_rank=row["rank"],
                eligible_rank=None if reasons else eligible_rank,
                exclusion_reasons=reasons,
            )
        )
    indexed = {row.treatment_id: row for row in distribution}
    direct = ranking.get("direct")
    original = ranking.get("original_decision", side["state"].get("decision"))
    final = ranking.get("final_decision")
    if original is None:
        route = side["sections"].get("antipsychotic_route")
        if route is not None:
            original = {"terminal": route["value"]}
            final = original if side["ranking"] is not None else None
    return Side(
        model_status=ranking.get("status", "partial"),
        distribution=distribution,
        candidates=[indexed[row["drug_id"]] for row in ranking.get("candidates", ())],
        original_decision=_decision(original),
        final_decision=_decision(final),
        direct=Direct(
            treatment_id=direct.get("drug_id"), exclusion_reasons=direct["removal_reasons"]
        )
        if direct is not None
        else None,
        sections=jsonable_encoder(side["sections"]),
        assessments=Assessments.model_validate(side["assessments"]),
        factors={
            name: Factor(
                value=factor["value"],
                source=factor["source"],
                simulated_override=factor.get("simulated_override", []),
            )
            for name, factor in side["factors"].items()
        },
    )


def _comparison_rows(patient: Side, scenario: Side) -> list[ComparisonRow]:
    previous = {row.treatment_id: row for row in patient.distribution}
    current = {row.treatment_id: row for row in scenario.distribution}
    result = []
    for treatment in previous | current:
        p, s = previous.get(treatment), current.get(treatment)
        result.append(
            ComparisonRow(
                treatment_id=treatment,
                patient_probability=p.probability if p else None,
                scenario_probability=s.probability if s else None,
                patient_raw_rank=p.raw_rank if p else None,
                scenario_raw_rank=s.raw_rank if s else None,
                patient_eligible_rank=p.eligible_rank if p else None,
                scenario_eligible_rank=s.eligible_rank if s else None,
                patient_exclusion_reasons=p.exclusion_reasons if p else [],
                scenario_exclusion_reasons=s.exclusion_reasons if s else [],
                probability_delta=s.probability - p.probability if p and s else None,
            )
        )
    return result


def _selection(side: Side) -> Selection:
    top = side.candidates[0] if side.candidates else None
    return Selection(
        top=top,
        tied_eligible_ids=[
            row.treatment_id
            for row in side.distribution
            if top and row.eligible_rank is not None and row.probability == top.probability
        ],
        candidate_ids=[candidate.treatment_id for candidate in side.candidates],
        original_decision=side.original_decision,
        final_decision=side.final_decision,
        direct=side.direct,
    )


def _banner(diff: Mapping[str, Any], patient: Selection, scenario: Selection) -> BannerKind:
    def terminals(selection: Selection) -> tuple[Terminal | None, Terminal | None]:
        return (
            selection.original_decision.terminal if selection.original_decision else None,
            selection.final_decision.terminal if selection.final_decision else None,
        )

    p, s = patient.top, scenario.top
    if terminals(patient) != terminals(scenario):
        return "decision_branch_changed"
    if (p.treatment_id if p else None) != (s.treatment_id if s else None):
        return "top_choice_changed"
    if diff["exclusions"] or patient.direct != scenario.direct:
        return "eligibility_changed"
    if p and s and p.probability != s.probability:
        return "top_probability_changed"
    if diff["sections"]:
        return "section_changed"
    if diff["assessments"]:
        return "assessment_changed"
    if diff["raw_inputs"]:
        return "inputs_changed"
    return "no_change"


def _project_diff(
    internal: Mapping[str, Any], ids: Mapping[str, str], patient: Side, scenario: Side
) -> Diff:
    previous, current = _selection(patient), _selection(scenario)
    raw = {
        ids[path]: {
            key: value == "yes" if path.startswith("cssrs.") and value in ("yes", "no") else value
            for key, value in pair.items()
        }
        for path, pair in internal["raw_inputs"].items()
    }
    return Diff.model_validate(
        {
            **{
                key: jsonable_encoder(internal[key])
                for key in ("factors", "assessments", "sections", "probabilities", "exclusions")
            },
            "raw_inputs": raw,
            "selection": SelectionPair(patient=previous, scenario=current),
            "banner_kind": _banner(internal, previous, current),
            "message_parameters": MessageParameters(
                changed_control_ids=list(raw), patient_top=previous.top, scenario_top=current.top
            ),
        }
    )


def _project_attribution(
    result: exploration.Exploration, patient: Side, scenario: Side
) -> Attribution | None:
    record = result["selected_attribution"]
    if record is None:
        return None

    def project(data: Mapping[str, Any] | None, side: Side) -> AttributionSide | None:
        if data is None:
            return None
        return AttributionSide(
            posterior_probability=data["posterior_probability"],
            contributions=[
                FactorAttribution(
                    factor_id=name, contribution=value, source=side.factors[name].source
                )
                for name, value in data["contributions"]
            ],
            reconstruction_residual=data["reconstruction_residual"],
        )

    reference = record["patient"] or record["scenario"]
    return Attribution(
        treatment_id=record["treatment_id"],
        model_reference_probability=reference["model_reference_probability"],
        patient=project(record["patient"], patient),
        scenario=project(record["scenario"], scenario),
    )


def _project_graph(result: exploration.Exploration) -> ActiveGraph:
    graph = jsonable_encoder(result["active_graph"])
    for node in graph["nodes"]:
        for side in ("patient", "scenario"):
            observation = node[side]
            if observation is not None and observation["provenance"] is not None:
                observation["provenance"].pop("manual_actor_id", None)
                observation["provenance"].pop("manual_recorded_at", None)
    return ActiveGraph.model_validate(graph)


def _evaluation(internal: exploration.Side, side: Side) -> Evaluation:
    return Evaluation(
        status=internal["status"],
        issues=[
            Issue(field=issue["field"], message_key=issue["message_key"])
            for issue in internal["issues"]
        ],
        unevaluated_reason=None if side.distribution else side.model_status,
    )


def simulation_response(
    request: SimulationRequest, proposal: Mapping[str, Any], result: exploration.Exploration
) -> SimulationResponse:
    patient = _project_side(result["patient"])
    scenario = _project_side(result["scenario"])
    changes = result["diff"]["raw_inputs"]
    if (
        request.selected_treatment_id is None
        and changes
        and patient.distribution
        and scenario.distribution
    ):
        raise _invalid_change(
            "selected_treatment_id", "Select a treatment for this modeled scenario."
        )
    pinned = result["identity"]
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": oral.ATTRIBUTION_ALGORITHM,
        "workflow": pinned["workflow"],
        "baseline_proposal_id": pinned["proposal_id"],
        "baseline_input_hash": pinned["input_hash"],
        "baseline_payload_hash": pinned["payload_hash"],
        "content_hash": pinned["content_hash"],
        "model_sha256": pinned["model_sha256"],
        "assessment_hashes": pinned["assessment_hashes"],
        "assessment_hash_origin": pinned["assessment_hash_origin"],
        "ddi_release_id": pinned["ddi_release_id"],
        "availability_snapshot_hash": contracts.canonical_hash(
            proposal["payload"]["availability_snapshot"]
        ),
    }
    scenario_hash = contracts.canonical_hash(
        {
            **baseline,
            "overrides": {path: change["scenario"] for path, change in changes.items()},
        }
    )
    controls, source_ids = _control_descriptors(
        pinned["workflow"], result["patient"], result["scenario"]
    )
    return SimulationResponse(
        identity=Identity.model_validate(
            {
                **baseline,
                "client_request_id": request.client_request_id,
                "selected_treatment_id": result["active_graph"]["selected_treatment_id"],
                "scenario_hash": scenario_hash,
            }
        ),
        controls=controls,
        patient=patient,
        scenario=scenario,
        comparison_rows=_comparison_rows(patient, scenario),
        selected_attribution=_project_attribution(result, patient, scenario),
        diff=_project_diff(result["diff"], source_ids, patient, scenario),
        active_graph=_project_graph(result),
        evaluation=EvaluationPair(
            patient=_evaluation(result["patient"], patient),
            scenario=_evaluation(result["scenario"], scenario),
        ),
    )
