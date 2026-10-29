# Durable decisions

Rationale that is hard to recover from code. Full entries with Ids, evidence and status live in
[`context/`](../context/index.md) — read that index for the authoritative records. This file
captures what a future task needs before it re-derives the same conclusions.

## Product-level constraints (owner-confirmed, documented in `context/architecture.md`)

- **Empty evidence set.** Patient variables are given to the LLM only to estimate CPTs; they are
  never submitted as observed/soft/likelihood evidence. Inference runs with `evidence={}`
  (`models/inference.py`). Reason: results stay reproducible from stored CPTs and pinned config.
  Anything conditioning inference on patient values contradicts this rule.
- **One open draft per patient, author-only.** Enforced by the partial unique index
  `one_open_draft_per_patient` plus a patient-row `FOR UPDATE` during creation; another physician
  gets a generic 409 with no content. Draft restrictions apply transitively to every derived
  artifact (runs, CPTs, results, downloads).
- **No manual-plan signing bypass.** Signing requires successful *current* executions and
  acceptance of their current results; a failed initial generation cannot be bypassed.
- **Addenda, not edits.** Any active physician may append an attributed addendum to any signed
  encounter; the snapshot itself never changes and the new author does not replace the signer.
- **Exact integer percentages.** 100 % = 100 000 000 units, ≤6 decimals, every row totals
  exactly. Redistribution uses floor + largest-remainder with the pinned network's state order for
  tie-breaking. Floating-point percentages were rejected (drift, inexact totals).

## Reasoning pipeline (`context/mcp.md`, `docs/proposal-generation.md`)

- **One private stdio MCP tool, one scoped grant.** `get_question_patient_inputs` returns only the
  current question's saved projection; a worker-owned subprocess per read, no public MCP port and
  no in-process production shortcut. Reason: isolation between concurrent provider slots.
- **Questions are sequential** in the bundle's declared order; a failed/clarifying question pauses
  later ones. Parallelizing inside a run was rejected.
- **Bounded retries: three attempts total**, shared across transport and validation failures.
- **Invalid CPT output is rejected, never repaired** (no clamping, renormalizing, default-filling).
  Every CPT, including roots, must be estimated — registered tables are never a fallback.

## DDI (`context/ddi.md`, `docs/ddinter-operations.md`)

- No LLM in DDI checking — deterministic lookup against an immutable, content-addressed release.
- **Unavailable coverage is reported as unavailable, never as "no interaction."** Only a
  `released_complete` release can claim `covered_no_listed_interaction`.
- Publication requires an owner-reviewed manifest (`approved_complete` / `approved_limited`);
  the bundled template stays `awaiting_review` and cannot authorize anything. Releases are
  content-addressed (`ddi-<12 hex>`) and immutable, so identical content reuses the existing release.
- Normal startup bootstraps the bundled approved release from committed sources by re-deriving the
  content hash locally, so no generated artifact and no Git LFS are needed.

## Frontend

- **No router library** — web deps are only `react`/`react-dom`; `web/src/app/router.ts` is a
  hand-rolled hash router and wizard steps live in a second `#` fragment.
  *(Evidence: inferred from source; no decision record found in `context/`.)*
- **CSRF token in `localStorage`** (`xi_csrf_token`) rather than session-scoped storage, because the
  HttpOnly session cookie is shared across tabs. This is deliberate, not sloppiness. The token is
  re-read on every mutation so another tab's rotation is observed.
- **Teal `#0A9E8F` on white is only ~3.33:1**, insufficient for small text; the darker
  `--primary-action` tokens (`#06786D` light, `#2DD4BF` dark) are the accessible action fill
  (`context/ui-contrast.md`). Do not "restore" the canonical teal on buttons.
- **Continue does not require a completed step.** Only registration Diagnosis→PANSS asks for
  confirmation, and it records no bypass sentinel. This is asserted by `e2e/panss.spec.ts`.

## Testing and fixtures

- **Child-first TRUNCATE order in test fixtures** (`TRUNCATE notes, encounters, patients`).
  Migration 0006 added `notes→encounters` FK and the fixture helpers swallow failures, so a wrong
  order silently poisons unrelated suites with 409s. Any new child table with an FK must be added
  to those lists (`context/test-fixtures.md`).
- `make test-web` intentionally exits non-zero: there is no web unit suite; browser coverage is
  `e2e/` via `make test-e2e` (Chromium + Firefox).

## Project status facts that get mistaken

- **Research prototype, release gate open.** Not clinically validated, not highly available; no
  live paid-provider request, full-repository suite, or clinical validation is claimed anywhere.
- **The working note is not page notes.** Page notes were removed (2026-10-20); only
  `draft_data.working_note` survives, and it never enters prompts, projections, applicability,
  inference, or DDI. Page-note routes no longer exist.
- **Docs lag the code in places.** Example: README and design docs still say new signed snapshots
  use `signed-encounter-v2`, while `cases/signing.py` sets `signed-encounter-v3`; the progress
  index says schema head `0022` while `db.EXPECTED_SCHEMA_VERSION` is `0024`. Treat source as
  authoritative and update the doc rather than the code.
- **Progress reporting is mandatory per `AGENTS.md`**: record work in the relevant
  `project-documents/dev/progress/sNN.md` and keep `README.md` there short.
