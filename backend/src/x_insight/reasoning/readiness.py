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
) -> dict[str, Any]:
    """Report every blocker without freezing a batch or changing any saved fact.

    Required-input uncertainty is checked before false applicability, just as
    execution does. A known false gate is execution-ready and needs no job.
    Optional packages are the immutable packages of a run being retried.
    """
    if packages is None:
        packages, _ = resolve_workflow(workflow)
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
        required = manifest["applicability"]["required_fields"]
        values: dict[str, Any] = {}
        blockers: list[dict[str, Any]] = []
        answered = 0
        resolved = 0
        for path in required:
            raw = snapshots._resolve_raw_value(path, draft_data)
            values[path] = raw
            input_status, _ = snapshots._typed_status(raw)
            spec = definitions.get(path, {})
            saved = snapshots._get_history_values(draft_data).get(path, snapshots._MISSING)
            if input_status == "observed" and raw != "Unresolved":
                answered += 1
                resolved += 1
                continue
            if raw == "Unresolved":
                input_status = "unresolved"
            elif input_status == "missing":
                if saved is None:
                    input_status = "null"
                elif saved == "unknown":
                    input_status = "unknown"
                elif not isinstance(saved, snapshots._MissingType):
                    input_status = "invalid"
            if isinstance(saved, str) and saved in spec.get("permitted", []):
                answered += 1
            blockers.append(
                _blocker(key, question_label, path, spec, input_status, _input_reason(input_status))
            )
        # Follow-up baseline and trial prerequisites can be known but still
        # invalid for execution. Share their rules with the actual gate.
        blocked_paths = {entry["source_path"] for entry in blockers}
        for field, gate_reason in history_gate_requirements(key, values):
            for path in required:
                if path.rsplit("/", 1)[-1] == field and path not in blocked_paths:
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
        "questions": questions,
    }


def _input_reason(status: str) -> str:
    return {
        "missing": "No saved response.",
        "null": "The saved response is empty.",
        "unknown": "Unknown does not resolve this required input.",
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
