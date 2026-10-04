# Architecture and governing constraints

## Four logical subsystems in a modular monolith

**Id:** 28460024-2dce-414d-9ae4-03ac73f2b0a4
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** system-architecture.md, 2026-09-28

The system keeps four logical subsystems — S1 Case and Encounter
Management, S2 Research Knowledge Management, S3 Decision Support,
S4 Administration and Governance — implemented as a modular monolith
with a separate background worker, an internal MCP process and one
authoritative PostgreSQL database. The four subsystems are logical
ownership groups, not four applications.

**Reason:** the split follows independently useful responsibilities
(maintaining cases, maintaining knowledge, producing analysis,
governing operation) with one authoritative owner per mutable
category. Explicit ownership preserves the distinction between system
estimates, physician-adjusted calculations and signed clinical
decisions. The cost is coordination: input freshness, CPT revision,
result identity and acceptance must agree at sign-off.

**Rejected alternative:** microservices per module. Rejected —
microservices are not required at this scale, and any future
separation must preserve private draft access, single-draft
uniqueness, local-only recalculation, immutable provenance and atomic
signing eligibility.

## Patient variables inform estimation only

**Id:** dbca3245-90f9-4004-a3f9-5271a4fbb099
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** system-design.md §1.1, 2026-09-28

Patient-variable values are supplied to the LLM only to estimate CPTs.
They are never also submitted as observed, soft, likelihood or virtual
evidence to Bayesian execution. Record workflows, applicability rules
and DDI checks still use their relevant clinical inputs; the
prohibition concerns feeding them into inference as additional
evidence.

**Reason:** owner-confirmed product rule. Patient-specific information
has already entered through the LLM-estimated CPTs; the engine
executes with an empty evidence set so results stay reproducible from
stored CPTs and pinned configuration.

**Rejected alternative:** conditioning inference on observed patient
values (hard/soft/virtual evidence). Rejected by the confirmed product
contract; content review cannot enable it either.

## One open draft per patient, author-only

**Id:** ac0fbbd0-1319-43a3-8f60-dca0f0d0fc6e
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** system-design.md §1.1, 2026-09-28

A patient has at most one open encounter draft across all physicians
and encounter types, including drafts awaiting generation or
probability review. Only the draft's author can view its clinical
content, edit it, adjust/reset probabilities, retry work, accept
results or sign it. Enforced with a partial unique database
constraint on lifecycle `draft` plus a patient-row lock during
creation; another physician receives a generic open-draft conflict
without contents.

**Reason:** owner-confirmed collaboration policy. Shared access to
demographics and signed records does not confer draft access; draft
restrictions apply transitively to assessments, notes, history,
medications, runs, CPTs, results, proposal text, downloads and
notifications.

**Rejected alternative:** separate drafts per physician or encounter
type. Rejected — simultaneous drafts for one patient are prohibited,
including multiple drafts by the same author.

## Failed generation cannot be bypassed by manual signing

**Id:** 27febc8c-829c-468e-a200-9b8c8cccaa11
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** system-design.md §1.1, 2026-09-28

A physician cannot bypass failed initial proposal generation by
signing a manual plan. Every applicable question must have a
successful, current original execution, and its final probabilities
and results must be accepted before signing. A question without a
successful original execution exposes no adjustable result; a partial
pipeline cannot authorize signing.

**Reason:** owner-confirmed rule protecting the integrity of the
signed record — the signature must rest on executed, accepted
artifacts, not on a manually assembled plan.

**Rejected alternative:** manual-plan signing after failed
generation. Rejected; generation failure has no manual-plan bypass.

## Any physician may append addenda to signed encounters

**Id:** 19dbcd31-5dfe-4366-ad39-ace0118ccc3d
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** system-design.md §1.1, 2026-09-28

Any active physician may append a dated, attributed addendum to any
signed encounter, including one signed by another physician. The
signed record itself is immutable; the new author does not replace
the original signer, and addenda never alter accepted probabilities
or prior results.

**Reason:** supports shared follow-up while preserving the original
signer, plan and probability artifacts. New clinical assessment
belongs in a new follow-up encounter.

**Rejected alternative:** only the original signer may correct a
signed record. Rejected — corrections use attributed addenda so the
shared chart stays current without rewriting history.

## Integer percentage units and deterministic redistribution

**Id:** c441dba6-1a50-40c7-a93b-04c540b68b4c
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** system-design.md §7.4 and §8.2, 2026-09-28
**Revisit when:** the numerical policy is validated during implementation

The proposed storage policy represents percentages as decimal strings
with up to six decimal places and stores integer units with
100% = 100,000,000 (one unit = 0.000001 percentage point). Every row
must total exactly 100% — no approximate tolerance. Slider
redistribution keeps the selected value and allocates the remainder
proportionally to the other states' immediately preceding values
(equally when their prior total is zero), using floor plus
largest-remainder with the pinned network's state order for tie
breaking.

**Reason:** exact row totals and stable deterministic rounding; the
selected precision becomes versioned policy so replay reproduces the
same values.

**Rejected alternative:** binary floating-point percentages. Rejected
because of parsing drift and inexact totals; conversion to the
engine's numeric representation happens only at the pinned engine
boundary and is recorded with the execution policy version.
