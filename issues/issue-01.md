# Issue 01 — Provider setup requires an environment allowlist

Date: 2026-10-26  
Status: root cause identified; fix proposed, not implemented  
Affected flow: Administrator → Provider settings → Save, Test provider, generation

## Finding

The application treats the administrator's provider URL as usable only after an
operator has independently approved its **hostname** through
`X_INSIGHT_PROVIDER_ALLOWED_HOSTS`. That variable defaults to an empty list,
which denies every destination. Consequently, a valid URL, model, and API key
entered in the UI cannot complete the first save on a default installation.

This is a mismatch between the current configuration policy and the requested
product behavior. The intended flow is that an authenticated administrator enters
the provider details and the application validates and stores them without an
environment edit or process restart.

Impact: provider onboarding is blocked whenever the selected host is missing
from the process environment. This affects later provider changes too; it is
not a condition tied to the first login. Basic record access remains available.

## Root cause and execution path

1. The form sends `base_url`, `model`, and `api_key` to
   `PUT /api/v1/api-settings`. For an unsaved configuration it correctly omits
   `If-Match` and `secret_action`; the backend defaults the action to `replace`.
2. After administrator and CSRF checks, `put_api_settings()` calls
   `save_settings()`.
3. The **first operation** in `save_settings()` is
   `_require_allowed_destination(base_url)`, before reading a settings revision,
   encrypting the API key, or inserting a row.
4. `_allowed_hosts()` reads `os.environ`, splits the value on commas, and returns
   normalized exact hostnames. An unset or blank variable becomes an empty set.
5. `_require_allowed_destination()` parses the URL and requires its hostname to
   be a member of that set, alongside scheme and userinfo checks. A missing
   membership raises HTTP **422**, code **`PROVIDER_DESTINATION_DENIED`**, message
   **`Provider base URL is not an allowed destination.`**
6. The frontend displays that backend message. Its error reader discards the
   backend `field_errors`, so the more specific operator-allowlist explanation
   is not shown.

For example, `https://api.openai.com/v1/chat/completions` is rejected when the
list is empty or contains only another provider. The current list expects
`api.openai.com`, **not the full URL**.

The backend does not open `.env` during this validation. It reads the running
process environment. The local launcher sources `.env`; production Compose
passes the variable to the API and worker. Editing a file alone does not update
an already-running process.

### Evidence in the repository

| Source | Relevant behavior |
| --- | --- |
| [provider_config.py](../backend/src/x_insight/reasoning/provider_config.py), `_allowed_hosts()` and `_require_allowed_destination()` (lines 283–315) | Exact-host membership is mandatory; missing configuration denies all URLs. Contains the reported message and error code. |
| [provider_config.py](../backend/src/x_insight/reasoning/provider_config.py), `save_settings()` (line 385), `record_capability_test()` (line 526), `open_runtime()` (line 461) | Save, capability testing, and saved-revision generation all enforce the host gate. Testing and runtime also check resolved addresses. |
| [ProviderSettingsPage.tsx](../web/src/features/admin/provider-settings/ProviderSettingsPage.tsx) and [api.ts](../web/src/features/admin/provider-settings/api.ts) | Correct first-save payload; backend errors are surfaced through `messageFor()` and `readJson()`. The browser cannot satisfy the environment gate. |
| [.env.example](../.env.example), lines 44–48 | Both the encryption key and allowed-host list are blank; explicitly documents that an empty list denies all destinations. |
| [compose.prod.yaml](../compose.prod.yaml), API/worker environment blocks, and [Makefile](../Makefile), `dev` | Supplies process configuration at launch; both API and worker depend on it. |
| [worker.py](../backend/src/x_insight/reasoning/worker.py), `resolve_production_provider()` | Saved settings already drive generation using the revision pinned to the job. Missing host approval returns `PROVIDER_DESTINATION_DENIED`. |
| [provider.py](../backend/src/x_insight/reasoning/provider.py), `post_json()`, `probe_capability()`, `_post_once()` | Shared HTTP POST boundary; sends the supplied URL directly and refuses redirects. The transport currently does not enforce destination policy itself. |
| [plan.md §11](../project-documents/dev/plan.md#11-security-operations-capacity-and-trade-offs) and [S42 report](../project-documents/dev/progress/s42.md) | Explicitly specifies operator allowlists, resolved-address checks, and explicit local-host configuration. This gate was intentional, rather than an accidental first-login branch. |

The current policy is established by the plan and implementation. Its exact
original rejected alternatives are unknown; none are claimed here. The user's
requested onboarding behavior changes the requirement that a public provider
must first be named by an operator.

### Why a save-only change is insufficient

`record_capability_test()` repeats `_require_allowed_destination()`, and
`open_runtime()` repeats both the host and resolved-address checks. Removing only
the call in `save_settings()` would allow storage but leave testing and generation
blocked. Updating only the API process environment would also leave the worker
using its own configuration.

Generation integration is already implemented. Older historical statements that
the worker ignores saved settings are superseded by the
[saved-settings generation follow-up](../project-documents/dev/progress/s62.md#follow-up-2026-10-18--saved-provider-settings-drive-generation).

## Verification and limits

The actual `_allowed_hosts()`, `_require_allowed_destination()`,
`save_settings()`, and `_deployment_key()` function bodies were extracted with
Python's AST and executed in isolation with a minimal error wrapper. No database,
DNS lookup, HTTP request, or real API key was involved.

| Isolated check | Observed result |
| --- | --- |
| Allowed-host variable unset | 422 `PROVIDER_DESTINATION_DENIED`, exact reported message |
| Allowed-host variable empty | Same rejection |
| Different hostname configured | Same rejection |
| Full URL entered into the host list | Same rejection |
| Exact hostname configured | Destination validator accepts the URL |
| First save, empty list, database sentinel that fails if accessed | Destination rejection before any database access |
| Deployment encryption key unset | `Provider data key is not configured.` |

This verifies the local control flow, not a complete authenticated HTTP request.
The focused backend test run could not start because the existing virtual
environment's Python executable is inaccessible; retrying outside the sandbox
also failed. No backend-suite pass, browser verification, or live-provider
verification is claimed for this investigation.

The existing tests explain the coverage gap: the API settings fixture supplies
`X_INSIGHT_PROVIDER_ALLOWED_HOSTS=127.0.0.1`, and
`test_save_rejects_destinations_outside_the_operator_allowlist` explicitly expects
an otherwise valid unlisted public URL to fail. The worker tests also expect
removing the allowlist to disable a pinned job. These tests enforce the previous
policy; they do not establish the desired onboarding experience.

## Proposed behavior

For ordinary public providers, entering the URL, model, and API key and clicking
Save must be enough to persist the configuration. Test provider and generation
must use that saved configuration without adding a hostname to `.env` or
restarting services.

The administrator's authenticated save selects the provider. Destination checks
remain the application's responsibility: accept supported public destinations
automatically, reject malformed or unsafe destinations with a specific error,
and keep credentials encrypted and masked.

This does not promise that every upstream model supports the required chat,
tool, and JSON exchange. Saving remains distinct from capability verification;
existing Test provider behavior can remain, with clear saved/untested/verified/
failed feedback.

## Fix plan

### 1. Replace mandatory public-host approval with one destination policy

Introduce a shared destination-policy module used by provider settings and the
HTTP transport. In the default mode, an unset or empty
`X_INSIGHT_PROVIDER_ALLOWED_HOSTS` must allow eligible public **HTTPS** provider
destinations rather than deny all of them.

Validate URL structure and normalize hostnames consistently, including IDNA,
IPv4/IPv6, case, valid ports, whitespace, and parser errors. Reject userinfo,
unsupported schemes, invalid hosts/ports, fragments, and credential-bearing URL
query parameters. Keep API credentials in the encrypted key field and bearer
header. Preserve supported custom endpoint paths.

Before any connection, resolve and check all candidate addresses. Deny loopback,
private networks, link-local/cloud metadata, multicast, unspecified, reserved,
and other nonpublic targets under the default public policy. Mixed safe/unsafe
DNS answers must fail closed. Check IPv4 and IPv6, including mapped addresses.
DNS failure should produce a connection/resolution diagnostic, rather than an
instruction to edit an allowlist.

Compatibility policy:

- Retain a **nonempty** legacy host list as an optional operator restriction for
  existing deployments, with explicit documentation of its restrictive mode.
  Public onboarding in a default installation must not require it.
- For intentionally local/self-hosted providers, provide an explicit admin UI
  option backed by a persisted, narrowly scoped endpoint approval. Require no
  routine environment edit. Normal provider submission must not silently approve
  arbitrary internal networks or metadata services. Keep metadata/link-local and
  unspecified targets denied. Plain HTTP belongs only to explicitly approved
  local endpoints.
- The existing `198.18.0.0/15` fake-IP proxy exception must be assessed explicitly.
  It was added for a real proxy compatibility problem under the old host gate.
  Do not silently treat it as a generally public range when removing that gate;
  preserve necessary compatibility through an explicit trusted-proxy policy.

Update the governing plan to describe this requirement change. This report is a
proposal; it does not silently replace the existing policy in code or docs.

### 2. Enforce the policy at the actual HTTP connection

Route capability probes and generation requests through the same validated
transport at `provider.post_json()` or its replacement. Apply checks to every
request, including tool rounds, format fallbacks, retries, and legacy environment
fallbacks. Retain redirect refusal, TLS certificate verification, request budgets,
and secret-safe diagnostics.

The current `getaddrinfo()` precheck and later independent `httpx.post()` leave a
gap between address validation and connection. Bind the connection to an approved
resolved address, preserving the original hostname for TLS SNI, certificate
verification, and the HTTP Host header; a custom transport/resolver must implement
and test this. A second standalone DNS check does not close that gap. Revalidate
new connections. Explicitly control proxy/environment handling so it cannot
bypass this policy; supported trusted proxies need equivalent enforcement.

Use the same policy result in `save_settings()`, `record_capability_test()`, and
`open_runtime()`. Save should perform structural/policy validation without needing
a successful provider response. The network boundary remains authoritative when
the provider is actually contacted. Map denials to stable domain errors, rather
than allowing a security rejection to become a retryable provider failure.

Keep append-only revisions, revision preconditions, audit redaction, job pinning,
and existing replace/clear cancellation behavior. Never have the worker substitute
the latest URL for the revision already pinned on a job. If local endpoint
approvals require new stored fields, migrate and include them in backup/restore
validation; public-only policy changes do not require changing the settings schema.

### 3. Remove the second first-install blocker: encryption-key setup

The destination error masks another prerequisite. `_seal()` requires
`X_INSIGHT_PROVIDER_DATA_KEY`, and `.env.example` leaves it blank. Once the host
gate is fixed, a fresh deployment without that key would return HTTP 503
`UNAVAILABLE` / `Provider data key is not configured.` instead.

For the complete three-field onboarding experience, provision a random 32-byte
deployment key once during installation/startup, store it in protected persistent
secret storage, and make the same key available to the API and worker. A concrete
deployment approach is a one-time initializer and dedicated secrets volume,
mounted read-only by both services, plus an equivalent protected local key file
for development. Keep the existing environment key as an override for deployments
that already manage one.

Initialization must be atomic across concurrent processes, persist across restarts
and upgrades, and never replace a missing key automatically when encrypted
settings already exist. Preserve existing ciphertext and its key; do not silently
rotate credentials. Keep the key out of source control, responses, logs, audit
details, and ordinary database backup archives. Document separate recovery/escrow
and verify restore behavior. If this provisioning work is deferred, describe the
remaining deployment prerequisite clearly; the full fresh-install expectation
would still be incomplete.

### 4. Make the URL contract and failures clear in the UI

The field says “Base URL,” but `ProviderConfig` and `post_json()` currently expect
the **full chat-completions POST URL** and append no path. This is separate from
the reported rejection, but can become the next onboarding failure.

Support documented OpenAI-compatible forms by normalizing an origin to
`/v1/chat/completions`, a base ending in `/v1` to `/v1/chat/completions`, and an
already complete endpoint without duplicating its suffix. Preserve explicitly
supplied custom full endpoints. Show the effective endpoint/help text so the app
does not guess silently. Test normalization independently of destination approval.

Display backend field errors next to the affected input. Distinguish malformed
URL, blocked address, DNS/connectivity, credential rejection, model rejection,
and capability failure. Successful save must clear the password input and retain
the masked key status. Preserve administrator authorization and CSRF checks.

### 5. Add regressions and update deployment documentation

Update existing policy tests deliberately rather than merely deleting their
assertions. Use deterministic DNS and HTTP fixtures; ordinary CI requires no paid
provider calls.

Required coverage:

- Fresh settings, unset and empty host list: administrator saves an eligible
  public provider, gets a masked revision, reloads it, tests it, and generates
  through that pinned revision. Repeat with another public provider absent from
  every environment host list.
- Invalid URL/scheme/userinfo/port, unsafe literal IP, unsafe or mixed DNS answers,
  mapped IPv6, DNS rebinding, proxy behavior, and redirects: no outbound request
  or bearer sent to a denied destination.
- Both capability and generation use the checked transport on every tool/fallback
  request. A later settings save does not redirect a pinned job.
- Explicit local approval and optional legacy restrictive mode behave as
  documented; fake-IP proxy compatibility does not grant broad internal access.
- Managed encryption key: first startup, concurrent initialization, restart,
  API/worker sharing, existing environment key, missing/unreadable existing key,
  and backup/restore recovery.
- Existing authentication, CSRF, stale revision, key masking/redaction,
  replace/clear, and job-cancellation checks continue to pass.
- Chromium and Firefox exercise the real fresh-settings form against the backend
  with no host allowlist configured. A mocked settings response alone does not
  cover this defect.

Update `.env.example`, Compose/startup wiring, README, installation/deployment
guides, the provider section of the development plan, and the S42 handoff. Remove
instructions that make a public provider host edit mandatory. Keep any remaining
optional restrictive-mode and recovery requirements explicit.

## Acceptance criteria

1. On a fresh installation, the administrator can save a supported public
   provider using only URL, model, and API key, with no host environment edit.
2. Capability testing and saved-revision generation work under that same default
   configuration, without restarting the API or worker.
3. The deployment encryption key is already provisioned and survives restarts;
   this fix does not merely expose a new missing-key error.
4. Unsafe destinations remain blocked at connection time, including address
   changes and every subsequent provider request. Secrets remain encrypted,
   masked, and absent from diagnostics.
5. URL normalization, actionable field errors, optional local approvals, and
   legacy restrictive-mode compatibility are covered by the stated regressions.

## Alternatives assessed for this proposal

- **Manually add each provider hostname to `.env`:** explains the existing
  workaround, but retains the onboarding defect and service-restart dependency.
- **Auto-edit `.env` or `os.environ` when Save is clicked:** leaves configuration
  split between services, is not reliable across restarts/containers, and does
  not establish a destination-safety policy.
- **Remove URL/DNS checks or allow every destination:** makes the UI writable
  while exposing server-side network access and provider credentials to unsafe
  targets.
- **Change only the first-save guard:** leaves capability testing and generation
  blocked by the remaining checks.

These are alternatives evaluated in this investigation, not claims about the
original implementation discussion. The recommended fix is a shared public-
destination policy enforced at the transport, with application-managed settings
and deployment-key provisioning.
