"""Read-only proposal input readiness from the execution manifests and gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from x_insight.models.workflow_release import history_fields, resolve_workflow
from x_insight.reasoning import snapshots
from x_insight.reasoning.history_gates import history_gate_requirements


def compute_proposal_readiness(
    draft_data: Any,
    source_revision: int,
    workflow: str,
    packages: list[dict[str, Any]] | None = None,
    *,
    clozapine_eligible: bool = False,
) -> dict[str, Any]:
    """Report blockers and nonblocking uncertainty without freezing a batch.

    Unknown, omitted, null, empty-string and whitespace-only clinical inputs
    are nonblocking missing-context warnings: they allow estimation with
    missing/null projection entries and disclosed uncertainty. Explicit
    ``not_assessed``, ``Unresolved``, conflicting answers, malformed nonempty
    values, invalid packages and follow-up baseline/trial prerequisites stay
    blocking. A known false gate is execution-ready and needs no job.
    """
    if packages is None:
        packages, _ = resolve_workflow(workflow, clozapine_eligible=clozapine_eligible)
    definitions = history_fields()
    questions: list[dict[str, Any]] = []
    for package in packages:
        manifest: Mapping[str, Any] = package["manifest"]
        # Validate the same projection source allowlist as generation, even for
        # inputs that the applicability gate ultimately finds not applicable.
        snapshots.build_question_projection(manifest, draft_data, source_revision)
        status, reason = snapshots.evaluate_gate(manifest, draft_data)
        key = str(manifest["question_key"])
        question_label = key.replace("_", " ").capitalize()
        required = snapshots.applicable_required_fields(
            manifest["applicability"]["required_fields"], draft_data
        )
        values: dict[str, Any] = {}
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        answered = 0
        resolved = 0
        for path in required:
            raw = snapshots._resolve_raw_value(path, draft_data)
            values[path] = raw
            spec = definitions.get(path, {})
            if path == "synthetic/assessments/cssrs":
                spec = {"definition": "C-SSRS assessment"}
            saved = snapshots._get_history_values(draft_data).get(path, snapshots._MISSING)
            blocking, diagnostic = snapshots._diagnose_required_input(path, draft_data)
            if not blocking and diagnostic == "observed":
                answered += 1
                resolved += 1
                continue
            if isinstance(saved, str) and saved in spec.get("permitted", []):
                answered += 1
            entry = _blocker(key, question_label, path, spec, diagnostic, _input_reason(diagnostic))
            if blocking:
                blockers.append(entry)
            else:
                # Nonblocking uncertainty stays visible but never blocks start.
                warnings.append(entry)
                resolved += 1
            # Observed-but-Unresolved is handled as blocking above via diagnose.
            # Keep the explicit answered/execution-ready split: answered counts
            # permitted explicit answers (including Unknown), execution-ready
            # counts everything that allows estimation (observed + warnings).
        # Follow-up baseline and trial prerequisites can be known but still
        # invalid for execution. Share their rules with the actual gate.
        # Missing prerequisites must not be asserted as met: they stay blocking.
        blocked_paths = {entry["source_path"] for entry in blockers}
        warned_paths = {entry["source_path"] for entry in warnings}
        for field, gate_reason in history_gate_requirements(key, values):
            for path in required:
                if path.rsplit("/", 1)[-1] == field and path not in blocked_paths:
                    # A prerequisite warning (e.g. unknown baseline) becomes a
                    # blocker: relaxing missing context must not assert it met.
                    if path in warned_paths:
                        warnings = [w for w in warnings if w["source_path"] != path]
                        warned_paths.discard(path)
                        resolved -= 1
                    blockers.append(
                        _blocker(
                            key,
                            question_label,
                            path,
                            definitions.get(path, {}),
                            "gate_requirement",
                            gate_reason,
                        )
                    )
                    blocked_paths.add(path)
                    resolved -= 1
        if status == snapshots.NEEDS_CLARIFICATION and not blockers:
            blockers.append(_blocker(key, question_label, None, {}, "gate_requirement", reason))
        # A question with only warnings is executable: keep the READY gate
        # from execution, with warnings carried separately.
        questions.append(
            {
                "question_key": key,
                "question_label": question_label,
                "status": status,
                "reason": reason,
                "required_count": len(required),
                "answered_count": answered,
                "execution_ready_count": resolved,
                "blockers": blockers,
                "warnings": warnings,
            }
        )
    ready = all(question["status"] != snapshots.NEEDS_CLARIFICATION for question in questions)
    return {
        "workflow": workflow,
        "source_revision": source_revision,
        "ready": ready,
        "status": snapshots.READY if ready else snapshots.NEEDS_CLARIFICATION,
        "required_count": sum(question["required_count"] for question in questions),
        "answered_count": sum(question["answered_count"] for question in questions),
        "execution_ready_count": sum(question["execution_ready_count"] for question in questions),
        "blockers": [blocker for question in questions for blocker in question["blockers"]],
        "warnings": [warning for question in questions for warning in question["warnings"]],
        "questions": questions,
    }


def _input_reason(status: str) -> str:
    return {
        "missing": "No saved response; estimation proceeds with missing context.",
        "null": "The saved response is empty; estimation proceeds with missing context.",
        "empty": "The saved response is empty; estimation proceeds with missing context.",
        "unknown": "Unknown remains uncertain; estimation proceeds with missing context.",
        "not_assessed": "This required input has not been assessed.",
        "conflict": "Conflicting responses need clarification.",
        "invalid": "The saved response is not a permitted answer.",
        "unresolved": "This required input remains unresolved.",
    }[status]


def _blocker(
    key: str,
    question_label: str,
    path: str | None,
    spec: dict[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "question_key": key,
        "question_label": question_label,
        "source_path": path,
        "label": spec.get("definition") or question_label,
        "status": status,
        "reason": reason,
    }
