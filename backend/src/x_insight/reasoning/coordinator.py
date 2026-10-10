"""Run one full synthetic clinical question end to end (S45, seams T1/T8 exercising T5-T7).

Plan.md §§7.4, 8-9 (FR-32-35): public generation start → worker → real MCP →
controlled provider → all-CPT validation → effective XML → empty-evidence
inference → template section, observable through
``GET /generation-batches/{id}`` and question review.

Reuses real owned modules (no second paths, no fake succeeded endpoint):

- S40 ``snapshots`` — frozen projection/gates/freshness (read-only here).
- S44 ``queue`` — leases/fencing/fairness; failure commits reuse
  ``queue.commit_job_result``; success commits atomically with the baseline
  in one transaction (same fencing + eligibility checks).
- S41 ``mcp_host`` real stdio — via S43 ``BoundedProviderAdapter`` (no
  direct-call bypass; this module never imports ``resolve_projection``).
- S43 ``BoundedProviderAdapter`` + ``DeterministicProviderEndpoint`` —
  one bounded estimation attempt with real tool bridging and shared strict
  validation. Templates never pass through the provider.
- S23 ``validate_cpts`` + ``build_effective_artifact`` + ``infer_effective``
  — all-CPT validation, run-local effective XML/hash, empty-evidence exact
  inference (projection is provenance only, never evidence).
- S25 package validation + template schema — plus a small local renderer
  below (escaped-value slots, explicit branches, no LLM prose).

Synthetic small network from plan.md §7.4: ``A`` root ``P(A=yes)=.20``,
``B`` child of ``A`` ``P(B=yes)=.22`` empty evidence. No clinical release
package is required for this engineering proof, but no fake ``succeeded``
endpoint is acceptable: success requires real provider CPTs, real
validation, real inference, and real rendering.

Persistence (migration ``0011``):

- ``question_runs.pinned_package`` — frozen full package stored at start.
- ``original_baselines`` — one immutable row per successful run (raw +
  validated percentages, effective XML/hash, query/posteriors, section,
  versions/model, projection hash, provenance). Created atomically with
  the job success; failed originals create no row (no adjustable baseline).

Template rendering (small local renderer, no LLM prose):

- Branch selection uses the stored inference result only: per-node argmax
  (largest posterior, ties broken by declared state order). ``==`` matches
  when the argmax equals the branch state, ``!=`` when it differs. Other
  operators fail closed (unsupported for this single-question proof).
- Slots ``{Node}`` are replaced with the escaped
  ``"<argmax-state> <pct>%"`` summary for that node (``html.escape``).
  Unknown slots fail closed. Provider text is never read for rendering.

Five transparency fields (all from persisted data, never recomputed chart):

- ``question_key``, ``network_version``, ``saved_patient_inputs``,
  ``returned_cpt_percentages``, ``deterministic_result``.
"""

from __future__ import annotations

import html
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight import db as db_module
from x_insight.reasoning import queue as queue_module
from x_insight.reasoning import tables as reasoning_tables

RENDER_UNSUPPORTED_OPERATORS = frozenset({"in", "and", "or", "not"})


def safe_proposal(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public proposal shape (persisted assembly only, never LLM prose)."""
    return {
        "id": str(row.get("id")),
        "batch_id": str(row.get("batch_id")),
        "fingerprint": str(row.get("fingerprint")),
        "sections": list(row.get("sections") or []),
        "skipped": list(row.get("skipped") or []),
        "coverage_warnings": list(row.get("coverage_warnings") or []),
        "ddi_report": dict(row.get("ddi_report") or {}),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def get_proposal(session: Session, batch_id: Any) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(reasoning_tables.proposal_snapshots).where(
                reasoning_tables.proposal_snapshots.c.batch_id == batch_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_baselines_for_batch(session: Session, batch_id: Any) -> list[dict[str, Any]]:
    """Ordered baselines per run (S46 sections; null when pending)."""
    runs = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
                reasoning_tables.question_runs.c.position.asc(),
                reasoning_tables.question_runs.c.question_key.asc(),
            )
        )
        .mappings()
        .all()
    )
    ordered: list[dict[str, Any]] = []
    for entry in runs:
        run = dict(entry)
        stored = get_baseline(session, run["id"])
        baseline = safe_baseline(stored) if stored is not None else None
        transparency = build_transparency(run, stored) if stored is not None else None
        try:
            position = int(run.get("position", 0))
        except Exception:
            position = 0
        ordered.append(
            {
                "question_run_id": str(run.get("id")),
                "question_key": str(run.get("question_key", "")),
                "position": int(position),
                "status": str(run.get("status", "")),
                "baseline": baseline,
                "transparency": transparency,
            }
        )
    return ordered


def build_coverage_warnings(ddi_report: Mapping[str, Any]) -> list[str]:
    """Coverage warnings from a pinned valid DDI report (limited vs complete)."""
    warnings: list[str] = []
    limitations = ddi_report.get("limitations")
    if isinstance(limitations, list):
        for entry in limitations:
            if isinstance(entry, str) and entry.strip():
                warnings.append(str(entry))
            elif entry is not None:
                warnings.append(str(entry))
    uncovered_meds = ddi_report.get("coverage_unavailable_medications")
    if isinstance(uncovered_meds, list):
        for entry in uncovered_meds:
            if isinstance(entry, Mapping):
                catalog = entry.get("catalog_drug_id") or entry.get("concept_id")
                warnings.append(f"coverage unavailable: {catalog}")
            elif entry is not None:
                warnings.append(f"coverage unavailable: {entry}")
    pairs = ddi_report.get("pairs")
    if isinstance(pairs, list):
        for pair in pairs:
            if not isinstance(pair, Mapping):
                continue
            if str(pair.get("status")) == "coverage_unavailable":
                warnings.append(
                    f"coverage unavailable: {pair.get('drug_a')} + {pair.get('drug_b')}"
                )
    # Deduplicate preserving order (small N).
    seen: set[str] = set()
    deduped: list[str] = []
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            deduped.append(warning)
    return deduped


def recompute_pinned_ddi(
    session: Session, batch: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Recompute the pinned DDI report from frozen facts (never live chart).

    Uses the frozen fingerprint payload medications + pinned dataset version
    (both immutable at start). Returns (report, None) when valid, or
    (None, error_code) when the dataset is failed/unavailable. Valid
    limited-coverage reports (``released_limited`` with
    ``coverage_unavailable``) return valid here — absence is only claimed
    via explicit complete coverage, never synthesized.
    """
    from x_insight import db as db_module
    from x_insight.ddi import checker as checker_module

    raw_pinned = batch.get("pinned_bundle")
    pinned: dict[str, Any] = raw_pinned if isinstance(raw_pinned, dict) else {}
    dataset_version = pinned.get("ddi_dataset_version")
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        return None, "DATASET_UNAVAILABLE"
    payload = batch.get("fingerprint_payload")
    frozen_meds: list[str] = []
    if isinstance(payload, Mapping):
        meds = payload.get("medications")
        if isinstance(meds, list):
            frozen_meds = [str(entry) for entry in meds if isinstance(entry, str)]
    try:
        engine = session.get_bind()
    except Exception:
        engine = None
    if engine is None:
        try:
            engine = db_module.get_engine()
        except Exception:
            engine = None
    if engine is None:
        return None, "DATASET_UNAVAILABLE"
    try:
        report = checker_module.check(engine, list(frozen_meds), str(dataset_version))
    except checker_module.CheckError as exc:
        return None, str(exc.code)
    except Exception:
        return None, "DATASET_UNAVAILABLE"
    return dict(report), None


def try_assemble_proposal(session: Session, batch_id: Any, now: datetime) -> dict[str, Any] | None:
    """Assemble the immutable proposal when complete (same tx as success).

    Succeeds only after every applicable question has its baseline and a
    valid pinned DDI report exists. Skipped ``not_applicable`` reasons and
    DDI coverage warnings are stored in the row. Partial runs return None
    (no row, derived incomplete view). No LLM proposal-writing request
    exists: sections come from stored baselines, DDI from the pinned report.
    SELECT+INSERT only (immutable proposals, never UPDATE).
    """
    batch_row = (
        session.execute(
            select(reasoning_tables.generation_batches).where(
                reasoning_tables.generation_batches.c.id == batch_id
            )
        )
        .mappings()
        .first()
    )
    if batch_row is None:
        return None
    batch = dict(batch_row)
    existing = get_proposal(session, batch_id)
    if existing is not None:
        return existing
    runs = (
        session.execute(
            select(reasoning_tables.question_runs)
            .where(reasoning_tables.question_runs.c.batch_id == batch_id)
            .order_by(
                reasoning_tables.question_runs.c.position.asc(),
                reasoning_tables.question_runs.c.question_key.asc(),
            )
        )
        .mappings()
        .all()
    )
    ordered_runs = [dict(entry) for entry in runs]
    sections: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for run in ordered_runs:
        status = str(run.get("status"))
        try:
            position = int(run.get("position", 0))
        except Exception:
            position = 0
        if status == "not_applicable":
            skipped.append(
                {
                    "question_key": str(run.get("question_key", "")),
                    "position": int(position),
                    "reason": str(run.get("gate_reason", "")),
                }
            )
            continue
        if status == "needs_clarification":
            return None
        if status != "ready":
            return None
        stored = get_baseline(session, run["id"])
        if stored is None:
            return None
        safe = safe_baseline(stored)
        sections.append(
            {
                "question_run_id": str(run.get("id")),
                "question_key": str(run.get("question_key", "")),
                "position": int(position),
                "baseline_id": str(safe.get("id")),
                "section_text": str(safe.get("section_text", "")),
                "posteriors": list(safe.get("posteriors") or []),
                "query_nodes": list(safe.get("query_nodes") or []),
                "effective_hash": str(safe.get("effective_hash", "")),
                "template_version": str(safe.get("template_version", "")),
                "network_version": str(safe.get("network_version", "")),
            }
        )
    ddi_report, _ = recompute_pinned_ddi(session, batch)
    if ddi_report is None:
        return None
    coverage_warnings = build_coverage_warnings(ddi_report)
    session.execute(
        insert(reasoning_tables.proposal_snapshots).values(
            id=uuid.uuid4(),
            batch_id=batch["id"],
            fingerprint=str(batch.get("fingerprint")),
            sections=list(sections),
            skipped=list(skipped),
            coverage_warnings=list(coverage_warnings),
            ddi_report=dict(ddi_report),
            created_at=now,
        )
    )
    session.flush()
    created = get_proposal(session, batch_id)
    return created


def build_workflow_view(
    session: Session, batch: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Derived complete/incomplete view (partial labeled incomplete)."""
    proposal = get_proposal(session, batch["id"])
    if proposal is not None:
        safe = safe_proposal(proposal)
        raw_bundle = batch.get("pinned_bundle")
        bundle: dict[str, Any] = raw_bundle if isinstance(raw_bundle, dict) else {}
        return {
            "complete": True,
            "status": "complete",
            "pending_question_keys": [],
            "needs_clarification": [],
            "skipped": list(safe.get("skipped") or []),
            "coverage_warnings": list(safe.get("coverage_warnings") or []),
            "ddi_status": str(bundle.get("ddi_status") or "valid"),
        }
    pending: list[str] = []
    clarification: list[str] = []
    skipped: list[dict[str, Any]] = []
    for run in runs:
        status = str(run.get("status"))
        key = str(run.get("question_key", ""))
        if status == "not_applicable":
            try:
                position = int(run.get("position", 0))  # type: ignore[arg-type]
            except Exception:
                position = 0
            skipped.append(
                {
                    "question_key": key,
                    "position": int(position),
                    "reason": str(run.get("gate_reason", "")),
                }
            )
            continue
        if status == "needs_clarification":
            clarification.append(key)
            continue
        if status != "ready":
            pending.append(key)
            continue
        stored = get_baseline(session, run.get("id"))
        if stored is None:
            pending.append(key)
    raw_pinned = batch.get("pinned_bundle")
    pinned_bundle: dict[str, Any] = raw_pinned if isinstance(raw_pinned, dict) else {}
    ddi_status = str(pinned_bundle.get("ddi_status") or "unavailable")
    return {
        "complete": False,
        "status": "incomplete",
        "pending_question_keys": pending,
        "needs_clarification": clarification,
        "skipped": skipped,
        "coverage_warnings": [],
        "ddi_status": ddi_status,
    }


def _fail_result(error_code: str, retryable: bool) -> dict[str, Any]:
    return {"ok": False, "error_code": error_code, "retryable": retryable}


def load_document(package: Mapping[str, Any]) -> Any:
    """Parse and bind the registered network XML (base bytes never mutated).

    Requires ``package["network_xml"]`` (str) and
    ``manifest.network_hash`` equal to its SHA-256. Returns the S21
    ``ValidatedXmlbif``. Raises ``ValueError(code: message)`` on any
    mismatch (caller maps to a failed job, never a repaired default).
    """
    from x_insight.models.validation import validate as validate_xml

    manifest = package.get("manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("PACKAGE_INVALID: manifest missing")
    network_hash = manifest.get("network_hash")
    if not isinstance(network_hash, str) or len(network_hash) != 64:
        raise ValueError("PACKAGE_INVALID: network_hash must be sha256 hex")
    raw_xml = package.get("network_xml")
    if not isinstance(raw_xml, str) or not raw_xml.strip():
        raise ValueError("MISSING_NETWORK_XML: package has no network_xml")
    xml_bytes = raw_xml.encode("utf-8")
    if len(xml_bytes) > 1_048_576:
        raise ValueError("NETWORK_TOO_LARGE: network_xml exceeds 1 MiB")
    try:
        document = validate_xml(xml_bytes)
    except Exception as exc:
        raise ValueError(f"NETWORK_INVALID: {exc}") from exc
    if document.source_sha256 != network_hash:
        raise ValueError("HASH_MISMATCH: network_xml hash differs from manifest")
    return document


def build_provider_request(
    run: Mapping[str, Any], batch: Mapping[str, Any], package: Mapping[str, Any]
) -> Any:
    """Build the pinned S43 provider request from frozen run/batch/package."""
    from x_insight.reasoning.provider import ProviderRequest

    manifest = package.get("manifest")
    prompt = package.get("prompt")
    assert isinstance(manifest, Mapping) and isinstance(prompt, Mapping)
    cpt_contract = manifest.get("cpt_contract")
    if not isinstance(cpt_contract, dict):
        cpt_contract = dict(cpt_contract) if isinstance(cpt_contract, Mapping) else {}
    return ProviderRequest(
        question_key=str(run.get("question_key", "")),
        projection=dict(run.get("projection") or {}),
        projection_hash=str(run.get("projection_hash", "")),
        prompt_version=str(manifest.get("prompt_version", "v1")),
        question_run_id=str(run.get("id")),
        batch_id=str(batch.get("id")),
        prompt_text=str(prompt.get("text", "")),
        network_hash=str(manifest.get("network_hash", "")),
        network_version=str(manifest.get("version", "v1")),
        cpt_contract=dict(cpt_contract),
    )


def _posterior_fields(post: Any) -> tuple[str, list[Any], list[Any]]:
    """Single Mapping/object shape for posteriors (no duplicated branches)."""
    if isinstance(post, Mapping):
        return (
            str(post.get("node_id", "")),
            list(post.get("states", ())),
            list(post.get("probabilities", ())),
        )
    return (
        str(getattr(post, "node_id", "")),
        list(getattr(post, "states", ())),
        list(getattr(post, "probabilities", ())),
    )


def _argmax_per_node(
    posteriors: Sequence[Any], declared: Mapping[str, Sequence[str]]
) -> dict[str, tuple[str, float]]:
    """Map node_id -> (argmax_state, prob). Ties break by declared order."""
    result: dict[str, tuple[str, float]] = {}
    for post in posteriors:
        node, states, probs = _posterior_fields(post)
        if not node or len(states) != len(probs) or not states:
            raise ValueError(f"RESULT_INVALID: posterior shape drifted for {node!r}")
        order = list(declared.get(node, states))
        # Deterministic: first max in declared order wins ties.
        best_state = states[0]
        best_prob = float(probs[states.index(best_state)] if best_state in states else probs[0])
        # Walk declared order for tie-breaking.
        for state in order:
            if state not in states:
                continue
            prob = float(probs[states.index(state)])
            if prob > best_prob:
                best_prob = prob
                best_state = state
        result[node] = (best_state, float(best_prob))
    return result


def render_section(
    template: Mapping[str, Any],
    posteriors: Sequence[Any],
    declared_states: Mapping[str, Sequence[str]],
) -> str:
    """Render one template section from the stored result only.

    No provider text is read here (caller never passes it). Branch
    ``when`` is ``{node, state, operator}``; ``==``/``!=`` compare the
    node's argmax state. Slots ``{Node}`` expand to the escaped
    ``"<state> <pct>%"`` summary. Raises ``ValueError`` when no branch
    matches or a slot/operator is unsupported (rendering failure, never
    LLM prose fallback).
    """
    branches = template.get("branches")
    if not isinstance(branches, (list, tuple)) or not branches:
        raise ValueError("TEMPLATE_INVALID: template needs branches")
    argmax = _argmax_per_node(posteriors, declared_states)
    chosen: Mapping[str, Any] | None = None
    for entry in branches:
        if not isinstance(entry, Mapping):
            continue
        when = entry.get("when")
        text = entry.get("text")
        if not isinstance(when, Mapping) or not isinstance(text, str) or not text.strip():
            continue
        node = str(when.get("node", ""))
        state = str(when.get("state", ""))
        operator = str(when.get("operator", "=="))
        if node not in argmax:
            continue
        current, _ = argmax[node]
        if operator == "==":
            if current == state:
                chosen = entry
                break
        elif operator == "!=":
            if current != state:
                chosen = entry
                break
        elif operator in RENDER_UNSUPPORTED_OPERATORS:
            raise ValueError(f"TEMPLATE_UNSUPPORTED_OPERATOR: {operator!r}")
        else:
            raise ValueError(f"TEMPLATE_UNSUPPORTED_OPERATOR: {operator!r}")
    if chosen is None:
        raise ValueError("TEMPLATE_NO_MATCH: no branch matched the stored result")
    text = str(chosen.get("text", ""))
    # Slot expansion: {Node} -> escaped "<argmax-state> <pct>%".
    import re

    def _replace(match: Any) -> str:
        token = str(match.group(1))
        if token not in argmax:
            raise ValueError(f"TEMPLATE_BAD_SLOT: {{{token}}} is not a result node")
        state, prob = argmax[token]
        summary = f"{state} {prob * 100:.2f}%"
        return html.escape(summary, quote=True)

    try:
        rendered = re.sub(r"\{([A-Za-z0-9_]+)\}", _replace, text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"TEMPLATE_INVALID: {exc}") from exc
    # A template without any slot is a shape error (S25 requires slots).
    if "{" in rendered or "}" in rendered:
        raise ValueError("TEMPLATE_BAD_SLOT: unbalanced value slots")
    return rendered


def _posteriors_to_json(posteriors: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for post in posteriors:
        node, states, probs = _posterior_fields(post)
        out.append(
            {
                "node_id": node,
                "states": [str(s) for s in states],
                "probabilities": [float(v) for v in probs],
            }
        )
    return out


def _validated_tables_to_json(report: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table in report.tables:
        out.append(
            {
                "node_id": str(table.node_id),
                "parent_ids": [str(p) for p in list(table.parent_ids)],
                "states": [str(s) for s in list(table.states)],
                "rows": [
                    {
                        "parent_states": [str(s) for s in list(row.parent_states)],
                        "percentages": [str(v) for v in list(row.percentages)],
                    }
                    for row in table.rows
                ],
            }
        )
    return out


def safe_baseline(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public baseline shape (persisted data only, never tokens/secrets)."""
    return {
        "id": str(row.get("id")),
        "question_run_id": str(row.get("question_run_id")),
        "batch_id": str(row.get("batch_id")),
        "source_hash": str(row.get("source_hash")),
        "effective_hash": str(row.get("effective_hash")),
        "effective_xml": str(row.get("effective_xml")),
        "raw_response": dict(row.get("raw_response") or {}),
        "validated_tables": list(row.get("validated_tables") or []),
        "query_nodes": list(row.get("query_nodes") or []),
        "posteriors": list(row.get("posteriors") or []),
        "section_text": str(row.get("section_text")),
        "template_version": str(row.get("template_version")),
        "prompt_version": str(row.get("prompt_version")),
        "network_version": str(row.get("network_version")),
        "provider_model": str(row.get("provider_model")),
        "projection_hash": str(row.get("projection_hash")),
        "provenance": dict(row.get("provenance") or {}),
        "created_at": contracts.serialize_utc(row["created_at"]),
    }


def get_baseline(session: Session, question_run_id: Any) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(reasoning_tables.original_baselines).where(
                reasoning_tables.original_baselines.c.question_run_id == question_run_id
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def build_transparency(
    run: Mapping[str, Any], baseline: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Five required transparency fields from persisted data (or None)."""
    if baseline is None:
        return None
    projection = run.get("projection") if isinstance(run.get("projection"), dict) else {}
    variables = projection.get("variables") if isinstance(projection, dict) else []
    return {
        "question_key": str(run.get("question_key", "")),
        "network_version": str(baseline.get("network_version", "")),
        "saved_patient_inputs": list(variables or []),
        "returned_cpt_percentages": list(baseline.get("validated_tables") or []),
        "deterministic_result": {
            "posteriors": list(baseline.get("posteriors") or []),
            "section_text": str(baseline.get("section_text", "")),
            "query_nodes": list(baseline.get("query_nodes") or []),
            "effective_hash": str(baseline.get("effective_hash", "")),
        },
    }


def _resume_bundle(
    job: Mapping[str, Any], manifest: Mapping[str, Any], run: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Accepted stage artifacts from a prior attempt of this same job (S47).

    Returns ``{"kind": "render", ...}`` (resume template rendering from the
    stored inference result) or ``{"kind": "infer", ...}`` (resume inference
    from the stored CPTs without re-estimating), or None for a fresh
    estimate. Bundles only match the same pinned network + projection; a
    mismatch (new inputs) never reuses stale artifacts.
    """
    diagnostics = job.get("diagnostics")
    store = diagnostics.get("stage_artifacts") if isinstance(diagnostics, dict) else None
    if not isinstance(store, dict):
        return None
    want_network = str(manifest.get("network_hash") or "")
    want_projection = str(run.get("projection_hash") or "")
    for key, kind in (("s47_inference", "render"), ("s47_validated", "infer")):
        bundle = store.get(key)
        if not isinstance(bundle, dict):
            continue
        if str(bundle.get("network_hash") or "") != want_network:
            continue
        if str(bundle.get("projection_hash") or "") != want_projection:
            continue
        if not isinstance(bundle.get("tables"), list) or not bundle["tables"]:
            continue
        if kind == "render":
            if not isinstance(bundle.get("posteriors"), list) or not bundle["posteriors"]:
                continue
            if not isinstance(bundle.get("effective_xml"), str) or not bundle["effective_xml"]:
                continue
            if not isinstance(bundle.get("effective_hash"), str):
                continue
            if not isinstance(bundle.get("validated_tables"), list):
                continue
        return {"kind": kind, **{k: v for k, v in bundle.items()}}
    return None


def _validated_bundle(
    network_hash: str,
    projection_hash: str,
    tables: Any,
    validated_json: list[dict[str, Any]] | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Stage artifact persisted when inference fails (resume without re-estimating)."""
    meta = payload if isinstance(payload, Mapping) else {}
    return {
        "s47_validated": {
            "network_hash": str(network_hash),
            "projection_hash": str(projection_hash),
            "tables": tables,
            "validated_tables": list(validated_json or []),
            "payload_meta": {
                "tool_calls_made": int(meta.get("tool_calls_made", 0) or 0),
                "capability": str(meta.get("capability", "") or ""),
            },
        }
    }


def _commit_failure(
    engine: Engine,
    job_id: Any,
    lease_token: str,
    error_code: str,
    now: datetime,
    retryable: bool = False,
    stage: str = "preparing_question",
    stage_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from x_insight.operations import audit as audit_module

    with db_module.session_scope(engine) as session:
        try:
            committed = queue_module.commit_job_result(
                session,
                job_id,
                lease_token,
                provider_ok=False,
                provider_payload={},
                provider_error=error_code,
                now=now,
                retryable=retryable,
                stage=stage,
                stage_artifact=stage_artifact,
            )
        except contracts.ContractError as exc:
            if exc.code in (
                "FENCING_TOKEN_MISMATCH",
                "LEASE_EXPIRED",
                "DEPLOYMENT_FENCED",
            ):
                return {"status": "fencing_failed", "job_id": str(job_id), "code": exc.code}
            raise
        # S52: terminal original-run failures audit accurate failure
        # metadata in the same transaction (no false success: success rows
        # are only written on the baseline path). Retryable intermediates
        # that requeue stay in the attempt ledger, not audit.
        if str(committed.get("status")) == queue_module.FAILED:
            job_row = dict(committed)
            batch_id = job_row.get("batch_id")
            run_id = job_row.get("question_run_id")
            batch_dict: dict[str, Any] = {}
            run_dict: dict[str, Any] = {}
            if batch_id is not None:
                found = (
                    session.execute(
                        select(reasoning_tables.generation_batches).where(
                            reasoning_tables.generation_batches.c.id == batch_id
                        )
                    )
                    .mappings()
                    .first()
                )
                batch_dict = dict(found) if found is not None else {}
            if run_id is not None:
                found = (
                    session.execute(
                        select(reasoning_tables.question_runs).where(
                            reasoning_tables.question_runs.c.id == run_id
                        )
                    )
                    .mappings()
                    .first()
                )
                run_dict = dict(found) if found is not None else {}
            patient_id = _patient_id_for_batch(session, batch_dict)
            try:
                attempt_index = int(job_row.get("attempt_index", 0))
            except Exception:
                attempt_index = 0
            audit_module.record_audit(
                session,
                operation=audit_module.ORIGINAL_RUN_FAILED_OPERATION,
                actor="worker",
                request_id=str(job_id),
                details={
                    "patient_id": patient_id,
                    "encounter_id": str(batch_dict.get("encounter_id"))
                    if batch_dict.get("encounter_id") is not None
                    else None,
                    "batch_id": str(batch_id),
                    "question_run_id": str(run_id),
                    "question_key": str(run_dict.get("question_key", "")),
                    "job_id": str(job_id),
                    "error_code": str(error_code),
                    "stage": str(stage),
                    "attempt_index": int(attempt_index),
                },
            )
    return {"status": str(committed.get("status")), "job_id": str(committed.get("id"))}


def _patient_id_for_batch(session: Session, batch: Mapping[str, Any]) -> str | None:
    """Patient id for audit target correlation (None when unresolvable)."""
    try:
        from x_insight.cases import tables as cases_tables

        encounter_id = batch.get("encounter_id")
        if encounter_id is None:
            return None
        row = (
            session.execute(
                select(cases_tables.encounters).where(
                    cases_tables.encounters.c.id == encounter_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return str(dict(row).get("patient_id"))
    except Exception:
        return None


def execute_claimed_job(
    engine: Engine,
    job: Mapping[str, Any],
    run: Mapping[str, Any],
    batch: Mapping[str, Any],
    lease_token: str,
    adapter: Any,
    now: datetime,
    worker_id: str,
) -> dict[str, Any]:
    """Execute one claimed generation job end to end (S45 single-question).

    ``adapter`` is a per-job ``BoundedProviderAdapter`` already bound to the
    claim's grant (real MCP) and the controlled endpoint. All stages run
    outside any DB transaction; only the final commit opens one. Success
    atomically inserts the immutable baseline and marks the job succeeded
    under the same fencing checks. Any failure marks the job failed with
    no baseline row (no adjustable baseline).
    """
    from x_insight.models.inference import (
        build_effective_artifact,
        infer_effective,
        validate_cpts,
    )
    from x_insight.models.question_package import validate_question_package

    job_id = job["id"]
    run_id = run["id"]

    # Frozen package (immutable at start; fail closed when absent).
    package: Any = run.get("pinned_package")
    if not isinstance(package, dict) or not isinstance(package.get("manifest"), Mapping):
        return {
            **_commit_failure(engine, job_id, lease_token, "MISSING_PACKAGE", now),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }

    # Registered network (base bytes never mutated below).
    try:
        document = load_document(package)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0].strip() or "NETWORK_INVALID"
        return {
            **_commit_failure(engine, job_id, lease_token, code, now),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }

    # Package shape against the real document (order/states/parents/hash).
    try:
        report_pkg = validate_question_package(package, document)
    except Exception:
        return {
            **_commit_failure(engine, job_id, lease_token, "PACKAGE_INVALID", now),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }
    if not report_pkg.valid:
        first = report_pkg.errors[0] if report_pkg.errors else None
        code = str(first.code).upper() if first is not None else "PACKAGE_INVALID"
        return {
            **_commit_failure(engine, job_id, lease_token, code, now),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }

    manifest = package["manifest"]
    assert isinstance(manifest, Mapping)
    prompt = package.get("prompt")
    template = package.get("template")
    if not isinstance(prompt, Mapping) or not isinstance(template, Mapping):
        return {
            **_commit_failure(engine, job_id, lease_token, "PACKAGE_INVALID", now),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }

    # S47 stage resume: a prior attempt of this job may have persisted
    # accepted CPTs (resume inference without a new provider request) or a
    # full inference result (resume rendering). Fresh attempts estimate.
    resume = _resume_bundle(job, manifest, run)
    render_posteriors_source: Any = None
    posteriors_json: list[dict[str, Any]] = []
    validated_json: list[dict[str, Any]] = []
    raw_json: dict[str, Any] = {}
    effective_text = ""
    effective_hash = ""
    query_nodes = list(manifest.get("query_nodes", []))
    payload: dict[str, Any] = {}
    cpt_report: Any = None
    artifact: Any = None
    inference: Any = None

    if resume is not None and resume.get("kind") == "render":
        # Rendering retry reuses the stored result (no provider/inference).
        posteriors_json = [dict(p) for p in resume["posteriors"] if isinstance(p, dict)]
        validated_json = list(resume["validated_tables"])
        raw_json = {"network_hash": resume["network_hash"], "tables": resume["tables"]}
        effective_text = str(resume["effective_xml"])
        effective_hash = str(resume["effective_hash"])
        if isinstance(resume.get("query_nodes"), list) and resume["query_nodes"]:
            query_nodes = [str(q) for q in resume["query_nodes"]]
        meta = resume.get("payload_meta")
        payload = dict(meta) if isinstance(meta, dict) else {}
        render_posteriors_source = list(posteriors_json)
    else:
        resumed_tables: Any = None
        if resume is not None and resume.get("kind") == "infer":
            resumed_tables = resume["tables"]
            meta = resume.get("payload_meta")
            payload = dict(meta) if isinstance(meta, dict) else {}
        # Bounded estimation over real MCP (initial + tool-bridged reads).
        # Skipped only when resuming inference from accepted CPTs.
        tables: Any = resumed_tables
        network_hash: Any = manifest.get("network_hash")
        if resumed_tables is None:
            try:
                request = build_provider_request(run, batch, package)
            except Exception:
                return {
                    **_commit_failure(engine, job_id, lease_token, "PROVIDER_CAPABILITY", now),
                    "worker_id": worker_id,
                    "batch_id": str(batch.get("id")),
                }
            try:
                estimate = adapter.estimate(request)
            except Exception:
                return {
                    **_commit_failure(
                        engine,
                        job_id,
                        lease_token,
                        "PROVIDER_TRANSIENT",
                        now,
                        retryable=True,
                        stage="estimating_cpts",
                    ),
                    "worker_id": worker_id,
                    "batch_id": str(batch.get("id")),
                }
            if not bool(getattr(estimate, "ok", False)):
                code = str(getattr(estimate, "error_code", None) or "PROVIDER_FAILED")
                return {
                    **_commit_failure(
                        engine,
                        job_id,
                        lease_token,
                        code,
                        now,
                        retryable=bool(getattr(estimate, "retryable", False)),
                        stage="estimating_cpts",
                    ),
                    "worker_id": worker_id,
                    "batch_id": str(batch.get("id")),
                }
            payload = getattr(estimate, "payload", {}) or {}
            if not isinstance(payload, dict):
                return {
                    **_commit_failure(
                        engine,
                        job_id,
                        lease_token,
                        "PROVIDER_INVALID_RESPONSE",
                        now,
                        retryable=True,
                        stage="validating_cpts",
                    ),
                    "worker_id": worker_id,
                    "batch_id": str(batch.get("id")),
                }
            tables = payload.get("tables")
            network_hash = payload.get("network_hash", manifest.get("network_hash"))
        s23_payload = {"network_hash": network_hash, "tables": tables}

        # All-CPT validation against the registered document (no defaults).
        # Resumed CPTs revalidate locally (no provider request); accepted
        # tables were already valid under this same pinned package.
        try:
            cpt_report = validate_cpts(document, s23_payload)
        except Exception:
            return {
                **_commit_failure(
                    engine,
                    job_id,
                    lease_token,
                    "CPT_INVALID",
                    now,
                    retryable=True,
                    stage="validating_cpts",
                ),
                "worker_id": worker_id,
                "batch_id": str(batch.get("id")),
            }
        if not cpt_report.valid:
            cpt_first = cpt_report.errors[0] if cpt_report.errors else None
            code = str(cpt_first.code).upper() if cpt_first is not None else "CPT_INVALID"
            return {
                **_commit_failure(
                    engine,
                    job_id,
                    lease_token,
                    code,
                    now,
                    retryable=True,
                    stage="validating_cpts",
                ),
                "worker_id": worker_id,
                "batch_id": str(batch.get("id")),
            }

        # Effective XML (new run-local bytes; registered source untouched).
        try:
            artifact = build_effective_artifact(
                document, s23_payload, query_nodes=query_nodes or None
            )
        except Exception as exc:
            code = getattr(exc, "code", "EFFECTIVE_INVALID")
            return {
                **_commit_failure(
                    engine,
                    job_id,
                    lease_token,
                    str(code).upper(),
                    now,
                    retryable=True,
                    stage="inferring",
                    stage_artifact=_validated_bundle(
                        str(network_hash),
                        str(run.get("projection_hash", "")),
                        tables,
                        _validated_tables_to_json(cpt_report),
                        payload,
                    ),
                ),
                "worker_id": worker_id,
                "batch_id": str(batch.get("id")),
            }
        # Empty-evidence exact inference (projection is provenance only).
        try:
            inference = infer_effective(artifact, patient_projection=run.get("projection"))
        except Exception as exc:
            code = getattr(exc, "code", "INFERENCE_FAILED")
            return {
                **_commit_failure(
                    engine,
                    job_id,
                    lease_token,
                    str(code).upper(),
                    now,
                    retryable=True,
                    stage="inferring",
                    stage_artifact=_validated_bundle(
                        str(network_hash),
                        str(run.get("projection_hash", "")),
                        tables,
                        _validated_tables_to_json(cpt_report),
                        payload,
                    ),
                ),
                "worker_id": worker_id,
                "batch_id": str(batch.get("id")),
            }
        render_posteriors_source = list(inference.posteriors)
        posteriors_json = _posteriors_to_json(list(inference.posteriors))
        validated_json = _validated_tables_to_json(cpt_report)
        raw_json = {"network_hash": network_hash, "tables": tables}
        try:
            effective_text = bytes(artifact.effective_bytes).decode("utf-8")
        except Exception:
            return {
                **_commit_failure(
                    engine,
                    job_id,
                    lease_token,
                    "EFFECTIVE_INVALID",
                    now,
                    retryable=True,
                    stage="rendering",
                    stage_artifact={
                        **_validated_bundle(
                            str(network_hash),
                            str(run.get("projection_hash", "")),
                            tables,
                            _validated_tables_to_json(cpt_report),
                            payload,
                        ),
                        "s47_inference": {
                            "network_hash": str(network_hash),
                            "projection_hash": str(run.get("projection_hash", "")),
                            "tables": tables,
                            "validated_tables": _validated_tables_to_json(cpt_report),
                            "posteriors": _posteriors_to_json(list(inference.posteriors)),
                            "query_nodes": list(query_nodes),
                            "effective_xml": "",
                            "effective_hash": str(artifact.effective_sha256),
                            "payload_meta": {
                                "tool_calls_made": int(payload.get("tool_calls_made", 0) or 0),
                                "capability": str(payload.get("capability", "") or ""),
                            },
                        },
                    },
                ),
                "worker_id": worker_id,
                "batch_id": str(batch.get("id")),
            }
        effective_hash = str(artifact.effective_sha256)

    # Local template rendering from the reviewed mapping + stored result.
    # Resumed attempts render the persisted posteriors (never provider text).
    declared_states: dict[str, Sequence[str]] = {}
    variables = manifest.get("variables", [])
    if isinstance(variables, (list, tuple)):
        for entry in variables:
            if isinstance(entry, Mapping) and isinstance(entry.get("node_id"), str):
                states = entry.get("states", [])
                if isinstance(states, (list, tuple)):
                    declared_states[str(entry["node_id"])] = [str(s) for s in states]
    try:
        section = render_section(template, list(render_posteriors_source), declared_states)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0].strip() or "TEMPLATE_INVALID"
        return {
            **_commit_failure(
                engine,
                job_id,
                lease_token,
                code,
                now,
                retryable=True,
                stage="rendering",
                stage_artifact={
                    **_validated_bundle(
                        str(raw_json.get("network_hash", "")),
                        str(run.get("projection_hash", "")),
                        raw_json.get("tables"),
                        validated_json or None,
                        payload,
                    ),
                    "s47_inference": {
                        "network_hash": str(raw_json.get("network_hash", "")),
                        "projection_hash": str(run.get("projection_hash", "")),
                        "tables": raw_json.get("tables"),
                        "validated_tables": list(validated_json),
                        "posteriors": list(posteriors_json),
                        "query_nodes": list(query_nodes),
                        "effective_xml": str(effective_text),
                        "effective_hash": str(effective_hash),
                        "payload_meta": {
                            "tool_calls_made": int(payload.get("tool_calls_made", 0) or 0),
                            "capability": str(payload.get("capability", "") or ""),
                        },
                    },
                },
            ),
            "worker_id": worker_id,
            "batch_id": str(batch.get("id")),
        }

    # Atomic success: fencing + eligibility + baseline insert + job success.
    model_name = ""
    try:
        model_name = str(adapter.config.model or "")
    except Exception:
        model_name = ""
    provenance: dict[str, Any] = {
        "question_key": str(run.get("question_key", "")),
        "projection_hash": str(run.get("projection_hash", "")),
        "prompt_version": str(manifest.get("prompt_version", "")),
        "template_version": str(manifest.get("template_version", "")),
        "network_version": str(manifest.get("version", "")),
        "network_hash": str(manifest.get("network_hash", "")),
        "provider_model": model_name,
        "attempt_index": int(job.get("attempt_index", 0)),
        "worker_id": worker_id,
        "tool_calls_made": int(payload.get("tool_calls_made", 0) or 0),
        "capability": str(payload.get("capability", "") or ""),
        "query_nodes": list(query_nodes),
        "effective_hash": str(effective_hash),
    }
    with db_module.session_scope(engine) as session:
        # Same fencing as queue.commit_job_result, kept inline so the
        # baseline insert stays atomic with the job success (one commit).
        # Failure paths reuse queue.commit_job_result via _commit_failure.
        locked = (
            session.execute(
                select(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if locked is None:
            raise contracts.ContractError(404, "NOT_FOUND", "Job not found.")
        current = dict(locked)
        if (
            str(current.get("status")) != queue_module.LEASED
            or str(current.get("lease_token") or "") != lease_token
        ):
            return {
                "status": "fencing_failed",
                "worker_id": worker_id,
                "job_id": str(job_id),
                "code": "FENCING_TOKEN_MISMATCH",
            }
        deadline = current.get("lease_deadline")
        if not isinstance(deadline, datetime) or deadline <= now:
            return {
                "status": "fencing_failed",
                "worker_id": worker_id,
                "job_id": str(job_id),
                "code": "LEASE_EXPIRED",
            }
        if int(current.get("deployment_generation", 0)) != queue_module.get_deployment_generation(
            session
        ):
            return {
                "status": "fencing_failed",
                "worker_id": worker_id,
                "job_id": str(job_id),
                "code": "DEPLOYMENT_FENCED",
            }
        batch_row = (
            session.execute(
                select(reasoning_tables.generation_batches).where(
                    reasoning_tables.generation_batches.c.id == batch["id"]
                )
            )
            .mappings()
            .first()
        )
        run_row = (
            session.execute(
                select(reasoning_tables.question_runs).where(
                    reasoning_tables.question_runs.c.id == run_id
                )
            )
            .mappings()
            .first()
        )
        if batch_row is None or run_row is None:
            raise contracts.ContractError(404, "NOT_FOUND", "Batch or run missing.")
        eligible, _ = queue_module.is_job_eligible(
            session, current, dict(batch_row), dict(run_row), now
        )
        if not eligible:
            session.execute(
                update(reasoning_tables.reasoning_jobs)
                .where(reasoning_tables.reasoning_jobs.c.id == job_id)
                .values(lease_token=None, status=queue_module.CANCELLED, updated_at=now)
            )
            session.execute(
                update(reasoning_tables.reasoning_job_attempts)
                .where(
                    reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
                    reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
                    reasoning_tables.reasoning_job_attempts.c.outcome == "started",
                )
                .values(finished_at=now, outcome="failed", error_code="STALE_INPUT")
            )
            session.execute(
                update(reasoning_tables.reasoning_grants)
                .where(
                    reasoning_tables.reasoning_grants.c.job_id == job_id,
                    reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            session.flush()
            return {
                "status": str(queue_module.CANCELLED),
                "worker_id": worker_id,
                "job_id": str(job_id),
                "batch_id": str(batch.get("id")),
            }
        # Immutable baseline first (unique per run; second success would conflict).
        existing = (
            session.execute(
                select(reasoning_tables.original_baselines).where(
                    reasoning_tables.original_baselines.c.question_run_id == run_id
                )
            )
            .mappings()
            .first()
        )
        baseline_id = uuid.uuid4()
        if existing is None:
            session.execute(
                insert(reasoning_tables.original_baselines).values(
                    id=baseline_id,
                    question_run_id=run_id,
                    batch_id=batch["id"],
                    source_hash=str(document.source_sha256),
                    effective_xml=effective_text,
                    effective_hash=str(effective_hash),
                    raw_response=dict(raw_json),
                    validated_tables=list(validated_json),
                    query_nodes=list(query_nodes),
                    posteriors=list(posteriors_json),
                    section_text=str(section),
                    template_version=str(manifest.get("template_version", "")),
                    prompt_version=str(manifest.get("prompt_version", "")),
                    network_version=str(manifest.get("version", "")),
                    provider_model=model_name,
                    projection_hash=str(run.get("projection_hash", "")),
                    provenance=dict(provenance),
                    created_at=now,
                )
            )
        session.execute(
            update(reasoning_tables.reasoning_jobs)
            .where(reasoning_tables.reasoning_jobs.c.id == job_id)
            .values(
                lease_token=None,
                status=queue_module.SUCCEEDED,
                result={
                    "projection_hash": str(run.get("projection_hash", "")),
                    "effective_hash": str(effective_hash),
                    "query_nodes": list(query_nodes),
                    "posteriors": list(posteriors_json),
                },
                updated_at=now,
            )
        )
        session.execute(
            update(reasoning_tables.reasoning_job_attempts)
            .where(
                reasoning_tables.reasoning_job_attempts.c.job_id == job_id,
                reasoning_tables.reasoning_job_attempts.c.lease_token == lease_token,
                reasoning_tables.reasoning_job_attempts.c.outcome == "started",
            )
            .values(
                finished_at=now,
                outcome="succeeded",
                error_code=None,
                result={
                    "projection_hash": str(run.get("projection_hash", "")),
                    "effective_hash": str(effective_hash),
                },
            )
        )
        session.execute(
            update(reasoning_tables.reasoning_grants)
            .where(
                reasoning_tables.reasoning_grants.c.job_id == job_id,
                reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        session.flush()
        # S46 ordered workflows, same atomic transaction: persist result +
        # rendered section (baseline above) before enabling the successor.
        # Only one queued job exists per batch at a time (no intra-run
        # parallelism). Then attempt immutable proposal assembly (all
        # applicable baselines + valid pinned DDI, no LLM writing).
        try:
            deployment_for_next = int(current.get("deployment_generation", 0))
        except Exception:
            deployment_for_next = int(queue_module.get_deployment_generation(session))
        queue_module.insert_successor_job(
            session,
            batch_id=batch["id"],
            completed_run_id=run_id,
            deployment_generation=int(deployment_for_next),
            now=now,
        )
        try_assemble_proposal(session, batch["id"], now)
        session.flush()
        # S52: original-run success audits tool/attempt metadata with the
        # baseline in the same transaction (mutation + event are atomic).
        from x_insight.operations import audit as audit_module

        run_dict = dict(run_row)
        batch_dict = dict(batch_row)
        try:
            attempt_index = int(current.get("attempt_index", 0))
        except Exception:
            attempt_index = 0
        audit_module.record_audit(
            session,
            operation=audit_module.ORIGINAL_RUN_SUCCESS_OPERATION,
            actor="worker",
            request_id=str(job_id),
            details={
                "patient_id": _patient_id_for_batch(session, batch_dict),
                "encounter_id": str(batch_dict.get("encounter_id")),
                "batch_id": str(batch.get("id")),
                "question_run_id": str(run_id),
                "question_key": str(run_dict.get("question_key", "")),
                "job_id": str(job_id),
                "baseline_id": str(baseline_id if existing is None else dict(existing).get("id")),
                "attempt_index": int(attempt_index),
                "tool_calls_made": int(payload.get("tool_calls_made", 0) or 0),
                "capability": str(payload.get("capability", "") or ""),
                "provider_model": model_name,
                "projection_hash": str(run.get("projection_hash", "")),
            },
        )
        session.flush()
        committed = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        assert committed is not None
        final = dict(committed)
    return {
        "status": str(final.get("status")),
        "worker_id": worker_id,
        "job_id": str(final.get("id")),
        "batch_id": str(final.get("batch_id")),
    }
