# backend/src/x_insight/reasoning/provider.py

Source SHA-256: 61c2bb49d2ad7e14b313a867d4d039d1a7ffc6f225f394372fc1577f5e7db51c

## Purpose
The only LLM boundary: bounded CPT estimation plus MCP tool bridging. Everything else in the
pipeline is deterministic.

## Important Symbols
- Bounds: `MAX_TOOL_CALLS=10`, configurable provider timeout, `MAX_PROVIDER_BYTES=1 MiB`,
  `RESPONSE_SCHEMA_VERSION="cpt-response-v1"`.
- `build_outbound_payload()` — system prompt + canonical JSON user message and exactly one
  function tool (the MCP tool), `tool_choice=auto`; capability `schema` → strict
  `response_format.json_schema`, `json` → `json_object`.
- `validate_strict_response()` — the single validator for both modes; numeric validation is
  delegated to `models.inference.validate_cpts`.
- `BoundedProviderAdapter.estimate()` — initial MCP read, then one HTTP exchange per step;
  each provider tool call must be `get_question_patient_inputs` with `{}` args, served by a
  real MCP stdio subprocess.
- `_post_once()` — budget check, bearer header, status→error mapping (3xx capability,
  401/403 auth, 404/422 model, 429 retryable, 5xx transient), plus one no-format retry when
  `response_format` is rejected.
- `probe_capability()` — synthetic forced tool call + JSON object for the admin diagnostic.
- `detect_capability_mode()` — separately detects schema or JSON format; JSON fallback is tried
  only after an explicit response-format rejection. Success requires an object containing only
  `ready` with the actual boolean `true` (numeric 1 and 1.0 are rejected). Detection does not
  choose the saved mode.
- `DeterministicProviderEndpoint` — test-only loopback double.

## Behavior and Constraints
- `_assistant_text()`: non-empty `content` wins; empty content falls back to
  `reasoning_content` (reasoning models).
- Redirects are never followed; all posting goes through
  `provider_destination.checked_post` with a per-revision approved endpoint.
- Templates and prompt text never cross this boundary in either direction — the provider
  returns numbers for the declared node contract only.

## Related Files
- `backend/src/x_insight/reasoning/provider_config.py`, `provider_destination.py`,
  `provider_key.py`, `mcp_host.py`.
