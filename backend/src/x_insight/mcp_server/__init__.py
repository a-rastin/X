"""Private scoped MCP server package (S41, seam T6; plan.md §8.2).

Exactly one server implementation exposing exactly one tool
(``get_question_patient_inputs``). No public listener, no patient
search/write/inference/shell/file tool exists anywhere else.

Role config: the server issues SELECT only (read-only query path over
``generation_batches``/``question_runs``/``reasoning_jobs``/grants plus
author/encounter/draft reads). Where feasible run the subprocess with the
``x_insight_readonly`` database role (``MCP_DATABASE_URL`` pointing at a
readonly login); the disposable local stack runs every login as the owner,
so the grants there harden deployments. Grant checks are enforced in the
server on every read regardless of role, and no UPDATE/INSERT/DELETE
exists on this path.

Production reads always cross the real stdio transport via
``reasoning.mcp_host``. ``mcp_server.server.resolve_projection`` is the
server-side validator (used inside the subprocess); it is not a
production direct-call bypass.

SDK pin: ``mcp==2.3.0`` (see ``backend/pyproject.toml`` + ``backend/uv.lock``).
The real subprocess is verified on the low-level ``Server`` +
``stdio_client``/``ClientSession`` combo from that pin.
"""

from __future__ import annotations

TOOL_NAME = "get_question_patient_inputs"

#: Protected worker-process environment variable carrying the opaque grant.
#: Never a CLI argument, never a model argument.
GRANT_ENV_VAR = "X_INSIGHT_MCP_GRANT"

#: Optional override for the database URL inside the subprocess.
#: Falls back to ``DATABASE_URL``.
MCP_DATABASE_URL_ENV_VAR = "MCP_DATABASE_URL"

#: Optional deterministic clock override (ISO-8601 UTC) for tests.
MCP_NOW_ENV_VAR = "X_INSIGHT_MCP_NOW"

#: Bounded MCP output (canonical JSON bytes of the tool result).
MAX_MCP_OUTPUT_BYTES = 65_536

#: Exact empty-argument schema for the single tool.
TOOL_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
