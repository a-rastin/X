# Issue 04 — Proposal generation requires resolved inputs

Date: 2026-10-27  
Status: investigated; implementation proposed, no application-code changes  
Affected flow: Step 7 → automatic proposal generation, new runs, and retries

## Finding

Step 7 treats incomplete clinical context as a prohibition on generating the entire proposal. The reported message is the frontend's explicit response to server readiness being false:

> Generation paused: Review the unresolved proposal inputs below, then return to this step.

This behavior conflicts with the requested requirement: generate the primary treatment plan even when input values are Unknown or empty. It is enforced across the frontend, backend preflight, question gates, scheduling, and proposal assembly. Removing the displayed warning alone will not fix it.

The underlying mismatch is that **input completeness and permission to estimate are treated as the same condition**. The system already represents missing context without inventing patient values, but its execution policy prevents that context from reaching estimation.

## Root cause and evidence

Paths and line numbers refer to the inspected tree.

| Location | Current behavior and consequence |
| --- | --- |
| `web/src/features/reasoning/ProposalReviewPanel.tsx:501` (`prepareStart`) | Flushes edits, checks saved revision, and reads proposal readiness. If `ready.ready` is false, sets the quoted blocked message and returns `null` before `startGeneration`. Initial generation, explicit new runs, and retries use this preparation. The `Generation paused:` prefix is rendered at line 986. |
| `backend/src/x_insight/reasoning/readiness.py:30–104` | Evaluates every package's gate, collects unresolved required inputs, then computes workflow readiness with `all(question["status"] != NEEDS_CLARIFICATION ...)`. A missing secondary-question input therefore prevents starting the primary pharmacotherapy question too. |
| `backend/src/x_insight/reasoning/snapshots.py:243` (`_resolve_raw_value`) and `:304` (`_typed_status`) | Candidate History values are validated against their definitions. Missing, null, and Unknown become missing context with null model values. An empty string is not a permitted answer and resolves to Unknown; readiness reports it as invalid. These representations are subsequently blocked, rather than passed to estimation. |
| `snapshots.py:432` (`evaluate_gate`) | For candidate History packages, checks all applicable required fields before evaluating the clinical applicability gate. Any non-observed value or explicit `Unresolved` returns `needs_clarification`. This affects pharmacotherapy despite its universally applicable registration contract. |
| `snapshots.py:890–907` | Reviewed generation independently recomputes readiness and raises HTTP 422 `PROPOSAL_INPUTS_INCOMPLETE` before freezing a batch or enqueueing work. Bypassing the React condition still fails here. |
| `snapshots.py:679–713`, `queue.py:619`, `queue.py:886` | Batch/job selection and ordered execution stop at `needs_clarification`. Removing only the HTTP preflight would leave clarification runs and blocked successors. |
| `backend/src/x_insight/reasoning/coordinator.py:245–300` | Proposal assembly returns no proposal when an included run needs clarification or lacks a successful baseline. Partial execution alone cannot produce the complete proposal. |
| `content/questions/pharmacotherapy/s39-reviewed-v1/prompt.txt:1` and `manifest.json:963–980` | The content contract explicitly requires clarification before estimation for required unknown/missing inputs. The active pharmacotherapy package retains that instruction; changing runtime gates alone would contradict the provider prompt. |

`build_question_projection` (`snapshots.py:330`) already produces typed entries such as `{"status":"missing","value":null}`. Bayesian execution also requires empty evidence (`backend/src/x_insight/models/inference.py:834`). Empty inference evidence is a separate rule: patient context informs CPT estimation, while the inference engine calculates from the resulting tables. It does not imply that current generation accepts incomplete context.

### How this behavior arose

Commit `363ec24` (`fix(proposals): check saved input readiness before generation`) introduced the workflow readiness preflight. The [S48 October 22 follow-up](../project-documents/dev/progress/s48.md#follow-up-2026-10-22--check-proposal-readiness-before-creating-a-run) explicitly records that Unknown and Not assessed remain unresolved and that reviewed starts/retries reject incomplete inputs. Earlier execution gates already stopped questions requiring clarification; this change moved that policy ahead of batch creation and exposed all blockers in the UI.

The mechanism is established by the source and synthetic checks below. The explanation that completeness was deliberately enforced is supported by that session report and the retained prompt. The present request supersedes that behavior for Unknown and empty inputs. This is an application-policy change, rather than evidence of a provider failure. The actual affected encounter and live runtime were not inspected.

## Verification performed

Used the real active registration packages and database-free synthetic drafts. No patient data, database, or provider was accessed.

Starting from known permitted context, changed only `candidate/history/pharmacotherapy.v2/h_physical_vulnerability`:

| Input | Workflow ready | Pharmacotherapy gate | Readiness diagnostic |
| --- | --- | --- | --- |
| Known permitted value | true | `ready` | None |
| `"unknown"` | false | `needs_clarification` | `unknown` |
| `null` | false | `needs_clarification` | `null` |
| Empty string | false | `needs_clarification` | `invalid` |
| Omitted field | false | `needs_clarification` | `unknown` after current defaults |

Each uncertain case produced a valid pharmacotherapy projection entry with `status: "missing"` and `value: null`. A completely empty draft also projected successfully but returned `ready: false`, 12 required inputs and only one execution-ready input. Current defaults explain why some absent inputs count as answered; answered does not mean execution-ready.

A separate check left pharmacotherapy ready and changed only LAI preference to Unknown. Overall readiness became false, confirming that another question can prevent primary generation.

Existing regression suite: `cd backend && python3 -B -m pytest -p no:cacheprovider tests/models/test_proposal_readiness.py -q` → **17 passed**, one dependency deprecation warning. These tests validate today's strict policy, including uncertainty blocking; passing them does not satisfy the requested behavior. No browser or end-to-end provider completion was tested.

## Proposed solution and implementation plan

### 1. Separate estimation eligibility from completeness

Create one server-owned decision used by readiness and execution. Unknown and empty clinical context must produce nonblocking completeness notices while allowing estimation. Represent omitted fields, null, empty strings, and whitespace-only form values consistently as missing context at the appropriate input boundary. Keep explicit Unknown distinguishable in diagnostics where useful, with null patient values in estimation context.

Do not convert uncertainty to a negative clinical finding, a known treatment status, or a fabricated answer. Keep malformed nonempty values, conflicting answers, invalid packages, and operational failures distinguishable from empty inputs. Proposed scope: retain validation for those cases rather than silently accepting arbitrary corrupt data. Define Not assessed and explicit `Unresolved` separately so existing mappings cannot accidentally determine their new policy.

For applicability, retain explicit known-false outcomes. An unknown clinical gate must not silently become false or remove a proposal section. Proposed behavior is to estimate under uncertain applicability and disclose that uncertainty. Distinguish this from structural eligibility, such as whether an established-case follow-up package belongs in the workflow at all. Follow-up baseline/trial requirements also need explicit treatment; relaxing missing context must not assert that a documented prerequisite was met.

### 2. Align the versioned content contract

Update the derived application input policy in `models/workflow_release.py` and associated History definitions, manifests, and prompts. Remove contradictory instructions that demand clarification for the newly permitted incomplete context. Tell estimation to use available context, preserve missingness, estimate complete CPTs, and avoid inventing patient observations. Preserve strict table validation and empty inference evidence.

Version and hash the changed application contract and its derived packages. Preserve historical reviewed artifacts and their approval provenance; do not rewrite old approved bytes or imply new clinical approval. Check the package validator's supported policy vocabulary before introducing new manifest values.

### 3. Apply the policy throughout generation

Change readiness aggregation, reviewed-start validation, and per-question gate outcomes together. Missingness allowed by the new policy must result in executable questions, with warnings carried separately, rather than retained `needs_clarification` statuses. Audit scheduler eligibility, proposal assembly, and freshness re-evaluation against the same decision. This should allow both the primary question and the complete proposal to finish with permitted incomplete context.

Retain author access, save/revision checks, diagnosis acknowledgment, medication reconciliation, admission limits, and actual provider/CPT/inference failure handling. These are independent prerequisites, not unresolved clinical-input completeness.

### 4. Update Step 7 and existing-run recovery

Have `prepareStart` block only on genuine generation blockers. Show incomplete-context notices in Step 7 and History without telling the user to resolve every field before generation. Update `ProposalReadinessSummary.tsx`, its API types, and the frontend readiness-response validation: the current check rejects any ready response containing blockers, so warnings need a distinct contract.

Cover automatic entry, manual new runs, and retries. Keep old immutable runs readable. Offer a new run using the current policy when restoring an older clarification run; the existing `source_batch_id` replacement path preserves frozen packages, so it must not silently reuse the old restrictive policy. Include policy identity in fingerprints/reuse decisions to prevent restoring an old blocked batch as the new result. Do not mutate historical projections, results, or approvals.

### 5. Verify the requested outcome

- Update strict-policy assertions in readiness, snapshot/workflow, reviewed-generation HTTP, and browser tests. Retain separate tests for genuinely invalid/conflicting inputs and operational failures.
- Cover known, Unknown, omitted, null, empty-string, and whitespace-only inputs; one missing secondary input; all optional clinical context empty; known-false applicability; uncertain applicability; and relevant follow-up prerequisites.
- Assert that eligible starts create jobs, estimation receives typed missing/null context, all required CPTs are validated, inference keeps empty evidence, and a proposal is assembled. Check actual completion, not just disappearance of the warning.
- Verify refresh, retry, old-run replacement, policy fingerprinting, and a subsequent Unknown-to-known edit causing the appropriate stale/new-run behavior. Ensure saves and revision conflicts still prevent freezing unacknowledged edits.
- Run focused backend and Chromium/Firefox proposal/history suites, then required project checks. Perform a controlled provider-backed completion to confirm the revised prompt handles incomplete context without fabricating clinical facts.

Acceptance criterion: with other operational prerequisites satisfied, entering Step 7 with Unknown or empty clinical inputs produces the primary treatment plan and completes the proposal workflow. Missing context remains visible as uncertainty and does not produce `Generation paused` solely because those values are unresolved.

## Scope of this report

No application source, content package, test, configuration, or database changes were made. The implementation above remains proposed. Only this report and the required progress documentation were updated.
