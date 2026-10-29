# backend/src/x_insight/mcp_server/server.py

Source SHA-256: 5ac3e6d4c3cb34163f321b2e75f0e3d0ba7959e169444f2e7c091c1f3b459164
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
The private MCP server: exactly one SELECT-only tool that hands a **scoped** patient
projection to the worker. Run as a per-call stdio subprocess; never a listening service.

## Important Symbols
- Contract (in `mcp_server/__init__.py`): `TOOL_NAME="get_question_patient_inputs"`,
  `GRANT_ENV_VAR="X_INSIGHT_MCP_GRANT"`, `MAX_MCP_OUTPUT_BYTES=65_536`, input schema an empty
  object with `additionalProperties: false`.
- `resolve_projection(grant_token, ...)` — the whole authorization story: grant hash lookup →
  not revoked / not expired → job must be `leased` with a live lease → deployment generation
  must match → job/batch/run ids must match the grant → run status `ready` → author active →
  encounter still a `draft` owned by the batch author → recomputed fingerprint must equal the
  stored batch fingerprint → projection hash must match → output size bound.
- `build_server()` — exposes only `list_tools` and `call_tool`; unknown tool names or non-empty
  args return a bounded error.

## Behavior and Constraints
- All six failure modes collapse to bounded messages so the subprocess is not an existence
  oracle. SELECT-only; intended to run as the read-only DB role.
- The grant travels only through the child environment; the worker revokes it afterwards.

## Related Files
- `backend/src/x_insight/mcp_server/__main__.py` — `python -m x_insight.mcp_server` entry.
- `backend/src/x_insight/reasoning/mcp_host.py` — spawns this per MCP read.
