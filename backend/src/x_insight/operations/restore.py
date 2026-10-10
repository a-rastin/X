"""Staged restore validation without live mutation (S55, seams T10/T1).

Tasks.md S55 §§1-4 (FR-41, NFR-02/04; plan.md §10.3 steps 1-3, staging only):

- §1: a valid S54 app-generated archive stages into isolated
  filesystem storage (``RESTORE_STAGING_DIR/<id>.zip``, mode 0600, never
  the live database), reports backup date/schema/content/checksums/impact,
  and returns an immutable confirmation digest for S56 to require exactly.
- §2: traversal/absolute/symlink, oversized expansion, corrupt hash,
  unsupported schema, missing artifact, and malformed DB content all fail
  BEFORE any live mutation (only ``restore_jobs`` + audit rows are written,
  in the same request transaction; clinical tables are never touched).
- §3: uploaded bytes are untrusted and never executed as SQL/superuser,
  never via COPY/host-file/program access — staging only parses JSON/XML
  through stdlib + the supported S21/S23 inspection commands
  (:func:`x_insight.models.validation.validate`,
  :func:`x_insight.models.inference.validate_cpts` where pinned packages
  exist) with the existing engineering bounds. Singleton admin,
  patient/encounter/reference, and model/effective-artifact consistency are
  checked in memory. Live impact reads use the existing restricted
  SELECT-only role (``x_insight_readonly`` — see 0018 rationale, no new
  role). No ``POST /restores/commit`` or any live-mutation path exists here;
  that is S56.
- §4: failed staging leaves live patients/login/jobs unchanged (proven by
  live-side read checks in dev-test, never by private-helper mocks); a new
  upload expires prior succeeded digests and expiry marks old confirmations
  stale. The public job shape never exposes an enabled Commit.

Reuse-first minimal diff (ponytail: shortest working diff wins): manifest
envelope (``backup-v1``), ``BACKUP_TABLES`` inventory, ``MAX_ARCHIVE_BYTES``
bound, ``verify_archive`` hash/inventory precedent, ``operations/router``
admin-gate/audit/``private, no-store`` patterns, and 0017 table/grant shape.
New migration 0018 only adds the staging table (never reuses ``backup_jobs``
for live mutation, per 0017 docstring).

BFRI 3 (Moderate, proceed with tests + monitoring): Fit 5 (router →
this service → DB, reuses backup/audit/identity/validation patterns) +
Testability 5 (T1 HTTP + T10 archive seams, real PostgreSQL/archives) −
Complexity 3 (zip safety + singleton/refs/model checks, stdlib only) −
Data Risk 2 (staging-only; only restore_jobs writes) −
Operational Risk 2 (admin-only, no worker restart, no commit) = 3.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from x_insight import contracts
from x_insight.operations import audit as audit_module
from x_insight.operations import backup as backup_module

# Supported app-generated envelope (reuse, never a new version here).
RESTORE_BACKUP_SCHEMA_VERSION = backup_module.BACKUP_MANIFEST_VERSION

# 0018 only adds the staging table; clinical rows are identical to 0017, so
# both manifests restore through the same staging checks. Bump only with a
# clinical migration, per plan.md §4.2 versioning discipline.
SUPPORTED_APP_SCHEMA_VERSIONS = frozenset({"0017", "0018"})

# Bounds (ponytail: 256 MiB expanded / 4096 entries / 100 MiB per file;
# S58 measures real 10k-patient sizes first).
MAX_ARCHIVE_BYTES = backup_module.MAX_ARCHIVE_BYTES
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ZIP_ENTRIES = 4096
MAX_FILE_BYTES = 100 * 1024 * 1024

# Staging window (ponytail: 30 min; S56 commit must present the exact digest
# before expiry, otherwise the confirmation is stale).
RESTORE_STAGING_TTL_SECONDS = 30 * 60

# Restricted inspection role (reuse, see 0018 rationale): live impact reads
# are SELECT-only; staging never needs a new privileged role.
RESTORE_ROLE = "x_insight_readonly"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

metadata = MetaData()

restore_jobs = Table(
    "restore_jobs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("created_by_id", PG_UUID(as_uuid=True), nullable=True),
    Column("created_by_username", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("backup_id", PG_UUID(as_uuid=True), nullable=True),
    Column("backup_created_at", Text, nullable=True),
    Column("app_schema_version", Text, nullable=True),
    Column("archive_path", Text, nullable=True),
    Column("archive_sha256", Text, nullable=True),
    Column("archive_bytes", BigInteger(), nullable=True),
    Column("manifest", JSONB(), nullable=True),
    Column("report", JSONB(), nullable=True),
    Column("confirmation_digest", Text, nullable=True),
    Column("error_code", Text, nullable=True),
    Column("error_message", Text, nullable=True),
)


def staging_dir() -> Path:
    """Isolated staging directory for uploaded zips (env override)."""
    configured = os.environ.get("RESTORE_STAGING_DIR", "")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "x_insight_restores"
    root.mkdir(parents=True, exist_ok=True)
    return root


def staged_path_for(job_id: uuid.UUID) -> Path:
    return staging_dir() / f"{job_id}.zip"


def _safe_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return contracts.serialize_utc(value)
    except Exception:
        return str(value)


def _is_expired(row: dict[str, Any], now: datetime) -> bool:
    expires = row.get("expires_at")
    if expires is None:
        return False
    try:
        moment = expires if expires.tzinfo is not None else expires.replace(tzinfo=now.tzinfo)
        return moment <= now
    except Exception:
        return False


def safe_job(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Public staging shape: status + report/digest, no paths, no Commit.

    Never exposes a commit URL/flag (S56 owns commit; S55 must not offer an
    enabled premature Commit). ``expired`` is computed on read so expiry
    invalidates prior confirmations without a background job.
    """
    moment = now or contracts.utcnow()
    manifest = row.get("manifest")
    report = row.get("report")
    return {
        "id": str(row.get("id")),
        "status": str(row.get("status")),
        "created_by": {
            "id": str(row.get("created_by_id")) if row.get("created_by_id") is not None else None,
            "username": str(row.get("created_by_username") or ""),
        },
        "created_at": _safe_time(row.get("created_at")),
        "updated_at": _safe_time(row.get("updated_at")),
        "completed_at": _safe_time(row.get("completed_at")),
        "expires_at": _safe_time(row.get("expires_at")),
        "expired": bool(_is_expired(row, moment)),
        "backup_id": str(row.get("backup_id")) if row.get("backup_id") is not None else None,
        "backup_created_at": row.get("backup_created_at"),
        "app_schema_version": row.get("app_schema_version"),
        "archive_sha256": row.get("archive_sha256"),
        "archive_bytes": row.get("archive_bytes"),
        "confirmation_digest": row.get("confirmation_digest"),
        "report": dict(report) if isinstance(report, dict) else None,
        "manifest": dict(manifest) if isinstance(manifest, dict) else None,
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
    }


def get_restore(session: Session, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        session.execute(select(restore_jobs).where(restore_jobs.c.id == job_id)).mappings().first()
    )
    return dict(row) if row is not None else None


def _zip_entry_error(name: str, info: zipfile.ZipInfo) -> str | None:
    """Reject absolute/traversal/symlink entries before any extraction."""
    if not name:
        return "archive holds an empty entry name"
    if name.startswith("/") or name.startswith("\\") or (len(name) >= 2 and name[1] == ":"):
        return f"archive holds absolute path: {name[:80]}"
    if "\\" in name:
        return f"archive holds backslash path: {name[:80]}"
    stripped = name.rstrip("/")
    if not stripped:
        return "archive holds an empty entry name"
    parts = stripped.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return f"archive holds traversal entry: {name[:80]}"
    # Symlink bit (unix file type 0120000 in the upper 16 bits).
    try:
        if ((int(info.external_attr) >> 16) & 0o170000) == 0o120000:
            return f"archive holds symlink entry: {name[:80]}"
    except Exception:
        pass
    return None


def _decode_bytes_cell(cell: Any) -> bytes | None:
    if isinstance(cell, dict) and "__bytes_b64__" in cell:
        try:
            return base64.b64decode(cell["__bytes_b64__"])
        except Exception:
            return None
    if isinstance(cell, str):
        try:
            return base64.b64decode(cell)
        except Exception:
            return None
    if isinstance(cell, (bytes, bytearray, memoryview)):
        return bytes(cell)
    return None


def _check_singleton_identity(tables: dict[str, Any]) -> str | None:
    users = tables.get("users")
    if not isinstance(users, list):
        return "database content malformed: users is not a list"
    admins = [u for u in users if isinstance(u, dict) and u.get("role") == "admin"]
    if len(admins) != 1:
        return f"singleton admin violated: found {len(admins)} admin rows, expected 1"
    admin = admins[0]
    if str(admin.get("username", "")) != "admin":
        return "singleton admin violated: admin username must stay 'admin'"
    if not admin.get("active", False):
        return "singleton admin violated: admin must stay active"
    seen: set[str] = set()
    for entry in users:
        if not isinstance(entry, dict):
            return "database content malformed: users holds a non-object row"
        username = entry.get("username")
        if not isinstance(username, str) or not username:
            return "database content malformed: users holds a row without username"
        if username in seen:
            return f"database content malformed: duplicate username {username!r}"
        seen.add(username)
    return None


def _check_patient_shape(tables: dict[str, Any]) -> str | None:
    import re as _re

    patients = tables.get("patients")
    if not isinstance(patients, list):
        return "database content malformed: patients is not a list"
    seen: set[str] = set()
    for entry in patients:
        if not isinstance(entry, dict):
            return "database content malformed: patients holds a non-object row"
        identifier = entry.get("identifier")
        if not isinstance(identifier, str) or _re.fullmatch(r"[0-9]{10}", identifier) is None:
            return "database content malformed: patient identifier must be ten ASCII digits"
        if identifier in seen:
            return "database content malformed: duplicate patient identifier"
        seen.add(identifier)
    return None


def _check_references(tables: dict[str, Any]) -> str | None:
    """In-memory FK spot-checks over staged rows (no live reads/writes)."""

    def _ids(name: str) -> set[str]:
        rows = tables.get(name)
        if not isinstance(rows, list):
            return set()
        out: set[str] = set()
        for entry in rows:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                out.add(str(entry["id"]))
        return out

    user_ids = _ids("users")
    patient_ids = _ids("patients")
    encounter_ids = _ids("encounters")
    batch_ids = _ids("generation_batches")
    run_ids = _ids("question_runs")
    baseline_ids = _ids("original_baselines")
    revision_ids = _ids("cpt_revisions")
    network_ids = _ids("model_networks")
    version_ids = _ids("model_network_versions")
    proposal_ids = _ids("proposal_snapshots")
    job_ids = _ids("reasoning_jobs")

    def _need(value: Any) -> str | None:
        return str(value) if isinstance(value, str) and value else None

    encounters = tables.get("encounters")
    if not isinstance(encounters, list):
        return "database content malformed: encounters is not a list"
    for entry in encounters:
        if not isinstance(entry, dict):
            return "database content malformed: encounters holds a non-object row"
        patient = _need(entry.get("patient_id"))
        author = _need(entry.get("author_id"))
        if patient is None or patient not in patient_ids:
            return "staged references broken: encounters.patient_id has no staged patient"
        if author is None or author not in user_ids:
            return "staged references broken: encounters.author_id has no staged user"
        if entry.get("lifecycle") not in ("draft", "signed", "discarded"):
            return "database content malformed: encounters.lifecycle is unknown"

    for table, field, target, label in (
        ("notes", "encounter_id", encounter_ids, "notes.encounter_id"),
        ("notes", "author_id", user_ids, "notes.author_id"),
        ("secondary_plans", "encounter_id", encounter_ids, "secondary_plans.encounter_id"),
        ("generation_batches", "encounter_id", encounter_ids, "generation_batches.encounter_id"),
        ("generation_batches", "author_id", user_ids, "generation_batches.author_id"),
        ("question_runs", "batch_id", batch_ids, "question_runs.batch_id"),
        ("original_baselines", "question_run_id", run_ids, "original_baselines.question_run_id"),
        ("original_baselines", "batch_id", batch_ids, "original_baselines.batch_id"),
        ("proposal_snapshots", "batch_id", batch_ids, "proposal_snapshots.batch_id"),
        ("reasoning_jobs", "batch_id", batch_ids, "reasoning_jobs.batch_id"),
        ("reasoning_jobs", "question_run_id", run_ids, "reasoning_jobs.question_run_id"),
        ("cpt_revisions", "question_run_id", run_ids, "cpt_revisions.question_run_id"),
        ("cpt_revisions", "batch_id", batch_ids, "cpt_revisions.batch_id"),
        ("question_review_states", "question_run_id", run_ids, "question_review_states.batch_id"),
        ("calculation_results", "question_run_id", run_ids, "calculation_results.question_run_id"),
        ("calculation_results", "batch_id", batch_ids, "calculation_results.batch_id"),
        ("calculation_results", "cpt_revision_id", revision_ids, "calculation_results.revision"),
        ("probability_acceptances", "question_run_id", run_ids, "acceptances.question_run_id"),
        ("probability_acceptances", "batch_id", batch_ids, "acceptances.batch_id"),
        ("probability_acceptances", "encounter_id", encounter_ids, "acceptances.encounter_id"),
        ("probability_acceptances", "baseline_id", baseline_ids, "acceptances.baseline_id"),
        ("signed_encounter_snapshots", "encounter_id", encounter_ids, "signed.encounter_id"),
        ("signed_encounter_snapshots", "patient_id", patient_ids, "signed.patient_id"),
        ("signed_encounter_snapshots", "batch_id", batch_ids, "signed.batch_id"),
        ("signed_encounter_snapshots", "proposal_id", proposal_ids, "signed.proposal_id"),
        ("signed_encounter_snapshots", "signer_id", user_ids, "signed.signer_id"),
        ("encounter_addenda", "encounter_id", encounter_ids, "addenda.encounter_id"),
        ("encounter_addenda", "author_id", user_ids, "addenda.author_id"),
        ("model_network_versions", "network_id", network_ids, "model version network"),
        ("reasoning_job_attempts", "job_id", job_ids, "attempt.job_id"),
        ("reasoning_grants", "job_id", job_ids, "grant.job_id"),
        ("reasoning_grants", "batch_id", batch_ids, "grant.batch_id"),
        ("reasoning_grants", "question_run_id", run_ids, "grant.question_run_id"),
    ):
        rows = tables.get(table)
        if not isinstance(rows, list):
            continue
        for entry in rows:
            if not isinstance(entry, dict):
                return f"database content malformed: {table} holds a non-object row"
            # question_review_states stores its run id under question_run_id;
            # the label above reuses the batch wording for the same column.
            column = "question_run_id" if table == "question_review_states" else field
            want = _need(entry.get(column))
            if want is None:
                # Nullable self-links (parent_revision_id, reused_from_baseline_id,
                # current_revision_id, cpt_revision_id on acceptances) skip here.
                continue
            if want not in target:
                return f"staged references broken: {label} has no staged target"
    # Nullable self-links with explicit targets.
    revisions = tables.get("cpt_revisions")
    if isinstance(revisions, list):
        for entry in revisions:
            if not isinstance(entry, dict):
                continue
            parent = entry.get("parent_revision_id")
            if parent is not None and str(parent) not in revision_ids:
                return "staged references broken: cpt_revisions.parent has no staged revision"
    states = tables.get("question_review_states")
    if isinstance(states, list):
        for entry in states:
            if not isinstance(entry, dict):
                continue
            current = entry.get("current_revision_id")
            if current is not None and str(current) not in revision_ids:
                return "staged references broken: review_state.current has no staged revision"
    results = tables.get("calculation_results")
    if isinstance(results, list):
        for entry in results:
            if not isinstance(entry, dict):
                continue
            reused = entry.get("reused_from_baseline_id")
            if reused is not None and str(reused) not in baseline_ids:
                return "staged references broken: calculation reuse has no staged baseline"
    acceptances = tables.get("probability_acceptances")
    if isinstance(acceptances, list):
        for entry in acceptances:
            if not isinstance(entry, dict):
                continue
            rev = entry.get("cpt_revision_id")
            if rev is not None and str(rev) not in revision_ids:
                return "staged references broken: acceptance revision has no staged revision"
    releases = tables.get("ddi_dataset_releases")
    release_ids: set[str] = set()
    if isinstance(releases, list):
        for entry in releases:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                release_ids.add(str(entry["id"]))
    for table in ("ddi_source_documents", "ddi_concepts", "ddi_interaction_evidence"):
        rows = tables.get(table)
        if not isinstance(rows, list):
            continue
        for entry in rows:
            if not isinstance(entry, dict):
                return f"database content malformed: {table} holds a non-object row"
            rid = _need(entry.get("release_id"))
            if rid is None or rid not in release_ids:
                return f"staged references broken: {table}.release_id has no staged release"
    versions = tables.get("model_network_versions")
    if isinstance(versions, list):
        for entry in versions:
            if not isinstance(entry, dict):
                continue
            if _need(entry.get("network_id")) not in network_ids:
                return "staged references broken: model version has no staged network"
            if _need(entry.get("id")) is None:
                return "database content malformed: model version lacks id"
            _ = version_ids
    return None


def _check_model_artifacts(tables: dict[str, Any], network_blobs: dict[str, bytes]) -> str | None:
    """Model/effective-artifact consistency via supported inspection commands."""
    from x_insight.models import validation as validation_module

    versions = tables.get("model_network_versions")
    if not isinstance(versions, list):
        return "database content malformed: model_network_versions is not a list"
    by_id: dict[str, dict[str, Any]] = {}
    for entry in versions:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            by_id[str(entry["id"])] = entry
    for name, raw in network_blobs.items():
        version_id = name.split("/")[-1].removesuffix(".xml")
        row = by_id.get(version_id)
        if row is None:
            return f"model consistency broken: {name} has no staged version row"
        try:
            document = validation_module.validate(raw)
        except validation_module.XmlValidationError as exc:
            return f"model consistency broken: {name} unsafe/malformed ({exc.code})"
        if document.source_sha256 != hashlib.sha256(raw).hexdigest():
            return f"model consistency broken: {name} hash drifted"
        staged_hash = str(row.get("source_hash") or "")
        if staged_hash != document.source_sha256:
            return f"model consistency broken: {name} source_hash differs from staged row"
        staged_bytes = _decode_bytes_cell(row.get("source_bytes"))
        if staged_bytes is None or staged_bytes != raw:
            return f"model consistency broken: {name} bytes differ from staged row"
    # Effective artifacts: hash-anchored, no inference child here (bounded staging).
    baselines = tables.get("original_baselines")
    if isinstance(baselines, list):
        for entry in baselines:
            if not isinstance(entry, dict):
                return "database content malformed: original_baselines holds a non-object row"
            effective_xml = entry.get("effective_xml")
            effective_hash = entry.get("effective_hash")
            if not isinstance(effective_xml, str) or not effective_xml.strip():
                return "model consistency broken: baseline lacks effective_xml"
            if not isinstance(effective_hash, str) or len(effective_hash) != 64:
                return "model consistency broken: baseline lacks effective_hash"
            if hashlib.sha256(effective_xml.encode("utf-8")).hexdigest() != effective_hash:
                return "model consistency broken: baseline effective_hash mismatch"
            validated = entry.get("validated_tables")
            if not isinstance(validated, list) or not validated:
                return "model consistency broken: baseline lacks validated_tables"
    revisions = tables.get("cpt_revisions")
    if isinstance(revisions, list):
        for entry in revisions:
            if not isinstance(entry, dict):
                return "database content malformed: cpt_revisions holds a non-object row"
            artifact = entry.get("cpt_artifact")
            digest = entry.get("cpt_hash")
            if not isinstance(artifact, list) or not artifact:
                return "model consistency broken: revision lacks cpt_artifact"
            if not isinstance(digest, str) or len(digest) != 64:
                return "model consistency broken: revision lacks cpt_hash"
            if contracts.canonical_hash(list(artifact)) != digest:
                return "model consistency broken: revision cpt_hash mismatch"
    results = tables.get("calculation_results")
    if isinstance(results, list):
        for entry in results:
            if not isinstance(entry, dict):
                return "database content malformed: calculation_results holds a non-object row"
            effective_xml = entry.get("effective_xml")
            effective_hash = entry.get("effective_hash")
            if not isinstance(effective_xml, str) or not effective_xml.strip():
                return "model consistency broken: result lacks effective_xml"
            if not isinstance(effective_hash, str) or len(effective_hash) != 64:
                return "model consistency broken: result lacks effective_hash"
            if hashlib.sha256(effective_xml.encode("utf-8")).hexdigest() != effective_hash:
                return "model consistency broken: result effective_hash mismatch"
    return None


def validate_archive_bytes(blob: bytes) -> dict[str, Any]:
    """Validate uploaded bytes WITHOUT touching live state (T10).

    Returns ``{ok, errors, manifest, database, policies, networks,
    inventory, file_hashes, archive_sha256, archive_bytes}``. ``errors`` are
    bounded user-facing strings; the caller turns the first into the failed
    job's ``error_code/message``. No filesystem or database access here, so
    every §2 negative fails before any staging write.
    """
    if not blob:
        return {"ok": False, "errors": ["archive is empty"], "warnings": []}
    if len(blob) > MAX_ARCHIVE_BYTES:
        return {
            "ok": False,
            "errors": [f"archive {len(blob)} bytes exceeds {MAX_ARCHIVE_BYTES} bound"],
            "warnings": [],
        }
    try:
        handle = zipfile.ZipFile(io.BytesIO(blob), "r")
    except Exception:
        return {"ok": False, "errors": ["archive unreadable: not a zip file"], "warnings": []}
    with handle:
        try:
            infos = handle.infolist()
        except Exception:
            return {
                "ok": False,
                "errors": ["archive unreadable: cannot list entries"],
                "warnings": [],
            }
        if len(infos) > MAX_ZIP_ENTRIES:
            return {
                "ok": False,
                "errors": [f"archive holds {len(infos)} entries (limit {MAX_ZIP_ENTRIES})"],
                "warnings": [],
            }
        total_uncompressed = 0
        for info in infos:
            problem = _zip_entry_error(info.filename, info)
            if problem is not None:
                return {"ok": False, "errors": [problem], "warnings": []}
            try:
                size = int(info.file_size)
            except Exception:
                size = 0
            if size > MAX_FILE_BYTES:
                return {
                    "ok": False,
                    "errors": [f"archive entry {info.filename[:60]} exceeds file bound"],
                    "warnings": [],
                }
            total_uncompressed += size
            if total_uncompressed > MAX_EXPANDED_BYTES:
                return {
                    "ok": False,
                    "errors": [
                        f"archive expands beyond {MAX_EXPANDED_BYTES} bytes (zip-bomb guard)"
                    ],
                    "warnings": [],
                }
        names = set(info.filename for info in infos)
        for required in ("manifest.json", "database.json", "policies.json"):
            if required not in names:
                return {
                    "ok": False,
                    "errors": [f"missing required entry: {required}"],
                    "warnings": [],
                }
        try:
            manifest = json.loads(handle.read("manifest.json").decode("utf-8"))
        except Exception:
            return {"ok": False, "errors": ["manifest unreadable: invalid JSON"], "warnings": []}
        if not isinstance(manifest, dict):
            return {"ok": False, "errors": ["manifest is not an object"], "warnings": []}
        if manifest.get("schema_version") != RESTORE_BACKUP_SCHEMA_VERSION:
            return {
                "ok": False,
                "errors": [f"unsupported manifest schema: {manifest.get('schema_version')}"],
                "warnings": [],
            }
        app_schema = manifest.get("app_schema_version")
        if app_schema not in SUPPORTED_APP_SCHEMA_VERSIONS:
            return {
                "ok": False,
                "errors": [f"unsupported app schema: {app_schema}"],
                "warnings": [],
            }
        if "sessions" in (manifest.get("inventory") or {}):
            return {
                "ok": False,
                "errors": ["manifest inventory must not include sessions"],
                "warnings": [],
            }
        if not str(manifest.get("key_reentry_note", "")):
            return {
                "ok": False,
                "errors": ["manifest lacks the provider key-reentry note"],
                "warnings": [],
            }
        artifacts = manifest.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            return {"ok": False, "errors": ["manifest artifacts malformed"], "warnings": []}
        file_hashes: dict[str, str] = {}
        for entry in ("database.json", "policies.json"):
            try:
                raw = handle.read(entry)
            except Exception:
                return {"ok": False, "errors": [f"cannot read {entry}"], "warnings": []}
            digest = hashlib.sha256(raw).hexdigest()
            file_hashes[entry] = digest
            if entry in artifacts and artifacts[entry] != digest:
                return {"ok": False, "errors": [f"hash mismatch: {entry}"], "warnings": []}
        network_names = sorted(n for n in names if n.startswith("networks/"))
        network_blobs: dict[str, bytes] = {}
        for entry in network_names:
            try:
                raw = handle.read(entry)
            except Exception:
                return {"ok": False, "errors": [f"cannot read {entry}"], "warnings": []}
            digest = hashlib.sha256(raw).hexdigest()
            file_hashes[entry] = digest
            if entry in artifacts and artifacts[entry] != digest:
                return {"ok": False, "errors": [f"hash mismatch: {entry}"], "warnings": []}
            network_blobs[entry] = raw
        try:
            database = json.loads(handle.read("database.json").decode("utf-8"))
        except Exception:
            return {
                "ok": False,
                "errors": ["database.json unreadable: invalid JSON"],
                "warnings": [],
            }
        db_tables = database.get("tables") if isinstance(database, dict) else None
        if not isinstance(db_tables, dict):
            return {"ok": False, "errors": ["database.json lacks tables"], "warnings": []}
        if "sessions" in db_tables:
            return {
                "ok": False,
                "errors": ["database snapshot must not include sessions"],
                "warnings": [],
            }
        manifest_inventory = manifest.get("inventory") or {}
        if not isinstance(manifest_inventory, dict):
            return {"ok": False, "errors": ["manifest inventory malformed"], "warnings": []}
        for name, info in manifest_inventory.items():
            actual = db_tables.get(name)
            if actual is None:
                if not (isinstance(info, dict) and info.get("missing")):
                    return {
                        "ok": False,
                        "errors": [f"inventory lists {name} but database.json lacks it"],
                        "warnings": [],
                    }
                continue
            if isinstance(info, dict) and int(info.get("rows", -1)) != len(actual):
                return {
                    "ok": False,
                    "errors": [f"inventory count mismatch: {name}"],
                    "warnings": [],
                }
        for name in backup_module.BACKUP_TABLES:
            if name not in db_tables:
                return {"ok": False, "errors": [f"missing staged table: {name}"], "warnings": []}
            if not isinstance(db_tables[name], list):
                return {
                    "ok": False,
                    "errors": [f"database content malformed: {name} is not a list"],
                    "warnings": [],
                }
        problem = _check_singleton_identity(db_tables)
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
        problem = _check_patient_shape(db_tables)
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
        problem = _check_references(db_tables)
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
        problem = _check_model_artifacts(db_tables, network_blobs)
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
        try:
            policies = json.loads(handle.read("policies.json").decode("utf-8"))
        except Exception:
            return {
                "ok": False,
                "errors": ["policies.json unreadable: invalid JSON"],
                "warnings": [],
            }
        if not isinstance(policies, dict) or not policies.get("cpt_policy_version"):
            return {"ok": False, "errors": ["policies lack cpt_policy_version"], "warnings": []}
        if not isinstance(policies.get("engine_config"), dict):
            return {"ok": False, "errors": ["policies lack engine_config"], "warnings": []}
        archive_sha256 = hashlib.sha256(blob).hexdigest()
        inventory = dict(manifest_inventory) if isinstance(manifest_inventory, dict) else {}
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "manifest": manifest,
            "database": database,
            "policies": policies,
            "networks": network_blobs,
            "inventory": inventory,
            "file_hashes": file_hashes,
            "archive_sha256": archive_sha256,
            "archive_bytes": len(blob),
        }


def build_confirmation_digest(
    *,
    backup_id: str,
    archive_sha256: str,
    app_schema_version: str,
    inventory: dict[str, Any],
    file_hashes: dict[str, str],
) -> str:
    """Immutable staging digest (S56 must require this exact value)."""
    envelope = {
        "restore_schema": "restore-v1",
        "backup_id": str(backup_id),
        "archive_sha256": str(archive_sha256),
        "app_schema_version": str(app_schema_version),
        "inventory": {
            name: {
                "rows": int(info.get("rows", 0)) if isinstance(info, dict) else 0,
                "sha256": str(info.get("sha256", "")) if isinstance(info, dict) else "",
            }
            for name, info in sorted(inventory.items())
        },
        "artifacts": {name: str(digest) for name, digest in sorted(file_hashes.items())},
    }
    return contracts.canonical_hash(envelope)


def build_impact_report(
    session: Session,
    *,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    archive_sha256: str,
    archive_bytes: int,
    confirmation_digest: str,
) -> dict[str, Any]:
    """Backup date/schema/content/checksums/impact for the progress read.

    Live counts come from SELECT-only reads in the caller's transaction (no
    writes here); staged counts come from the validated manifest inventory.
    Replacement impact is a count comparison, never a merge preview.
    """
    staged_counts = {
        name: int(info.get("rows", 0)) if isinstance(info, dict) else 0
        for name, info in inventory.items()
    }
    live_counts: dict[str, int] = {}
    for table in ("users", "patients", "encounters", "audit_events", "backup_jobs"):
        try:
            live_counts[table] = int(
                session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0
            )
        except Exception:
            live_counts[table] = -1
    try:
        live_generation = session.execute(
            text("SELECT generation FROM reasoning_deployment WHERE id = 1")
        ).scalar()
        live_generation = int(live_generation) if live_generation is not None else None
    except Exception:
        live_generation = None
    staged_generation = manifest.get("deployment_generation")
    try:
        backup_created = str(manifest.get("created_at") or "")
        contracts.parse_utc(backup_created)
    except Exception:
        backup_created = str(manifest.get("created_at") or "")
    return {
        "backup_id": str(manifest.get("backup_id") or ""),
        "backup_created_at": backup_created,
        "manifest_schema": str(manifest.get("schema_version") or ""),
        "app_schema_version": str(manifest.get("app_schema_version") or ""),
        "archive_sha256": str(archive_sha256),
        "archive_bytes": int(archive_bytes),
        "artifacts": dict(manifest.get("artifacts") or {}),
        "inventory": dict(inventory),
        "staged_counts": staged_counts,
        "live_counts": live_counts,
        "replacement_impact": {
            name: {"live": live_counts.get(name, -1), "staged": staged_counts.get(name, 0)}
            for name in ("users", "patients", "encounters", "audit_events")
        },
        "deployment_generation": {"live": live_generation, "staged": staged_generation},
        "key_consequence": str(manifest.get("key_reentry_note") or ""),
        "confirmation_digest": str(confirmation_digest),
        "commit_available": False,
        "commit_note": (
            "Staging only: no restore-over-live/commit/switch/rollback exists "
            "until S56. This digest is the exact value S56 will require."
        ),
    }


def stage_restore(
    session: Session,
    engine: Engine,
    *,
    actor: dict[str, Any],
    request_id: str,
    archive_blob: bytes,
) -> tuple[dict[str, Any], bool]:
    """Stage one uploaded archive; returns (job, failed).

    Synchronous bounded staging with an async-compatible polling shape: the
    returned job is terminal (``succeeded`` or ``failed``). Only
    ``restore_jobs`` + audit rows are written; clinical tables are never
    touched, so failed staging leaves live patients/login/jobs unchanged. A
    new successful upload expires prior unexpired succeeded digests, so replacing the
    upload (or TTL expiry) invalidates prior confirmations.
    """
    _ = engine
    actor_id = actor["id"]
    job_id = uuid.uuid4()
    moment = contracts.utcnow()
    session.execute(
        insert(restore_jobs).values(
            id=job_id,
            status=STATUS_RUNNING,
            created_by_id=actor_id,
            created_by_username=str(actor.get("username", "")),
            created_at=moment,
            updated_at=moment,
        )
    )
    session.flush()

    checked = validate_archive_bytes(archive_blob)
    if not checked.get("ok"):
        message = str((checked.get("errors") or ["restore validation failed"])[0])[:500]
        completed = contracts.utcnow()
        session.execute(
            update(restore_jobs)
            .where(restore_jobs.c.id == job_id)
            .values(
                status=STATUS_FAILED,
                error_code="RESTORE_VALIDATION_FAILED",
                error_message=message,
                completed_at=completed,
                updated_at=completed,
            )
        )
        audit_module.record_audit(
            session,
            operation=audit_module.RESTORE_VALIDATE_FAILED_OPERATION,
            actor=str(actor.get("username")),
            request_id=request_id,
            details={
                "actor_id": str(actor.get("id")),
                "actor_display": str(actor.get("username", "")),
                "restore_id": str(job_id),
                "error_code": "RESTORE_VALIDATION_FAILED",
            },
        )
        session.flush()
        job = get_restore(session, job_id)
        assert job is not None
        return job, True

    manifest = checked["manifest"]
    inventory = checked["inventory"]
    file_hashes = checked["file_hashes"]
    archive_sha256 = str(checked["archive_sha256"])
    archive_bytes = int(checked["archive_bytes"])
    try:
        backup_uuid = uuid.UUID(str(manifest.get("backup_id")))
    except Exception:
        backup_uuid = None
    app_schema = str(manifest.get("app_schema_version") or "")
    digest = build_confirmation_digest(
        backup_id=str(manifest.get("backup_id")),
        archive_sha256=archive_sha256,
        app_schema_version=app_schema,
        inventory=inventory,
        file_hashes=file_hashes,
    )
    report = build_impact_report(
        session,
        manifest=manifest,
        inventory=inventory,
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        confirmation_digest=digest,
    )
    # Isolated staging write (0600, per-job path). Resolved inside the try so
    # an unwritable staging dir yields an observable failed job, not a 500.
    try:
        staged_path = staged_path_for(job_id)
        staged_path.write_bytes(archive_blob)
        try:
            os.chmod(staged_path, 0o600)
        except Exception:
            pass
        completed = contracts.utcnow()
        expires_at = completed + timedelta(seconds=RESTORE_STAGING_TTL_SECONDS)
        session.execute(
            update(restore_jobs)
            .where(restore_jobs.c.id == job_id)
            .values(
                status=STATUS_SUCCEEDED,
                completed_at=completed,
                updated_at=completed,
                expires_at=expires_at,
                backup_id=backup_uuid,
                backup_created_at=str(manifest.get("created_at") or ""),
                app_schema_version=app_schema,
                archive_path=str(staged_path),
                archive_sha256=archive_sha256,
                archive_bytes=archive_bytes,
                manifest=dict(manifest),
                report=dict(report),
                confirmation_digest=digest,
            )
        )
        _expire_prior_successes(session, except_id=job_id, now=completed)
        audit_module.record_audit(
            session,
            operation=audit_module.RESTORE_VALIDATE_SUCCESS_OPERATION,
            actor=str(actor.get("username")),
            request_id=request_id,
            details={
                "actor_id": str(actor.get("id")),
                "actor_display": str(actor.get("username", "")),
                "restore_id": str(job_id),
                "backup_id": str(manifest.get("backup_id")),
                "archive_sha256": archive_sha256,
                "archive_bytes": archive_bytes,
                "confirmation_digest": digest,
            },
        )
        session.flush()
        failed = False
    except Exception as exc:
        failed = True
        try:
            candidate = staged_path_for(job_id)
            if candidate.exists():
                candidate.unlink()
        except Exception:
            pass
        completed = contracts.utcnow()
        session.execute(
            update(restore_jobs)
            .where(restore_jobs.c.id == job_id)
            .values(
                status=STATUS_FAILED,
                error_code="RESTORE_STAGING_FAILED",
                error_message=str(exc)[:500] or "staged write failed",
                completed_at=completed,
                updated_at=completed,
            )
        )
        audit_module.record_audit(
            session,
            operation=audit_module.RESTORE_VALIDATE_FAILED_OPERATION,
            actor=str(actor.get("username")),
            request_id=request_id,
            details={
                "actor_id": str(actor.get("id")),
                "actor_display": str(actor.get("username", "")),
                "restore_id": str(job_id),
                "error_code": "RESTORE_STAGING_FAILED",
            },
        )
        session.flush()
    job = get_restore(session, job_id)
    assert job is not None
    return job, failed


def _expire_prior_successes(session: Session, *, except_id: uuid.UUID, now: datetime) -> None:
    """Invalidate prior confirmations on a successful replacing upload (S55 §4)."""
    session.execute(
        update(restore_jobs)
        .where(
            restore_jobs.c.id != except_id,
            restore_jobs.c.status == STATUS_SUCCEEDED,
            restore_jobs.c.expires_at.is_not(None),
            restore_jobs.c.expires_at > now,
        )
        .values(expires_at=now, updated_at=now)
    )
    session.flush()
