"""Model version administration registry (S24, seams T1/T5; plan.md §§4.1, 7.3).

Public seam (callers, routes, and tests use the same functions):

- :func:`create_network` / :func:`create_version` — admin import and
  immutable editing; edits insert a new row, prior bytes/hash/reports stay.
- :func:`list_networks` / :func:`list_versions` / :func:`get_network` /
  :func:`get_version` — version history reads (stable order, pagination).
- :func:`build_reports` — separate structural/semantic/content/admission
  reports over stored bytes (S21 + S22 + S25); XSD success is structural
  only, never labeled executable or clinically valid.
- :func:`build_graph` — read-only ordered nodes/edges/states + validation
  state; no edit semantics.
- :func:`export_version_bytes` — exact preserved source bytes by identity.
- :func:`activate_bundle` / :func:`rollback_bundle` / :func:`get_pointer` —
  atomic workflow pointer moves with revision + transaction-scoped audit.

What this module does and does not promise:

- Stored bytes are exact; ``source_hash`` is SHA-256 over those bytes.
  Validation reports are recomputed from bytes (deterministic), so prior
  versions keep their bytes/hash/reports after later edits by construction.
- ``valid``/``executable`` mean pipeline admission only, never clinical
  validity. Drafts may be incomplete and viewable; only reviewed +
  semantically valid versions can activate.
- Bundles are synthetic selections until S39 defines real bundles
  (``ponytail: pointer stores synthetic selections as JSON; add a real
  bundles table when S39 needs immutable bundle rows beyond the pointer``).
- No draft BN is active by default (no seed pointer rows).

Bounds are engineering limits (``MAX_*``), not clinical thresholds.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.models import admission as admission_module
from x_insight.models import question_package as package_module
from x_insight.models import validation as validation_module

# --- Engineering guardrails (resource/diagnostic bounds, not clinical) ---

MAX_NETWORK_NAME_CHARS = 100
MAX_MESSAGE_CHARS = 500
MAX_BUNDLE_SELECTIONS = 32
MAX_VERSIONS_PER_NETWORK = 256
SUPPORTED_WORKFLOWS = frozenset({"registration", "followup"})
SUPPORTED_REVIEW_DECISIONS = frozenset({"draft", "awaiting_review", "approved"})


def _clip(message: str) -> str:
    return message[:MAX_MESSAGE_CHARS]


def _fail(
    status: int, code: str, message: str, field_errors: dict[str, list[str]] | None = None
) -> contracts.ContractError:
    return contracts.ContractError(status, code, _clip(message), field_errors)


def validate_name(name: Any) -> str:
    """Admin network name: non-empty, bounded (422 otherwise)."""
    if not isinstance(name, str) or not name.strip():
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "Network name is required.",
            {"name": ["Must be a non-empty string."]},
        )
    cleaned = name.strip()
    if len(cleaned) > MAX_NETWORK_NAME_CHARS:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            f"Network name exceeds {MAX_NETWORK_NAME_CHARS} characters.",
            {"name": [f"Must be 1-{MAX_NETWORK_NAME_CHARS} characters."]},
        )
    return cleaned


def parse_review(review: Any) -> dict[str, Any]:
    """Normalize an optional review record; status derives from decision.

    Absent review means ``draft`` with an empty record. Supplied review needs
    non-empty reviewer, decision in ``SUPPORTED_REVIEW_DECISIONS``, and an
    ISO-8601 date. Messages are bounded; never invents approval.
    """
    if review is None:
        return {"reviewer": "", "decision": "draft", "date": "", "status": "draft"}
    if not isinstance(review, dict):
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "Review must be an object.",
            {"review": ["Must be an object."]},
        )
    reviewer = review.get("reviewer", "")
    decision = review.get("decision", "draft")
    date = review.get("date", "")
    errors: dict[str, list[str]] = {}
    if not isinstance(reviewer, str) or not reviewer.strip():
        # Draft without reviewer is allowed; approved requires one (checked below).
        reviewer = reviewer if isinstance(reviewer, str) else ""
    if decision not in SUPPORTED_REVIEW_DECISIONS:
        errors["review.decision"] = [f"Must be one of {sorted(SUPPORTED_REVIEW_DECISIONS)}."]
    if not isinstance(date, str) or not date.strip():
        # Draft without date is allowed; approved requires one (checked below).
        date = date if isinstance(date, str) else ""
    else:
        try:
            datetime.fromisoformat(date.strip())
        except ValueError:
            errors["review.date"] = ["Must be ISO-8601."]
    if errors:
        raise _fail(422, "VALIDATION_FAILED", "Invalid review record.", errors)
    cleaned = {
        "reviewer": reviewer.strip(),
        "decision": decision,
        "date": date.strip(),
        "status": decision,
    }
    return cleaned


def require_approved_review(review: dict[str, Any], *, label: str) -> None:
    """Gate for activation: reviewer + approved decision + date required."""
    if (
        review.get("decision") != "approved"
        or not str(review.get("reviewer", "")).strip()
        or not str(review.get("date", "")).strip()
    ):
        raise _fail(
            422,
            "UNREVIEWED_BUNDLE",
            f"{label} needs an approved review (reviewer, decision approved, date).",
            {"review": ["Requires reviewer + approved decision + date."]},
        )


def coerce_xml_bytes(xml_text: Any) -> bytes:
    """Coerce an ``xml_text`` string to exact bytes; 422 on bad input.

    Oversize/malformed/unsafe inputs raise 422 here; XSD violations do not —
    they are stored as drafts with ``xsd.valid False`` (S21 slice 3).
    """
    if not isinstance(xml_text, str) or not xml_text.strip():
        raise _fail(
            422,
            "VALIDATION_FAILED",
            "xml_text is required.",
            {"xml_text": ["Must be a non-empty XML string."]},
        )
    data = xml_text.encode("utf-8")
    _ensure_safe_xml_bytes(data)
    return data


def _ensure_safe_xml_bytes(xml_bytes: bytes) -> None:
    """Reject oversize/unsafe/malformed bytes; XSD-invalid stays storable."""
    if len(xml_bytes) > validation_module.MAX_XML_BYTES:
        raise _fail(
            422,
            "XML_TOO_LARGE",
            f"XML is {len(xml_bytes)} bytes (limit {validation_module.MAX_XML_BYTES}).",
            {"xml_text": [f"Must be <= {validation_module.MAX_XML_BYTES} bytes."]},
        )
    try:
        validation_module.validate(xml_bytes)
    except validation_module.XmlValidationError as exc:
        raise _fail(
            422, "XML_INVALID", f"{exc.code}: {exc.message}", {"xml_text": [exc.code]}
        ) from None


def _version_row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("id", "network_id"):
        value = item.get(key)
        if isinstance(value, uuid.UUID):
            item[key] = str(value)
    created = item.get("created_at")
    if isinstance(created, datetime):
        try:
            item["created_at"] = contracts.serialize_utc(created)
        except ValueError:
            item["created_at"] = str(created)
    return item


def create_network(
    session: Session,
    *,
    name: str,
    xml_bytes: bytes,
    review: dict[str, Any] | None,
    actor: str | None,
) -> dict[str, Any]:
    """Import one network + its first immutable version in one transaction.

    Caller owns commit (transaction-scoped, S02); this flushes rows only.
    """
    cleaned_name = validate_name(name)
    normalized = parse_review(review)
    _ensure_safe_xml_bytes(xml_bytes)
    source_hash = hashlib.sha256(xml_bytes).hexdigest()
    network_id = uuid.uuid4()
    session.execute(
        text("INSERT INTO model_networks (id, name, created_by) VALUES (:id, :name, :actor)"),
        {"id": network_id, "name": cleaned_name, "actor": actor},
    )
    version_id = uuid.uuid4()
    import json as _json

    session.execute(
        text(
            "INSERT INTO model_network_versions "
            "(id, network_id, version_number, source_bytes, source_hash, "
            "status, review, created_by) "
            "VALUES (:id, :network_id, 1, :bytes, :hash, :status, "
            "CAST(:review AS JSONB), :actor)"
        ),
        {
            "id": version_id,
            "network_id": network_id,
            "bytes": xml_bytes,
            "hash": source_hash,
            "status": normalized["status"],
            "review": _json.dumps({k: v for k, v in normalized.items() if k != "status"}),
            "actor": actor,
        },
    )
    session.flush()
    network = get_network(session, network_id)
    version = get_version(session, version_id)
    assert network is not None and version is not None
    return {"network": network, "version": version}


def create_version(
    session: Session,
    *,
    network_id: uuid.UUID | str,
    xml_bytes: bytes,
    review: dict[str, Any] | None,
    actor: str | None,
) -> dict[str, Any]:
    """Edit-as-new-version: prior rows untouched (immutable)."""
    try:
        target = uuid.UUID(str(network_id))
    except ValueError:
        raise _fail(404, "NOT_FOUND", "Network not found.") from None
    existing = get_network(session, target)
    if existing is None:
        raise _fail(404, "NOT_FOUND", "Network not found.")
    normalized = parse_review(review)
    _ensure_safe_xml_bytes(xml_bytes)
    count = session.execute(
        text("SELECT count(*) AS n FROM model_network_versions WHERE network_id = :nid"),
        {"nid": target},
    ).scalar()
    next_number = int(count or 0) + 1
    if next_number > MAX_VERSIONS_PER_NETWORK:
        raise _fail(
            422,
            "TOO_MANY_VERSIONS",
            f"Network exceeds {MAX_VERSIONS_PER_NETWORK} versions.",
            {"network_id": ["Too many versions."]},
        )
    source_hash = hashlib.sha256(xml_bytes).hexdigest()
    version_id = uuid.uuid4()
    import json as _json

    session.execute(
        text(
            "INSERT INTO model_network_versions "
            "(id, network_id, version_number, source_bytes, source_hash, "
            "status, review, created_by) "
            "VALUES (:id, :network_id, :number, :bytes, :hash, :status, "
            "CAST(:review AS JSONB), :actor)"
        ),
        {
            "id": version_id,
            "network_id": target,
            "number": next_number,
            "bytes": xml_bytes,
            "hash": source_hash,
            "status": normalized["status"],
            "review": _json.dumps({k: v for k, v in normalized.items() if k != "status"}),
            "actor": actor,
        },
    )
    session.flush()
    version = get_version(session, version_id)
    assert version is not None
    return version


def get_network(session: Session, network_id: uuid.UUID | str) -> dict[str, Any] | None:
    try:
        target = uuid.UUID(str(network_id))
    except ValueError:
        return None
    row = (
        session.execute(
            text("SELECT id, name, created_at, created_by FROM model_networks WHERE id = :id"),
            {"id": target},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    item = dict(row)
    item["id"] = str(item["id"])
    created = item.get("created_at")
    if isinstance(created, datetime):
        try:
            item["created_at"] = contracts.serialize_utc(created)
        except ValueError:
            item["created_at"] = str(created)
    count = session.execute(
        text("SELECT count(*) AS n FROM model_network_versions WHERE network_id = :nid"),
        {"nid": target},
    ).scalar()
    item["version_count"] = int(count or 0)
    latest = (
        session.execute(
            text(
                "SELECT id, version_number, source_hash, status FROM model_network_versions "
                "WHERE network_id = :nid ORDER BY version_number DESC LIMIT 1"
            ),
            {"nid": target},
        )
        .mappings()
        .first()
    )
    if latest is not None:
        latest_dict = dict(latest)
        latest_dict["id"] = str(latest_dict["id"])
        item["latest_version"] = latest_dict
    else:
        item["latest_version"] = None
    return item


def list_networks(
    session: Session, *, limit: int | None, offset: int | None
) -> tuple[list[dict[str, Any]], int]:
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    total = session.execute(text("SELECT count(*) AS n FROM model_networks")).scalar()
    rows = (
        session.execute(
            text(
                "SELECT id, name, created_at, created_by FROM model_networks "
                "ORDER BY created_at ASC, id ASC LIMIT :limit OFFSET :offset"
            ),
            {"limit": resolved_limit, "offset": resolved_offset},
        )
        .mappings()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        created = item.get("created_at")
        if isinstance(created, datetime):
            try:
                item["created_at"] = contracts.serialize_utc(created)
            except ValueError:
                item["created_at"] = str(created)
        count = session.execute(
            text("SELECT count(*) AS n FROM model_network_versions WHERE network_id = :nid"),
            {"nid": uuid.UUID(item["id"])},
        ).scalar()
        item["version_count"] = int(count or 0)
        items.append(item)
    return items, int(total or 0)


def get_version(session: Session, version_id: uuid.UUID | str) -> dict[str, Any] | None:
    try:
        target = uuid.UUID(str(version_id))
    except ValueError:
        return None
    row = (
        session.execute(
            text(
                "SELECT id, network_id, version_number, source_bytes, source_hash, "
                "status, review, created_at, created_by "
                "FROM model_network_versions WHERE id = :id"
            ),
            {"id": target},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    item = _version_row_to_dict(row)
    raw = item.get("source_bytes")
    if isinstance(raw, memoryview):
        item["source_bytes"] = bytes(raw)
    return item


def list_versions(
    session: Session, *, network_id: uuid.UUID | str, limit: int | None, offset: int | None
) -> tuple[list[dict[str, Any]], int]:
    try:
        target = uuid.UUID(str(network_id))
    except ValueError:
        raise _fail(404, "NOT_FOUND", "Network not found.")
    if get_network(session, target) is None:
        raise _fail(404, "NOT_FOUND", "Network not found.")
    resolved_limit, resolved_offset = contracts.parse_pagination(limit, offset)
    total = session.execute(
        text("SELECT count(*) AS n FROM model_network_versions WHERE network_id = :nid"),
        {"nid": target},
    ).scalar()
    rows = (
        session.execute(
            text(
                "SELECT id, network_id, version_number, source_hash, status, review, "
                "created_at, created_by FROM model_network_versions "
                "WHERE network_id = :nid ORDER BY version_number ASC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"nid": target, "limit": resolved_limit, "offset": resolved_offset},
        )
        .mappings()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        item["network_id"] = str(item["network_id"])
        created = item.get("created_at")
        if isinstance(created, datetime):
            try:
                item["created_at"] = contracts.serialize_utc(created)
            except ValueError:
                item["created_at"] = str(created)
        # Validation summary recomputed from stored bytes (deterministic).
        full = get_version(session, item["id"])
        assert full is not None
        summary = summarize_validation(bytes(full["source_bytes"]))
        item["validation"] = summary
        items.append(item)
    return items, int(total or 0)


def summarize_validation(source_bytes: bytes) -> dict[str, Any]:
    """Small validation summary for history lists (recomputed, deterministic)."""
    try:
        document = validation_module.validate(source_bytes)
    except validation_module.XmlValidationError as exc:
        return {
            "xsd_valid": False,
            "activatable_v1": False,
            "semantic_valid": False,
            "code": exc.code,
        }
    semantic = admission_module.check_semantics(document)
    return {
        "xsd_valid": bool(document.xsd.valid),
        "activatable_v1": bool(document.activatable_v1),
        "nonactivatable_reasons": list(document.nonactivatable_reasons),
        "semantic_valid": bool(semantic.valid),
    }


def build_reports(source_bytes: bytes, package: Any | None = None) -> dict[str, Any]:
    """Separate structural/semantic/content/admission reports (T5 reuse).

    Structural comes from S21 (XSD + activatable, never executable);
    semantic from S22; content from S25; admission from S22. XSD success is
    structural only — this function never labels it executable/clinically valid.
    """
    document = validation_module.validate(source_bytes)
    structural = {
        "xsd_valid": bool(document.xsd.valid),
        "xsd_errors": [
            {"line": e.line, "column": e.column, "message": e.message} for e in document.xsd.errors
        ],
        "activatable_v1": bool(document.activatable_v1),
        "nonactivatable_reasons": list(document.nonactivatable_reasons),
        "source_hash": document.source_sha256,
        "network_count": len(document.networks),
    }
    semantic_report = admission_module.check_semantics(document)
    semantic = {
        "valid": bool(semantic_report.valid),
        "errors": [
            {"code": e.code, "message": e.message, "node": e.node} for e in semantic_report.errors
        ],
    }
    content_report = package_module.validate_question_package(package, document)
    content = {
        "valid": bool(content_report.valid),
        "errors": [{"code": e.code, "message": e.message} for e in content_report.errors],
    }
    decision = admission_module.admission_decision(document, package)
    admission = {
        "executable": bool(decision.executable),
        "diagnostics": [{"code": d.code, "message": d.message} for d in decision.diagnostics],
    }
    return {
        "structural": structural,
        "semantic": semantic,
        "content": content,
        "admission": admission,
        "source_hash": document.source_sha256,
    }


def build_graph(source_bytes: bytes) -> dict[str, Any]:
    """Read-only ordered graph + validation state (no edit semantics)."""
    document = validation_module.validate(source_bytes)
    semantic = admission_module.check_semantics(document)
    graphs: list[dict[str, Any]] = []
    for network in document.networks:
        display = validation_module.display_graph(network)
        states = {v.name: list(v.states) for v in network.variables}
        graphs.append(
            {
                "network_name": display.network_name,
                "nodes": list(display.nodes),
                "edges": [list(edge) for edge in display.edges],
                "states": states,
            }
        )
    return {
        "graphs": graphs,
        "validation": {
            "xsd_valid": bool(document.xsd.valid),
            "activatable_v1": bool(document.activatable_v1),
            "nonactivatable_reasons": list(document.nonactivatable_reasons),
            "semantic_valid": bool(semantic.valid),
        },
        "source_hash": document.source_sha256,
    }


def export_version_bytes(session: Session, version_id: uuid.UUID | str) -> bytes:
    """Exact preserved source bytes by identity (order/metadata intact)."""
    version = get_version(session, version_id)
    if version is None:
        raise _fail(404, "NOT_FOUND", "Network version not found.")
    raw = version["source_bytes"]
    if isinstance(raw, memoryview):
        return bytes(raw)
    return bytes(raw)


def validate_version(
    session: Session, version_id: uuid.UUID | str, package: Any | None = None
) -> dict[str, Any]:
    """Full validation details for one stored version (history exposure)."""
    data = export_version_bytes(session, version_id)
    reports = build_reports(data, package)
    version = get_version(session, version_id)
    assert version is not None
    return {
        "version_id": str(version["id"]),
        "network_id": str(version["network_id"]),
        "version_number": int(version["version_number"]),
        "source_hash": str(version["source_hash"]),
        "status": str(version["status"]),
        "review": version["review"],
        **reports,
    }


# --- Activation (slice 2; pointer revision + audit, synthetic bundles) ---


def _parse_workflow(workflow: Any) -> str:
    if workflow not in SUPPORTED_WORKFLOWS:
        raise _fail(
            422,
            "VALIDATION_FAILED",
            f"Workflow must be one of {sorted(SUPPORTED_WORKFLOWS)}.",
            {"workflow": [f"Must be one of {sorted(SUPPORTED_WORKFLOWS)}."]},
        )
    return str(workflow)


def _parse_selections(selections: Any) -> list[dict[str, str]]:
    if not isinstance(selections, list) or not selections:
        raise _fail(
            422,
            "INCOMPLETE_BUNDLE",
            "Bundle needs at least one selection.",
            {"selections": ["Must list at least one version."]},
        )
    if len(selections) > MAX_BUNDLE_SELECTIONS:
        raise _fail(
            422,
            "INCOMPLETE_BUNDLE",
            f"Bundle exceeds {MAX_BUNDLE_SELECTIONS} selections.",
            {"selections": [f"Must be 1-{MAX_BUNDLE_SELECTIONS}."]},
        )
    cleaned: list[dict[str, str]] = []
    for entry in selections:
        if not isinstance(entry, dict):
            raise _fail(
                422,
                "INCOMPLETE_BUNDLE",
                "Each selection needs network/version IDs.",
                {"selections": ["Must hold network_id + version_id."]},
            )
        try:
            network_id = str(uuid.UUID(str(entry.get("network_id"))))
            version_id = str(uuid.UUID(str(entry.get("version_id"))))
        except (ValueError, AttributeError):
            raise _fail(
                422,
                "INCOMPLETE_BUNDLE",
                "Each selection needs valid UUID network/version IDs.",
                {"selections": ["Must hold UUID network_id + version_id."]},
            ) from None
        cleaned.append({"network_id": network_id, "version_id": version_id})
    return cleaned


def get_pointer(session: Session, workflow: str) -> dict[str, Any] | None:
    """Current activation pointer for one workflow, or None (never active)."""
    row = (
        session.execute(
            text(
                "SELECT workflow, active_bundle, revision, updated_at, updated_by "
                "FROM model_workflow_pointers WHERE workflow = :workflow"
            ),
            {"workflow": workflow},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    item = dict(row)
    updated = item.get("updated_at")
    if isinstance(updated, datetime):
        try:
            item["updated_at"] = contracts.serialize_utc(updated)
        except ValueError:
            item["updated_at"] = str(updated)
    return item


def _check_selections_complete(
    session: Session, selections: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Synthetic completeness: every version exists, belongs, is semantic-valid."""
    checked: list[dict[str, Any]] = []
    for entry in selections:
        version = get_version(session, entry["version_id"])
        if version is None:
            raise _fail(404, "NOT_FOUND", f"Network version {entry['version_id']} not found.")
        if str(version["network_id"]) != entry["network_id"]:
            raise _fail(
                422,
                "INCOMPLETE_BUNDLE",
                "Selection version does not belong to its network.",
                {"selections": ["version_id must belong to network_id."]},
            )
        try:
            document = validation_module.validate(bytes(version["source_bytes"]))
        except validation_module.XmlValidationError as exc:
            raise _fail(
                422,
                "INCOMPLETE_BUNDLE",
                f"Selection {entry['version_id']} is not safely inspectable: {exc.code}.",
                {"selections": [exc.code]},
            ) from None
        semantic = admission_module.check_semantics(document)
        if not semantic.valid:
            codes = [e.code for e in semantic.errors]
            raise _fail(
                422,
                "INCOMPLETE_BUNDLE",
                f"Selection {entry['version_id']} is incomplete: {','.join(codes[:3])}.",
                {"selections": codes[:3]},
            )
        review = version.get("review") or {}
        if not isinstance(review, dict):
            review = {}
        if review.get("decision") != "approved":
            raise _fail(
                422,
                "UNREVIEWED_BUNDLE",
                f"Selection {entry['version_id']} is not approved.",
                {"selections": ["Each version needs an approved review."]},
            )
        checked.append(
            {
                "network_id": entry["network_id"],
                "version_id": entry["version_id"],
                "source_hash": str(version["source_hash"]),
                "version_number": int(version["version_number"]),
            }
        )
    return checked


def _move_pointer(
    session: Session,
    *,
    workflow: str,
    selections: list[dict[str, Any]],
    review: dict[str, Any],
    actor: str | None,
    operation: str,
    request_id: str,
    expected_revision: int | None,
) -> dict[str, Any]:
    import json as _json

    from x_insight.operations import audit as audit_module

    current = get_pointer(session, workflow)
    if expected_revision is not None:
        current_revision = int(current["revision"]) if current else 0
        if current_revision != expected_revision:
            raise _fail(
                412, "STALE_REVISION", "The workflow pointer changed. Reload and reconcile."
            )
    bundle = {"workflow": workflow, "selections": selections, "review": review}
    if current is None:
        session.execute(
            text(
                "INSERT INTO model_workflow_pointers "
                "(workflow, active_bundle, revision, updated_by) "
                "VALUES (:workflow, CAST(:bundle AS JSONB), 1, :actor)"
            ),
            {"workflow": workflow, "bundle": _json.dumps(bundle), "actor": actor},
        )
        revision = 1
    else:
        revision = int(current["revision"]) + 1
        session.execute(
            text(
                "UPDATE model_workflow_pointers SET active_bundle = CAST(:bundle AS JSONB), "
                "revision = :revision, updated_at = now(), updated_by = :actor "
                "WHERE workflow = :workflow"
            ),
            {
                "workflow": workflow,
                "bundle": _json.dumps(bundle),
                "revision": revision,
                "actor": actor,
            },
        )
    session.flush()
    audit_module.record_audit(
        session,
        operation=operation,
        actor=actor,
        request_id=request_id,
        details={
            "workflow": workflow,
            "revision": revision,
            "selections": selections,
            "review_decision": review.get("decision"),
        },
    )
    session.flush()
    pointer = get_pointer(session, workflow)
    assert pointer is not None
    return pointer


def activate_bundle(
    session: Session,
    *,
    workflow: Any,
    selections: Any,
    review: Any,
    actor: str | None,
    request_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically activate a complete reviewed synthetic bundle (T1).

    Rejects incomplete (semantic-invalid/missing) or unreviewed bundles
    with 422; stale pointers with 412. Success bumps the pointer revision
    and records one audit event in the same transaction (S02).
    """
    cleaned_workflow = _parse_workflow(workflow)
    cleaned_selections = _parse_selections(selections)
    normalized = parse_review(review)
    require_approved_review(normalized, label="Bundle")
    checked = _check_selections_complete(session, cleaned_selections)
    return _move_pointer(
        session,
        workflow=cleaned_workflow,
        selections=checked,
        review={k: v for k, v in normalized.items() if k != "status"},
        actor=actor,
        operation="model_bundles.activate.success",
        request_id=request_id,
        expected_revision=expected_revision,
    )


def rollback_bundle(
    session: Session,
    *,
    workflow: Any,
    selections: Any,
    review: Any,
    actor: str | None,
    request_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Rollback is a new activation event selecting old valid versions."""
    cleaned_workflow = _parse_workflow(workflow)
    cleaned_selections = _parse_selections(selections)
    normalized = parse_review(review)
    require_approved_review(normalized, label="Rollback")
    checked = _check_selections_complete(session, cleaned_selections)
    return _move_pointer(
        session,
        workflow=cleaned_workflow,
        selections=checked,
        review={k: v for k, v in normalized.items() if k != "status"},
        actor=actor,
        operation="model_bundles.rollback.success",
        request_id=request_id,
        expected_revision=expected_revision,
    )
