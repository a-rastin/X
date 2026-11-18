"""Build selected reasoning pathways from evaluated inputs and their recorded traces."""

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from x_insight.cases import follow_up_rules, new_case_rules
from x_insight.models import oral_antipsychotic

EdgeKind = Literal["input_derivation", "bn_dependency", "rule_dependency", "candidate_filter"]
NodeKind = Literal["source", "factor", "assessment", "rule", "section", "treatment", "selection"]
PathState = Literal["patient", "scenario", "both"]
ScenarioState = Literal["active", "superseded"]


class NodeObservation(TypedDict):
    value: Any
    provenance: dict[str, Any] | None


class GraphNode(TypedDict):
    id: str
    kind: NodeKind
    subject: str
    patient: NodeObservation | None
    scenario: NodeObservation | None
    path_state: PathState
    scenario_state: ScenarioState


class GraphEdge(TypedDict):
    id: str
    kind: EdgeKind
    source: str
    target: str
    path_state: PathState
    scenario_state: ScenarioState


class ActiveGraph(TypedDict):
    selected_treatment_id: str | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    pruning_counts: dict[str, int]


class _GraphSide:
    def __init__(self, raw: Mapping[str, Any], overrides: Mapping[str, Any]) -> None:
        self.raw = raw
        self.overrides = overrides
        self.nodes: dict[str, tuple[NodeKind, str, NodeObservation]] = {}
        self.edges: dict[str, tuple[EdgeKind, str, str]] = {}

    def node(
        self, kind: NodeKind, subject: str, value: Any, provenance: dict[str, Any] | None = None
    ) -> str:
        node_id = f"{kind}:{subject}"
        self.nodes[node_id] = (kind, subject, {"value": value, "provenance": provenance})
        return node_id

    def source(self, path: str, *, default: Any = None) -> str:
        value = _value_at(self.raw, path, default)
        provenance: dict[str, Any] = {"kind": "source", "path": path}
        if path == "medications.entries":
            value = tuple(entry["catalog_drug_id"] for entry in value or ())
        if path in self.overrides:
            provenance["kind"] = "simulated_override"
        elif path.startswith("workflow_default."):
            provenance["kind"] = "workflow_default"
        elif path.endswith(".manual") and value is not None:
            answer = _value_at(self.raw, path.removesuffix(".manual"), {})
            provenance.update(
                kind="recorded_override",
                manual_actor_id=answer.get("manual_actor_id"),
                manual_recorded_at=answer.get("manual_recorded_at"),
            )
        return self.node("source", path, value, provenance)

    def edge(self, kind: EdgeKind, source: str, target: str) -> None:
        self.edges[f"{kind}:{source}->{target}"] = (kind, source, target)

    def connect(self, kind: EdgeKind, paths: tuple[str, ...], target: str) -> None:
        for path in paths:
            self.edge(kind, self.source(path), target)


def _value_at(raw: Mapping[str, Any], path: str, default: Any = None) -> Any:
    value: Any = raw
    for segment in path.split("."):
        if not isinstance(value, Mapping) or segment not in value:
            return default
        value = value[segment]
    return value


def _flag_paths(
    raw: Mapping[str, Any], prefix: str, name: str, flag: Mapping[str, Any]
) -> tuple[tuple[str, ...], str]:
    root = f"{prefix}.conditions.{name}"
    if prefix == "new_case" and name == "orthostatic_risk":
        root = "new_case.considerations.orthostatic_risk"
    manual_path = root + ".manual"
    if _value_at(raw, manual_path) is not None:
        return (manual_path,), manual_path

    findings = []
    for finding in flag.get("findings", ()):
        if finding == "heart_failure":
            findings.append(prefix + ".heart_failure")
        elif finding in _value_at(raw, prefix + ".labs.values", {}):
            findings.append(f"{prefix}.labs.values.{finding}")
        elif name == "orthostatic_risk":
            findings.append("medications.entries")
    if findings:
        return tuple(sorted(set(findings))), manual_path
    if (
        prefix == "follow_up.history"
        and name == "pregnant"
        and _value_at(raw, prefix + ".pregnant") is not None
    ):
        return (prefix + ".pregnant",), manual_path
    if _value_at(raw, root + ".inherited") is not None:
        return (root + ".inherited",), manual_path
    if prefix == "follow_up.history" and name == "hyperprolactinemia" and flag.get("established"):
        return (prefix + ".labs.values.prolactin",), manual_path
    # No current finding or inherited value established this factor.
    # Keep the automatic source gate, including when its value is False.
    paths: tuple[str, ...] = (prefix + ".labs.present", manual_path)
    if name == "orthostatic_risk":
        paths += (prefix + ".heart_failure", "medications.entries")
    return paths, manual_path


def factor_source_paths(
    state: Mapping[str, Any], workflow: str, name: str, source: str
) -> tuple[tuple[str, ...], str | None]:
    """Return active raw-source paths and the manual field, when applicable."""
    is_new = workflow == "new-case-v1"
    raw = state["raw"]
    history = "new_case" if is_new else "follow_up.history"
    if source.startswith("flags."):
        flag_name = source.split(".")[1]
        return _flag_paths(raw, history, flag_name, state["flags"][flag_name])
    if name == "Marijuana_Smoking":
        paths: tuple[str, ...] = (history + ".substances.present",)
        if _value_at(raw, paths[0]):
            paths += (history + ".substances.marijuana",)
        return paths, None
    if name == "Weight_Gain_Risk":
        bmi = history + ".bmi"
        paths = (bmi + ".source",)
        source_kind = _value_at(raw, paths[0])
        if source_kind == "manual":
            paths += (bmi + ".manual_category",)
        elif source_kind == "calculated":
            paths += (bmi + ".height_cm", bmi + ".weight_kg")
        return paths, None
    if source == "workflow_default":
        return (f"workflow_default.{name}",), None
    return (source if is_new else f"follow_up.{source}",), None


def _add_model(
    graph: _GraphSide,
    state: Mapping[str, Any],
    workflow: str,
    selected: Mapping[str, Any],
    parents: tuple[str, ...],
) -> str:
    treatment = graph.node("treatment", selected["drug_id"], selected["probability"])
    is_new = workflow == "new-case-v1"
    for name in parents:
        if is_new:
            value, source = state["evidence"][name], state["evidence_sources"][name]
        else:
            factor = state["model_factors"][name]
            value, source = factor["value"], factor["source"]
        paths, manual_path = factor_source_paths(state, workflow, name, source)
        changed = sorted(graph.overrides.keys() & set((*paths, manual_path)))
        provenance = {
            "kind": "simulated_override" if changed else "derived",
            "source": source,
            "paths": paths,
            "overrides": changed,
        }
        factor_node = graph.node("factor", name, value, provenance)
        for path in paths:
            graph.edge("input_derivation", graph.source(path, default=value), factor_node)
        graph.edge("bn_dependency", factor_node, treatment)
    return treatment


def _add_follow_up_decision(graph: _GraphSide, decision: Mapping[str, Any]) -> str:
    reason = decision["reason"]
    terminal = decision["terminal"]
    target = graph.node(
        "rule", f"terminal:{terminal if terminal is not None else 'pending'}", terminal
    )
    branch = graph.node("rule", f"decision:{reason}", reason)
    graph.edge("rule_dependency", branch, target)

    reached = set(decision["reached_questions"])
    dystonia = reached & {"dystonia_resolved", "dose_reduction_possible"}
    if "n15_n17" not in decision["trace"]:
        dystonia.clear()
    branch_questions = reached - dystonia
    if reason == "n4_n5":
        branch_questions.add("dose_reduction_possible")
    elif reason == "dystonia_response_required":
        branch_questions.add("dystonia_resolved")
    answer_paths = tuple(f"follow_up.branches.{name}" for name in sorted(branch_questions))
    paths = tuple(
        path if path.startswith("medications.") else f"follow_up.{path}"
        for path in follow_up_rules.DECISION_REASON_SOURCES.get(reason, ())
    )
    graph.connect("rule_dependency", paths + answer_paths, branch)
    for trace in decision["trace"]:
        if trace == reason:
            continue
        rule = graph.node("rule", f"decision:{trace}", trace)
        if trace.startswith("effect_"):
            effect = trace.removeprefix("effect_")
            graph.connect("rule_dependency", (f"follow_up.effects.{effect}",), rule)
            if effect == "acute_dystonia":
                graph.connect("rule_dependency", ("follow_up.branches.dystonia_resolved",), rule)
            graph.edge("rule_dependency", rule, branch)
        else:
            paths = tuple(
                f"follow_up.{path}" for path in follow_up_rules.DECISION_REASON_SOURCES[trace]
            )
            if trace == "n15_n17":
                paths += tuple(f"follow_up.branches.{name}" for name in sorted(dystonia))
            graph.connect("rule_dependency", paths, rule)
            adjunct = graph.node("selection", "adjuncts", decision["adjuncts"])
            graph.edge("rule_dependency", rule, adjunct)
            if reason == "controlled_effect_terminal":
                graph.edge("rule_dependency", rule, branch)
    return target


def _add_care_setting(graph: _GraphSide, state: Mapping[str, Any], workflow: str) -> None:
    care = state["sections"]["care_setting"]
    target = graph.node("section", "care_setting", care.get("setting", care.get("value")))
    sources = (
        new_case_rules.CARE_SETTING_SOURCES
        if workflow == "new-case-v1"
        else follow_up_rules.CARE_SETTING_SOURCES
    )
    for reason in care["reasons"]:
        rule = graph.node("rule", f"care_setting:{reason}", reason)
        graph.connect("rule_dependency", (sources[reason],), rule)
        graph.edge("rule_dependency", rule, target)


def _add_changed_sections(
    graph: _GraphSide,
    state: Mapping[str, Any],
    ranking: Mapping[str, Any] | None,
    workflow: str,
    changed: set[str],
    decision: str,
    selection: str | None,
) -> None:
    is_new = workflow == "new-case-v1"
    rules = new_case_rules if is_new else follow_up_rules
    history = "new_case" if is_new else "follow_up.history"
    sections = (ranking or {}).get("sections", state["sections"])
    if "care_setting" in changed:
        _add_care_setting(graph, state, workflow)
    if "next_visit" in changed:
        target = graph.node("section", "next_visit", sections["next_visit"])
        graph.edge("rule_dependency", selection if selection and not is_new else decision, target)
    for name in sorted(changed & (rules.SECTION_SOURCES.keys() | rules.PREGNANCY_SECTIONS)):
        target = graph.node("section", name, sections[name])
        paths = rules.SECTION_SOURCES.get(name, ())
        psychiatric = history + ".psychiatric.present"
        if name == "antidepressant" and not _value_at(state["raw"], psychiatric):
            paths = (psychiatric,)
        graph.connect("rule_dependency", paths, target)
        if name in rules.PREGNANCY_SECTIONS:
            paths, manual_path = _flag_paths(
                state["raw"], history, "pregnant", state["flags"]["pregnant"]
            )
            if manual_path in graph.overrides and manual_path not in paths:
                paths += (manual_path,)
            graph.connect("rule_dependency", paths, target)
    if not is_new and "actions" in changed:
        target = graph.node("section", "actions", sections["actions"])
        for name in sorted(changed & {"pregnancy", "breastfeeding", "contraception"}):
            graph.edge("rule_dependency", f"section:{name}", target)


def _add_filters(graph: _GraphSide, row: Mapping[str, Any], treatment: str, selection: str) -> bool:
    single_reason = row.get("removed_reason")
    reasons = row.get("removal_reasons") or ([single_reason] if single_reason else [])
    for reason in reasons:
        rule = graph.node("rule", f"filter:{row['drug_id']}:{reason}", reason)
        graph.edge("candidate_filter", treatment, rule)
        graph.edge("candidate_filter", rule, selection)
    return bool(reasons)


def _add_selection(
    graph: _GraphSide,
    ranking: Mapping[str, Any],
    decision: str,
    selected: Mapping[str, Any] | None,
    treatment: str | None,
) -> str:
    direct = ranking.get("direct")
    candidates = ranking["candidates"]
    final_drug = (
        candidates[0]["drug_id"]
        if candidates
        else direct["drug_id"]
        if direct and ranking["status"] == "direct_available"
        else None
    )
    final = ranking.get("final_decision", {})
    selection = graph.node(
        "selection",
        "final",
        {"drug_id": final_drug, "terminal": final.get("terminal"), "status": ranking["status"]},
    )
    graph.edge("rule_dependency", decision, selection)
    if selected is not None and treatment is not None:
        eligible_rank = next(
            (
                index
                for index, candidate in enumerate(candidates, 1)
                if candidate["drug_id"] == selected["drug_id"]
            ),
            None,
        )
        candidate = graph.node("selection", f"eligible:{selected['drug_id']}", eligible_rank)
        if not _add_filters(graph, selected, treatment, candidate):
            graph.edge("candidate_filter", treatment, candidate)
        if eligible_rank == 1 or not candidates:
            graph.edge("candidate_filter", candidate, selection)
    if (
        final_drug is not None
        and candidates
        and (selected is None or final_drug != selected["drug_id"])
    ):
        top = graph.node("treatment", final_drug, candidates[0]["probability"])
        graph.edge("candidate_filter", top, selection)
    if direct is not None and direct["drug_id"] is not None:
        choice = graph.node("treatment", direct["drug_id"], None)
        graph.edge("rule_dependency", decision, choice)
        if not _add_filters(graph, direct, choice, selection):
            graph.edge("candidate_filter", choice, selection)
    elif direct is not None:
        # A missing direct mapping has no drug ID but still executes a filter.
        _add_filters(graph, direct, decision, selection)
    return selection


def _build_side(
    workflow: str,
    state: Mapping[str, Any],
    ranking: Mapping[str, Any] | None,
    selected: Mapping[str, Any] | None,
    parents: tuple[str, ...],
    assessment_paths: Mapping[str, tuple[str, ...]],
    assessments: Mapping[str, Any],
    sections_changed: set[str],
    overrides: Mapping[str, Any],
) -> _GraphSide:
    graph = _GraphSide(state["raw"], overrides)
    if workflow == "follow-up-v1":
        decision = _add_follow_up_decision(graph, state["decision"])
    else:
        route = state["sections"]["antipsychotic_route"]["value"]
        decision = graph.node("rule", f"antipsychotic_route:{route}", route)
        graph.connect("rule_dependency", ("new_case.considerations.lai",), decision)
    treatment = _add_model(graph, state, workflow, selected, parents) if selected else None
    if treatment is not None:
        graph.edge("rule_dependency", decision, treatment)
    selection = (
        _add_selection(graph, ranking, decision, selected, treatment)
        if ranking is not None
        else None
    )
    _add_changed_sections(graph, state, ranking, workflow, sections_changed, decision, selection)
    for name, paths in assessment_paths.items():
        outcome = assessments.get(name)
        target = graph.node("assessment", name, outcome)
        if outcome is None or outcome["status"] != "not_assessed":
            graph.connect("rule_dependency", paths, target)
    return graph


def _membership(patient: bool, scenario: bool) -> tuple[PathState, ScenarioState]:
    if not scenario:
        return "patient", "superseded"
    return ("both" if patient else "scenario"), "active"


def _selected_row(
    ranking: Mapping[str, Any] | None, treatment_id: str | None
) -> dict[str, Any] | None:
    return next(
        (row for row in (ranking or {}).get("distribution", ()) if row["drug_id"] == treatment_id),
        None,
    )


def build_active_graph(
    *,
    workflow: str,
    patient_state: Mapping[str, Any],
    scenario_state: Mapping[str, Any],
    patient_ranking: Mapping[str, Any] | None,
    scenario_ranking: Mapping[str, Any] | None,
    selected_treatment_id: str | None,
    model_bytes: bytes | None,
    model_sha256: str,
    overrides: Mapping[str, Any],
    patient_assessments: Mapping[str, Any] | None = None,
    scenario_assessments: Mapping[str, Any] | None = None,
) -> ActiveGraph:
    """Return the deterministic union of model, selection and executed rule paths."""
    if workflow not in ("new-case-v1", "follow-up-v1"):
        raise ValueError("Unsupported reasoning workflow")
    patient_selected = _selected_row(patient_ranking, selected_treatment_id)
    scenario_selected = _selected_row(scenario_ranking, selected_treatment_id)
    parents: tuple[str, ...] = ()
    if patient_selected is not None or scenario_selected is not None:
        if model_bytes is None or selected_treatment_id is None:
            raise ValueError("A selected model outcome requires the pinned oral model")
        parents = oral_antipsychotic.dependent_parents(
            model_bytes, model_sha256, selected_treatment_id
        )
    assessment_paths = {
        name: tuple(sorted(path for path in overrides if path.startswith(f"{name}.answers.")))
        for name in ("panss", "cssrs")
    }
    assessment_paths = {name: paths for name, paths in assessment_paths.items() if paths}
    patient_sections = (patient_ranking or {}).get("sections", patient_state["sections"])
    scenario_sections = (scenario_ranking or {}).get("sections", scenario_state["sections"])
    sections_changed = {
        name
        for name in patient_sections.keys() & scenario_sections.keys()
        if patient_sections[name] != scenario_sections[name]
    }
    patient = _build_side(
        workflow,
        patient_state,
        patient_ranking,
        patient_selected,
        parents,
        assessment_paths,
        patient_assessments or {},
        sections_changed,
        {},
    )
    scenario = _build_side(
        workflow,
        scenario_state,
        scenario_ranking,
        scenario_selected,
        parents,
        assessment_paths,
        scenario_assessments or {},
        sections_changed,
        overrides,
    )
    nodes: list[GraphNode] = []
    for node_id in sorted(patient.nodes.keys() | scenario.nodes.keys()):
        p_node, s_node = patient.nodes.get(node_id), scenario.nodes.get(node_id)
        node = p_node or s_node
        assert node is not None
        kind, subject, _ = node
        path_state, scenario_path = _membership(p_node is not None, s_node is not None)
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "subject": subject,
                "patient": p_node[2] if p_node else None,
                "scenario": s_node[2] if s_node else None,
                "path_state": path_state,
                "scenario_state": scenario_path,
            }
        )

    edges: list[GraphEdge] = []
    all_edges = patient.edges | scenario.edges
    for edge_id in sorted(all_edges):
        edge_kind, source, target = all_edges[edge_id]
        path_state, scenario_path = _membership(edge_id in patient.edges, edge_id in scenario.edges)
        edges.append(
            {
                "id": edge_id,
                "kind": edge_kind,
                "source": source,
                "target": target,
                "path_state": path_state,
                "scenario_state": scenario_path,
            }
        )
    return {
        "selected_treatment_id": selected_treatment_id,
        "nodes": nodes,
        "edges": edges,
        "pruning_counts": {
            "patient_non_dependent_model_factors": (
                len(oral_antipsychotic.PARENTS) - len(parents) if patient_selected else 0
            ),
            "scenario_non_dependent_model_factors": (
                len(oral_antipsychotic.PARENTS) - len(parents) if scenario_selected else 0
            ),
        },
    }
