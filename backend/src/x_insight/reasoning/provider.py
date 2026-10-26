"""Bounded provider CPT estimation + tool bridging (S43, seam T7; plan.md §§8.2–8.3).

S43 mechanics over S23 (CPT/inference contract), S25 (question-package
contract) and S41 (real private MCP transport):

- §1: outbound HTTP payload holds ONLY the pinned question prompt, fixed
  network/CPT contract, persisted projection, strict CPT response contract
  and the one permitted tool declaration. Tests inspect the captured
  external request via the deterministic test double, never an internal
  mock. Notes/names/ID/phone/secrets never enter the payload by
  construction (projection is the S40 allowlisted projection; prompt and
  contract come from the pinned package; the grant never enters the body).
  Templates never pass through the provider in either direction.
- §2: initial patient read AND every provider-requested tool call cross the
  SAME real MCP stdio transport (``mcp_host.read_projection_blocking`` with
  the per-attempt grant via the protected environment). Only
  ``get_question_patient_inputs`` with ``{}`` is allowed; disallowed
  name/args and spoofed/forged context (hash/grant mismatch) are rejected
  with bounded errors. No direct-call production bypass exists here and no
  extra MCP tool is added.
- §3: ``schema`` and ``json`` capability modes send their verified
  ``response_format`` but share ONE strict validator. Extra prose,
  ambiguous/multiple outputs, malformed/truncated/oversized payloads and
  S23 policy violations (out-of-range/nonfinite/excess-precision/inexact
  totals, missing tables/rows) are rejected. Numeric/table checks reuse
  ``models.inference.validate_cpts`` via a contract-derived document — no
  second percentage validator lives here.
- §4: explicit error/budget mapping (auth/model/capability = non-retryable
  config error; timeout/rate-limit/transient = retryable). Max ten tool
  calls per attempt, request/response byte budgets, 60s per HTTP request.
  One attempt performs a single HTTP exchange per tool step with no hidden
  nested retries; the queue-level 3-attempt budget lives in S47 (deferred).

Deferred (NOT implemented here):

- S42 persistence and encryption live in
  ``reasoning/provider_config.py``. The capability exchange is
  ``probe_capability`` here, on the same chat payload and ``post_json``
  path as estimation. This module still accepts an injected
  :class:`ProviderConfig` and does not load saved rows. The generation
  worker loads the pinned saved revision, with an environment fallback for
  jobs without a settings revision. ``post_json`` applies the shared destination
  policy at every DNS-bound HTTP connection and does not follow redirects.
- S45 CPT validation/inference/rendering/OriginalBaseline, S46 ordered
  workflows and proposal assembly, S47 retry budgets/backoff/fencing
  re-exercise and S48b local-calculation proof are deferred. Success here
  returns a candidate CPT payload for S45 to admit/execute; it never
  creates an OriginalBaseline or renders a template section.

BFRI 4 (Moderate, proceed with tests + monitoring): Fit 5 (plan §8.3
adapter shape, reuses S23/S41, no layer skipping) + Testability 5 (real PG
+ real stdio + deterministic endpoint) − Complexity 2 (single adapter,
shared validator/host) − Data Risk 2 (read-only projection, no DB writes)
− Operational Risk 2 (test double only, no live billing/model) = 4.

Backward compat: :class:`ControlledStubAdapter` is unchanged for S44 queue
tests; :class:`BoundedProviderAdapter` sits alongside it behind the same
:class:`ProviderAdapter` protocol. :class:`ProviderRequest` gains optional
pinned S43 fields with defaults so existing ``run_once()`` call sites keep
working; the bounded adapter requires them.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from x_insight.mcp_server import TOOL_INPUT_SCHEMA, TOOL_NAME

MAX_TOOL_CALLS = 10
PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_BYTES = 1_048_576  # ponytail: reuse 1 MiB edge cap; S58 measures real CPT sizes.
RESPONSE_SCHEMA_VERSION = "cpt-response-v1"


@dataclass(frozen=True)
class ProviderRequest:
    """Estimation input: persisted projection + pinned contract.

    The first six fields are the S44 minimal shape (worker still builds
    them). S43 callers additionally pin ``prompt_text`` (question prompt),
    ``network_hash``/``network_version`` and ``cpt_contract`` (fixed
    ``{nodes: [{node_id, parent_ids, states}]}`` in declared order); the
    bounded adapter rejects requests without them as capability errors.
    No template, note, name, ID, phone or secret field exists here.
    """

    question_key: str
    projection: dict[str, Any]
    projection_hash: str
    prompt_version: str
    question_run_id: str
    batch_id: str
    prompt_text: str = ""
    network_hash: str = ""
    network_version: str = "v1"
    cpt_contract: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResult:
    """Adapter outcome: success carries a candidate payload, failure an error."""

    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False


class ProviderAdapter(Protocol):
    """External estimation interface (one call per attempt, no DB access)."""

    def estimate(self, request: ProviderRequest) -> ProviderResult: ...


class ControlledStubAdapter:
    """Deterministic test stub: fixed success or injected failure.

    ``mode='succeed'`` returns a minimal candidate artifact referencing the
    projection hash (S45 replaces this with full CPT validation). Other
    modes simulate bounded failures for recovery slices without network.
    """

    def __init__(self, mode: str = "succeed") -> None:
        if mode not in ("succeed", "fail_retryable", "fail_fatal"):
            raise ValueError("mode must be succeed|fail_retryable|fail_fatal")
        self.mode = mode
        self.calls: list[ProviderRequest] = []

    def estimate(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        if self.mode == "succeed":
            return ProviderResult(
                ok=True,
                payload={
                    "schema_version": "stub-estimation-v1",
                    "question_key": request.question_key,
                    "projection_hash": request.projection_hash,
                    "note": "controlled stub; full CPT estimation lands in S43/S45",
                },
            )
        if self.mode == "fail_retryable":
            return ProviderResult(ok=False, error_code="PROVIDER_TIMEOUT", retryable=True)
        return ProviderResult(ok=False, error_code="PROVIDER_AUTH", retryable=False)


@dataclass(frozen=True)
class ProviderConfig:
    """Injected endpoint config. S42 stores settings in ``provider_config``.

    This adapter does not load them. ``endpoint_url`` is the full POST URL
    of an OpenAI-compatible ``chat/completions`` endpoint (tests point it
    at the deterministic double). ``capability`` is the verified mode:
    ``"schema"`` sends ``response_format json_schema``, ``"json"`` sends
    ``json_object``; both share the strict validator. No secret material
    lives on :class:`ProviderConfig`.
    """

    endpoint_url: str
    model: str
    capability: str = "schema"
    timeout_seconds: float = PROVIDER_TIMEOUT_SECONDS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_request_bytes: int = MAX_PROVIDER_BYTES
    max_response_bytes: int = MAX_PROVIDER_BYTES
    local_endpoint_approved: bool = False
    trusted_proxy_approved: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("endpoint_url must be an http(s) URL")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be non-empty")
        if self.capability not in ("schema", "json"):
            raise ValueError("capability must be schema|json")
        timeout = float(self.timeout_seconds)
        if not 0 < timeout <= PROVIDER_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 1 <= int(self.max_tool_calls) <= MAX_TOOL_CALLS:
            raise ValueError("max_tool_calls must be within [1, 10]")
        if int(self.max_request_bytes) <= 0 or int(self.max_response_bytes) <= 0:
            raise ValueError("byte budgets must be positive")


def _fail(code: str, retryable: bool) -> ProviderResult:
    return ProviderResult(ok=False, error_code=code, retryable=retryable)


def _contract_nodes(cpt_contract: Any) -> list[dict[str, Any]] | None:
    if not isinstance(cpt_contract, dict):
        return None
    nodes = cpt_contract.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in nodes:
        if not isinstance(entry, dict):
            return None
        node_id = entry.get("node_id")
        parent_ids = entry.get("parent_ids")
        states = entry.get("states")
        if (
            not isinstance(node_id, str)
            or not node_id
            or not isinstance(parent_ids, list)
            or not all(isinstance(p, str) for p in parent_ids)
            or not isinstance(states, list)
            or not states
            or not all(isinstance(s, str) and s for s in states)
        ):
            return None
        cleaned.append({"node_id": node_id, "parent_ids": list(parent_ids), "states": list(states)})
    return cleaned


def _response_schema(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["network_hash", "tables"],
        "additionalProperties": False,
        "properties": {
            "network_hash": {"type": "string"},
            "tables": {
                "type": "array",
                "minItems": len(nodes),
                "maxItems": len(nodes),
                "items": {
                    "type": "object",
                    "required": ["node_id", "parent_ids", "states", "rows"],
                    "additionalProperties": False,
                    "properties": {
                        "node_id": {"type": "string"},
                        "parent_ids": {"type": "array", "items": {"type": "string"}},
                        "states": {"type": "array", "items": {"type": "string"}},
                        "rows": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["parent_states", "percentages"],
                                "additionalProperties": False,
                                "properties": {
                                    "parent_states": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "percentages": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def chat_completion_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: Any,
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    """OpenAI chat/completions body shared by estimation and the capability probe."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def build_outbound_payload(config: ProviderConfig, request: ProviderRequest) -> dict[str, Any]:
    """Build the pinned outbound payload (no template/notes/PII/secrets).

    Contains ONLY: question prompt, fixed network/CPT contract, persisted
    projection (+ hashes/versions for binding), strict CPT response contract
    and the one permitted tool declaration. The grant never enters the body.
    """
    nodes = _contract_nodes(request.cpt_contract)
    if nodes is None:
        raise ValueError("cpt_contract must hold nodes [{node_id, parent_ids, states}]")
    response_contract: dict[str, Any] = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "network_hash": request.network_hash,
        "tables_spec": nodes,
        "policy": "decimal-strings-max-6-places-exact-100-percent",
    }
    user_content = json.dumps(
        {
            "question_key": request.question_key,
            "prompt_version": request.prompt_version,
            "network_hash": request.network_hash,
            "network_version": request.network_version,
            "network_contract": {"nodes": nodes},
            "projection": request.projection,
            "projection_hash": request.projection_hash,
            "response_contract": response_contract,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if config.capability == "schema":
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": "cpt_tables",
                "strict": True,
                "schema": _response_schema(nodes),
            },
        }
    else:
        response_format = {"type": "json_object"}
    return chat_completion_payload(
        model=config.model,
        messages=[
            {"role": "system", "content": request.prompt_text},
            {"role": "user", "content": user_content},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": (
                        "Return the bound stored question projection. Takes no arguments."
                    ),
                    "parameters": dict(TOOL_INPUT_SCHEMA),
                },
            }
        ],
        tool_choice="auto",
        response_format=response_format,
    )


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _contract_document(network_hash: str, nodes: list[dict[str, Any]]) -> Any:
    """Synthesize a minimal S23 document from the fixed contract (no duplicate validator)."""
    from x_insight.models.validation import (
        ValidatedXmlbif,
        XmlbifDefinition,
        XmlbifNetwork,
        XmlbifVariable,
        XsdReport,
    )

    variables = tuple(
        XmlbifVariable(name=n["node_id"], kind="nature", states=tuple(n["states"]), properties=())
        for n in nodes
    )
    definitions = tuple(
        XmlbifDefinition(
            for_node=n["node_id"], parents=tuple(n["parent_ids"]), table=(), properties=()
        )
        for n in nodes
    )
    network = XmlbifNetwork(
        name="contract", properties=(), variables=variables, definitions=definitions
    )
    return ValidatedXmlbif(
        source_sha256=network_hash,
        source_bytes=b"",
        xsd=XsdReport(valid=True, errors=()),
        networks=(network,),
        activatable_v1=True,
        nonactivatable_reasons=(),
    )


def validate_strict_response(
    content: Any, *, network_hash: str, nodes: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    """Shared strict validator for both capability modes.

    Returns (payload, None) on success or (None, reason) on rejection:
    extra prose, non-object/multiple outputs, malformed/truncated JSON and
    any S23 contract violation (missing tables/rows, out-of-range,
    nonfinite, excess precision, inexact totals). Numeric checks delegate to
    ``inference.validate_cpts``; nothing here re-parses percentages.
    """
    from x_insight.models.inference import validate_cpts

    if not isinstance(content, str) or not content.strip():
        return None, "empty_response"
    if len(content.encode("utf-8")) > MAX_PROVIDER_BYTES:
        return None, "oversized_response"
    try:
        parsed = json.loads(content)
    except Exception:
        return None, "malformed_response"
    if not isinstance(parsed, dict):
        return None, "ambiguous_response"
    document = _contract_document(network_hash, nodes)
    try:
        report = validate_cpts(document, parsed)
    except Exception:
        return None, "malformed_response"
    if not report.valid:
        first = report.errors[0] if report.errors else None
        return None, str(first.code) if first is not None else "invalid_cpts"
    return parsed, None


def _parse_tool_arguments(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped in ("", "{}"):
            return {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None
    return None


class ApprovedEndpoint(str):
    """Carry immutable revision approvals alongside the normalized endpoint."""

    local_endpoint_approved: bool
    trusted_proxy_approved: bool

    def __new__(cls, url: str, local: bool = False, proxy: bool = False):
        value = super().__new__(cls, url)
        value.local_endpoint_approved = local
        value.trusted_proxy_approved = proxy
        return value


def approved_endpoint(url: str, local: bool = False, proxy: bool = False) -> str:
    return ApprovedEndpoint(url, local, proxy)


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> Any:
    """One DNS-bound HTTP POST. Redirects and environment proxies are unused."""
    from x_insight.reasoning.provider_destination import checked_post, destination

    local = bool(getattr(url, "local_endpoint_approved", False))
    proxy = bool(getattr(url, "trusted_proxy_approved", False))
    return checked_post(
        destination(url, local=local, trusted_proxy=proxy), payload, headers, timeout
    )


CAPABILITY_SUPPORTS = "Provider supports tools and JSON."
CAPABILITY_CONNECT = "Provider capability check could not connect."
CAPABILITY_REDIRECT = "Provider returned a redirect."
CAPABILITY_AUTH = "Provider rejected the API key."
CAPABILITY_MODEL = "Provider model was not found."
CAPABILITY_TOOLS = "Provider does not support tools."
CAPABILITY_JSON = "Provider does not support JSON responses."
CAPABILITY_INCOMPLETE = "Provider capability check did not succeed."

_CAPABILITY_TOOL_NAME = "ping"
_RETRY_FORMAT = "retry-format"
_MODEL_TOKENS = frozenset({"model", "model_not_found"})
_TOOL_TOKENS = frozenset({"tools", "tool_choice", "functions", "function"})
_FORMAT_TOKENS = frozenset({"response_format"})
_CAPABILITY_MESSAGES: tuple[dict[str, str], ...] = (
    {"role": "system", "content": "Return one JSON object and no prose."},
    {"role": "user", "content": 'Call ping, then return {"ready":true}.'},
)
_CAPABILITY_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _CAPABILITY_TOOL_NAME,
        "description": "Synthetic capability probe. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def _error_tokens(body: Any) -> frozenset[str]:
    """Structured error fields only. Never the message, which may echo the key."""
    if not isinstance(body, dict):
        return frozenset()
    error = body.get("error")
    if not isinstance(error, dict):
        return frozenset()
    found: set[str] = set()
    for key in ("param", "code", "type"):
        value = error.get(key)
        if isinstance(value, str) and value.isascii() and 0 < len(value) <= 64:
            found.add(value)
    return frozenset(found)


def _assistant_text(message: dict[str, Any]) -> Any:
    """Reply text. Some reasoning models leave content empty and put the JSON in reasoning_content.

    Non-empty content wins, so thinking prose is never used when a visible reply exists.
    """
    content = message.get("content")
    if isinstance(content, dict) or (isinstance(content, str) and content.strip()):
        return content
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return content


def _json_object_content(content: Any) -> bool:
    if isinstance(content, dict):
        return True
    if not isinstance(content, str) or not content.strip():
        return False
    if len(content.encode("utf-8")) > MAX_PROVIDER_BYTES:
        return False
    try:
        parsed = json.loads(content)
    except Exception:
        return False
    return isinstance(parsed, dict)


def _ping_call_id(message: dict[str, Any]) -> str | None:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return None
    call = calls[0]
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id or len(call_id) > 128:
        return None
    if not isinstance(function, dict) or function.get("name") != _CAPABILITY_TOOL_NAME:
        return None
    args = _parse_tool_arguments(function.get("arguments"))
    if args != {}:
        return None
    return call_id


def _capability_payload(
    model: str,
    messages: list[dict[str, Any]],
    tool_choice: Any,
    use_format: bool,
) -> dict[str, Any]:
    return chat_completion_payload(
        model=model,
        messages=messages,
        tools=[_CAPABILITY_TOOL],
        tool_choice=tool_choice,
        response_format={"type": "json_object"} if use_format else None,
    )


def _read_body(response: Any) -> Any:
    raw = getattr(response, "content", b"")
    if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_PROVIDER_BYTES:
        return None
    try:
        return response.json()
    except Exception:
        return None


def _classify_http(response: Any, *, allow_format_retry: bool) -> tuple[str, Any]:
    """Return ``("ok", body)``, ``("retry-format", None)``, or a fixed diagnostic."""
    status = int(getattr(response, "status_code", 0) or 0)
    if 300 <= status < 400:
        return CAPABILITY_REDIRECT, None
    if status in (401, 403):
        return CAPABILITY_AUTH, None
    if status in (404, 422):
        return CAPABILITY_MODEL, None
    body = _read_body(response)
    if status == 400:
        tokens = _error_tokens(body)
        if tokens & _MODEL_TOKENS:
            return CAPABILITY_MODEL, None
        if tokens & _TOOL_TOKENS:
            return CAPABILITY_TOOLS, None
        if allow_format_retry and tokens & _FORMAT_TOKENS:
            return _RETRY_FORMAT, None
        if tokens & _FORMAT_TOKENS:
            return CAPABILITY_JSON, None
        return CAPABILITY_INCOMPLETE, None
    if status != 200 or body is None:
        return CAPABILITY_INCOMPLETE, None
    return "ok", body


def probe_capability(url: str, model: str, api_key: str) -> tuple[bool, str]:
    """Synthetic chat/tool exchange. No patient payload. Fixed diagnostics only.

    One forced ``ping`` call, then one JSON object. ``json_schema`` is not
    required. A ``response_format`` rejection is retried once without it.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    use_format = True

    def post(payload: dict[str, Any]) -> tuple[str, Any]:
        try:
            response = post_json(url, payload, headers, PROVIDER_TIMEOUT_SECONDS)
        except Exception as exc:
            from x_insight.reasoning.provider_destination import DestinationDenied, ResolutionFailed

            if isinstance(exc, DestinationDenied):
                return "Provider destination is blocked by destination policy.", None
            if isinstance(exc, ResolutionFailed):
                return "Provider hostname could not be resolved.", None
            return CAPABILITY_CONNECT, None
        retry = use_format and "response_format" in payload
        return _classify_http(response, allow_format_retry=retry)

    def post_with_format_fallback(
        messages: list[dict[str, Any]], tool_choice: Any
    ) -> tuple[str, Any]:
        nonlocal use_format
        kind, body = post(_capability_payload(model, messages, tool_choice, use_format))
        if kind != _RETRY_FORMAT:
            return kind, body
        use_format = False
        return post(_capability_payload(model, messages, tool_choice, False))

    messages: list[dict[str, Any]] = [dict(item) for item in _CAPABILITY_MESSAGES]
    kind, body = post_with_format_fallback(
        messages, {"type": "function", "function": {"name": _CAPABILITY_TOOL_NAME}}
    )
    if kind != "ok":
        return False, kind
    message, _reason = BoundedProviderAdapter._envelope_message(body)
    call_id = _ping_call_id(message) if isinstance(message, dict) else None
    if call_id is None:
        return False, CAPABILITY_TOOLS
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": _CAPABILITY_TOOL_NAME, "arguments": "{}"},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": call_id, "content": "{}"})
    kind, body = post_with_format_fallback(messages, "none")
    if kind != "ok":
        return False, kind
    message, _reason = BoundedProviderAdapter._envelope_message(body)
    if not isinstance(message, dict) or not _json_object_content(_assistant_text(message)):
        return False, CAPABILITY_JSON
    return True, CAPABILITY_SUPPORTS


class BoundedProviderAdapter:
    """Real bounded estimation adapter (one attempt, no hidden retries).

    ``grant_token`` is the per-attempt opaque grant from
    ``queue.claim_next_job`` (falls back to ``X_INSIGHT_MCP_GRANT`` when
    omitted so worker wiring can stay env-only). Every patient read — the
    initial read and each provider-requested tool call — crosses the real
    MCP stdio transport for the SAME persisted projection; the grant never
    enters the provider body.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        grant_token: str | None = None,
        database_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._config = config
        self._grant_token = grant_token
        self._database_url = database_url
        # Not on ProviderConfig: that object is logged and copied. Never log this.
        self._api_key = api_key if isinstance(api_key, str) and api_key else None
        self.tool_calls_made = 0

    @property
    def config(self) -> ProviderConfig:
        """Explicit accessor for the injected provider config (no _ access)."""
        return self._config

    @property
    def database_url(self) -> str | None:
        """Explicit accessor for the MCP read URL override (or None)."""
        return self._database_url

    @property
    def api_key(self) -> str | None:
        """Bearer token for this attempt. Callers must not log it."""
        return self._api_key

    def _grant(self) -> str:
        import os

        from x_insight.mcp_server import GRANT_ENV_VAR

        if isinstance(self._grant_token, str) and self._grant_token:
            return self._grant_token
        env_grant = os.environ.get(GRANT_ENV_VAR, "")
        return env_grant

    def _mcp_read(self, expected_hash: str) -> tuple[dict[str, Any] | None, ProviderResult | None]:
        from x_insight.reasoning import mcp_host

        grant = self._grant()
        if not grant:
            return None, _fail("PROVIDER_AUTH", False)
        try:
            output = mcp_host.read_projection_blocking(
                grant,
                database_url=self._database_url,
                expected_projection_hash=expected_hash,
            )
        except Exception as exc:
            name = type(exc).__name__
            if "Auth" in name:
                return None, _fail("PROVIDER_AUTH", False)
            if "Context" in name:
                return None, _fail("PROVIDER_CONTEXT", False)
            return None, _fail("PROVIDER_TRANSIENT", True)
        if not isinstance(output, dict):
            return None, _fail("PROVIDER_TRANSIENT", True)
        return output, None

    def _post_once(
        self, payload: dict[str, Any], *, allow_format_retry: bool = True
    ) -> tuple[Any, ProviderResult | None]:
        if len(_payload_bytes(payload)) > int(self._config.max_request_bytes):
            return None, _fail("PROVIDER_BUDGET_EXCEEDED", False)
        try:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            response = post_json(
                approved_endpoint(
                    self._config.endpoint_url,
                    self._config.local_endpoint_approved,
                    self._config.trusted_proxy_approved,
                ),
                payload,
                headers,
                float(self._config.timeout_seconds),
            )
        except Exception as exc:
            from x_insight.reasoning.provider_destination import DestinationDenied, ResolutionFailed

            if isinstance(exc, DestinationDenied):
                return None, _fail("PROVIDER_DESTINATION_DENIED", False)
            if isinstance(exc, ResolutionFailed):
                return None, _fail("PROVIDER_CONNECTION", True)
            name = type(exc).__name__.lower()
            text = str(exc).lower()
            if "timeout" in name or "timeout" in text or "timed out" in text:
                return None, _fail("PROVIDER_TIMEOUT", True)
            return None, _fail("PROVIDER_TRANSIENT", True)
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 200:
            try:
                body = response.json()
            except Exception:
                return None, _fail("PROVIDER_INVALID_RESPONSE", True)
            raw = getattr(response, "content", b"")
            size = len(bytes(raw)) if isinstance(raw, (bytes, bytearray)) else 0
            if size > int(self._config.max_response_bytes):
                return None, _fail("PROVIDER_BUDGET_EXCEEDED", False)
            return body, None
        if 300 <= status < 400:
            return None, _fail("PROVIDER_CAPABILITY", False)
        if status in (401, 403):
            return None, _fail("PROVIDER_AUTH", False)
        if status in (404, 422):
            return None, _fail("PROVIDER_MODEL", False)
        # Same one-shot format fallback as the capability probe. Not a retry
        # budget: NVIDIA-style endpoints reject json_schema and still return
        # JSON the strict validator accepts.
        if (
            allow_format_retry
            and status == 400
            and "response_format" in payload
            and _error_tokens(_read_body(response)) & _FORMAT_TOKENS
        ):
            stripped = {key: value for key, value in payload.items() if key != "response_format"}
            return self._post_once(stripped, allow_format_retry=False)
        if status == 400:
            return None, _fail("PROVIDER_CAPABILITY", False)
        if status == 429:
            return None, _fail("PROVIDER_RATE_LIMIT", True)
        if 500 <= status <= 599:
            return None, _fail("PROVIDER_TRANSIENT", True)
        if 400 <= status < 500:
            return None, _fail("PROVIDER_CAPABILITY", False)
        return None, _fail("PROVIDER_TRANSIENT", True)

    @staticmethod
    def _envelope_message(body: Any) -> tuple[dict[str, Any] | None, str | None]:
        if not isinstance(body, dict):
            return None, "malformed_response"
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            return None, "ambiguous_response"
        entry = choices[0]
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict):
            return None, "malformed_response"
        return message, None

    def estimate(self, request: ProviderRequest) -> ProviderResult:
        config = self._config
        if (
            not isinstance(request.prompt_text, str)
            or not request.prompt_text.strip()
            or not isinstance(request.network_hash, str)
            or not request.network_hash.strip()
            or not isinstance(request.projection, dict)
            or not isinstance(request.projection_hash, str)
            or not request.projection_hash.strip()
        ):
            return _fail("PROVIDER_CAPABILITY", False)
        nodes = _contract_nodes(request.cpt_contract)
        if nodes is None:
            return _fail("PROVIDER_CAPABILITY", False)

        # §2 initial patient read over the real MCP transport (same projection).
        initial, failure = self._mcp_read(request.projection_hash)
        if failure is not None:
            return failure
        assert initial is not None
        if str(initial.get("projection_hash", "")) != request.projection_hash:
            return _fail("PROVIDER_CONTEXT", False)

        try:
            payload = build_outbound_payload(config, request)
        except ValueError:
            return _fail("PROVIDER_CAPABILITY", False)

        messages = list(payload["messages"])
        self.tool_calls_made = 0
        # Single HTTP exchange per tool step + tool-call loop cap only (no retries).
        while True:
            step_payload = dict(payload)
            step_payload["messages"] = messages
            body, failure = self._post_once(step_payload)
            if failure is not None:
                return failure
            assert body is not None
            message, reason = self._envelope_message(body)
            if message is None:
                return _fail("PROVIDER_INVALID_RESPONSE", True)
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                content = _assistant_text(message)
                if len(str(content or "").encode("utf-8")) > int(config.max_response_bytes):
                    return _fail("PROVIDER_BUDGET_EXCEEDED", False)
                validated, _ = validate_strict_response(
                    content, network_hash=request.network_hash, nodes=nodes
                )
                if validated is None:
                    return _fail("PROVIDER_INVALID_RESPONSE", True)
                return ProviderResult(
                    ok=True,
                    payload={
                        "schema_version": "bounded-cpt-v1",
                        "question_key": request.question_key,
                        "projection_hash": request.projection_hash,
                        "network_hash": request.network_hash,
                        "network_version": request.network_version,
                        "prompt_version": request.prompt_version,
                        "tables": validated["tables"],
                        "tool_calls_made": self.tool_calls_made,
                        "capability": config.capability,
                    },
                )
            if not isinstance(tool_calls, list) or not tool_calls:
                return _fail("PROVIDER_INVALID_RESPONSE", True)
            if self.tool_calls_made + len(tool_calls) > int(config.max_tool_calls):
                return _fail("PROVIDER_BUDGET_EXCEEDED", False)
            assistant_content = message.get("content")
            tool_results: list[dict[str, Any]] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    return _fail("PROVIDER_TOOL_REJECTED", False)
                call_id = call.get("id")
                function = call.get("function")
                if not isinstance(call_id, str) or not call_id:
                    return _fail("PROVIDER_INVALID_RESPONSE", True)
                if not isinstance(function, dict):
                    return _fail("PROVIDER_TOOL_REJECTED", False)
                name = function.get("name")
                args = _parse_tool_arguments(function.get("arguments"))
                if name != TOOL_NAME or args is None or dict(args) != {}:
                    return _fail("PROVIDER_TOOL_REJECTED", False)
                # Same real MCP transport for every provider-requested read.
                output, failure = self._mcp_read(request.projection_hash)
                if failure is not None:
                    return failure
                assert output is not None
                if str(output.get("projection_hash", "")) != request.projection_hash:
                    return _fail("PROVIDER_CONTEXT", False)
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            output,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    }
                )
                self.tool_calls_made += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": tool_calls,
                }
            )
            messages.extend(tool_results)


class DeterministicProviderEndpoint:
    """Test-only deterministic HTTP double (seam T7). Never used in production.

    Spins a loopback ``ThreadingHTTPServer`` that captures decoded JSON
    request bodies (``captured``) and replays a scripted OpenAI-compatible
    envelope per POST. No network beyond loopback, no live model, no paid
    calls. Script entries (consumed in order; the last repeats)::

        {"type": "final", "cpt": {...}}            # 200 + assistant content
        {"type": "tool", "calls": [{"id":..,"name":..,"arguments":..}]}
        {"type": "status", "status": 401, "body": {...}}
        {"type": "delay", "seconds": 2.0, "then": {"type": "final", ...}}
        {"type": "hold", "release": <threading.Event>, "timeout": 60.0,
         "then": {...}}  # block until the test releases (bounded wait),
                         # proving work ordered strictly before release
    ``on_exchange`` (optional) is called with each decoded request body
    before the scripted step is served — the public hook for proving a
    request is in flight (claim complete) without touching privates.
    """

    def __init__(
        self,
        script: list[dict[str, Any]] | None = None,
        on_exchange: Any | None = None,
    ) -> None:
        self.script: list[dict[str, Any]] = list(script or [])
        self.captured: list[Any] = []
        self.captured_auth: list[str] = []
        self.on_exchange = on_exchange
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def _next(self) -> dict[str, Any]:
        if not self.script:
            return {"type": "final", "cpt": {"network_hash": "x", "tables": []}}
        if len(self.script) == 1:
            return self.script[0]
        return self.script.pop(0)

    def _envelope(self, step: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        kind = str(step.get("type", "final"))
        if kind == "status":
            return int(step.get("status", 500)), dict(step.get("body", {}))
        if kind == "tool":
            calls: list[dict[str, Any]] = []
            for entry in list(step.get("calls", [])):
                assert isinstance(entry, dict)
                arguments = entry.get("arguments", {})
                arg_text = (
                    arguments
                    if isinstance(arguments, str)
                    else json.dumps(arguments, separators=(",", ":"))
                )
                calls.append(
                    {
                        "id": str(entry.get("id", "call_1")),
                        "type": "function",
                        "function": {
                            "name": str(entry.get("name", TOOL_NAME)),
                            "arguments": arg_text,
                        },
                    }
                )
            return 200, {
                "choices": [
                    {"message": {"role": "assistant", "content": None, "tool_calls": calls}}
                ]
            }
        if kind == "delay":
            import time as _time

            _time.sleep(float(step.get("seconds", 0.0)))
            then = step.get("then")
            if isinstance(then, dict):
                return self._envelope(then)
            return 200, {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}
        if kind == "hold":
            release = step.get("release")
            timeout = float(step.get("timeout", 60.0))
            wait = getattr(release, "wait", None)
            if callable(wait):
                wait(timeout=timeout)
            then = step.get("then")
            if isinstance(then, dict):
                return self._envelope(then)
            return 200, {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}
        cpt = step.get("cpt", {"network_hash": "x", "tables": []})
        return 200, {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(cpt, separators=(",", ":")),
                    }
                }
            ],
        }

    def start(self) -> str:
        endpoint: DeterministicProviderEndpoint = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    decoded = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    decoded = {"_raw_unparseable": raw.decode("utf-8", "replace")[:2000]}
                endpoint.captured.append(decoded)
                endpoint.captured_auth.append(self.headers.get("Authorization", ""))
                hook = endpoint.on_exchange
                if hook is not None:
                    hook(decoded)
                status, body = endpoint._envelope(endpoint._next())
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except BrokenPipeError:
                    pass

            def log_message(self, *args: Any) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        self._server = server
        self._thread = thread
        self.url = f"http://127.0.0.1:{port}/chat/completions"
        return self.url

    def stop(self) -> None:
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        finally:
            self._server = None
            self._thread = None
