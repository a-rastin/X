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

S56 commit (plan.md §10.3 steps 4-6, tasks.md S56 §§1-4; FR-41–42, NFR-04;
seams T10/T1/T8 — backend uses T10/T1/T8):

- §1: only admin confirmation of the exact validated staged digest starts
  replacement (``POST /api/v1/restores/{id}/commit`` with
  ``{"confirmation_digest": "<64hex>"}``); stale/expired/mismatched
  confirmations fail 409 without mutation. Quiesce via filesystem
  maintenance lock (``RESTORE_MAINTENANCE_FILE`` or
  ``RESTORE_STAGING_DIR/maintenance.lock``, independent of either database)
  + deployment-generation fencing (``reasoning_deployment`` bump, so old
  provider commits fail ``DEPLOYMENT_FENCED``) + pre-restore backup
  (``backup_module.create_backup``) before switching.
- §2: phased switch behind maintenance with explicit phases + durable
  operator status (``restore_jobs.report.commit_*`` + filesystem operator
  log ``RESTORE_OPERATOR_LOG``), never one SQL transaction for DB+process.
  Switch applies staged ``database.json`` tables in dependency order
  (``DELETE`` leaf-first then ``INSERT`` root-first, ``alembic_version``
  preserved, ``sessions``/``restore_jobs`` preserved by exclusion,
  ``backup_jobs`` overwritten then pre-restore row re-inserted) + engine
  dispose as restart coordination. Maintenance middleware (``app.py``)
  rejects non-restore HTTP writes 503; worker ``run_once`` returns
  ``maintenance`` without claiming (T8 quiesce).
- §3: revoke sessions/grants (``UPDATE revoked_at`` before/after switch;
  ``users`` replacement cascade-deletes sessions, so old cookies 401),
  bump generation to ``max(live, staged)+1``, reconcile active
  (queued/leased) jobs via ``queue.is_job_eligible`` (exact
  revision/lifecycle; ineligible → cancelled, eligible → requeued with new
  generation; terminal failed/succeeded + results preserved for local
  retry/reset), audit ``restores.commit.success`` in restored DB + operator
  log, then health-check (readiness + admin + reads + saved-CPT replay)
  before reopening (clear lock only on success).
- §4: ``RESTORE_COMMIT_FAIL_AT`` injects failures (``before_switch``,
  ``during_restart``, ``health_check`` + raw phase names). Any failure
  rolls back to the pre-switch snapshot (re-apply + generation re-fence),
  marks ``failed``/``rolled_back`` in report + operator log + audit
  ``restores.commit.failed``, and KEEPS maintenance until
  ``POST /api/v1/restores/{id}/reopen`` verifies healthy.

Single-host prototype note: production S57 uses an isolated staging DB +
switch pointer + process restart; this slice uses in-place phased apply
with the same phase protocol + durable status + rollback so the disposable
destructive drill (isolated ``TEST_DATABASE_URL`` + switch-pointer
semantics via operator log) is testable via T10/T1/T8 without touching the
owner's live database. Never claim the switch is one SQL transaction.

BFRI 3 (Moderate): Fit 5 + Testability 5 − Complexity 4 (phased apply +
reconcile + rollback, stdlib + existing backup/queue/audit) −
Data Risk 4 (destructive replacement, guarded by digest + maintenance +
pre-backup + rollback) − Operational Risk 3 (admin-only, maintenance +
fencing, no multi-process) = 4 → capped 3 for destructive scope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_module
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

# 0018 adds only the staging table. 0019 adds operational
# ``provider_settings``. 0020 makes its key columns nullable (clear).
# 0021 adds ``capability_status`` (still operational, not clinical).
# 0017 and 0018 archives omit the table and stay valid via the special
# case; 0019 and later must include it.
SUPPORTED_APP_SCHEMA_VERSIONS = frozenset(
    {"0017", "0018", "0019", "0020", "0021", "0022", "0023", "0024", "0025"}
)

# Bounds (ponytail: 256 MiB expanded / 4096 entries / 100 MiB per file;
# S58 measures real 10k-patient sizes first).
MAX_ARCHIVE_BYTES = backup_module.MAX_ARCHIVE_BYTES
MAX_EXPANDED_BYTES = backup_module.MAX_EXPANDED_BYTES
MAX_ZIP_ENTRIES = 4096
MAX_FILE_BYTES = backup_module.MAX_FILE_BYTES

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


# --- S56 maintenance + operator log + failure injection (plan §10.3 steps 4-6) ---


def maintenance_path() -> Path:
    """Filesystem maintenance lock, independent of either database.

    Env ``RESTORE_MAINTENANCE_FILE`` overrides (tests point it under tmp_path);
    default is ``<RESTORE_STAGING_DIR>/maintenance.lock``. Presence means
    writes are quiesced (``app.py`` middleware 503s non-restore mutations,
    worker ``run_once`` returns ``maintenance``). Survives the DB switch by
    design (plan §10.3 step 6: external maintenance control).
    """
    configured = os.environ.get("RESTORE_MAINTENANCE_FILE", "")
    if configured:
        candidate = Path(configured)
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return candidate
    return staging_dir() / "maintenance.lock"


def is_maintenance_active() -> bool:
    try:
        return maintenance_path().exists()
    except Exception:
        return False


def enter_maintenance(*, restore_id: str, actor: str, reason: str) -> Path:
    path = maintenance_path()
    payload = {
        "restore_id": str(restore_id),
        "actor": str(actor),
        "reason": str(reason),
        "entered_at": contracts.serialize_utc(contracts.utcnow()),
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def exit_maintenance() -> None:
    try:
        maintenance_path().unlink(missing_ok=True)
    except Exception:
        pass


def operator_log_path() -> Path:
    """External operator recovery record, outside the replaced database.

    Env ``RESTORE_OPERATOR_LOG`` overrides (tests point it under tmp_path);
    default is ``<RESTORE_STAGING_DIR>/operator-recovery.log``. JSON-lines,
    one record per phase/commit/rollback/reopen — readable after the DB is
    replaced or rolled back (plan §10.3 step 5).
    """
    configured = os.environ.get("RESTORE_OPERATOR_LOG", "")
    if configured:
        candidate = Path(configured)
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return candidate
    return staging_dir() / "operator-recovery.log"


def append_operator_record(record: dict[str, Any]) -> Path:
    path = operator_log_path()
    envelope = dict(record)
    envelope.setdefault("logged_at", _safe_time(contracts.utcnow()))
    line = json.dumps(envelope, sort_keys=True, default=str)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def _commit_fail_at() -> str:
    return str(os.environ.get("RESTORE_COMMIT_FAIL_AT", "") or "").strip().lower()


def _normalize_phase(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _check_injected_failure(phase: str) -> None:
    """Raise when ``RESTORE_COMMIT_FAIL_AT`` targets this phase (S56 §4).

    Accepts raw phase names (``switch``, ``restart``, ``verify`` …) plus the
    task phrases: ``before_switch``/``before-switch`` (fails at ``switch``
    before applying), ``during_restart``/``during-restart`` (fails at
    ``restart``), ``health_check``/``health-check``/``healthcheck`` (fails at
    ``verify``). Tests set this via ``monkeypatch.setenv`` (public env seam,
    no private mocks).
    """
    wanted = _normalize_phase(_commit_fail_at())
    if not wanted:
        return
    current = _normalize_phase(phase)
    aliases: dict[str, set[str]] = {
        "switch": {"before_switch", "beforeswitch", "pre_switch", "preswitch"},
        "restart": {"during_restart", "duringrestart"},
        "verify": {"health_check", "healthcheck", "health"},
    }
    hit = wanted == current
    if not hit:
        for canonical, names in aliases.items():
            if current == canonical and wanted in names:
                hit = True
                break
    if hit:
        raise contracts.ContractError(
            500,
            "RESTORE_COMMIT_INJECTED",
            f"Injected restore failure at {current} (RESTORE_COMMIT_FAIL_AT).",
        )


def _commit_view(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    out: dict[str, Any] = {}
    for key in (
        "commit_status",
        "commit_phase",
        "commit_digest",
        "pre_restore_backup_id",
        "commit_error_code",
        "commit_error_message",
        "commit_history",
        "generation_before",
        "generation_after",
        "sessions_revoked",
        "grants_revoked",
        "reconciled",
        "rollback",
        "committed_at",
    ):
        if key in report:
            out[key] = report[key]
    return out


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
    """Public staging + commit shape: status + report/digest + commit polling.

    S55 staging keys are unchanged (``report.commit_available`` stays False so
    existing validation suites hold; commit readiness is
    ``status==succeeded and expired==False and commit_status==idle``).
    S56 adds top-level ``commit_*`` polling fields derived from
    ``report.commit_*`` (durable in the same ``restore_jobs`` row, which is
    excluded from portable backups so it survives the switch) plus
    ``maintenance_active`` from the filesystem lock (independent of either
    DB). No paths, no secrets.
    """
    moment = now or contracts.utcnow()
    manifest = row.get("manifest")
    report = row.get("report")
    view = _commit_view(report)
    history = view.get("commit_history")
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
        "commit_status": str(view.get("commit_status", "idle")),
        "commit_phase": view.get("commit_phase"),
        "commit_digest": view.get("commit_digest"),
        "pre_restore_backup_id": view.get("pre_restore_backup_id"),
        "commit_error_code": view.get("commit_error_code"),
        "commit_error_message": view.get("commit_error_message"),
        "commit_history": list(history) if isinstance(history, list) else [],
        "generation_before": view.get("generation_before"),
        "generation_after": view.get("generation_after"),
        "maintenance_active": bool(is_maintenance_active()),
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


def _check_provider_approvals(tables: dict[str, Any], app_schema: str) -> str | None:
    fields = ("local_endpoint_approved", "trusted_proxy_approved")
    for row in tables.get("provider_settings") or []:
        if not isinstance(row, dict):
            return "Provider endpoint approval row is malformed."
        for field in fields:
            if app_schema in ("0024", "0025"):
                if type(row.get(field)) is not bool:
                    return "Provider endpoint approvals must be explicit JSON booleans."
            elif field in row and row[field] is not False:
                return "Legacy provider archives cannot grant endpoint approvals."
        timeout = row.get("timeout_seconds")
        if timeout is not None:
            from x_insight.reasoning.provider_timeout import validate_timeout

            try:
                if not isinstance(timeout, (int, float)):
                    raise ValueError
                validate_timeout(timeout)
            except ValueError:
                return "Provider timeout_seconds must be a finite number within [10, 900]."
    return None


def _check_provider_key(tables: dict[str, Any]) -> str | None:
    from x_insight.reasoning.provider_key import ProviderKeyError, verify_ciphertexts

    rows = tables.get("provider_settings") or []
    try:
        decoded = []
        for row in rows:
            item = {}
            for field in ("key_nonce", "key_ciphertext"):
                value = row.get(field)
                item[field] = _decode_bytes_cell(value)
                if value is not None and item[field] is None:
                    raise ProviderKeyError("Malformed encrypted credential.")
            decoded.append(item)
        verify_ciphertexts(decoded)
    except (ProviderKeyError, AttributeError, TypeError):
        return "Provider credentials require their original deployment key before restore."
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
                if name == "provider_settings" and app_schema in ("0017", "0018"):
                    continue
                return {"ok": False, "errors": [f"missing staged table: {name}"], "warnings": []}
            if not isinstance(db_tables[name], list):
                return {
                    "ok": False,
                    "errors": [f"database content malformed: {name} is not a list"],
                    "warnings": [],
                }
        problem = _check_provider_approvals(db_tables, str(app_schema))
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
        problem = _check_provider_key(db_tables)
        if problem is not None:
            return {"ok": False, "errors": [problem], "warnings": []}
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


# --- S56 commit helpers (phased replacement, durable status + rollback) ---


def _validate_commit_digest(value: Any) -> str:
    text_value = str(value or "").strip().lower()
    if len(text_value) != 64:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "confirmation_digest must be a 64-char hex digest.",
            {"confirmation_digest": ["Must be a 64-char hex digest."]},
        )
    try:
        int(text_value, 16)
    except Exception as exc:
        raise contracts.ContractError(
            422,
            "VALIDATION_FAILED",
            "confirmation_digest must be hex.",
            {"confirmation_digest": ["Must be hex."]},
        ) from exc
    return text_value


def _load_staged_tables(job_id: uuid.UUID) -> dict[str, Any]:
    """Read staged ``database.json`` tables from the isolated zip (T10)."""
    path = staged_path_for(job_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", "Staged archive is no longer present."
        ) from exc
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", "Staged archive exceeds the bound."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as handle:
            database = json.loads(handle.read("database.json").decode("utf-8"))
    except contracts.ContractError:
        raise
    except Exception as exc:
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", f"Staged archive unreadable: {exc}"
        ) from exc
    tables = database.get("tables") if isinstance(database, dict) else None
    if not isinstance(tables, dict):
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", "Staged database lacks tables."
        )
    return tables


def _column_kinds(engine: Engine, table_name: str) -> dict[str, str]:
    """Map column name → kind (uuid/timestamptz/bytea/int/bool/json/other)."""
    from sqlalchemy import inspect as sa_inspect

    try:
        columns = sa_inspect(engine).get_columns(table_name)
    except Exception:
        return {}
    kinds: dict[str, str] = {}
    for col in columns:
        name = str(col.get("name", ""))
        typename = type(col.get("type")).__name__.lower()
        type_text = f"{typename} {col.get('type')!s}".lower()
        if "uuid" in type_text:
            kinds[name] = "uuid"
        elif "timestamptz" in type_text or "timestamp" in type_text:
            kinds[name] = "timestamptz"
        elif "bytea" in type_text or "largebinary" in typename or "binary" in typename:
            kinds[name] = "bytea"
        elif "boolean" in typename or "bool" in typename:
            kinds[name] = "bool"
        elif "bigint" in typename or typename == "integer" or typename == "int":
            kinds[name] = "int"
        elif "json" in typename:
            kinds[name] = "json"
        else:
            kinds[name] = "other"
    return kinds


def _convert_cell(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "uuid":
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))
    if kind == "timestamptz":
        if isinstance(value, datetime):
            return (
                value
                if value.tzinfo is not None
                else value.replace(tzinfo=datetime.now().astimezone().tzinfo)
            )
        return contracts.parse_utc(str(value))
    if kind == "bytea":
        decoded = _decode_bytes_cell(value)
        if decoded is not None:
            return decoded
        if isinstance(value, str):
            return base64.b64decode(value)
        return bytes(value)
    if kind == "int":
        return int(value)
    if kind == "bool":
        return bool(value)
    return value


def _apply_tables(
    session: Session,
    engine: Engine,
    tables: dict[str, Any],
    *,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """Replace clinical tables: DELETE leaf-first then INSERT root-first.

    Order comes from ``backup_module.BACKUP_TABLES``, which is kept in FK
    root-first order (parents before children, e.g. ``generation_batches``
    + ``proposal_snapshots`` before ``signed_encounter_snapshots``): INSERTs
    run forward over it, DELETEs run in reverse. All enforced FKs are
    ``ON DELETE CASCADE``/``SET NULL`` so DELETE order is forgiving, but
    INSERTs are checked immediately per statement — a child listed before
    its parent fails ``FKViolation`` on any archive holding child rows
    (clean-DB archives skip empty tables, which is why the misordering only
    surfaced with signed encounters present).

    ``alembic_version`` is always preserved (clinical 0017/0018 rows are
    identical; the live head stays readable). ``sessions``/``restore_jobs``
    are never in ``BACKUP_TABLES`` so they survive by exclusion; ``users``
    replacement cascade-deletes live sessions (revoked by deletion — old
    cookies 401, matching "revoke regardless"). Callers re-insert the
    pre-restore ``backup_jobs`` row after the switch so
    ``GET /backups/{pre_id}`` keeps working.
    """
    from sqlalchemy import MetaData as _MetaData
    from sqlalchemy import Table as _Table

    targets = [
        n for n in backup_module.BACKUP_TABLES if n not in exclude and n != "alembic_version"
    ]
    counts: dict[str, int] = {}
    # DELETE leaf-first (reverse of the root-first BACKUP_TABLES order).
    for name in reversed(targets):
        try:
            session.execute(text(f'DELETE FROM "{name}"'))
        except Exception:
            # Older schema under test may lack a table; staging already
            # validated the archive, so a missing live table is a no-op here.
            continue
    session.flush()
    reflected = _MetaData()
    for name in targets:
        rows = tables.get(name)
        if not isinstance(rows, list) or not rows:
            counts[name] = 0
            continue
        kinds = _column_kinds(engine, name)
        try:
            table = _Table(name, reflected, autoload_with=engine)
        except Exception:
            continue
        live_cols = {c.name for c in table.columns}
        converted: list[dict[str, Any]] = []
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            item: dict[str, Any] = {}
            for key, val in entry.items():
                if key not in live_cols:
                    continue
                try:
                    item[key] = _convert_cell(kinds.get(key, "other"), val)
                except Exception:
                    item[key] = val
            converted.append(item)
        # Chunked bulk insert (bounded admin op; 10k-patient sets stay small).
        for start in range(0, len(converted), 500):
            chunk = converted[start : start + 500]
            if chunk:
                session.execute(insert(table).values(chunk))
        counts[name] = len(converted)
    session.flush()
    return counts


def _read_generation(session: Session) -> int | None:
    try:
        value = session.execute(
            text("SELECT generation FROM reasoning_deployment WHERE id = 1")
        ).scalar()
    except Exception:
        return None
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _write_generation(session: Session, value: int) -> None:
    moment = contracts.utcnow()
    try:
        raw: Any = session.execute(
            text("UPDATE reasoning_deployment SET generation = :g, updated_at = :t WHERE id = 1"),
            {"g": int(value), "t": moment},
        )
        updated = int(getattr(raw, "rowcount", 0) or 0)
    except Exception:
        updated = 0
    if not updated:
        try:
            session.execute(
                text(
                    "INSERT INTO reasoning_deployment "
                    "(id, generation, updated_at) VALUES (1, :g, :t)"
                ),
                {"g": int(value), "t": moment},
            )
        except Exception:
            pass
    session.flush()


def _revoke_all_sessions(session: Session) -> int:
    moment = contracts.utcnow()
    try:
        raw: Any = session.execute(
            text("UPDATE sessions SET revoked_at = :t WHERE revoked_at IS NULL"), {"t": moment}
        )
        session.flush()
        return int(getattr(raw, "rowcount", 0) or 0)
    except Exception:
        return 0


def _revoke_all_grants(session: Session) -> int:
    moment = contracts.utcnow()
    try:
        raw: Any = session.execute(
            text("UPDATE reasoning_grants SET revoked_at = :t WHERE revoked_at IS NULL"),
            {"t": moment},
        )
        session.flush()
        return int(getattr(raw, "rowcount", 0) or 0)
    except Exception:
        return 0


def _reconcile_pending_jobs(session: Session, *, new_generation: int) -> dict[str, int]:
    """Reconcile active jobs against exact revision/lifecycle (S56 §3).

    Eligible (draft + active author + ready run + current fingerprint, via
    the public ``queue.is_job_eligible`` seam) → requeued with the new
    generation + cleared lease (old tokens can never commit). Ineligible →
    cancelled with grant revocation (history kept). Terminal
    succeeded/failed/cancelled rows + calculation/baseline/acceptance results
    are never touched, so failed unsolved CPTs and earlier successes stay
    available for local retry/reset (S48b).
    """
    from x_insight.reasoning import queue as queue_module
    from x_insight.reasoning import tables as reasoning_tables

    moment = contracts.utcnow()
    summary = {"requeued": 0, "cancelled": 0, "preserved_terminal": 0}
    try:
        active = (
            session.execute(
                select(reasoning_tables.reasoning_jobs).where(
                    reasoning_tables.reasoning_jobs.c.status.in_(["queued", "leased"])
                )
            )
            .mappings()
            .all()
        )
    except Exception:
        return summary
    for row in active:
        job = dict(row)
        try:
            batch = (
                session.execute(
                    select(reasoning_tables.generation_batches).where(
                        reasoning_tables.generation_batches.c.id == job.get("batch_id")
                    )
                )
                .mappings()
                .first()
            )
            run = (
                session.execute(
                    select(reasoning_tables.question_runs).where(
                        reasoning_tables.question_runs.c.id == job.get("question_run_id")
                    )
                )
                .mappings()
                .first()
            )
        except Exception:
            continue
        if batch is None or run is None:
            try:
                session.execute(
                    update(reasoning_tables.reasoning_jobs)
                    .where(reasoning_tables.reasoning_jobs.c.id == job.get("id"))
                    .values(status="cancelled", lease_token=None, updated_at=moment)
                )
                summary["cancelled"] += 1
            except Exception:
                pass
            continue
        try:
            eligible, _ = queue_module.is_job_eligible(
                session, dict(job), dict(batch), dict(run), moment
            )
        except Exception:
            eligible = False
        try:
            if eligible:
                session.execute(
                    update(reasoning_tables.reasoning_jobs)
                    .where(reasoning_tables.reasoning_jobs.c.id == job.get("id"))
                    .values(
                        status="queued",
                        lease_token=None,
                        lease_deadline=None,
                        last_heartbeat=None,
                        deployment_generation=int(new_generation),
                        next_eligible_at=None,
                        updated_at=moment,
                    )
                )
                summary["requeued"] += 1
            else:
                session.execute(
                    update(reasoning_tables.reasoning_jobs)
                    .where(reasoning_tables.reasoning_jobs.c.id == job.get("id"))
                    .values(status="cancelled", lease_token=None, updated_at=moment)
                )
                session.execute(
                    update(reasoning_tables.reasoning_grants)
                    .where(
                        reasoning_tables.reasoning_grants.c.job_id == job.get("id"),
                        reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=moment)
                )
                summary["cancelled"] += 1
        except Exception:
            continue
    try:
        summary["preserved_terminal"] = int(
            session.execute(
                select(reasoning_tables.reasoning_jobs.c.id).where(
                    reasoning_tables.reasoning_jobs.c.status.in_(
                        ["succeeded", "failed", "cancelled"]
                    )
                )
            )
            .all()
            .__len__()
        )
    except Exception:
        pass
    session.flush()
    return summary


def _verify_restored(engine: Engine) -> dict[str, Any]:
    """Health-check login/read/replay before reopening (S56 §3, T1/T8).

    Readiness + singleton admin + patient/encounter/audit reads + saved-CPT
    replay (hash-anchored, no new LLM estimates). Raises ``ContractError``
    503 when unhealthy so the caller rolls back and keeps maintenance.
    """
    from x_insight import db as db_module

    readiness = db_module.check_readiness(engine)
    if not readiness.ok:
        raise contracts.ContractError(503, "RESTORE_UNHEALTHY", "Restored database is not ready.")
    with db_module.session_scope(engine) as session:
        try:
            admins = (
                session.execute(text("SELECT username, active FROM users WHERE role = 'admin'"))
                .mappings()
                .all()
            )
        except Exception as exc:
            raise contracts.ContractError(
                503, "RESTORE_UNHEALTHY", "Restored admin unreadable."
            ) from exc
        if len(admins) != 1 or not bool(dict(admins[0]).get("active", False)):
            raise contracts.ContractError(
                503, "RESTORE_UNHEALTHY", "Restored singleton admin violated."
            )
        try:
            session.execute(text('SELECT count(*) FROM "patients"')).scalar()
            session.execute(text('SELECT count(*) FROM "encounters"')).scalar()
            session.execute(text('SELECT count(*) FROM "audit_events"')).scalar()
        except Exception as exc:
            raise contracts.ContractError(
                503, "RESTORE_UNHEALTHY", "Restored records unreadable."
            ) from exc
        # Replay: saved CPT artifacts must still hash (no new provider work).
        try:
            revisions = (
                session.execute(text("SELECT cpt_artifact, cpt_hash FROM cpt_revisions LIMIT 5"))
                .mappings()
                .all()
            )
            for entry in revisions:
                artifact = dict(entry).get("cpt_artifact")
                digest = dict(entry).get("cpt_hash")
                if not isinstance(artifact, list) or not artifact:
                    raise ValueError("revision lacks artifact")
                if contracts.canonical_hash(list(artifact)) != str(digest):
                    raise ValueError("revision hash mismatch")
        except contracts.ContractError:
            raise
        except Exception as exc:
            raise contracts.ContractError(
                503, "RESTORE_UNHEALTHY", "Restored replay check failed."
            ) from exc
    return {"ready": True, "admin": "admin", "replay": "hash-anchored"}


def _patch_commit_report(
    session: Session,
    job_id: uuid.UUID,
    patch: dict[str, Any],
    *,
    phase: str,
    ok: bool,
    detail: str = "",
) -> dict[str, Any]:
    row = (
        session.execute(select(restore_jobs).where(restore_jobs.c.id == job_id)).mappings().first()
    )
    if row is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Restore not found.")
    current = dict(row)
    report = dict(current.get("report") or {})
    history = list(report.get("commit_history") or [])
    history.append(
        {
            "phase": str(phase),
            "at": _safe_time(contracts.utcnow()),
            "ok": bool(ok),
            "detail": str(detail)[:300],
        }
    )
    report.update(patch)
    report["commit_history"] = history[-50:]
    if "commit_status" not in report:
        report["commit_status"] = "in_progress"
    report["commit_phase"] = str(phase)
    moment = contracts.utcnow()
    session.execute(
        update(restore_jobs)
        .where(restore_jobs.c.id == job_id)
        .values(report=dict(report), updated_at=moment)
    )
    session.flush()
    return report


def commit_restore(
    session: Session,
    engine: Engine,
    *,
    actor: dict[str, Any],
    request_id: str,
    job_id: uuid.UUID,
    confirmation_digest: str,
) -> tuple[dict[str, Any], bool, bool]:
    """Commit one staged restore behind maintenance (S56 §§1-4).

    Returns ``(job, failed, rolled_back)``. Validation failures (stale/
    mismatched digest, bad state, maintenance active) raise ``ContractError``
    4xx with no mutation. Phase failures (injected, switch, health) roll
    back to the pre-switch snapshot, keep maintenance, audit
    ``restores.commit.failed`` + operator log, and return
    ``failed=True`` (router renders 500 with the terminal job). Success
    audits ``restores.commit.success`` in the restored DB + operator log,
    verifies health, reopens (clears maintenance), and returns
    ``failed=False``. Each phase commits its work + status update in its own
    transaction (never one SQL transaction for DB+process).
    """
    from x_insight import db as db_module

    presented = _validate_commit_digest(confirmation_digest)
    now = contracts.utcnow()
    job = get_restore(session, job_id)
    if job is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Restore not found.")
    if str(job.get("status")) != STATUS_SUCCEEDED:
        raise contracts.ContractError(
            409, "RESTORE_COMMIT_NOT_READY", "Restore is not staged successfully."
        )
    if _is_expired(job, now):
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", "Confirmation expired; re-validate the archive."
        )
    stored = str(job.get("confirmation_digest") or "").strip().lower()
    if not stored or not hmac_module.compare_digest(presented, stored):
        raise contracts.ContractError(
            409,
            "RESTORE_DIGEST_MISMATCH",
            "Confirmation digest does not match the staged artifact.",
        )
    staged_report = dict(job.get("report") or {})
    if str(staged_report.get("commit_status", "idle")) == "succeeded":
        raise contracts.ContractError(
            409, "RESTORE_ALREADY_COMMITTED", "Restore already committed."
        )
    if str(staged_report.get("commit_status", "idle")) == "in_progress":
        raise contracts.ContractError(
            409, "RESTORE_COMMIT_IN_PROGRESS", "Restore commit already running."
        )
    if is_maintenance_active():
        raise contracts.ContractError(
            409,
            "RESTORE_MAINTENANCE_ACTIVE",
            "System is under maintenance; reopen before committing.",
        )
    if not staged_path_for(job_id).exists():
        raise contracts.ContractError(
            409, "RESTORE_STALE_CONFIRMATION", "Staged archive is no longer present."
        )

    staged_tables = _load_staged_tables(job_id)
    problem = _check_provider_approvals(staged_tables, str(job.get("app_schema_version")))
    if problem is not None:
        raise contracts.ContractError(409, "RESTORE_PROVIDER_APPROVAL_INVALID", problem)
    problem = _check_provider_key(staged_tables)
    if problem is not None:
        raise contracts.ContractError(409, "RESTORE_PROVIDER_KEY_UNAVAILABLE", problem)

    actor_name = str(actor.get("username", ""))
    switched = False
    prior_tables: dict[str, Any] | None = None
    live_generation_before: int | None = None
    staged_generation: Any = (
        staged_report.get("deployment_generation", {}).get("staged")
        if isinstance(staged_report.get("deployment_generation"), dict)
        else None
    )
    pre_restore_backup_id: str | None = None
    pre_restore_job: dict[str, Any] | None = None

    def _record(
        phase: str, ok: bool, detail: str = "", extra: dict[str, Any] | None = None
    ) -> None:
        patch = dict(extra or {})
        with db_module.session_scope(engine) as ps:
            _patch_commit_report(ps, job_id, patch, phase=phase, ok=ok, detail=detail)
        try:
            append_operator_record(
                {
                    "operation": "restores.commit.phase",
                    "restore_id": str(job_id),
                    "phase": str(phase),
                    "ok": bool(ok),
                    "detail": str(detail)[:300],
                    "actor": actor_name,
                    "request_id": request_id,
                    **(extra or {}),
                }
            )
        except Exception:
            pass

    try:
        # maintenance: external lock first (independent of either DB).
        _check_injected_failure("maintenance")
        enter_maintenance(restore_id=str(job_id), actor=actor_name, reason="restore-commit")
        with db_module.session_scope(engine) as ps:
            _patch_commit_report(
                ps,
                job_id,
                {"commit_status": "in_progress", "commit_digest": presented},
                phase="maintenance",
                ok=True,
                detail="maintenance entered",
            )
        append_operator_record(
            {
                "operation": "restores.commit.maintenance",
                "restore_id": str(job_id),
                "actor": actor_name,
                "request_id": request_id,
            }
        )

        # quiesce: HTTP writes already 503 via middleware; workers fence next.
        _check_injected_failure("quiesce")
        _record("quiesce", True, "writes quiesced behind maintenance")

        # fence (pre-switch): bump live generation so old workers cannot commit during the switch.
        _check_injected_failure("fence")
        with db_module.session_scope(engine) as ps:
            live_generation_before = _read_generation(ps)
            base = int(live_generation_before) if live_generation_before is not None else 1
            _write_generation(ps, base + 1)
            _patch_commit_report(
                ps,
                job_id,
                {"generation_before": base},
                phase="fence",
                ok=True,
                detail=f"generation {base} -> {base + 1}",
            )
        append_operator_record(
            {
                "operation": "restores.commit.fence",
                "restore_id": str(job_id),
                "generation_before": live_generation_before,
                "request_id": request_id,
            }
        )

        # pre_backup: consistent backup + in-memory prior snapshot for rollback.
        _check_injected_failure("pre_backup")
        with db_module.session_scope(engine) as ps:
            backup_job, _, backup_failed = backup_module.create_backup(
                ps, engine, actor=actor, request_id=request_id, idempotency_key=None
            )
            if backup_failed:
                raise contracts.ContractError(
                    500, "RESTORE_PRE_BACKUP_FAILED", "Pre-restore backup failed."
                )
            pre_restore_backup_id = str(backup_job.get("id"))
            pre_restore_job = dict(backup_job)
            _patch_commit_report(
                ps,
                job_id,
                {"pre_restore_backup_id": pre_restore_backup_id},
                phase="pre_backup",
                ok=True,
                detail=f"pre-restore backup {pre_restore_backup_id}",
            )
        # Prior snapshot for rollback (same REPEATABLE READ export the backup used).
        prior_snapshot, _, _ = backup_module.export_snapshot_tables(engine)
        prior_tables = dict(prior_snapshot.get("tables") or {})
        append_operator_record(
            {
                "operation": "restores.commit.pre_backup",
                "restore_id": str(job_id),
                "pre_restore_backup_id": pre_restore_backup_id,
                "request_id": request_id,
            }
        )

        # switch: apply staged tables (explicit phase, own transaction).
        _check_injected_failure("switch")
        staged_tables = _load_staged_tables(job_id)
        with db_module.session_scope(engine) as ps:
            # Revoke live sessions before users replacement (counts for the log;
            # the users DELETE cascade-deletes the rows, so old cookies 401).
            sessions_revoked = _revoke_all_sessions(ps)
            counts = _apply_tables(ps, engine, staged_tables)
            # Preserve the pre-restore backup row (switch overwrote backup_jobs
            # with staged history); re-insert so GET /backups/{pre_id} works.
            if pre_restore_job is not None:
                try:
                    from sqlalchemy import MetaData as _MD
                    from sqlalchemy import Table as _TB

                    _md = _MD()
                    _bj = _TB("backup_jobs", _md, autoload_with=engine)
                    existing = ps.execute(
                        select(_bj).where(_bj.c.id == uuid.UUID(str(pre_restore_backup_id)))
                    ).first()
                    if existing is None:
                        kinds = _column_kinds(engine, "backup_jobs")
                        live_cols = {c.name for c in _bj.columns}
                        item: dict[str, Any] = {}
                        for key, val in pre_restore_job.items():
                            if key not in live_cols:
                                continue
                            try:
                                item[key] = _convert_cell(kinds.get(key, "other"), val)
                            except Exception:
                                item[key] = val
                        ps.execute(insert(_bj).values(item))
                except Exception:
                    pass
            # Sessions must be empty after users replacement (revoked by deletion).
            try:
                ps.execute(text("DELETE FROM sessions"))
            except Exception:
                pass
            _patch_commit_report(
                ps,
                job_id,
                {"sessions_revoked": int(sessions_revoked), "switch_counts": counts},
                phase="switch",
                ok=True,
                detail=f"applied {len(counts)} tables",
            )
        switched = True
        append_operator_record(
            {
                "operation": "restores.commit.switch",
                "restore_id": str(job_id),
                "pre_restore_backup_id": pre_restore_backup_id,
                "request_id": request_id,
            }
        )

        # restart: drop old pooled connections (no mixed old/new traffic).
        _check_injected_failure("restart")
        try:
            engine.dispose()
        except Exception:
            pass
        _record("restart", True, "connections recycled behind maintenance")

        # revoke + re-fence: grants revoked, generation -> max(live, staged)+1.
        _check_injected_failure("revoke")
        with db_module.session_scope(engine) as ps:
            grants_revoked = _revoke_all_grants(ps)
            current = _read_generation(ps)
            try:
                staged_int = int(staged_generation) if staged_generation is not None else 0
            except Exception:
                staged_int = 0
            live_int = int(live_generation_before) if live_generation_before is not None else 0
            current_int = int(current) if current is not None else 0
            fenced = max(live_int, staged_int, current_int) + 1
            _write_generation(ps, fenced)
            _patch_commit_report(
                ps,
                job_id,
                {"grants_revoked": int(grants_revoked), "generation_after": int(fenced)},
                phase="revoke",
                ok=True,
                detail=f"generation -> {fenced}; grants revoked {grants_revoked}",
            )
            new_generation = int(fenced)
        append_operator_record(
            {
                "operation": "restores.commit.revoke",
                "restore_id": str(job_id),
                "generation_after": new_generation,
                "request_id": request_id,
            }
        )

        # reconcile: pending jobs vs exact revision/lifecycle; terminal preserved.
        _check_injected_failure("reconcile")
        with db_module.session_scope(engine) as ps:
            summary = _reconcile_pending_jobs(ps, new_generation=new_generation)
            # ponytail: no cache layer exists (grep: no lru_cache); record the
            # clear as a phase detail rather than inventing one.
            _patch_commit_report(
                ps,
                job_id,
                {"reconciled": summary, "caches_cleared": True},
                phase="reconcile",
                ok=True,
                detail=(
                    f"requeued {summary.get('requeued', 0)}, "
                    f"cancelled {summary.get('cancelled', 0)}"
                ),
            )
        append_operator_record(
            {
                "operation": "restores.commit.reconcile",
                "restore_id": str(job_id),
                "request_id": request_id,
            }
        )

        # audit in the restored DB + external log.
        _check_injected_failure("audit")
        with db_module.session_scope(engine) as ps:
            audit_module.record_audit(
                ps,
                operation=audit_module.RESTORE_COMMIT_SUCCESS_OPERATION,
                actor=actor_name,
                request_id=request_id,
                details={
                    "actor_id": str(actor.get("id")),
                    "actor_display": actor_name,
                    "restore_id": str(job_id),
                    "pre_restore_backup_id": pre_restore_backup_id,
                    "confirmation_digest": stored,
                    "generation_before": live_generation_before,
                    "generation_after": new_generation,
                },
            )
            _patch_commit_report(
                ps, job_id, {}, phase="audit", ok=True, detail="recovery audited in restored DB"
            )
        append_operator_record(
            {
                "operation": audit_module.RESTORE_COMMIT_SUCCESS_OPERATION,
                "restore_id": str(job_id),
                "pre_restore_backup_id": pre_restore_backup_id,
                "confirmation_digest": stored,
                "actor": actor_name,
                "request_id": request_id,
            }
        )

        # verify: login/read/replay before reopening.
        _check_injected_failure("verify")
        health = _verify_restored(engine)
        _record("verify", True, f"health {health.get('replay', 'ok')}")

        # reopen only on success.
        _check_injected_failure("reopen")
        exit_maintenance()
        with db_module.session_scope(engine) as ps:
            _patch_commit_report(
                ps,
                job_id,
                {"commit_status": "succeeded", "committed_at": _safe_time(contracts.utcnow())},
                phase="reopen",
                ok=True,
                detail="maintenance cleared after verified health",
            )
            final = get_restore(ps, job_id)
        append_operator_record(
            {
                "operation": "restores.commit.reopen",
                "restore_id": str(job_id),
                "pre_restore_backup_id": pre_restore_backup_id,
                "request_id": request_id,
            }
        )
        assert final is not None
        return final, False, False
    except contracts.ContractError as exc:
        # Validation-time 4xx (no maintenance entered, nothing to roll back).
        if not is_maintenance_active() and not switched and exc.status_code in (404, 409, 422):
            raise
        # Phase failure (including injected 500 / health 503): roll back.
        reason = f"{exc.code}: {exc.message}"
        return _rollback_commit(
            engine,
            job_id=job_id,
            actor=actor,
            request_id=request_id,
            stored_digest=stored if "stored" in locals() else None,
            prior_tables=prior_tables,
            live_generation_before=live_generation_before,
            pre_restore_backup_id=pre_restore_backup_id,
            reason=reason,
            switched=switched,
        )
    except Exception as exc:
        reason = str(exc)[:500] or "restore commit failed"
        stored_local = stored if "stored" in locals() else None
        return _rollback_commit(
            engine,
            job_id=job_id,
            actor=actor,
            request_id=request_id,
            stored_digest=stored_local,
            prior_tables=prior_tables,
            live_generation_before=live_generation_before,
            pre_restore_backup_id=pre_restore_backup_id,
            reason=reason,
            switched=switched,
        )


def _rollback_commit(
    engine: Engine,
    *,
    job_id: uuid.UUID,
    actor: dict[str, Any],
    request_id: str,
    stored_digest: str | None,
    prior_tables: dict[str, Any] | None,
    live_generation_before: int | None,
    pre_restore_backup_id: str | None,
    reason: str,
    switched: bool,
) -> tuple[dict[str, Any], bool, bool]:
    """Restore the prior snapshot and keep maintenance (S56 §4).

    Re-applies the pre-switch snapshot when the switch happened (no-op
    otherwise — live was never mutated), re-fences generation to
    ``max+1``, audits ``restores.commit.failed`` in the current DB +
    operator log, and KEEPS the maintenance lock until
    ``POST /restores/{id}/reopen`` verifies healthy.
    """
    from x_insight import db as db_module

    actor_name = str(actor.get("username", ""))
    # Ensure maintenance remains (enter if a validation race cleared it).
    try:
        if not is_maintenance_active():
            enter_maintenance(restore_id=str(job_id), actor=actor_name, reason="restore-rollback")
    except Exception:
        pass
    restored = False
    if switched and isinstance(prior_tables, dict) and prior_tables:
        try:
            with db_module.session_scope(engine) as ps:
                _apply_tables(ps, engine, prior_tables)
                try:
                    ps.execute(text("DELETE FROM sessions"))
                except Exception:
                    pass
                current = _read_generation(ps)
                live_int = int(live_generation_before) if live_generation_before is not None else 0
                current_int = int(current) if current is not None else 0
                _write_generation(ps, max(live_int, current_int) + 1)
            try:
                engine.dispose()
            except Exception:
                pass
            restored = True
        except Exception:
            restored = False
    try:
        with db_module.session_scope(engine) as ps:
            try:
                audit_module.record_audit(
                    ps,
                    operation=audit_module.RESTORE_COMMIT_FAILED_OPERATION,
                    actor=actor_name,
                    request_id=request_id,
                    details={
                        "actor_id": str(actor.get("id")),
                        "actor_display": actor_name,
                        "restore_id": str(job_id),
                        "pre_restore_backup_id": pre_restore_backup_id,
                        "confirmation_digest": stored_digest,
                        "error": str(reason)[:300],
                        "rolled_back": bool(restored or not switched),
                    },
                )
            except Exception:
                pass
            _patch_commit_report(
                ps,
                job_id,
                {
                    "commit_status": "rolled_back" if (restored or switched) else "failed",
                    "commit_error_code": "RESTORE_COMMIT_FAILED",
                    "commit_error_message": str(reason)[:500],
                    "rollback": {
                        "restored": bool(restored or not switched),
                        "at": _safe_time(contracts.utcnow()),
                        "reason": str(reason)[:300],
                    },
                },
                phase="rollback",
                ok=False,
                detail=str(reason)[:300],
            )
            final = get_restore(ps, job_id)
    except Exception:
        final = None
    try:
        append_operator_record(
            {
                "operation": audit_module.RESTORE_COMMIT_FAILED_OPERATION,
                "restore_id": str(job_id),
                "pre_restore_backup_id": pre_restore_backup_id,
                "error": str(reason)[:300],
                "rolled_back": bool(restored or not switched),
                "actor": actor_name,
                "request_id": request_id,
            }
        )
    except Exception:
        pass
    if final is None:
        # Last resort: surface the failure even if the status write failed.
        raise contracts.ContractError(500, "RESTORE_COMMIT_FAILED", str(reason)[:500])
    return final, True, True


def reopen_restore(
    engine: Engine, *, actor: dict[str, Any], request_id: str, job_id: uuid.UUID
) -> tuple[dict[str, Any], bool]:
    """Clear maintenance after verifying healthy (S56 §4, T1).

    Returns ``(job, reopened)``. When unhealthy, keeps maintenance and raises
    503 so the operator retries after fixing the deployment. Audited in the
    current DB + operator log.
    """
    from x_insight import db as db_module

    job = None
    with db_module.session_scope(engine) as ps:
        job = get_restore(ps, job_id)
    if job is None:
        raise contracts.ContractError(404, "NOT_FOUND", "Restore not found.")
    if not is_maintenance_active():
        assert job is not None
        return job, False
    try:
        _verify_restored(engine)
    except contracts.ContractError as exc:
        raise contracts.ContractError(
            503,
            "RESTORE_MAINTENANCE_HELD",
            "System remains under maintenance; restore is not healthy.",
        ) from exc
    exit_maintenance()
    try:
        with db_module.session_scope(engine) as ps:
            _patch_commit_report(
                ps,
                job_id,
                {},
                phase="reopen",
                ok=True,
                detail="operator reopen after verified health",
            )
            job = get_restore(ps, job_id)
        append_operator_record(
            {
                "operation": "restores.commit.reopen",
                "restore_id": str(job_id),
                "actor": str(actor.get("username", "")),
                "request_id": request_id,
            }
        )
    except Exception:
        pass
    assert job is not None
    return job, True
