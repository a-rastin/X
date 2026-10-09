"""Workflow bundle validation (S39, seam T5; plan.md §§7.1/7.3).

Public seam (T5 only — tests and callers use the same functions):

- :func:`validate_bundle` — validate one ordered workflow bundle against
  on-disk question packages + validated XML documents.
- :func:`load_bundle` — validate and freeze a canonical bundle hash.

What this module does and does not promise:

- Success means "bundle shape fits the S39 contract and every referenced
  package is shape-valid, reviewed, and executable". It never means
  clinically valid or owner-approved beyond the explicit review records.
- All patient mappings stay CPT-estimation context only (S25); execution
  evidence stays empty; no posterior chaining; one LAI discussion/review
  network in registration; pinned assessment/history/DDI/template/prompt
  references. Missing/unreviewed, duplicate, incompatible, unresolved, or
  nonexecutable bundles cannot activate.
- Activation itself stays with ``registry.activate_bundle`` (T1, DB +
  audit); this module never touches the database.

Bounds are engineering limits, not clinical thresholds.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BUNDLE_SCHEMA_VERSION = "workflow-bundle-v1"

REGISTRATION_ORDER: tuple[str, ...] = (
    "pharmacotherapy",
    "high_suicide_clozapine",
    "lai_indication_choice",
    "aggression_clozapine",
    "established_case_clozapine",
)

FOLLOWUP_ORDER: tuple[str, ...] = (
    "tardive_dyskinesia",
    "akathisia",
    "parkinsonism",
    "acute_dystonia",
    "no_improvement_clozapine",
    "continue_or_adjust",
)

SUPPORTED_WORKFLOWS: frozenset[str] = frozenset({"registration", "followup"})

#: Bundle-level source allowlist (default-deny, candidate history only).
#: Per-package tests pin exact paths; this prefix covers all eleven
#: candidate histories while still rejecting notes/posterior/unknown.
BUNDLE_SOURCE_PREFIXES: tuple[str, ...] = ("candidate/history/",)

MAX_BUNDLE_ERRORS = 50
MAX_MESSAGE_CHARS = 500

_UNRESOLVED_CODES = frozenset(
    {
        "undeclared_query",
        "undeclared_output_state",
        "undeclared_state",
        "missing_template",
        "missing_prompt",
        "bad_template_slot",
        "unsupported_operator",
        "argmax_selection",
        "bad_prompt_reference",
        "bad_template_reference",
        "llm_prose",
        "forbidden_prompt_directive",
        "bad_prompt_content",
    }
)


@dataclass(frozen=True)
class BundleIssue:
    code: str
    message: str


@dataclass(frozen=True)
class BundleReport:
    valid: bool
    errors: tuple[BundleIssue, ...]


@dataclass(frozen=True)
class LoadedBundle:
    workflow: str
    version: str
    question_keys: tuple[str, ...]
    bundle_hash: str


class BundleError(ValueError):
    """Hard failure for the loader: bundle shape does not fit the contract."""

    def __init__(self, code: str, message: str, errors: tuple[BundleIssue, ...] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message[:MAX_MESSAGE_CHARS]
        self.errors = errors


def _clip(text: str) -> str:
    return text[:MAX_MESSAGE_CHARS]


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_bundle(
    bundle: Mapping[str, Any] | None,
    packages: Mapping[str, Any] | None,
    documents: Mapping[str, Any] | None,
    *,
    expected_order: tuple[str, ...] | list[str] | None = None,
    known_source_prefixes: tuple[str, ...] = BUNDLE_SOURCE_PREFIXES,
) -> BundleReport:
    """Validate one workflow bundle in fixed check order (default-deny).

    Order: envelope → bundle review → duplicates/missing/order →
    per-question pinned refs + hashes → S25 content → semantic/executable →
    LAI single + no chaining. Bounded to ``MAX_BUNDLE_ERRORS``.
    """
    from x_insight.models import admission as admission_module
    from x_insight.models import question_package as package_module

    errors: list[BundleIssue] = []

    def add(code: str, message: str) -> None:
        if len(errors) < MAX_BUNDLE_ERRORS:
            errors.append(BundleIssue(code=code, message=_clip(message)))

    if not isinstance(bundle, Mapping):
        add("missing_bundle", "No workflow bundle supplied.")
        return BundleReport(valid=False, errors=tuple(errors))
    if not isinstance(packages, Mapping):
        add("missing_question", "No question packages supplied for bundle validation.")
        return BundleReport(valid=False, errors=tuple(errors))
    if not isinstance(documents, Mapping):
        add("nonexecutable_network", "No validated documents supplied for bundle validation.")
        return BundleReport(valid=False, errors=tuple(errors))

    # --- Envelope ---
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        add("incompatible_content", f"schema_version must be {BUNDLE_SCHEMA_VERSION!r}.")
    workflow = bundle.get("workflow")
    if workflow not in SUPPORTED_WORKFLOWS:
        add("incompatible_content", f"workflow must be one of {sorted(SUPPORTED_WORKFLOWS)}.")
        workflow_str = ""
    else:
        workflow_str = str(workflow)
    version = bundle.get("version")
    if not isinstance(version, str) or not version.strip():
        add("incompatible_content", "bundle needs a non-empty version.")
    questions = bundle.get("questions")
    if not isinstance(questions, (list, tuple)) or not questions:
        add("missing_question", "bundle needs a non-empty questions list.")
        return BundleReport(valid=False, errors=tuple(errors))
    review = bundle.get("review")
    if not isinstance(review, Mapping):
        add("unreviewed_bundle", "bundle needs a review mapping.")
    else:
        reviewer = review.get("reviewer")
        decision = review.get("decision")
        date = review.get("date")
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or decision != "approved"
            or not isinstance(date, str)
            or not date.strip()
        ):
            add(
                "unreviewed_bundle",
                "bundle needs reviewer + approved decision + date; unreviewed cannot activate.",
            )
    assessment_refs = bundle.get("assessment_refs")
    if not isinstance(assessment_refs, (list, tuple)) or not assessment_refs:
        add("missing_pinned_ref", "bundle needs non-empty assessment_refs.")
    ddi_ref = bundle.get("ddi_ref")
    if not isinstance(ddi_ref, str) or not ddi_ref.strip():
        add("missing_pinned_ref", "bundle needs a non-empty ddi_ref.")

    # --- Order / duplicates ---
    keys: list[str] = []
    for idx, entry in enumerate(questions):
        if not isinstance(entry, Mapping):
            add("incompatible_content", f"questions[{idx}] must be a mapping.")
            continue
        key = entry.get("question_key")
        if not isinstance(key, str) or not key:
            add("incompatible_content", f"questions[{idx}] has no question_key.")
            continue
        keys.append(key)
    if len(set(keys)) != len(keys):
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                add("duplicate_key", f"question_key {key!r} appears twice.")
                break
            seen.add(key)
    resolved_order: tuple[str, ...]
    if expected_order is not None:
        resolved_order = tuple(str(k) for k in expected_order)
    elif workflow_str == "registration":
        resolved_order = REGISTRATION_ORDER
    elif workflow_str == "followup":
        resolved_order = FOLLOWUP_ORDER
    else:
        resolved_order = tuple(keys)
    missing = [k for k in resolved_order if k not in keys]
    extra = [k for k in keys if k not in resolved_order]
    for key in missing:
        add("missing_question", f"bundle omits required question {key!r}.")
    for key in extra:
        add("incompatible_content", f"bundle holds unexpected question {key!r}.")
    if not missing and not extra and keys != list(resolved_order):
        add(
            "incompatible_content",
            f"bundle order {keys} must match {list(resolved_order)}.",
        )

    # --- Per-question ---
    for entry in questions:
        if not isinstance(entry, Mapping):
            continue
        key = entry.get("question_key")
        if not isinstance(key, str) or not key:
            continue
        package = packages.get(key)
        document = documents.get(key)
        if package is None:
            # Already reported as missing when expected; still flag when ad-hoc.
            if key not in missing:
                add("missing_question", f"package for {key!r} is missing.")
            continue
        if document is None:
            add("missing_question", f"document for {key!r} is missing.")
            continue
        if not isinstance(package, Mapping):
            add("incompatible_content", f"package for {key!r} must be a mapping.")
            continue
        manifest = package.get("manifest")
        if not isinstance(manifest, Mapping):
            add("incompatible_content", f"package for {key!r} has no manifest.")
            continue
        # Pinned refs + hashes/versions.
        for field in (
            "version",
            "network_hash",
            "package_hash",
            "prompt_version",
            "template_version",
            "history_definition",
        ):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                add("missing_pinned_ref", f"bundle entry {key!r} needs {field}.")
        if isinstance(entry.get("version"), str) and isinstance(manifest.get("version"), str):
            if entry["version"] != manifest["version"]:
                add(
                    "incompatible_content",
                    f"bundle version for {key!r} must match manifest {manifest['version']!r}.",
                )
        if isinstance(entry.get("network_hash"), str) and isinstance(
            manifest.get("network_hash"), str
        ):
            if entry["network_hash"] != manifest["network_hash"]:
                add(
                    "incompatible_content",
                    f"bundle network_hash for {key!r} must match manifest.",
                )
        doc_hash = str(getattr(document, "source_sha256", ""))
        if isinstance(entry.get("network_hash"), str) and doc_hash:
            if entry["network_hash"] != doc_hash:
                add(
                    "incompatible_content",
                    f"bundle network_hash for {key!r} must match document hash.",
                )
        if isinstance(manifest.get("network_hash"), str) and doc_hash:
            if manifest["network_hash"] != doc_hash:
                add(
                    "incompatible_content",
                    f"manifest network_hash for {key!r} must match document hash.",
                )
        for ver_field in ("prompt_version", "template_version"):
            if isinstance(entry.get(ver_field), str) and isinstance(manifest.get(ver_field), str):
                if entry[ver_field] != manifest[ver_field]:
                    add(
                        "unresolved_query_template",
                        f"bundle {ver_field} for {key!r} must match manifest.",
                    )
        if isinstance(entry.get("history_definition"), str) and isinstance(
            manifest.get("history_definition"), str
        ):
            if entry["history_definition"] != manifest["history_definition"]:
                add(
                    "missing_pinned_ref",
                    f"bundle history_definition for {key!r} must match manifest.",
                )
        # Review gate: unreviewed cannot activate.
        pkg_review = package.get("review")
        manifest_status = manifest.get("review_status")
        pkg_decision = pkg_review.get("decision") if isinstance(pkg_review, Mapping) else None
        if manifest_status != "approved" or pkg_decision != "approved":
            add(
                "unreviewed_question",
                f"question {key!r} is not approved "
                f"(manifest {manifest_status!r}, review {pkg_decision!r}); cannot activate.",
            )
        # S25 content (mapping/query/template) using only the public seam.
        try:
            content_report = package_module.validate_question_package(
                package, document, known_source_prefixes=known_source_prefixes
            )
        except Exception as exc:  # noqa: BLE001 - surface as bundle error, never raise
            add("incompatible_content", f"package for {key!r} raised {type(exc).__name__}.")
            continue
        if not content_report.valid:
            first = content_report.errors[0] if content_report.errors else None
            code = first.code if first is not None else "incompatible_content"
            detail = f" ({code})" if first is not None else ""
            if code in _UNRESOLVED_CODES:
                add(
                    "unresolved_query_template",
                    f"package for {key!r} has unresolved query/template{detail}.",
                )
            else:
                add(
                    "incompatible_mapping",
                    f"package for {key!r} is incompatible{detail}.",
                )
            continue
        # Package hash must match the frozen loader hash.
        try:
            loaded = package_module.load_question_package(
                package, document, known_source_prefixes=known_source_prefixes
            )
        except Exception as exc:  # noqa: BLE001 - loader failure is a bundle error
            add(
                "incompatible_content",
                f"package for {key!r} cannot freeze ({type(exc).__name__}).",
            )
            continue
        if isinstance(entry.get("package_hash"), str):
            if entry["package_hash"] != loaded.package_hash:
                add(
                    "incompatible_content",
                    f"bundle package_hash for {key!r} must match frozen package hash.",
                )
        # Executable network: XSD + single-network + semantic (public T5 seams).
        try:
            xsd_valid = bool(getattr(getattr(document, "xsd", None), "valid", False))
        except Exception:
            xsd_valid = False
        try:
            semantic = admission_module.check_semantics(document)
            semantic_valid = bool(semantic.valid)
        except Exception:
            semantic_valid = False
        if not xsd_valid or not semantic_valid:
            add("nonexecutable_network", f"network for {key!r} is not executable.")
            continue
        # No implicit chaining: posterior markers never appear in mappings.
        try:
            mappings = manifest.get("patient_mappings", ())
            chained = False
            if isinstance(mappings, (list, tuple)):
                for mapping in mappings:
                    if not isinstance(mapping, Mapping):
                        continue
                    for path in mapping.get("allowed_source_paths", ()):
                        lowered = str(path).lower()
                        if "posterior" in lowered or "questions/" in lowered:
                            chained = True
            if chained:
                add("incompatible_mapping", f"package for {key!r} chains another result.")
        except Exception:
            pass

    # --- Cross-package: one LAI discussion/review network in registration ---
    # Enforced only for the canonical S39 orders; synthetic expected_order
    # overrides (TDD slices) bypass it. ponytail: keep the check here, not
    # in a second module, until S46 needs runtime ordering.
    if tuple(resolved_order) == tuple(REGISTRATION_ORDER):
        lai_count = sum(1 for k in keys if k == "lai_indication_choice")
        if lai_count != 1:
            add(
                "incompatible_content",
                "registration bundle must hold exactly one lai_indication_choice network.",
            )
    if tuple(resolved_order) == tuple(FOLLOWUP_ORDER) and "lai_indication_choice" in keys:
        add(
            "incompatible_content",
            "followup bundle must not hold lai_indication_choice.",
        )

    return BundleReport(valid=not errors, errors=tuple(errors))


def load_bundle(
    bundle: Mapping[str, Any] | None,
    packages: Mapping[str, Any] | None,
    documents: Mapping[str, Any] | None,
    *,
    expected_order: tuple[str, ...] | list[str] | None = None,
    known_source_prefixes: tuple[str, ...] = BUNDLE_SOURCE_PREFIXES,
) -> LoadedBundle:
    """Validate and freeze one workflow bundle for S39 handoff.

    Returns :class:`LoadedBundle` with workflow/version/ordered keys and a
    canonical ``bundle_hash`` over workflow/version/questions. Raises
    :class:`BundleError` when the bundle cannot activate. Never touches the
    database; activation stays with ``registry.activate_bundle``.
    """
    report = validate_bundle(
        bundle,
        packages,
        documents,
        expected_order=expected_order,
        known_source_prefixes=known_source_prefixes,
    )
    if not report.valid:
        first = report.errors[0] if report.errors else None
        code = first.code if first is not None else "invalid_bundle"
        raise BundleError(code, "Workflow bundle failed validation.", report.errors)
    assert isinstance(bundle, Mapping)
    workflow = str(bundle["workflow"])
    version = str(bundle["version"])
    questions = bundle["questions"]
    assert isinstance(questions, (list, tuple))
    keys = tuple(str(e["question_key"]) for e in questions if isinstance(e, Mapping))
    core = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "workflow": workflow,
        "version": version,
        "questions": [
            {k: e.get(k) for k in ("question_key", "version", "network_hash", "package_hash")}
            for e in questions
            if isinstance(e, Mapping)
        ],
    }
    return LoadedBundle(
        workflow=workflow, version=version, question_keys=keys, bundle_hash=_canonical_hash(core)
    )
