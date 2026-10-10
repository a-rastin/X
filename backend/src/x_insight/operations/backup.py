"""Consistent full backups (S54, seams T10/T1; plan.md §§4.1, 10.2).

What this module does (tasks.md S54 §§1-4; FR-41–42):

- §1: one archive holds a consistent database snapshot (every owned table
  as stored rows) + network XML bytes, original baselines/raw estimates,
  all CPT revisions/results/failures/acceptances, signed snapshots/audit,
  prompts/templates/manifests (frozen inside ``question_runs``,
  ``generation_batches`` and workflow pointers), numerical policies and
  pinned runtime/configuration/dependency locks. Adjusted-signed,
  failed-revision and retained private-draft rows are covered by
  construction: the export has NO lifecycle/status/kind filters — drafts,
  failed jobs/attempts, resets and adjustments are all selected.
- §2: consistency comes from ONE ``REPEATABLE READ`` export transaction on
  its own engine connection: PostgreSQL freezes the snapshot at the first
  SELECT, so concurrent mutations cannot skew exported XML against DB
  rows. Network XML bytes are read inside that same snapshot, never from
  mutable live files. REPEATABLE READ (not SERIALIZABLE) is sufficient and
  deliberate: the export is read-only, so there is nothing to serialize
  against — SERIALIZABLE would only add abort/retry risk with no benefit.
  ``pg_dump --snapshot`` was rejected: it needs a dump binary + superuser
  coordination for no stronger guarantee than this single snapshot.
- §3: ``sessions`` rows are EXCLUDED from the portable archive (usable
  sessions never travel; restore revokes regardless since there is nothing
  to replay). The deployment encryption key is never in the database, so
  it cannot enter the archive either; the manifest carries an explicit
  key-reentry/recovery note (S42 provider persistence is still deferred,
  so the note is generic by design, not a placeholder for a missing
  table). Admin-only routes, bounded synchronous build with an
  async-compatible polling interface, safe audit (counts/hashes only —
  no secrets, no clinical bodies, following the S52/S53 audit pattern).
- §4: archives are written to ``<id>.zip.part`` then atomically renamed;
  an interrupted dump leaves only the ``.part`` file, the job is marked
  failed, and download requires a succeeded job + final path — so there
  is never a downloadable "complete" archive from a failed job. Every
  attempt mints a fresh UUID/path, so a retry never overwrites a good
  prior backup. Only ``*.part`` files are auto-removed; successful
  archives persist until operator purge (no clinical history is deleted).

S55/S56 restore-commit is NOT implemented here: this module only creates
and inspects archives. :func:`verify_archive` is the T10 tooling that
lets dev-test (and S55 staging) check coherence — manifest hashes,
schema/content inventory, exclusion proof — from the zip alone without
touching live state. A downloadable zip without that manifest would not
satisfy tasks.md S54 Verify/exit.

Synchronous-bounded note (ponytail: no background thread, worker queue,
or lease machinery for a bounded admin operation): ``POST /backups``
builds the archive inside the request (bounded by ``MAX_ARCHIVE_BYTES``)
and returns a terminal job; ``GET /backups/{id}`` is the stable polling
interface S58 can keep if a measured dataset ever needs true background
execution. Job + audit commit together in the request transaction
(plan.md §10.1); intermediate running states are not externally visible,
which is the documented cost of that atomicity.

BFRI 3 (Moderate, proceed with tests + monitoring): Fit 5 (routes →
this service → DB, reuses audit/contracts/identity patterns) +
Testability 5 (T1 HTTP + T10 manifest/verify seams, real PostgreSQL) −
Complexity 2 (single-pass read-only export, stdlib zip) − Data Risk 3
(read-only over clinical tables; only ``backup_jobs`` writes) −
Operational Risk 2 (admin-only, no live mutation, no worker restart) = 3.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
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

# Manifest/archive schema version (part of the hashed envelope conceptually;
# bump only with a migration, per plan.md §4.2 versioning discipline).
BACKUP_MANIFEST_VERSION = "backup-v1"

# ponytail: 100 MiB ceiling; S58 measures real CPT/snapshot sizes first.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

# Tables captured in stable order (deterministic inventory). ``sessions`` is
# deliberately absent (§3: usable sessions never travel). No lifecycle /
# status / kind filters are applied anywhere below, so adjusted-signed
# (cpt_revisions + signed snapshots with accepted CPTs), failed-revision
# (reasoning_jobs/attempts with failed outcomes, which correctly have NO
# calculation_results row) and retained private-draft rows (encounters with
# lifecycle draft + draft_data, notes) are all exported as stored.
BACKUP_TABLES: tuple[str, ...] = (
    "users",
    "patients",
    "encounters",
    "notes",
    "secondary_plans",
    "signed_encounter_snapshots",
    "encounter_addenda",
    "generation_batches",
    "question_runs",
    "original_baselines",
    "proposal_snapshots",
    "reasoning_jobs",
    "reasoning_job_attempts",
    "reasoning_grants",
    "reasoning_fairness",
    "reasoning_deployment",
    "cpt_revisions",
    "question_review_states",
    "calculation_results",
    "probability_acceptances",
    "audit_events",
    "ddi_dataset_releases",
    "ddi_source_documents",
    "ddi_concepts",
    "ddi_interaction_evidence",
    "model_networks",
    "model_network_versions",
    "model_workflow_pointers",
    "idempotency_records",
    "backup_jobs",
    "alembic_version",
)

EXCLUSIONS_NOTE = (
    "sessions are excluded: usable session tokens never travel in a portable "
    "archive; restore revokes sessions regardless since there is nothing to replay. "
    "The deployment encryption key is never stored in the database and never "
    "enters the archive; secure key escrow is an operator responsibility."
)

KEY_REENTRY_NOTE = (
    "Encrypted provider credentials (when S42 persists them) decrypt only with "
    "the deployment-held key, which is NOT in this archive. Migrating or "
    "restoring without the original key requires provider key re-entry; "
    "generation stays unavailable until the key is re-entered, while charts "
    "and administration remain usable."
)

RETENTION_NOTE = (
    "Successful archives persist in the staging directory until operator purge; "
    "only '<id>.zip.part' temp files from failed/interrupted exports are "
    "auto-removed. No clinical history is ever deleted by backup cleanup."
)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

IDEMPOTENCY_OPERATION = "backups.create"

metadata = MetaData()

backup_jobs = Table(
    "backup_jobs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("created_by_id", PG_UUID(as_uuid=True), nullable=True),
    Column("created_by_username", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("manifest", JSONB(), nullable=True),
    Column("archive_path", Text, nullable=True),
    Column("archive_sha256", Text, nullable=True),
    Column("archive_bytes", BigInteger(), nullable=True),
    Column("error_code", Text, nullable=True),
    Column("error_message", Text, nullable=True),
)


def staging_dir() -> Path:
    """Staging directory for archives (env override, created on demand)."""
    configured = os.environ.get("BACKUP_STAGING_DIR", "")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "x_insight_backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def archive_path_for(job_id: uuid.UUID) -> Path:
    return staging_dir() / f"{job_id}.zip"


def part_path_for(job_id: uuid.UUID) -> Path:
    return staging_dir() / f"{job_id}.zip.part"


def _json_safe(value: Any) -> Any:
    """Convert a DB cell to JSON-safe form (UUID/str, timestamptz, bytes)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        try:
            from datetime import UTC

            moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            return contracts.serialize_utc(moment)
        except Exception:
            return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_b64__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _row_sha(rows: list[dict[str, Any]]) -> str:
    """Deterministic sha256 over rows regardless of DB return order."""
    ordered = sorted(contracts.canonical_json(r) for r in (dict(r) for r in rows))
    digest = hashlib.sha256()
    for blob in ordered:
        digest.update(blob)
        digest.update(b"\n")
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    """Canonical JSON bytes for archive entries (never presentation text)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def export_snapshot_tables(
    engine: Engine,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    """Read every :data:`BACKUP_TABLES` inside ONE REPEATABLE READ snapshot.

    Own engine connection (separate from the request session, whose earlier
    admin/idempotency SELECTs already started a READ COMMITTED transaction).
    Missing tables (older schema under test) export as empty with a manifest
    warning rather than failing the whole backup.
    """
    tables: dict[str, list[dict[str, Any]]] = {}
    inventory: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    with engine.begin() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        try:
            gen_row = (
                connection.execute(text("SELECT generation FROM reasoning_deployment WHERE id = 1"))
                .mappings()
                .first()
            )
            deployment_generation = int(gen_row["generation"]) if gen_row is not None else None
        except Exception:
            deployment_generation = None
        for name in BACKUP_TABLES:
            try:
                rows = connection.execute(text(f'SELECT * FROM "{name}"')).mappings().all()
            except Exception:
                tables[name] = []
                inventory[name] = {
                    "rows": 0,
                    "sha256": hashlib.sha256(b"").hexdigest(),
                    "missing": True,
                }
                warnings.append(f"table {name} unreadable; exported as empty")
                continue
            safe = [{str(k): _json_safe(v) for k, v in dict(r).items()} for r in rows]
            tables[name] = safe
            inventory[name] = {"rows": len(safe), "sha256": _row_sha(safe)}
        try:
            net_rows = (
                connection.execute(
                    text(
                        "SELECT id, version_number, source_bytes, source_hash "
                        'FROM "model_network_versions"'
                    )
                )
                .mappings()
                .all()
            )
            networks = [
                {
                    "version_id": str(dict(r).get("id")),
                    "version_number": int(dict(r).get("version_number") or 0),
                    "source_hash": str(dict(r).get("source_hash") or ""),
                    "source_bytes_b64": base64.b64encode(
                        bytes(dict(r).get("source_bytes") or b"")
                    ).decode("ascii"),
                }
                for r in net_rows
            ]
        except Exception:
            networks = []
            warnings.append("table model_network_versions unreadable; networks exported as empty")
    return (
        {"tables": tables, "networks": networks, "deployment_generation": deployment_generation},
        inventory,
        warnings,
    )


def build_manifest(
    *,
    job_id: uuid.UUID,
    actor: dict[str, Any],
    created_at: datetime,
    inventory: dict[str, dict[str, Any]],
    file_hashes: dict[str, str],
    deployment_generation: int | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Assemble the backup manifest (hashes/inventory, no clinical bodies).

    The manifest records per-file hashes for every sibling entry; the
    overall archive hash/size live on the job row (``GET /backups/{id}``),
    not inside the zip — recording them inside would be circular, since the
    manifest itself is part of the hashed bytes.
    """
    from x_insight import db as db_module
    from x_insight.models import inference as inference_module

    runtime: dict[str, Any] = {
        "python": sys.version.split()[0],
        "cpt_policy_version": inference_module.CPT_POLICY_VERSION,
        "engine_config": dict(inference_module.PINNED_ENGINE_CONFIG),
        "inference_tolerance": inference_module.INFERENCE_COMPARISON_TOLERANCE,
        "pinned": {
            "python": inference_module.PINNED_PYTHON_VERSION,
            "pgmpy": inference_module.PINNED_PGMPY_VERSION,
            "lxml": inference_module.PINNED_LXML_VERSION,
        },
    }
    try:
        backend_root = Path(__file__).resolve().parents[3]
        for lockfile in ("uv.lock", "pyproject.toml"):
            candidate = backend_root / lockfile
            if candidate.is_file():
                runtime[f"lock_{lockfile}"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except Exception:
        pass
    try:
        created_text = contracts.serialize_utc(created_at)
    except Exception:
        created_text = str(created_at)
    return {
        "schema_version": BACKUP_MANIFEST_VERSION,
        "backup_id": str(job_id),
        "created_at": created_text,
        "created_by": {"id": str(actor.get("id")), "username": str(actor.get("username", ""))},
        "app_schema_version": db_module.EXPECTED_SCHEMA_VERSION,
        "inventory": inventory,
        "artifacts": dict(file_hashes),
        "deployment_generation": deployment_generation,
        "exclusions": EXCLUSIONS_NOTE,
        "key_reentry_note": KEY_REENTRY_NOTE,
        "retention": RETENTION_NOTE,
        "runtime": runtime,
        "warnings": list(warnings),
    }


def build_file_payloads(snapshot: dict[str, Any], policies: dict[str, Any]) -> dict[str, bytes]:
    """Serialize sibling entries (everything except the manifest itself)."""
    database_doc = {"tables": snapshot["tables"]}
    payloads: dict[str, bytes] = {
        "database.json": _canonical_bytes(database_doc),
        "policies.json": _canonical_bytes(policies),
    }
    for entry in snapshot.get("networks", []):
        raw = base64.b64decode(entry.get("source_bytes_b64", "") or "")
        payloads[f"networks/{entry.get('version_id')}.xml"] = raw
    return payloads


def write_archive(part_path: Path, payloads: dict[str, bytes], manifest: dict[str, Any]) -> bytes:
    """Write the zip to ``part_path`` in one pass; returns the raw bytes.

    Layout: ``database.json`` + ``policies.json`` + ``networks/<id>.xml`` +
    ``manifest.json`` (single entry each). Raises on oversize; the caller
    owns the atomic rename/cleanup.
    """
    manifest_bytes = _canonical_bytes(manifest)
    with zipfile.ZipFile(
        part_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for name in sorted(payloads):
            archive.writestr(name, payloads[name])
        archive.writestr("manifest.json", manifest_bytes)
    blob = part_path.read_bytes()
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"backup archive {len(blob)} bytes exceeds {MAX_ARCHIVE_BYTES} bound")
    return blob


def safe_job(row: dict[str, Any]) -> dict[str, Any]:
    """Public job shape: status + manifest (hashes/inventory), no paths."""
    manifest = row.get("manifest")
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
        "archive_sha256": row.get("archive_sha256"),
        "archive_bytes": row.get("archive_bytes"),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "manifest": dict(manifest) if isinstance(manifest, dict) else None,
    }


def _safe_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return contracts.serialize_utc(value)
    except Exception:
        return str(value)


def get_backup(session: Session, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = session.execute(select(backup_jobs).where(backup_jobs.c.id == job_id)).mappings().first()
    return dict(row) if row is not None else None


def _request_hash() -> str:
    # POST /backups is bodyless: the key alone scopes replay per (op, actor).
    return contracts.canonical_hash({"operation": IDEMPOTENCY_OPERATION})


def create_backup(
    session: Session,
    engine: Engine,
    *,
    actor: dict[str, Any],
    request_id: str,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    """Create one backup attempt; returns (job, replayed, failed).

    Idempotent per (operation, actor, key): same key replays the original job
    without a new export; POST is bodyless so any same-key repeat replays
    (there is no "changed body" variant to 409 on). A fresh key mints a
    fresh UUID/path, so retries never overwrite a good prior backup.
    Failures mark the job failed, remove the ``.part`` file, and audit —
    never a downloadable complete archive.
    """
    from x_insight.identity import service as identity_service

    actor_id = actor["id"]
    if idempotency_key is not None:
        stored = identity_service.lookup_idempotency(
            session, operation=IDEMPOTENCY_OPERATION, actor_id=actor_id, key=idempotency_key
        )
        if stored is not None:
            if str(stored.get("request_hash")) != _request_hash():
                raise contracts.ContractError(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key was already used with a different request body.",
                )
            response_body = stored.get("response_body")
            job = None
            if isinstance(response_body, dict) and response_body.get("backup_id"):
                try:
                    job = get_backup(session, uuid.UUID(str(response_body["backup_id"])))
                except Exception:
                    job = None
            if job is not None:
                return job, True, str(job.get("status")) == STATUS_FAILED
            # Original job row is gone (operator purge): fall through and rebuild.

    job_id = uuid.uuid4()
    moment = contracts.utcnow()
    session.execute(
        insert(backup_jobs).values(
            id=job_id,
            status=STATUS_RUNNING,
            created_by_id=actor_id,
            created_by_username=str(actor.get("username", "")),
            created_at=moment,
            updated_at=moment,
        )
    )
    session.flush()

    try:
        # Resolved inside the try so an unwritable staging dir also yields an
        # observable failed job (§4) rather than an unhandled 500.
        part_path = part_path_for(job_id)
        final_path = archive_path_for(job_id)
        snapshot, inventory, warnings = export_snapshot_tables(engine)
        from x_insight.models import inference as inference_module

        policies = {
            "cpt_policy_version": inference_module.CPT_POLICY_VERSION,
            "units_for_100_pct": inference_module.UNITS_FOR_100_PCT,
            "max_decimal_places": inference_module.MAX_DECIMAL_PLACES,
            "engine_config": dict(inference_module.PINNED_ENGINE_CONFIG),
            "inference_tolerance": inference_module.INFERENCE_COMPARISON_TOLERANCE,
        }
        payloads = build_file_payloads(snapshot, policies)
        file_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()}
        manifest = build_manifest(
            job_id=job_id,
            actor=actor,
            created_at=moment,
            inventory=inventory,
            file_hashes=file_hashes,
            deployment_generation=snapshot.get("deployment_generation"),
            warnings=warnings,
        )
        blob = write_archive(part_path, payloads, manifest)
        archive_sha256 = hashlib.sha256(blob).hexdigest()
        os.replace(part_path, final_path)
        completed = contracts.utcnow()
        session.execute(
            update(backup_jobs)
            .where(backup_jobs.c.id == job_id)
            .values(
                status=STATUS_SUCCEEDED,
                manifest=dict(manifest),
                archive_path=str(final_path),
                archive_sha256=archive_sha256,
                archive_bytes=len(blob),
                completed_at=completed,
                updated_at=completed,
            )
        )
        audit_module.record_audit(
            session,
            operation=audit_module.BACKUP_CREATE_SUCCESS_OPERATION,
            actor=str(actor.get("username")),
            request_id=request_id,
            details={
                "actor_id": str(actor.get("id")),
                "actor_display": str(actor.get("username", "")),
                "backup_id": str(job_id),
                "archive_sha256": archive_sha256,
                "archive_bytes": len(blob),
                "table_rows": {name: int(info.get("rows", 0)) for name, info in inventory.items()},
            },
        )
        session.flush()
        failed = False
    except Exception as exc:
        failed = True
        code = "BACKUP_EXPORT_FAILED"
        message = str(exc)[:500] or "consistent snapshot export failed"
        try:
            candidate = locals().get("part_path")
            if candidate is not None and candidate.exists():
                candidate.unlink()
        except Exception:
            pass
        completed = contracts.utcnow()
        session.execute(
            update(backup_jobs)
            .where(backup_jobs.c.id == job_id)
            .values(
                status=STATUS_FAILED,
                error_code=code,
                error_message=message,
                completed_at=completed,
                updated_at=completed,
            )
        )
        audit_module.record_audit(
            session,
            operation=audit_module.BACKUP_CREATE_FAILED_OPERATION,
            actor=str(actor.get("username")),
            request_id=request_id,
            details={
                "actor_id": str(actor.get("id")),
                "actor_display": str(actor.get("username", "")),
                "backup_id": str(job_id),
                "error_code": code,
            },
        )
        session.flush()

    job = get_backup(session, job_id)
    assert job is not None
    if idempotency_key is not None:
        identity_service.store_idempotency(
            session,
            operation=IDEMPOTENCY_OPERATION,
            actor_id=actor_id,
            key=idempotency_key,
            request_hash=_request_hash(),
            response_status=201,
            response_body={"backup_id": str(job_id)},
        )
        session.flush()
    return job, False, failed


def read_archive_bytes(job: dict[str, Any]) -> bytes:
    """Bounded archive read for download (succeeded jobs only)."""
    if str(job.get("status")) != STATUS_SUCCEEDED:
        raise contracts.ContractError(
            409, "BACKUP_NOT_READY", "Backup is not complete; download is unavailable."
        )
    stored = job.get("archive_sha256")
    # Resolve the path from the staging dir by job id (stored paths are
    # absolute at creation; recompute so moved staging dirs keep working).
    try:
        job_id = uuid.UUID(str(job.get("id")))
    except Exception as exc:
        raise contracts.ContractError(404, "NOT_FOUND", "Backup not found.") from exc
    path = archive_path_for(job_id)
    try:
        blob = path.read_bytes()
    except FileNotFoundError as exc:
        raise contracts.ContractError(
            404, "NOT_FOUND", "Backup archive is no longer staged."
        ) from exc
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise contracts.ContractError(
            413, "REQUEST_TOO_LARGE", "Backup archive exceeds the download bound."
        )
    if isinstance(stored, str) and stored and hashlib.sha256(blob).hexdigest() != stored:
        raise contracts.ContractError(
            409, "BACKUP_CORRUPT", "Staged archive hash mismatch; download refused."
        )
    return blob


def verify_archive(path: str | Path) -> dict[str, Any]:
    """Coherence check of a backup zip WITHOUT touching live state (T10).

    Validates: required entries, per-file hashes vs the manifest, inventory
    counts vs actual database rows, schema version presence, sessions
    exclusion, and the key-reentry note. Returns ``{ok, errors, warnings,
    inventory}`` — S55 staging can gate on ``ok`` before any live mutation.
    """
    errors: list[str] = []
    warnings: list[str] = []
    inventory: dict[str, Any] = {}
    source = Path(path)
    try:
        handle = zipfile.ZipFile(source, "r")
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"archive unreadable: {exc}"],
            "warnings": [],
            "inventory": {},
        }
    with handle:
        names = set(handle.namelist())
        for required in ("manifest.json", "database.json", "policies.json"):
            if required not in names:
                errors.append(f"missing required entry: {required}")
        try:
            manifest = json.loads(handle.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "errors": [f"manifest unreadable: {exc}"],
                "warnings": [],
                "inventory": {},
            }
        if not isinstance(manifest, dict):
            return {
                "ok": False,
                "errors": ["manifest is not an object"],
                "warnings": [],
                "inventory": {},
            }
        if manifest.get("schema_version") != BACKUP_MANIFEST_VERSION:
            errors.append(f"unsupported manifest schema: {manifest.get('schema_version')}")
        if not manifest.get("app_schema_version"):
            errors.append("manifest lacks app_schema_version")
        if "sessions" in (manifest.get("inventory") or {}):
            errors.append("manifest inventory must not include sessions")
        if not str(manifest.get("key_reentry_note", "")):
            errors.append("manifest lacks the provider key-reentry note")
        artifacts = manifest.get("artifacts") or {}
        for entry in ("database.json", "policies.json"):
            if entry in names and entry in artifacts:
                try:
                    digest = hashlib.sha256(handle.read(entry)).hexdigest()
                except Exception as exc:
                    errors.append(f"cannot hash {entry}: {exc}")
                    continue
                if digest != artifacts[entry]:
                    errors.append(f"hash mismatch: {entry}")
        try:
            database = json.loads(handle.read("database.json").decode("utf-8"))
            db_tables = database.get("tables") if isinstance(database, dict) else None
        except Exception as exc:
            errors.append(f"database.json unreadable: {exc}")
            db_tables = None
        if not isinstance(db_tables, dict):
            errors.append("database.json lacks tables")
            db_tables = {}
        if "sessions" in db_tables:
            errors.append("database snapshot must not include sessions")
        manifest_inventory = manifest.get("inventory") or {}
        for name, info in manifest_inventory.items():
            actual = db_tables.get(name) if isinstance(db_tables, dict) else None
            if actual is None:
                if not (isinstance(info, dict) and info.get("missing")):
                    errors.append(f"inventory lists {name} but database.json lacks it")
                continue
            if isinstance(info, dict) and int(info.get("rows", -1)) != len(actual):
                errors.append(f"inventory count mismatch: {name}")
        for name in db_tables:
            if name not in manifest_inventory:
                warnings.append(f"database.json holds uninventoried table: {name}")
        network_names = [n for n in names if n.startswith("networks/")]
        if not network_names:
            warnings.append("archive holds no networks/ XML exports")
        for entry in network_names:
            try:
                raw = handle.read(entry)
            except Exception as exc:
                errors.append(f"cannot read {entry}: {exc}")
                continue
            if entry in artifacts and hashlib.sha256(raw).hexdigest() != artifacts[entry]:
                errors.append(f"hash mismatch: {entry}")
            if not raw.lstrip().startswith(b"<"):
                warnings.append(f"{entry} does not look like XML")
        try:
            policies = json.loads(handle.read("policies.json").decode("utf-8"))
        except Exception as exc:
            errors.append(f"policies.json unreadable: {exc}")
            policies = None
        if isinstance(policies, dict):
            if not policies.get("cpt_policy_version"):
                errors.append("policies lack cpt_policy_version")
            if not isinstance(policies.get("engine_config"), dict):
                errors.append("policies lack engine_config")
        inventory = dict(manifest_inventory) if isinstance(manifest_inventory, dict) else {}
    return {"ok": not errors, "errors": errors, "warnings": warnings, "inventory": inventory}
