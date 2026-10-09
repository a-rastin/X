"""Scoped private MCP server (S41, seam T6; plan.md §8.2).

One low-level server, one tool ``get_question_patient_inputs`` with
``inputSchema {"type":"object","properties":{},"additionalProperties":false}``
and output ``{question_key, network_version, projection_hash, variables[]}``.
The grant comes from the protected worker environment
(``X_INSIGHT_MCP_GRANT``), never CLI/model args. Every read validates the
live binding (grant/job lease/deployment, active author, draft encounter,
ready run, fresh fingerprint, projection hash, tool name/empty args,
output size) with SELECT only and bounded generic errors. Protocol goes to
stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from x_insight import contracts
from x_insight import db as db_module
from x_insight.mcp_server import (
    GRANT_ENV_VAR,
    MAX_MCP_OUTPUT_BYTES,
    MCP_DATABASE_URL_ENV_VAR,
    MCP_NOW_ENV_VAR,
    TOOL_INPUT_SCHEMA,
    TOOL_NAME,
)

_AUTH_FAILED = "Grant invalid or expired."
_CONTEXT_FAILED = "Context not eligible."
_UNKNOWN_TOOL = "Unknown tool."
_BAD_ARGS = "Invalid arguments."
_TOO_LARGE = "Projection too large."
_STORAGE_FAILED = "Storage unavailable. Retry shortly."


def _bounded(message: str, limit: int = 200) -> str:
    text = str(message)[:limit]
    return text if text else "Request failed."


def _hash_grant_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_database_url() -> str:
    override = os.environ.get(MCP_DATABASE_URL_ENV_VAR)
    if override:
        return db_module.normalize_url(override)
    raw = os.environ.get("DATABASE_URL")
    if raw:
        return db_module.normalize_url(raw)
    return db_module.get_database_url()


def resolve_now() -> datetime:
    raw = os.environ.get(MCP_NOW_ENV_VAR)
    if raw:
        try:
            moment = contracts.parse_utc(raw)
            return moment
        except Exception:
            return contracts.utcnow()
    return contracts.utcnow()


def _select_one(session: Session, table: Any, column: Any, value: Any) -> dict[str, Any] | None:
    row = session.execute(select(table).where(column == value)).mappings().first()
    return dict(row) if row is not None else None


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_bounded(message))],
        is_error=True,
    )


def resolve_projection(
    grant_token: str,
    *,
    now: datetime | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Validate the live grant binding and return the bound stored projection.

    SELECT only; raises :class:`McpServerError` with a bounded generic
    message on any failure (no secrets, no existence oracle).
    """
    from x_insight.cases import tables as cases_tables
    from x_insight.identity import tables as identity_tables
    from x_insight.reasoning import snapshots as snapshots_service
    from x_insight.reasoning import tables as reasoning_tables

    moment = now or contracts.utcnow()
    if not isinstance(grant_token, str) or not grant_token:
        raise McpServerError(_AUTH_FAILED)
    url = database_url or resolve_database_url()
    session: Session | None = None
    engine = None
    try:
        engine = db_module.build_engine(url)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        session = factory()
        grant = (
            session.execute(
                select(reasoning_tables.reasoning_grants).where(
                    reasoning_tables.reasoning_grants.c.grant_token_hash
                    == _hash_grant_token(grant_token)
                )
            )
            .mappings()
            .first()
        )
        if grant is None:
            raise McpServerError(_AUTH_FAILED)
        grant_row = dict(grant)
        if grant_row.get("revoked_at") is not None:
            raise McpServerError(_AUTH_FAILED)
        expires_at = grant_row.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
            raise McpServerError(_AUTH_FAILED)
        if expires_at <= moment:
            raise McpServerError(_AUTH_FAILED)

        job_row = _select_one(
            session,
            reasoning_tables.reasoning_jobs,
            reasoning_tables.reasoning_jobs.c.id,
            grant_row["job_id"],
        )
        if job_row is None:
            raise McpServerError(_AUTH_FAILED)
        if str(job_row.get("status")) != "leased":
            raise McpServerError(_CONTEXT_FAILED)
        if not isinstance(job_row.get("lease_token"), str) or not job_row["lease_token"]:
            raise McpServerError(_CONTEXT_FAILED)
        deadline = job_row.get("lease_deadline")
        if not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline <= moment:
            raise McpServerError(_CONTEXT_FAILED)
        if int(job_row.get("deployment_generation", 0)) != int(
            grant_row.get("deployment_generation", 0)
        ):
            raise McpServerError(_CONTEXT_FAILED)
        # Binding: grant must name this exact job/batch/run.
        if str(job_row.get("id")) != str(grant_row.get("job_id")):
            raise McpServerError(_CONTEXT_FAILED)
        if str(job_row.get("batch_id")) != str(grant_row.get("batch_id")):
            raise McpServerError(_CONTEXT_FAILED)
        if str(job_row.get("question_run_id")) != str(grant_row.get("question_run_id")):
            raise McpServerError(_CONTEXT_FAILED)

        deployment = (
            session.execute(select(reasoning_tables.reasoning_deployment)).mappings().first()
        )
        if deployment is None:
            raise McpServerError(_STORAGE_FAILED)
        if int(dict(deployment).get("generation", 0)) != int(
            grant_row.get("deployment_generation", 0)
        ):
            raise McpServerError(_CONTEXT_FAILED)

        batch_row = _select_one(
            session,
            reasoning_tables.generation_batches,
            reasoning_tables.generation_batches.c.id,
            grant_row["batch_id"],
        )
        run_row = _select_one(
            session,
            reasoning_tables.question_runs,
            reasoning_tables.question_runs.c.id,
            grant_row["question_run_id"],
        )
        if batch_row is None or run_row is None:
            raise McpServerError(_CONTEXT_FAILED)
        if str(run_row.get("batch_id")) != str(batch_row.get("id")):
            raise McpServerError(_CONTEXT_FAILED)
        if str(run_row.get("status")) != "ready":
            raise McpServerError(_CONTEXT_FAILED)

        author_row = _select_one(
            session,
            identity_tables.users,
            identity_tables.users.c.id,
            batch_row["author_id"],
        )
        if author_row is None or not bool(author_row.get("active", False)):
            raise McpServerError(_CONTEXT_FAILED)

        encounter_row = _select_one(
            session,
            cases_tables.encounters,
            cases_tables.encounters.c.id,
            batch_row["encounter_id"],
        )
        if encounter_row is None or str(encounter_row.get("lifecycle")) != "draft":
            raise McpServerError(_CONTEXT_FAILED)
        if str(encounter_row.get("author_id")) != str(batch_row.get("author_id")):
            raise McpServerError(_CONTEXT_FAILED)

        # Freshness: note-only edits stay eligible; relevant edits go stale.
        pinned = batch_row.get("pinned_bundle") or {}
        draft_data = encounter_row.get("draft_data") or {}
        try:
            current_fp, _ = snapshots_service.compute_analysis_fingerprint(draft_data, pinned)
        except Exception as exc:
            raise McpServerError(_CONTEXT_FAILED) from exc
        if current_fp != str(batch_row.get("fingerprint")):
            raise McpServerError(_CONTEXT_FAILED)
        if str(run_row.get("fingerprint")) != str(batch_row.get("fingerprint")):
            raise McpServerError(_CONTEXT_FAILED)

        projection = run_row.get("projection")
        if not isinstance(projection, dict):
            raise McpServerError(_STORAGE_FAILED)
        try:
            actual_hash = contracts.canonical_hash(projection)
        except Exception as exc:
            raise McpServerError(_STORAGE_FAILED) from exc
        if actual_hash != str(run_row.get("projection_hash")):
            raise McpServerError(_CONTEXT_FAILED)

        pinned_versions = run_row.get("pinned_versions") or {}
        network_version = "v1"
        if isinstance(pinned_versions, dict):
            candidate = pinned_versions.get("package_version")
            if isinstance(candidate, str) and candidate:
                network_version = candidate[:64]
        variables = projection.get("variables")
        if not isinstance(variables, list):
            raise McpServerError(_STORAGE_FAILED)
        output: dict[str, Any] = {
            "question_key": str(run_row.get("question_key", "")),
            "network_version": str(network_version),
            "projection_hash": str(run_row.get("projection_hash", "")),
            "variables": variables,
        }
        try:
            encoded = contracts.canonical_json(output)
        except Exception as exc:
            raise McpServerError(_STORAGE_FAILED) from exc
        if len(encoded) > MAX_MCP_OUTPUT_BYTES:
            raise McpServerError(_TOO_LARGE)
        return output
    except McpServerError:
        raise
    except Exception as exc:
        raise McpServerError(_STORAGE_FAILED) from exc
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            if engine is not None:
                engine.dispose()


class McpServerError(Exception):
    """Bounded server-side failure (message is already safe to return)."""

    def __init__(self, message: str) -> None:
        super().__init__(_bounded(message))


def _tool_definition() -> types.Tool:
    return types.Tool(
        name=TOOL_NAME,
        description="Return the bound stored question projection. Takes no arguments.",
        input_schema=dict(TOOL_INPUT_SCHEMA),
    )


async def _handle_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
    _ = (ctx, params)
    return types.ListToolsResult(tools=[_tool_definition()])


async def _handle_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    _ = ctx
    name = getattr(params, "name", "")
    if name != TOOL_NAME:
        return _error_result(_UNKNOWN_TOOL)
    raw_args: Any = getattr(params, "arguments", None)
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict) or dict(raw_args) != {}:
        return _error_result(_BAD_ARGS)
    grant_token = os.environ.get(GRANT_ENV_VAR, "")
    if not grant_token:
        return _error_result(_AUTH_FAILED)
    try:
        output = resolve_projection(grant_token, now=resolve_now())
    except McpServerError as exc:
        return _error_result(str(exc))
    except Exception:  # pragma: no cover - defensive, never leaks internals
        return _error_result(_STORAGE_FAILED)
    try:
        encoded = contracts.canonical_json(output)
    except Exception:
        return _error_result(_STORAGE_FAILED)
    if len(encoded) > MAX_MCP_OUTPUT_BYTES:
        return _error_result(_TOO_LARGE)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=encoded.decode("utf-8"))]
    )


def build_server() -> Server:
    """Build the single private server (exactly one tool, no other handlers)."""
    return Server(
        "x-insight-scoped",
        on_list_tools=_handle_list_tools,  # type: ignore[arg-type]
        on_call_tool=_handle_call_tool,  # type: ignore[arg-type]
    )


def parse_tool_output(text: str) -> dict[str, Any]:
    """Parse and shape-check one tool result (shared with host tests)."""
    try:
        payload = json.loads(text)
    except Exception as exc:
        raise McpServerError(_STORAGE_FAILED) from exc
    if not isinstance(payload, dict):
        raise McpServerError(_STORAGE_FAILED)
    for key in ("question_key", "network_version", "projection_hash", "variables"):
        if key not in payload:
            raise McpServerError(_STORAGE_FAILED)
    if not isinstance(payload["variables"], list):
        raise McpServerError(_STORAGE_FAILED)
    return payload
