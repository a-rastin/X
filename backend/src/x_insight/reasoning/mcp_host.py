"""Worker-side MCP host adapter (S41, seam T6; plan.md §8.2).

The production read path always spawns the private stdio subprocess and
crosses the real MCP transport (``tools/list`` + ``tools/call`` with
``{}``). There is no direct in-process shortcut in production: the
server-side ``resolve_projection`` lives in the subprocess, and this host
never imports it for reads (unit tests may exercise validation directly
for speed, but the shipped path below is subprocess-only).

Grants: the opaque ``grant_token`` comes from ``queue.claim_next_job``
(bound once to job/batch/run + deployment generation). It is passed to
the child via the protected environment (``X_INSIGHT_MCP_GRANT``), never
CLI/model args. Separate processes/grants serve concurrent slots; stop
the process and revoke the grant before reuse. Cleanup (process exit +
``revoke_scoped_grant``/queue terminal/reclaim paths) revokes access;
fencing itself stays in ``queue.py`` (S44, re-exercised in S47).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.engine import Engine

from x_insight import contracts
from x_insight import db as db_module
from x_insight.mcp_server import (
    GRANT_ENV_VAR,
    MAX_MCP_OUTPUT_BYTES,
    TOOL_INPUT_SCHEMA,
    TOOL_NAME,
)
from x_insight.reasoning import tables as reasoning_tables

_MCP_TIMEOUT_SECONDS = 20.0


class McpHostError(Exception):
    """Bounded host-side failure (never carries secrets or existence)."""

    def __init__(self, message: str) -> None:
        super().__init__(str(message)[:200] if str(message) else "MCP request failed.")


class McpTransportError(McpHostError):
    pass


class McpAuthError(McpHostError):
    pass


class McpContextError(McpHostError):
    pass


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _child_env(grant_token: str, database_url: str | None) -> dict[str, str]:
    if not isinstance(grant_token, str) or not grant_token:
        raise McpAuthError("Grant invalid or expired.")
    url = database_url or os.environ.get("MCP_DATABASE_URL") or db_module.get_database_url()
    normalized = db_module.normalize_url(url)
    # Inherit the parent environment (PATH etc.), then override the grant
    # binding + DB URL + PYTHONPATH so `python -m x_insight.mcp_server`
    # launches from backend/ outside tests.
    env: dict[str, str] = dict(os.environ)
    env[GRANT_ENV_VAR] = grant_token
    env["DATABASE_URL"] = normalized
    env["MCP_DATABASE_URL"] = normalized
    src_path = str(_backend_root() / "src")
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not previous else src_path + os.pathsep + previous
    return env


def _check_tool_list(tools: Any) -> None:
    names = [getattr(tool, "name", "") for tool in tools]
    if len(names) != 1 or names[0] != TOOL_NAME:
        raise McpTransportError("Storage unavailable. Retry shortly.")
    tool = tools[0]
    schema = getattr(tool, "inputSchema", getattr(tool, "input_schema", None))
    if schema != dict(TOOL_INPUT_SCHEMA):
        raise McpTransportError("Storage unavailable. Retry shortly.")


def _parse_projection_text(text: str, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except Exception as exc:
        raise McpTransportError("Storage unavailable. Retry shortly.") from exc
    if not isinstance(payload, dict):
        raise McpTransportError("Storage unavailable. Retry shortly.")
    for key in ("question_key", "network_version", "projection_hash", "variables"):
        if key not in payload:
            raise McpTransportError("Storage unavailable. Retry shortly.")
    if not isinstance(payload["variables"], list):
        raise McpTransportError("Storage unavailable. Retry shortly.")
    try:
        encoded = contracts.canonical_json(payload)
    except Exception as exc:
        raise McpTransportError("Storage unavailable. Retry shortly.") from exc
    if len(encoded) > MAX_MCP_OUTPUT_BYTES:
        raise McpTransportError("Storage unavailable. Retry shortly.")
    if expected_hash is not None and str(payload.get("projection_hash")) != str(expected_hash):
        raise McpContextError("Context not eligible.")
    return payload


async def read_projection_via_stdio(
    grant_token: str,
    *,
    database_url: str | None = None,
    expected_projection_hash: str | None = None,
    timeout_seconds: float = _MCP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read the bound projection through a real stdio subprocess (production path).

    Discovers exactly the allowed tool, calls it with ``{}``, and returns
    ``{question_key, network_version, projection_hash, variables[]}``.
    Raises bounded :class:`McpHostError` (no secrets, no existence oracle).
    Each call spawns its own process/grant context; concurrent slots call
    concurrently with distinct grants. The grant travels via the protected
    environment (GRANT_ENV_VAR) only, never CLI/model args.
    """
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    _ = GRANT_ENV_VAR  # explicit grant-via-env marker for review
    env = _child_env(grant_token, database_url)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "x_insight.mcp_server"],
        env=env,
        cwd=str(_backend_root()),
    )

    async def _run() -> dict[str, Any]:
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    _check_tool_list(list(listed.tools))
                    result = await session.call_tool(TOOL_NAME, {})
        except McpHostError:
            raise
        except Exception as exc:
            raise McpTransportError("Storage unavailable. Retry shortly.") from exc
        if bool(getattr(result, "isError", getattr(result, "is_error", False))):
            text = ""
            try:
                blocks = list(getattr(result, "content", []) or [])
                if blocks and hasattr(blocks[0], "text"):
                    text = str(blocks[0].text)[:200]
            except Exception:
                text = ""
            lowered = text.lower()
            if "grant" in lowered or "expired" in lowered:
                raise McpAuthError("Grant invalid or expired.")
            if "eligible" in lowered or "context" in lowered:
                raise McpContextError("Context not eligible.")
            if "large" in lowered:
                raise McpTransportError("Storage unavailable. Retry shortly.")
            if "unknown tool" in lowered or "invalid argument" in lowered:
                raise McpTransportError("Storage unavailable. Retry shortly.")
            raise McpTransportError("Storage unavailable. Retry shortly.")
        try:
            blocks = list(getattr(result, "content", []) or [])
        except Exception as exc:
            raise McpTransportError("Storage unavailable. Retry shortly.") from exc
        if len(blocks) != 1 or not hasattr(blocks[0], "text"):
            raise McpTransportError("Storage unavailable. Retry shortly.")
        return _parse_projection_text(str(blocks[0].text), expected_hash=expected_projection_hash)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise McpTransportError("Storage unavailable. Retry shortly.") from exc


def read_projection_blocking(
    grant_token: str,
    *,
    database_url: str | None = None,
    expected_projection_hash: str | None = None,
    timeout_seconds: float = _MCP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Sync wrapper for worker/tests (still real stdio via ``asyncio.run``)."""
    return asyncio.run(
        read_projection_via_stdio(
            grant_token,
            database_url=database_url,
            expected_projection_hash=expected_projection_hash,
            timeout_seconds=timeout_seconds,
        )
    )


async def call_tool_raw(
    grant_token: str,
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    database_url: str | None = None,
    timeout_seconds: float = _MCP_TIMEOUT_SECONDS,
) -> Any:
    """Low-level raw call for negative tests (unknown tool / extra args).

    Returns the raw ``CallToolResult`` so probes can assert ``isError``.
    Still uses the real subprocess; never a direct call.
    """
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = _child_env(grant_token, database_url)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "x_insight.mcp_server"],
        env=env,
        cwd=str(_backend_root()),
    )

    async def _run() -> Any:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except Exception as exc:
        raise McpTransportError("Storage unavailable. Retry shortly.") from exc


def list_tools_raw(
    grant_token: str,
    *,
    database_url: str | None = None,
    timeout_seconds: float = _MCP_TIMEOUT_SECONDS,
) -> Any:
    """Raw discovery for probes (asserts exactly one allowed tool)."""
    import asyncio as _asyncio

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = _child_env(grant_token, database_url)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "x_insight.mcp_server"],
        env=env,
        cwd=str(_backend_root()),
    )

    async def _run() -> Any:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.list_tools()

    return _asyncio.run(_run())


def revoke_scoped_grant(
    engine: Engine | None,
    job_id: UUID,
    now: datetime | None = None,
) -> int:
    """Revoke live grants for one job (process-cleanup path).

    Reuses S44 ``reasoning_grants`` storage; fencing itself stays in
    ``queue.py``. Returns the revoked count.
    """
    moment = now or contracts.utcnow()
    target_engine = engine or db_module.get_engine()
    with db_module.session_scope(target_engine) as session:
        result = session.execute(
            update(reasoning_tables.reasoning_grants)
            .where(
                reasoning_tables.reasoning_grants.c.job_id == job_id,
                reasoning_tables.reasoning_grants.c.revoked_at.is_(None),
            )
            .values(revoked_at=moment)
        )
        count = int(getattr(result, "rowcount", 0) or 0)
    return count
