"""Server-owned, hash-pinned workflow content shipped with this application.

Historical registry pointers select XML versions only; this release supplies the
complete reviewed package contract. Browser storage is never a package source.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from x_insight import contracts
from x_insight.models import bundles, validation


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
        return [packages[key] for key in loaded.question_keys], {
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
    return _history_fields(content_root().resolve())


@lru_cache(maxsize=4)
def _history_fields(directory: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in (*bundles.REGISTRATION_ORDER, *bundles.FOLLOWUP_ORDER):
        version = "v2" if key == "pharmacotherapy" else "v1"
        definition = json.loads(
            (directory / "history" / f"history.{key}.{version}.json").read_bytes()
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
