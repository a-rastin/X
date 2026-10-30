# X-INSIGHT User Guide

Last updated: 2026-10-30. This guide describes the current application for physicians and administrators. For installation and startup, use the [Installation and Running Guide](installation.md).

X-INSIGHT is a research workspace for recording patient encounters, completing structured assessments, reviewing medication interactions, generating proposals, and signing physician plans. Its research status and clinical-content review limitations still apply; a generated proposal requires physician review. This guide explains application controls and does not provide treatment advice.

## Contents

- [Sign in and manage your account](#sign-in-and-manage-your-account)
- [Find a patient and open their chart](#find-a-patient-and-open-their-chart)
- [Register a new patient](#register-a-new-patient)
- [Complete the registration assessments](#complete-the-registration-assessments)
- [Select medications and review interactions](#select-medications-and-review-interactions)
- [Generate and review a proposal](#generate-and-review-a-proposal)
- [Review and adjust probabilities](#review-and-adjust-probabilities)
- [Write and sign the physician plan](#write-and-sign-the-physician-plan)
- [Review signed records and add corrections](#review-signed-records-and-add-corrections)
- [Start a follow-up encounter](#start-a-follow-up-encounter)
- [Save, resume, and discard drafts](#save-resume-and-discard-drafts)
- [Administrator tasks](#administrator-tasks)
- [Troubleshooting](#troubleshooting)

## Sign in and manage your account

1. Open the application URL supplied by your administrator. A default local development installation uses `http://localhost:5173`.
2. Select your **Role** (Physician or Administrator), enter your username and password, and select **Sign in**. Ask your administrator for an account if you do not have one.
3. If the research/clinical warning dialog appears, read it and select **Acknowledge and continue** to enter the workspace.
4. Check the username and role in the header before opening patient records.

The navigation available to you depends on your role:

| Role | Main capabilities |
| --- | --- |
| Physician | Register patients, view charts, author private encounter drafts, complete assessments, review proposals, sign plans, and append addenda. |
| Administrator | View patient records and manage physician accounts, networks, provider settings, audit events, exports, backups, restores, and operational status. Administrator access does not grant access to another user's private draft content. |

Use **Dashboard** for the patient directory and **Patients** for its dedicated page. **Account** shows your identity and lets you change your password: enter **Current password** and **New password**, then submit the form. The header theme button switches between light and dark themes and saves the preference to your profile. Select **Sign out** when finished.

## Find a patient and open their chart

1. Open **Dashboard** or **Patients**.
2. Enter part of a name or identifier in **Search by name or ID**. Search updates as you type.
3. Use the clinical-status filter to narrow results and the pagination controls to move through them.
4. Open the patient's chart from their row. Verify the identifier and demographics.

The chart contains demographics, open-draft status, signed chronology, signed records, and follow-up entry controls. Signed records are shared; open-draft clinical content is private to its author. If another physician owns the open draft, you cannot open its contents or create a second draft for that patient.

Where available, use **Edit demographics**, change the required values, and select **Save demographics**. Changes to age, sex, or clinical status can make affected proposal results out of date. Review the resulting notice and regenerate affected results before signing.

## Register a new patient

As a physician, select **Register new patient** from the patient directory. The page title is **Add New Patient**.

1. Complete the demographics form using the current field rules:

   | Field | Accepted input |
   | --- | --- |
   | Given name and family name | Nonempty Unicode letters only; spaces, punctuation, and digits are rejected. |
   | Patient ID | Exactly 10 digits, using `0`–`9`. Leading zeros are preserved. |
   | Sex | Select M or F from the available choices. |
   | Age | A whole number from 18 through 99. |
   | Clinical status | Select first-time or established. |
   | Phone | Optional free text. |

2. Resolve any field errors. **Register patient** remains disabled while the form is invalid.
3. Select **Register patient** once. Successful registration creates the patient and a registration draft, then opens **Diagnosis** directly.
4. Use the first wizard step to return to the saved demographics and working note when needed.

If the identifier already exists, find the existing patient in the directory instead of registering another record.

The registration wizard has seven steps:

| Step | Page | Purpose |
| --- | --- | --- |
| 1 | Register patient | Create the record and access the working note. |
| 2 | Diagnosis | Record the diagnostic worksheet responses. |
| 3 | PANSS | Record symptom ratings. |
| 4 | C-SSRS | Record recent-ideation responses. |
| 5 | History | Enter clinical history and proposal context. |
| 6 | Medications | Select medications and review interactions. |
| 7 | Proposal review | Review generated results, accept them, and prepare/sign the physician plan. |

Use the numbered wizard links or **Back to…** and **Continue to…** controls to move between pages. Moving forward does not certify that an assessment is complete. Adverse effects are collected during follow-up, rather than as a registration step.

The **Working note** becomes editable once a draft exists and autosaves. It is a workspace note: its text does not enter proposal prompts, probability estimation, inference, applicability decisions, or interaction checking. Enter information needed for those functions in the corresponding structured fields.

## Complete the registration assessments

### Diagnosis

1. Work through the criterion groups and select the response matching the assessment. Missing editable responses initialize to **Unknown**.
2. Review the saved assessment result and criterion summary. Unknown responses can leave the result indeterminate.
3. Select **Continue to PANSS**. If the assessment does not confirm the diagnosis, a dialog offers **Return to diagnosis** or **Continue to PANSS anyway**.
4. For a completed below-threshold result, review the warning and select **Acknowledge below-threshold result** when appropriate. This recorded acknowledgment is required before proposal generation for that result.

The Continue dialog and below-threshold acknowledgment are separate actions. Continuing to PANSS does not record acknowledgment. Changing the diagnosis responses invalidates an earlier acknowledgment and can require another review.

### PANSS

1. Assess the displayed window: the **previous 7 days**.
2. Use the subscale navigation to move between Positive, Negative, and General psychopathology items.
3. Select a rating from **1 (absent)** through **7 (extreme)** for each assessed item.
4. Review the saved subscale scores and total in **Assessment review**.

There are 30 items. The total appears only after all 30 have valid ratings. An unanswered item does not become a minimum rating or zero; a dash means a score is unavailable. The selection progress bar reports local responses and does not by itself prove that they have saved.

### C-SSRS

1. Review all five recent-ideation questions for the **past month**.
2. In registration, select **Yes** or **No** for each item. The current registration form defaults missing or older Unknown responses to **No** when opened. Check every response; a preselected No does not establish that a clinician assessed the item.
3. Review the saved assessment summary before continuing.

Follow-up retains **Yes**, **No**, and **Unknown** choices. Unknown is not a negative screen. The current form covers recent ideation; do not expect lifetime or behavior questions on this page.

### History

1. Complete the visible core-history questions, including lifetime antipsychotic exposure and persistent involuntary movements.
2. Review additional questions revealed by your answers. For example, lifetime exposure **Yes** reveals prior-treatment context. Changing a controlling answer clears responses that become hidden; returning to that branch may require answering them again.
3. Complete the relevant proposal-context fields, including the displayed pharmacotherapy, LAI, aggression, and other workflow sections.
4. Review **Proposal readiness** and follow links to any blocking fields. The summary reflects saved data; pending changes need to save before readiness can update.

Core-history completion and proposal readiness measure different things. Allowed Unknown responses can count toward core completion. Unknown or empty proposal-context values generally proceed with disclosed uncertainty; explicit **Not assessed**, **Unresolved**, conflicting or invalid values, and missing follow-up prerequisites can still block the affected question. Record the actual assessment rather than choosing a different answer to clear a blocker.

The History page has no editable reconciliation section. Review and update its current responses directly. Medication reconciliation remains a separate follow-up action.

## Select medications and review interactions

1. Open **Medications**.
2. Enter a drug name or catalog ID in **Search medication catalog**. Use the result pagination when needed.
3. Add the matching catalog entry to the selected list. Remove entries that should not be included. Selection uses catalog drugs; this page does not capture dose or schedule.
4. Select **Save medications** and wait for the save to succeed. Save an empty list explicitly if no medications should be recorded.
5. Review the **Interaction review** panel beside the list. Changes to the selection hide the previous report until the updated list is saved.
6. Use **All pairs**, the severity filters, and **Coverage unavailable** to inspect the report. Expand the available evidence for each pair.

| Report display | Meaning |
| --- | --- |
| Interaction found with severity | The saved dataset contains an assertion for this pair. Review the evidence and any conflicting assertions. |
| Unknown severity | Severity is not known for at least some reported evidence. |
| No listed interaction in this dataset | The dataset explicitly covers the pair without a listed interaction; this is not a general assurance of safety. |
| Coverage unavailable | The dataset does not establish the pair's interaction status. |
| No medication pairs | Fewer than two medications are in the saved list. |

Check the report's dataset/version and coverage information. Some source data supplies severity only; mechanism and management text may be absent. **Retry report** retries retrieval when available. If the dataset is unavailable, the saved medication list remains recorded; ask the administrator to investigate.

For copied follow-up medications, review the entire list and select **Reconcile N medications** when shown. An ordinary save does not clear the reconciliation requirement, and the interaction report remains pending until reconciliation succeeds.

## Generate and review a proposal

1. Save the assessment, history, and medication information, then open **Proposal review**.
2. The page checks the saved inputs and operational prerequisites. When eligible, it automatically starts or restores a generation run; there is no separate initial Generate button.
3. If a check blocks generation, follow its explanation to the relevant page, resolve the issue, and return. **Check again** repeats the checks.
4. Watch the ordered per-question status displays. Questions can complete, be not applicable, require clarification, or fail. Background processing requires a running worker and configured provider.
5. Review the proposal text, skipped-question reasons, uncertainty notices, and pinned medication-interaction coverage.
6. Open **Show transparency for…** for a question to inspect the saved patient inputs, estimated probability tables, computed results, recommendation, and provenance.

A run preserves the inputs used when it started. Returning to this page or reopening the browser restores the recorded run rather than silently regenerating it. If the patient inputs changed, use **Start new run with current inputs** when offered. For an older History contract, the page may instead offer **Start new run with current History**. Earlier runs remain historical records.

Use the displayed question retry action for a failed question after addressing its cause. A question without a successful result has no result to accept. A proposal is ready for signing only when its required results are successful, current, and explicitly accepted.

## Review and adjust probabilities

The probability-review panel presents conditional probability tables (**CPTs**): percentages used by the reasoning network. The original values are estimated by the configured model; the application computes the resulting network probabilities.

1. Expand the question's CPT review and inspect the original and current values. Use **Search CPT rows** to locate a node or parent-state combination.
2. If an adjustment is appropriate, move the relevant slider. Other values in that row redistribute proportionally to preserve the total.
3. Wait for the local calculation to finish. This recalculation does not make another provider request.
4. Compare **Original result** with **Latest successful result**. Recommendation wording can remain the same even when probabilities change.
5. Select **Accept current result** after reviewing the exact current successful result. Repeat for every required completed question, including questions whose original probabilities you keep.

Use **Reset to original values** to restore the question's original percentages while retaining its revision history. If calculation fails, use **Retry local calculation** or reset. **Reload current values** fetches the saved review state.

A probability edit, reset, or relevant patient-data change can invalidate acceptance. Review and accept the resulting current result again. Resetting or locally retrying an out-of-date run does not refresh its patient inputs; start a new run from Proposal review for those changes.

## Write and sign the physician plan

1. In Proposal review, enter your final plan in **Physician secondary plan**. The generated proposal remains available for comparison.
2. Review **Clozapine included in final treatment plan** and select the appropriate structured value. A signed Yes enables relevant established-case questions at follow-up.
3. Select **Save secondary plan** or wait for its autosave acknowledgment. Confirm that a nonempty plan has saved.
4. Compare the proposal with your saved plan and verify that all required question results have current acceptance.
5. Select **Sign encounter** when available.
6. Wait for the **Signed** confirmation showing the signer and time, then follow the signed-chart link.

Signing freezes the encounter record and releases its open-draft slot. Pending, failed, out-of-date, or unaccepted required results block signing. A failed initial generation has no manual-plan signing bypass. If signing is denied, read the reason, resolve it, and retry; review the preserved plan text before submitting again.

## Review signed records and add corrections

1. Open the patient's chart and locate the encounter in the signed chronology or signed records.
2. Review the frozen plan, original and accepted probabilities, and signer/time attribution.
3. To correct a signed record as an active physician, enter text in **Append attributed correction**, review any optional structured confirmation, and select **Append addendum**.

Signed content cannot be edited in place. An addendum adds a dated, attributed correction while preserving the original. Use a new follow-up encounter for a later assessment.

**Print signed record** opens browser printing where the control is available; the chart labels physician printing as provisional policy. Administrators can also select **Open printable longitudinal report** for a report of signed encounters. That longitudinal-report route is administrator-only.

## Start a follow-up encounter

1. Open the patient's chart and select the follow-up draft action. The directory also provides a follow-up entry action.
2. If an open draft already exists, resume it if you are its author. Only one open draft is allowed per patient across physicians and encounter types.
3. Review the **Follow-up baseline** panel and copied information from the signed baseline. Prior scores are historical references, not answers to the new assessment.
4. Complete the current assessments, review/update History, record adverse effects, and reconcile the medications.
5. Review the new proposal, accept current results, save the physician plan, and sign as described above.

Follow-up uses a different seven-step sequence:

| Step | Page |
| --- | --- |
| 1 | Diagnosis |
| 2 | PANSS |
| 3 | C-SSRS |
| 4 | History |
| 5 | Adverse effects |
| 6 | Medications |
| 7 | Proposal review |

PANSS starts unanswered; C-SSRS retains Unknown defaults rather than copying prior answers. On **Adverse effects**, complete the displayed effect and trial-context fields relevant to this encounter. Address any missing baseline or treatment-trial prerequisites named by proposal readiness. Creating a follow-up draft alone does not establish that every follow-up question can execute.

## Save, resume, and discard drafts

Most worksheet edits autosave after a draft exists. Medication validation and reporting use **Save medications**, while the physician plan also has its own save control and status. Routine draft-save status may be hidden on assessment pages, but failed saves and conflicts remain visible. Server acknowledgment, rather than a selected control or progress count, establishes that work has saved.

- **Resume:** Return to the patient's chart or directory and open your existing draft. Another user's draft remains private. Older drafts may show a **Resume** assessment action for a previously skipped section.
- **Failed save:** Keep the page open, check the connection, and use the displayed retry control. Resolve validation errors before leaving.
- **Conflicting save:** Review the current server values against your edits. Reload adopts the saved server version; retry submits your retained edits against the current revision. For a physician-plan conflict, the options are **Use saved version** and **Keep my edits and retry**. Choose deliberately before discarding local work.
- **Navigation warning:** If warned about unsaved changes, stay on the page until the save or recovery succeeds when you need to preserve those edits.
- **Discard:** Select **Discard draft**, read the dialog, check **I understand this discards the draft but keeps the patient**, and select **Confirm discard**. This cannot be undone. It releases the draft slot and keeps the patient record.

## Administrator tasks

### Manage physician accounts

Open **Physicians** to create a physician with a username and password, edit the account, or deactivate/reactivate it. Before deactivation, inspect the open-draft preview and choose how to handle drafts:

- **Retain drafts** keeps them read-only while the account is inactive and leaves their patient slots occupied.
- **Discard drafts** releases those slots and requires the displayed review and discard confirmations. Reactivating the account does not restore discarded drafts.

The preview identifies affected drafts without exposing their clinical contents.

### Configure and test the proposal provider

1. Open **Provider**.
2. Enter **Base URL**, **Model**, and the required **API key** for your provider.
3. On subsequent edits, choose the **Saved key** action: **unchanged**, **replace**, or **clear**. Typing a replacement requires the replace action.
4. Use the local-endpoint or fake-IP-proxy approval controls only for the corresponding deployment configuration. Changing the URL clears those approvals.
5. Select **Save**, inspect the effective saved endpoint, then select **Test provider**. Testing is disabled until a settings revision has been saved; save later edits before testing them.
6. Review the capability diagnostic. A successful synthetic capability check does not establish that a clinical generation has completed.

A public HTTPS origin is expanded to `/v1/chat/completions`; a base ending in `/v1` gets `/chat/completions`. A full custom endpoint keeps its path. Replacing or clearing the key cancels active generation jobs. Already queued jobs otherwise use the saved settings revision pinned when they were created.

### Inspect networks, audit events, and status

| Navigation | How to use it |
| --- | --- |
| Networks | Import XMLBIF XML with a network name, inspect versions, validation details and the read-only graph, and use the available workflow-activation controls after review. Edits create immutable versions. Structural validity or activation does not establish clinical validity or replace a reviewed workflow bundle. |
| Audit | Set the displayed filters, select **Apply filters**, page through events, and open an event's detail to inspect its actor, time, action, and correlation references. |
| Status | Inspect queue, provider, inference-limit, disk, backup, and request information and any displayed operational alerts. Use this when generation or recovery appears stalled. |

### Export and print records

Open **Exports**, then select **Download patients CSV** or **Download physicians CSV**. Import the Patient ID column into a spreadsheet as **Text** to preserve leading zeros. List exports are not full backups.

For a patient's signed longitudinal record, open their chart and select **Open printable longitudinal report**, then use the report's print controls.

### Back up and restore

Open **Backups** to create a full backup, inspect its completed status and manifest, and download the resulting archive. Coordinate storage of the matching provider encryption key separately with the operator; that key is excluded from the archive.

**Restores** uploads a backup ZIP into staging for validation. Review the validation report, staged inventory, replacement impact, and confirmation digest before any commit. Committing a restore replaces live state and holds writes in maintenance mode; it is a planned recovery operation. Follow the [operator restore procedure](../deploy/README.md), including post-restore verification and reopening, and the displayed key-recovery notes. Uploading to staging alone does not commit the restore.

### Archive a patient

Use the chart's **Archive patient** action and its confirmation. Under the application's current provisional policy, archived patients are read-only: new drafts, edits, and signing are blocked. Archiving preserves the record and any retained private draft. **Unarchive patient** reverses the archive restriction; the draft remains accessible only to its author.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| Sign-in fails or the session expires | Verify the selected role and credentials or sign in again. Ask the administrator to check account status if needed; return to the saved draft after authentication. |
| Register patient is disabled | Check every required demographic field, especially letters-only names, a 10-digit ID, and whole-number age 18–99. |
| Another open draft blocks work | Resume your existing draft, or coordinate with its author. An administrator cannot read its private clinical contents. |
| Save failed or revision conflict appears | Keep local work open and use the save-recovery controls described above. Review server values before choosing Reload or Retry. |
| Proposal generation is blocked | Read the specific prerequisite or readiness message; follow field links, save corrections, and select Check again. Unknown/empty context warnings alone generally do not block generation. |
| A run remains queued | Ask the administrator to inspect Status, provider configuration, and the background worker. Reloading the page restores the run; it does not start processing services. |
| An earlier run still needs clarification after input changes | Save the corrected inputs, return to Proposal review, and use Start new run with current inputs, or the current History action if offered. |
| No CPT result can be accepted | Wait for a successful question result or resolve its failure/clarification. A failed or pending calculation has no current result to accept. |
| Results are out of date | Regenerate from current saved inputs and review/accept the current results again. A local retry or reset does not refresh old patient inputs. |
| DDI report is pending or unavailable | Save the selected list, reconcile copied follow-up medications when prompted, then retry the report. Ask the administrator to check dataset availability if necessary. |
| Sign encounter is unavailable or denied | Check successful/current question results, each required acceptance, the saved nonempty physician plan, patient archive status, and save conflicts. Follow the displayed reason. |
| A signed encounter will not open as a draft | Open the patient chart. Signing closes the draft and preserves it as an immutable signed record. |
| Writes are held for maintenance | Coordinate with the administrator handling recovery and wait for verification/reopening. |

For help, provide the administrator with the page name, patient identifier through an appropriate support channel, the displayed error/code, and the action attempted. Never include passwords or provider API keys.

For detailed operational recovery and generation contracts, see [Proposal generation](proposal-generation.md), [Installation and Running](installation.md), and the [operator runbook](../deploy/README.md). The [progress index](../project-documents/dev/progress/README.md) records verification scope and outstanding release work; this guide does not establish release readiness or clinical validation.
