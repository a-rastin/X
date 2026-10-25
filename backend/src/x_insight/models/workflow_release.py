"""Server-owned, hash-pinned workflow content shipped with this application.

Historical registry pointers select XML versions only. The reviewed release is
validated before deriving the current versioned application input contract.
Original model review does not approve the derived input changes. Browser
storage is never a package source.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from x_insight import contracts
from x_insight.models import bundles, question_package, validation

ACTIVE_HISTORY_VERSION = "history-inputs-2026-10-22-v3"
ACTIVE_PHARMACOTHERAPY_PATH = "content/history/history.pharmacotherapy.v3.json"
ACTIVE_PHARMACOTHERAPY_SHA256 = "7e045f7fb3e0a1bd6a194107ad96659bb42f6367b005db78c905bf4dfb0360dc"

ACTIVE_HISTORY_DEFINITIONS = {
    "pharmacotherapy": (ACTIVE_PHARMACOTHERAPY_PATH, ACTIVE_PHARMACOTHERAPY_SHA256),
    "high_suicide_clozapine": (
        "content/history/history.high_suicide_clozapine.v2.json",
        "a4811a366de06aea03df98d368701edb93231136b902338fce656e1747495a87",
    ),
    "lai_indication_choice": (
        "content/history/history.lai_indication_choice.v2.json",
        "c7bb78ba48a500ed6d6314095b44acfb0e0088509418891033155ec19fe099a3",
    ),
    "aggression_clozapine": (
        "content/history/history.aggression_clozapine.v2.json",
        "3fe0ef21d9537f532805d5d13631b50fab40291731bb8eecbcf049efb88e2dfb",
    ),
}
CSSRS_CONTEXT_PATH = "synthetic/assessments/cssrs"
ACTIVE_WORKFLOW_VERSION = "application-workflow-2026-10-24-v1"
ESTABLISHED_KEY = "established_case_clozapine"
ESTABLISHED_PREFIX = "candidate/history/established_case_clozapine.v1/"


def uses_established_history(manifest: Mapping[str, Any]) -> bool:
    """Recognize application source paths even when an inline key is renamed."""
    applicability = manifest.get("applicability")
    required = (
        applicability.get("required_fields", []) if isinstance(applicability, Mapping) else []
    )
    paths = list(required) if isinstance(required, (list, tuple)) else []
    mappings = manifest.get("patient_mappings")
    for mapping in mappings if isinstance(mappings, (list, tuple)) else []:
        sources = mapping.get("allowed_source_paths") if isinstance(mapping, Mapping) else None
        if isinstance(sources, (list, tuple)):
            paths.extend(sources)
    return any(isinstance(path, str) and path.startswith(ESTABLISHED_PREFIX) for path in paths)


def active_workflow_order(workflow: str, *, clozapine_eligible: bool = False) -> tuple[str, ...]:
    if workflow == "registration":
        return tuple(key for key in bundles.REGISTRATION_ORDER if key != ESTABLISHED_KEY)
    order = list(bundles.FOLLOWUP_ORDER)
    if clozapine_eligible:
        order.insert(order.index("no_improvement_clozapine"), ESTABLISHED_KEY)
    return tuple(order)


def content_root() -> Path:
    override = os.environ.get("X_INSIGHT_CONTENT_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[4] / "content"


def read_content(path: str) -> bytes:
    root = content_root().resolve()
    relative = Path(path).relative_to("content")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Content path escapes the content directory.")
    return target.read_bytes()


def _reviewed_workflow(workflow: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the release, dependency bytes, approval, order and executability."""
    try:
        release = json.loads(read_content("content/bundles/active-release.json"))
        if release["schema_version"] != "workflow-release-v1":
            raise ValueError("Unsupported workflow release schema.")
        review = release["review"]
        if review["decision"] != "approved" or not review["confirmed"] or not review["reviewed"]:
            raise ValueError("Workflow release is not owner-confirmed.")
        ref = release["bundles"][workflow]
        bundle = json.loads(read_content(ref["path"]))
        paths = [ref["path"], *bundle["assessment_refs"]]
        packages: dict[str, Any] = {}
        documents: dict[str, Any] = {}
        for entry in bundle["questions"]:
            directory = entry["package_directory"]
            paths.append(entry["history_definition"])
            paths.extend(
                f"{directory}/{name}"
                for name in (
                    "manifest.json",
                    "template.json",
                    "examples.json",
                    "review.json",
                    "prompt.txt",
                    "network.xml",
                )
            )
        for path in paths:
            if hashlib.sha256(read_content(path)).hexdigest() != release["files"][path]:
                raise ValueError(f"Content hash mismatch: {path}")
        for entry in bundle["questions"]:
            directory = entry["package_directory"]
            package = {
                name: json.loads(read_content(f"{directory}/{name}.json"))
                for name in ("manifest", "template", "examples", "review")
            }
            package["prompt"] = {
                "version": package["manifest"]["prompt_version"],
                "text": read_content(f"{directory}/prompt.txt").decode("utf-8"),
            }
            package["network_xml"] = read_content(f"{directory}/network.xml").decode("utf-8")
            key = entry["question_key"]
            packages[key] = package
            documents[key] = validation.validate(package["network_xml"].encode("utf-8"))
        loaded = bundles.load_bundle(bundle, packages, documents)
        if loaded.bundle_hash != ref["bundle_hash"] or loaded.workflow != workflow:
            raise ValueError("Workflow bundle identity mismatch.")
        active_definitions = {
            key: _active_history_definition(key) for key in ACTIVE_HISTORY_DEFINITIONS
        }
        active_packages = [
            _apply_history_contract(packages[key], active_definitions[key])
            if key in active_definitions
            else packages[key]
            for key in loaded.question_keys
        ]
        return active_packages, {
            "history_input_contract_version": ACTIVE_HISTORY_VERSION,
            "history_input_contract_hash": contracts.canonical_hash(ACTIVE_HISTORY_DEFINITIONS),
            "release_version": release["version"],
            "release_hash": contracts.canonical_hash(release),
            "bundle_version": loaded.version,
            "bundle_hash": loaded.bundle_hash,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise contracts.ContractError(
            422,
            "WORKFLOW_CONTENT_UNAVAILABLE",
            "The reviewed workflow content could not be loaded. Check the server content release.",
        ) from exc


def resolve_workflow(
    workflow: str, *, clozapine_eligible: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Derive active placement without changing the immutable reviewed release."""
    packages, pin = _reviewed_workflow(workflow)
    by_key = {package["manifest"]["question_key"]: package for package in packages}
    if workflow == "followup" and clozapine_eligible:
        registration, registration_pin = _reviewed_workflow("registration")
        original = next(p for p in registration if p["manifest"]["question_key"] == ESTABLISHED_KEY)
        adapted = deepcopy(original)
        adapted["manifest"].update(
            workflow="followup",
            version=ACTIVE_WORKFLOW_VERSION,
            review_status="awaiting_review",
            application_workflow_contract=ACTIVE_WORKFLOW_VERSION,
        )
        adapted["manifest"]["prompt_version"] = ACTIVE_WORKFLOW_VERSION
        adapted["prompt"] = {
            "version": ACTIVE_WORKFLOW_VERSION,
            "text": (
                "Application workflow: the historical R7 registration designation below is "
                "provenance. This package now runs only in follow-up when a prior signed final "
                "treatment plan explicitly includes clozapine. It remains separate from the "
                "no-improvement clozapine package. No new clinical approval is claimed.\n\n"
                + original["prompt"]["text"]
            ),
        }
        question_package.load_question_package(adapted)
        by_key[ESTABLISHED_KEY] = adapted
        pin["established_source_bundle_hash"] = registration_pin["bundle_hash"]
    order = active_workflow_order(workflow, clozapine_eligible=clozapine_eligible)
    pin.update(
        application_workflow_contract_version=ACTIVE_WORKFLOW_VERSION,
        established_clozapine_eligible=bool(workflow == "followup" and clozapine_eligible),
    )
    return [by_key[key] for key in order], pin


def require_current_workflow_contract(pin: Any, *, clozapine_eligible: bool) -> None:
    """Frozen runs remain readable; a new start must use today's placement/evidence."""
    if not isinstance(pin, dict) or not pin.get("bundle_hash"):
        return
    if pin.get("application_workflow_contract_version") != ACTIVE_WORKFLOW_VERSION:
        raise contracts.ContractError(
            422,
            "WORKFLOW_INPUT_CONTRACT_CHANGED",
            "This run uses a retired workflow. Start a new proposal with the current workflow.",
        )
    if pin.get("established_clozapine_eligible") is not clozapine_eligible:
        raise contracts.ContractError(
            422,
            "WORKFLOW_ELIGIBILITY_CHANGED",
            "Signed final-plan evidence changed. Start a new proposal with the current workflow.",
        )


def history_fields() -> dict[str, dict[str, Any]]:
    """Exact versioned paths; repeated field names stay in separate namespaces."""
    try:
        for key in ACTIVE_HISTORY_DEFINITIONS:
            _active_history_definition(key)
        directory = content_root().resolve()
        paths = [
            directory / "history" / f"history.{key}.v1.json"
            for key in (*bundles.REGISTRATION_ORDER, *bundles.FOLLOWUP_ORDER)
            if key not in ACTIVE_HISTORY_DEFINITIONS
        ]
        # Decode once per file revision, while detecting missing or changed
        # definitions even after this process has cached a valid inventory.
        signature = tuple(
            (stat.st_mtime_ns, stat.st_size) for path in paths for stat in (path.stat(),)
        )
        return _history_fields(directory, signature)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise contracts.ContractError(
            422,
            "WORKFLOW_CONTENT_UNAVAILABLE",
            "The History input definitions could not be loaded. Check the server content release.",
        ) from exc


@lru_cache(maxsize=4)
def _history_fields(
    directory: Path, signature: tuple[tuple[int, int], ...]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in (*bundles.REGISTRATION_ORDER, *bundles.FOLLOWUP_ORDER):
        definition = (
            _active_history_definition(key)
            if key in ACTIVE_HISTORY_DEFINITIONS
            else json.loads((directory / "history" / f"history.{key}.v1.json").read_bytes())
        )
        for field in definition["fields"]:
            result[field["source_path"]] = {**field, "question_key": key}
    return result


def workflow_history_fields(
    workflow: str, *, clozapine_eligible: bool = False
) -> list[dict[str, Any]]:
    order = active_workflow_order(workflow, clozapine_eligible=clozapine_eligible)
    fields = history_fields()
    return [
        dict(field, id=path)
        for key in order
        for path, field in fields.items()
        if field["question_key"] == key
    ]


def _active_history_definition(key: str) -> dict[str, Any]:
    path, expected_hash = ACTIVE_HISTORY_DEFINITIONS[key]
    payload = read_content(path)
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise contracts.ContractError(
            422,
            "WORKFLOW_CONTENT_UNAVAILABLE",
            "The active History input contract failed integrity validation.",
        )
    definition: dict[str, Any] = json.loads(payload)
    return definition


def _apply_history_contract(package: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    """Version the application input change without altering reviewed model bytes.

    Missing patient mappings leave nodes unrepresented in estimation context;
    full CPT estimation and empty inference evidence remain unchanged. Historical
    clinical review is provenance, not approval of this derived input contract.
    """
    adapted = deepcopy(package)
    manifest = adapted["manifest"]
    key = manifest["question_key"]
    definition_path, definition_hash = ACTIVE_HISTORY_DEFINITIONS[key]
    paths = {field["source_path"] for field in definition["fields"]}
    manifest["version"] = ACTIVE_HISTORY_VERSION
    manifest["review_status"] = "awaiting_review"
    manifest["history_input_contract"] = {
        "version": ACTIVE_HISTORY_VERSION,
        "sha256": definition_hash,
        "basis": "Explicit requested application-question cleanup on 2026-10-22.",
        "clinical_approval": "none; original model and its review preserved",
        "source_package_version": package["manifest"]["version"],
    }
    manifest["history_definition"] = definition_path
    manifest["source_refs"] = [
        path
        for path in manifest["source_refs"]
        if path != package["manifest"]["history_definition"]
    ] + [definition_path]
    manifest["patient_mappings"] = [
        mapping
        for mapping in manifest["patient_mappings"]
        if all(path in paths for path in mapping["allowed_source_paths"])
    ]
    manifest["applicability"]["required_fields"] = [
        path for path in manifest["applicability"]["required_fields"] if path in paths
    ]
    manifest["missingness_contract"]["required_source_paths"] = list(
        manifest["applicability"]["required_fields"]
    )
    manifest["missingness_contract"].pop("known_no_treatment", None)
    manifest["input_consistency_contract"] = {
        "history_review": (
            "Application input contract changed by explicit request; no new clinical approval."
        ),
        "unrepresented_inputs": (
            "Retired and inapplicable patient inputs are omitted, never assigned clinical values. "
            "All network CPTs are still estimated; inference evidence remains empty."
        ),
    }
    if key != "pharmacotherapy":
        # The derived prompt must not request the retired inputs still described
        # by the immutable original model-review artifacts.
        text = package["prompt"]["text"].split("Keep ", 1)[0]
        text += (
            "Use only the represented current inputs; retired controls are not prerequisites. "
            "Unrepresented nodes remain latent; do not invent patient values for them. "
            "Preserve all network CPTs and empty inference evidence. "
            "Do not author recommendations, "
            "rank treatments, or supply dosing, route or frequency. "
            "This changed application input contract awaits review "
            "and has no new clinical approval."
        )
        if key == "high_suicide_clozapine":
            manifest["assessment_context_source"] = CSSRS_CONTEXT_PATH
            manifest["applicability"]["expression"] = "true"
            manifest["applicability"]["required_fields"] = [CSSRS_CONTEXT_PATH]
            manifest["missingness_contract"]["required_source_paths"] = [CSSRS_CONTEXT_PATH]
            manifest["input_consistency_contract"]["assessment_context"] = (
                "Completed released C-SSRS supplies separate structured estimation context. "
                "Completion is an input-readiness gate, "
                "not a substantial-risk applicability finding. "
                "No C-SSRS threshold is equated to clinician synthesis, "
                "persistence or treatment history."
            )
            text += " " + manifest["input_consistency_contract"]["assessment_context"]
        manifest["prompt_version"] = ACTIVE_HISTORY_VERSION
        adapted["prompt"] = {"version": ACTIVE_HISTORY_VERSION, "text": text}
    question_package.load_question_package(adapted)
    return adapted


def is_history_field_applicable(
    path: str, values: dict[str, Any], *, fields: dict[str, dict[str, Any]] | None = None
) -> bool:
    inventory = history_fields() if fields is None else fields
    condition = inventory.get(path, {}).get("show_if")
    return not condition or values.get(condition["field_id"]) == condition["value"]


def require_current_history_contract(manifest: Any) -> None:
    """Old frozen contracts remain readable, but cannot request retired inputs."""
    if isinstance(manifest, dict) and uses_established_history(manifest):
        if (
            manifest.get("workflow") != "followup"
            or manifest.get("application_workflow_contract") != ACTIVE_WORKFLOW_VERSION
        ):
            raise contracts.ContractError(
                422,
                "WORKFLOW_INPUT_CONTRACT_CHANGED",
                "Established-case clozapine belongs to eligible follow-up. Start a new proposal.",
            )
    if (
        not isinstance(manifest, dict)
        or manifest.get("question_key") not in ACTIVE_HISTORY_DEFINITIONS
    ):
        return
    key = manifest["question_key"]
    applicability = manifest.get("applicability")
    required = applicability.get("required_fields") if isinstance(applicability, dict) else None
    paths = list(required) if isinstance(required, (list, tuple)) else []
    mappings = manifest.get("patient_mappings")
    for mapping in mappings if isinstance(mappings, (list, tuple)) else []:
        sources = mapping.get("allowed_source_paths") if isinstance(mapping, dict) else None
        if isinstance(sources, (list, tuple)):
            paths.extend(sources)
    definition = manifest.get("history_definition", "")
    application_contract = (
        "history_input_contract" in manifest
        or (
            isinstance(definition, str) and definition.startswith(f"content/history/history.{key}.")
        )
        or any(
            isinstance(path, str) and path.startswith(f"candidate/history/{key}.") for path in paths
        )
    )
    # Engineering packages may share a question key while using unrelated
    # synthetic inputs. Only the actual application contracts retire questions.
    if not application_contract:
        return
    contract = manifest.get("history_input_contract")
    if not isinstance(contract, dict) or contract.get("version") != ACTIVE_HISTORY_VERSION:
        raise contracts.ContractError(
            422,
            "HISTORY_INPUT_CONTRACT_CHANGED",
            (
                "This saved run uses retired History questions. "
                "Start a new proposal with the current History contract."
            ),
        )
