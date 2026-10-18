# Issue: Provider capability check cannot verify a real provider

Status: fixed
Severity: medium
Priority: high
Affected area: administrator provider capability test
Observed on: `POST /api/v1/api-settings/test` after saving provider settings

## Summary

After an administrator saves a provider and runs Test provider, the check can
complete the HTTP call and still fail with `Provider capability check did not
succeed.` A real OpenAI-compatible endpoint cannot be marked verified. The
probe posts a private payload and accepts only a synthetic JSON echo that no
production chat endpoint returns.

This is not the allowlist, missing-key, decrypt, connection, or redirect
failure. Those paths use different fixed diagnostics. This string means the
call returned and the body was not the echo the probe requires.

## Impact and rating

Test provider cannot prove that a saved endpoint accepts the credential and
model, supports tools, or returns usable JSON. `verified` is reachable only
from a stub that echoes `ok`, `supports_tools`, and `supports_json`. The same
diagnostic also covers HTTP 401, 404, 400, 5xx, and a normal HTTP 200 chat
completion, so an operator cannot tell a bad key, a wrong URL, an unknown
model, or this protocol mismatch apart.

The check fails closed. It does not echo the API key or the upstream body, and
it does not follow redirects. Generation is a separate gap: the worker still
reads `X_INSIGHT_PROVIDER_ENDPOINT`, `X_INSIGHT_PROVIDER_MODEL`, and
`X_INSIGHT_PROVIDER_CAPABILITY`, and it does not load saved settings. A passing
check would not configure generation either.

Severity is medium because there is no confidentiality bypass and the
generation path does not depend on this result. Priority is high because the
only administrator proof of a saved provider cannot succeed against the
protocol the product claims to use, and the diagnostic hides the upstream
reason.

## Root cause

`_capability_outcome` in `backend/src/x_insight/reasoning/provider_config.py`
does not perform the adapter's chat/tool exchange.

- It posts `{purpose: "capability", model, tools: [ping]}` to the saved URL
  through `post_json`, with the stored key as a Bearer token.
- It marks the provider verified only when the status is 200 and the JSON
  object has `ok`, `supports_tools`, and `supports_json` all exactly `true`.
- Every other completed response, including a valid chat-completion envelope,
  becomes `Provider capability check did not succeed.`
- The exchange does not go through `BoundedProviderAdapter`.

That contradicts the recorded contract:

- `project-documents/dev/plan.md` §8.3: use an OpenAI-compatible chat/tool
  exchange; a capability check uses synthetic input and proves tool exchange
  plus usable JSON. Do not assume every compatible endpoint supports structured
  outputs.
- `project-documents/dev/tasks.md` S42.3: implement that minimum exchange in
  the provider adapter S43 extends, and do not create a second permanent
  provider client path.
- `backend/src/x_insight/reasoning/provider.py`: the estimation endpoint is the
  full `chat/completions` POST URL. A successful response is a single
  `choices[0].message` envelope, not an `ok` / `supports_*` echo.

The current tests lock the private dialect. The backend probe accepts the
check only when `purpose` is `capability` and then returns the three flags.
The browser probe returns those flags for any POST. Both can pass without the
adapter ever seeing a chat/tool request.

Relevant code:

- `backend/src/x_insight/reasoning/provider_config.py`: `_capability_outcome`
  and `record_capability_test`.
- `backend/src/x_insight/reasoning/provider.py`: `post_json`,
  `build_outbound_payload`, and `BoundedProviderAdapter`.
- `backend/tests/http/test_api_settings.py`: `_capability_probe`.
- `e2e/provider-settings.spec.ts`: `startProbe`.
- `web/src/features/admin/provider-settings/ProviderSettingsPage.tsx`: displays
  the fixed diagnostic unchanged.

## Reproduction

1. Set `X_INSIGHT_PROVIDER_DATA_KEY` and include the provider hostname in
   `X_INSIGHT_PROVIDER_ALLOWED_HOSTS`. Restart the API so it has both.
2. Sign in as an administrator, open Provider, and save a full provider POST
   URL, a model name, and an API key.
3. Run Test provider.
4. Observe capability `failed` and `Provider capability check did not succeed.`

The same diagnostic is deterministic without a paid provider. Save a URL that
returns HTTP 200 JSON without those three boolean flags, or a double that
returns a normal `choices` envelope. Both fail. A server that returns only
`{"ok": true, "supports_tools": true, "supports_json": true}` is marked
verified even though it never accepted a chat/tool request.

A wrong key or a non-chat URL can produce the same string. Reproducing against
a stub that speaks neither dialect isolates the protocol defect from
credential and path mistakes.

## Expected behavior

Test provider uses synthetic input on the same OpenAI-compatible chat/tool
exchange the estimation adapter uses. Synthetic means no patient projection,
notes, names, or other clinical payload. Verified means the endpoint
demonstrated a tool exchange and a usable JSON response for the saved model.
Schema-constrained JSON and plain JSON remain distinct modes; lack of
`json_schema` support is not by itself a failed provider.

The check stays on the adapter HTTP path. It does not add a second payload
dialect. Saving still records `untested`. Redirects stay unfollowed, and the
host allowlist and special-use address rules stay in force. Diagnostics stay
fixed strings and still omit the key and the raw upstream body, but
authentication, model, connection, redirect, unsupported-tool, and
unsupported-JSON failures do not share one success-shaped miss.

Ordinary CI does not call a paid provider. The deterministic double must speak
the adapter request and response contract.

## Acceptance criteria

- A deterministic OpenAI-compatible double that completes a synthetic tool
  exchange and returns usable JSON is stored as `verified`.
- Returning only `{ok, supports_tools, supports_json}` is not sufficient for
  `verified`.
- Authentication failure, unknown model, connection failure, redirect,
  unsupported tools, and unsupported JSON remain distinct fixed diagnostics.
  None of them include the API key, ciphertext, or raw upstream body.
- The capability request contains no patient projection, note, name, identifier,
  or other clinical field.
- The check does not follow redirects and still applies the saved-settings
  destination allowlist, including the existing special-use address rules.
- The exchange uses the adapter's HTTP POST and payload builder, or one shared
  builder used by that adapter. It does not keep a private
  `purpose: "capability"` dialect.
- Request, response, log, and audit redaction tests still pass.
- Browser coverage shows `verified` and `failed` without exposing secrets.
- A passing check does not by itself switch generation off the environment
  endpoint. Loading saved settings into the worker remains separate work.

## Resolution

`probe_capability` in `backend/src/x_insight/reasoning/provider.py` performs the
check. `chat_completion_payload` is the one chat body builder, used by
`build_outbound_payload` and the probe. The probe posts through `post_json`
with the stored key as a Bearer token. It does not send `purpose` or any
patient field.

The exchange forces one synthetic `ping` tool call, then sends that tool result
and requires the final `choices[0].message.content` to be a JSON object.
`response_format` is `json_object`, not `json_schema`. One rejection of
`response_format` is retried without it; lack of schema support is not a
failure. Returning only `{ok, supports_tools, supports_json}` is not verified.

Fixed diagnostics, never the upstream body or key:

- connection exception: `Provider capability check could not connect.`
- 3xx: `Provider returned a redirect.`
- 401/403: `Provider rejected the API key.`
- 404/422, or a 400 whose `error.param` or `error.code` is `model` or
  `model_not_found`: `Provider model was not found.`
- 400 citing tools, or a 200 with no single empty `ping` call:
  `Provider does not support tools.`
- tool exchange completed but the final content is not a JSON object:
  `Provider does not support JSON responses.`
- other completed failures, including 5xx and an unclassified 400:
  `Provider capability check did not succeed.`

Allowlist, missing-key, and decrypt failures are unchanged and still run before
the POST. A passing check does not load saved settings into the worker.

## Verification

A local double of `probe_capability` passed the chat exchange, the private echo,
401, 404, a tools 400, non-JSON content, a `response_format` retry, and a 302
that was not followed. The captured chat body had no `purpose`, projection,
patient tool name, or API key; the key was only in the Bearer header. The
estimation payload builder still emits `tool_choice: auto` and `json_object`.

`backend/tests/http/test_api_settings.py` passed, 7 tests, against PostgreSQL.
Ruff check and mypy passed on the changed backend files. The browser spec was
updated to the same exchange and was not run.

## Workaround

No longer required for the protocol mismatch. A stub that only echoes
`{ok, supports_tools, supports_json}` is not verified. Generation still depends
on the environment endpoint variables and does not read this result.

## Out of scope

- Loading saved endpoint, model, and credentials into the generation worker.
- Live paid provider calls in ordinary CI.
- Weakening the allowlist, redirect refusal, or diagnostic redaction.
- Treating `198.18.0.0/15` as a blocked special-use address. That separate
  failure used `Resolved address is not an allowlisted IP.` and is already
  changed.
