# New case workflow rationale

Scope note (2026-11-12): New case behavior below remains current. A separate
[planned follow-up extension](../project-documents/dev/follow-up-specification.md)
will reuse the fixed oral model with current effect inputs and D/X exclusions;
existing legacy follow-up drafts retain their pipeline. This planned scope does
not alter New case's interaction-annotation behavior or historical records.

## New case reads one exact conditional row, not LLM-estimated CPTs

**Id:** c5207a93-050f-4600-8252-1a9a13dc58ad
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** user-provided new-case registration plan, 2026-11-09
**See:** architecture.md#patient-variables-inform-estimation-only — dbca3245-90f9-4004-a3f9-5271a4fbb099 — as of 2026-11-09

The experimental `new-case-v1` oral-antipsychotic ranking uses the supplied
fixed Bayesian network as executable truth. All 16 binary parent factors are
observed in this workflow, so `P(Antipsychotic | all 16 factors)` is exactly one
22-value target CPT row chosen by a deterministic bit index. This path has no
LLM request, private MCP projection, background generation job, generated CPT,
probability slider or per-question CPT acceptance. The legacy LLM/CPT pipeline
is kept unchanged for Known case and follow-up — the entry referenced above is
that legacy constraint, not a description of New case.

**Reason:** the supplied model has a star structure with fully observed parents
here, so an exact conditional row is available without estimating anything.
Reconstructing its ordinal weight formula in application code, marginalizing
unknown factors or calling an LLM would add nondeterminism and a provider
dependency while introducing factors this UI never produces. The asset is
SHA-256-pinned and validated so the lookup is reproducible from stored bytes.

**Rejected alternatives:** sending the 16 MB model through the generic 1 MiB
XML admission / empty-evidence inference path (rejected — it rejects the trusted
asset and that policy assumes unknown factors); using an LLM or worker to
estimate a CPT (rejected — no LLM, provider or job exists in this workflow);
treating the four movement-disorder factors as examined negative clinical
findings (rejected — they are explicit `No` workflow defaults, not measurements).

The bounded loader and ranking module are hash-pinned in
`models/oral_antipsychotic.py`. `cases/new_case_plans.py` now connects the exact
row to immutable proposals, personal availability and candidate-only Lexicomp
evidence without creating a legacy batch or provider request.

## New case signatures add a v4 shape without rewriting historical snapshots

**Id:** 5a776fa8-72dc-4e50-9d1a-e886eeba3bfa
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** user-provided new-case registration plan, 2026-11-09
**See:** registration-flow.md#historical-notes-survive-removal-of-active-capture — 4b28bd7e-d16b-47a3-a662-2e5d2dfe5b09 — as of 2026-11-09

New case signing is specified to use a new `signed-encounter-v4` snapshot shape
that references its own proposal through a dedicated foreign key with null
legacy batch/proposal references. Old signed snapshots keep their original JSON,
hashes and readers; they are not rewritten, rehashed or backfilled. The
signed-snapshot check admits exactly one of the two reference shapes. Older
backup archives keep their supported versions and restore with legacy defaults.

**Reason:** signed records are immutable audit artifacts. Rewriting historical
bytes or migrating them to a v4 shape would change their hashes, break
backup/restore compatibility and misrepresent what was originally signed. An
additive v4 shape keeps legacy chart/report rendering and historical releases
readable while allowing a real New case signature without fabricating a
generation batch the deterministic path never produced.

**Rejected alternatives:** retrofitting v4 fields onto existing snapshots or
recomputing their hashes (rejected — breaks immutability and archive checks);
requiring a legacy generation batch, proposal and CPT acceptances for New case
signing (rejected — the deterministic path has none, so those references would
be invented); relaxing the generic model admission bound to accommodate the
large asset (rejected — the trusted model uses a dedicated bounded loader).

Migration `0031` and `cases/new_case_plans.py` implement this additive contract,
including immutable proposals, atomic v4 signing, signed chart/report rendering
and legacy follow-up compatibility. A mixed DDInter/Lexicomp/model/proposal/v4
archive was restored on an isolated database with its signed JSON and hash
unchanged. See `docs/new-case-state.md` and the dated implementation checkpoint
for verification scope; integration remains distinct from clinical validation.
