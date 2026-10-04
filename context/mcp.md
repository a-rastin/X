# MCP and estimation protocol

## Private stdio MCP with one scoped tool

**Id:** 7d5e4a86-c7ca-4faa-bb9b-6d9acbac5a01
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc MCP-design.md, 2026-10-03
**Revisit when:** the transport or deployment topology changes

There is one MCP server implementation exposing one read-only tool,
`get_question_patient_inputs`, which returns only the current
question's saved patient projection. The worker is the MCP host/client
and owns a private stdio subprocess per active question with a
short-lived opaque authorization grant supplied through protected
process environment. No public MCP port and no in-process production
shortcut exist.

**Reason:** one real protocol path with a small access surface, and
concrete context isolation — concurrent provider slots get separate
processes and grants, avoiding a mutable global "current patient".

**Rejected alternative:** a public MCP listener, or a second
direct-call implementation that bypasses MCP in production. Rejected
because isolation and lifecycle management are the point of the
design; authorization is application state, not an MCP session
assumption or model argument.

## Sequential question processing

**Id:** b9b1d5d4-c3e3-463c-a6e1-63db76950eaf
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc MCP-design.md, 2026-10-03

Applicable questions are processed sequentially in the bundle's
declared order. Only the current question is eligible; a failed,
retrying or clarifying question pauses all later ones. No implicit
cross-question result chaining: later LLM requests still contain only
their own represented patient variables.

**Reason:** matches FR-32–34 and makes the resume position explicit —
a partial pipeline must never appear as a complete proposal.

**Rejected alternative:** parallel questions within one run. Rejected
because per-run latency is additive and scaling happens across
encounters, not by parallelizing questions inside a run.

## Two retries within one shared budget

**Id:** ec42a047-344b-4268-9486-ac181fb43beb
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc MCP-design.md, 2026-10-03

Two retries follow the initial LLM attempt — three attempts total,
within FR-36's allowed range — sharing one budget across transport and
validation failures. Tool loops, rate limits and worker recovery cannot
create unlimited retries. A manual retry creates a new bounded attempt
batch for the failed stage, retaining previous history.

**Reason:** bounded, predictable failure behavior; separate budgets
would multiply and obscure the retry count.

**Rejected alternative:** separate repair and request retry budgets, or
nested adapter retries. Rejected because they allow unbounded retries
and make the attempt ledger ambiguous.

## Strict CPT validation without silent repair

**Id:** 5d9e0033-0ba7-415f-8012-5e4d8fc5478c
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc MCP-design.md, 2026-10-03

Invalid CPT output is rejected, never repaired: no clamping,
renormalizing, default-filling or rounding of LLM estimates. Every
node's complete CPT is required, including root distributions; ordering
and dimensions must match the declared contract exactly; row sums must
total exactly 100% in integer units. XSD structure, mathematical
validity and clinical validity are separate concerns.

**Reason:** displayed estimates must match the values accepted for
inference; silently distorting them would hide model quality problems
and break replay identity.

**Rejected alternative:** silently normalize, clip or complete
invalid output. Rejected because it corrupts the provenance of what
the model returned; response quality is measured instead of being
papered over.

## Every CPT estimated, including roots

**Id:** 73f67d1e-6bf8-4542-be9f-2bb8297cdf59
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** project design doc MCP-design.md, 2026-10-03

The LLM estimates every CPT for each applicable question, including
root-node distributions. No registered table serves as a fallback for
an omitted or invalid estimate. The registered network remains
immutable; accepted values populate a run-local copy whose hash is
frozen before inference.

**Reason:** the confirmed CPT scope makes the response contract
complete and keeps inference deterministic and replayable from stored
artifacts.

**Rejected alternative:** falling back to registered or default
tables when an estimate is missing. Rejected because imported
defaults must never replace missing estimates; a validated CPT set
without successful execution is not an adjustable baseline.
