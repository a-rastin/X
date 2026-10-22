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
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from x_insight import contracts
from x_insight.models import bundles, question_package, validation

ACTIVE_HISTORY_VERSION = "history-inputs-2026-10-22-v2"
ACTIVE_PHARMACOTHERAPY_PATH = "content/history/history.pharmacotherapy.v3.json"
ACTIVE_PHARMACOTHERAPY_SHA256 = "7e045f7fb3e0a1bd6a194107ad96659bb42f6367b005db78c905bf4dfb0360dc"


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


def resolve_workflow(workflow: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        active_definition = _active_pharmacotherapy_definition()
        active_packages = [
            _apply_history_contract(packages[key], active_definition)
            if key == "pharmacotherapy"
            else packages[key]
            for key in loaded.question_keys
        ]
        return active_packages, {
            "history_input_contract_version": ACTIVE_HISTORY_VERSION,
            "history_input_contract_hash": ACTIVE_PHARMACOTHERAPY_SHA256,
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


def history_fields() -> dict[str, dict[str, Any]]:
    """Exact versioned paths; repeated field names stay in separate namespaces."""
    try:
        _active_pharmacotherapy_definition()
        directory = content_root().resolve()
        paths = [
            directory / "history" / f"history.{key}.v1.json"
            for key in (*bundles.REGISTRATION_ORDER, *bundles.FOLLOWUP_ORDER)
            if key != "pharmacotherapy"
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
            _active_pharmacotherapy_definition()
            if key == "pharmacotherapy"
            else json.loads((directory / "history" / f"history.{key}.v1.json").read_bytes())
        )
        for field in definition["fields"]:
            result[field["source_path"]] = {**field, "question_key": key}
    return result


def workflow_history_fields(workflow: str) -> list[dict[str, Any]]:
    order = bundles.REGISTRATION_ORDER if workflow == "registration" else bundles.FOLLOWUP_ORDER
    fields = history_fields()
    return [
        dict(field, id=path)
        for key in order
        for path, field in fields.items()
        if field["question_key"] == key
    ]


def _active_pharmacotherapy_definition() -> dict[str, Any]:
    payload = read_content(ACTIVE_PHARMACOTHERAPY_PATH)
    if hashlib.sha256(payload).hexdigest() != ACTIVE_PHARMACOTHERAPY_SHA256:
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
    paths = {field["source_path"] for field in definition["fields"]}
    manifest["version"] = ACTIVE_HISTORY_VERSION
    manifest["review_status"] = "awaiting_review"
    manifest["history_input_contract"] = {
        "version": ACTIVE_HISTORY_VERSION,
        "sha256": ACTIVE_PHARMACOTHERAPY_SHA256,
        "basis": "Explicit requested application-question cleanup on 2026-10-22.",
        "clinical_approval": "none; original model and its review preserved",
        "source_package_version": package["manifest"]["version"],
    }
    manifest["history_definition"] = ACTIVE_PHARMACOTHERAPY_PATH
    manifest["source_refs"] = [
        path
        for path in manifest["source_refs"]
        if path != package["manifest"]["history_definition"]
    ] + [ACTIVE_PHARMACOTHERAPY_PATH]
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
    if not isinstance(manifest, dict) or manifest.get("question_key") != "pharmacotherapy":
        return
    if (
        any(
            path.startswith("candidate/history/pharmacotherapy.v2/")
            for path in manifest.get("applicability", {}).get("required_fields", [])
        )
        and manifest.get("history_input_contract", {}).get("version") != ACTIVE_HISTORY_VERSION
    ):
        raise contracts.ContractError(
            422,
            "HISTORY_INPUT_CONTRACT_CHANGED",
            (
                "This saved run uses retired History questions. "
                "Start a new proposal with the current History contract."
            ),
        )
