# Registration flow

## Registration opens on the wizard

**Id:** c6dd360a-d80a-45a2-aa17-7239c4368829
**Type:** decision
**Status:** superseded
**Evidence:** confirmed
**Source:** product instruction, 2026-10-18
**Revisit when:** registration entry or where demographics notes are saved changes
**See:** registration-flow.md#adverse-effects-belong-to-follow-up — 70b5da8e-c401-4b56-97e3-5c11b6d787da — as of 2026-10-18
**Superseded by:** f4ca7d60-2b97-490b-a1d3-6ccbb0d3ba60

Register new patient opens the wizard immediately. Demographics are
entered on step 1, and the demographics page notes and working note are
on that same step. A valid submit still creates the patient and registration
draft together, then opens `#/encounters/:id#register` — the same step, with
the saved demographics and notes that can actually save. There is no second
notes page and no control that opens the draft separately. The wizard is
seven steps; adverse effects are not one of them.

**Reason:** showing the notes after create and again on wizard step 1 made
the same boxes appear twice. The physician should land on the wizard, enter
demographics there, and keep the notes on that step only.

**Rejected alternative:** keep the notes on `#/patients/new` after create and
also on the wizard register step. Rejected because that is the duplicate
page.

**Rejected alternative:** create an empty patient and draft before
demographics so the encounter URL exists on the first click. Rejected
because unsent demographics stay local, and a valid form creates the patient
and registration draft together.

**Consequence:** the note boxes are visible on step 1 before create, but
they are not durable until the draft exists. Later wizard steps stay closed
until then.

This placement was rechecked against the 2026-10-20 requirement removing page
notes. The wizard entry and demographics placement remain; the successor below
replaces the two-note-box behavior.

## Adverse effects belong to follow-up

**Id:** 70b5da8e-c401-4b56-97e3-5c11b6d787da
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** product instruction, 2026-10-18
**Revisit when:** the registration or follow-up wizard step list changes
**See:** registration-flow.md#registration-opens-on-the-wizard — c6dd360a-d80a-45a2-aa17-7239c4368829 — as of 2026-10-18

Scope rechecked 2026-11-12: this remains the `legacy-v1` contract. The confirmed
new follow-up design changes ordering and effects only for future `follow-up-v1`
encounters; existing drafts retain this behavior. See the
[planned specification](../project-documents/dev/follow-up-specification.md).
The registration/follow-up separation and fresh-effect principle remain active.

Adverse effects are recorded on a follow-up encounter, not while adding
a new patient. The registration wizard is Register patient, Diagnosis,
PANSS, C-SSRS, History, Medications, Proposal review. Follow-up keeps
Adverse effects between History and Medications. The server refuses a
new effects read, status change, or changed effects
section on a registration draft. An unchanged effects section already
stored on an older registration draft can still round-trip so other
pages can save; it is not deleted. A new follow-up draft does not copy
prior effect questionnaires.

**Reason:** registration pages are demographics, diagnosis, severity,
suicide, history and medications, then proposal review and sign. Recording
the four adverse effects is a follow-up task. The shared assessment step
list had put that page on both wizards, so new-patient registration showed
it as step 6.

**Rejected alternative:** hide the registration link and leave the capture
routes open. Rejected because a registration draft could still record
effects through autosave and the status route.

**Rejected alternative:** copy prior effect questionnaires into the new
follow-up draft as completed answers. Rejected because follow-up records
effects fresh. History and medications are what get copied; prior scores
stay historical only.

**Consequence:** registration is seven steps. Follow-up is seven steps
with Adverse effects as step 5. Section headings use that wizard index,
so the same page can show a different step number on each kind.

The original implementation also refused effects-page note creation on a
registration draft. That boundary became historical when all page-note routes
were removed on 2026-10-20; the assessment and autosave restrictions remain.

## Registration keeps the working note after page-note removal

**Id:** f4ca7d60-2b97-490b-a1d3-6ccbb0d3ba60
**Type:** decision
**Status:** needs-review
**Evidence:** unknown
**Source:** product requirement, 2026-10-20; implementation and verification recorded in `project-documents/dev/progress/s13.md`
**Verification:** contradicted — page-note UI and routes remain removed, but the Register patient working note is now hidden at the user’s request
**Revisit when:** note capture or registration entry requirements change
**See:** registration-flow.md#registration-opens-on-the-wizard — c6dd360a-d80a-45a2-aa17-7239c4368829 — as of 2026-10-20

Registration still opens on the seven-step wizard with demographics on step 1.
That step keeps one editable working note, disabled until patient and draft
creation and then saved through draft autosave. Page-note controls and backend
read/write routes are removed across registration and follow-up. The working
note remains outside analytical inputs. Addenda remain a separate signed-record
feature.

**Reason:** the feature scope is confirmed by the product requirement: remove
page notes and retain the working note. The product rationale for that change
was not supplied and remains unknown; the evidence grade reflects that gap.

**Alternatives:** unknown — no alternative product design or reason for its
rejection was supplied.

The 2026-10-28 direct-Diagnosis requirement triggers the registration-entry
revisit condition; see `issues/issue-06.md`. Successful creation now advances
to Diagnosis while working-note placement on persisted step 1 is retained. The
entry remains marked for review; the original product rationale is still unknown.

## Historical notes survive removal of active capture

**Id:** 4b28bd7e-d16b-47a3-a662-2e5d2dfe5b09
**Type:** decision
**Status:** active
**Evidence:** inferred
**Source:** current storage, signing and backup implementation; verification recorded in `project-documents/dev/progress/s13.md`, 2026-10-20
**Verification:** corroborated — migration history and legacy table remain; new snapshots use v2; existing signed hashes are not rewritten
**Revisit when:** a separate historical-data retention or backup-format change is authorized
**See:** registration-flow.md#registration-keeps-the-working-note-after-page-note-removal — f4ca7d60-2b97-490b-a1d3-6ccbb0d3ba60 — as of 2026-10-20

Active page-note capture is removed without dropping legacy note storage or
rewriting existing v1 signed snapshots and hashes. New `signed-encounter-v2`
snapshots omit page notes, and printable reports omit them for both versions.
Backup/restore retains the legacy table; schema head and inventory stay at
`0021` and 32 tables.

**Reason:** retaining historical storage and signed bytes preserves existing
records, immutable hashes and backup compatibility. This is an implementation
rationale inferred from the compatibility boundary, not a supplied product
reason for removing page notes.

**Alternatives:** unknown — no separately authorized data-destruction or
historical snapshot rewrite was established.

## Recent ideation is the live C-SSRS section

**Id:** acf3c233-4ce9-4a90-91ea-19d9ec5104f7
**Type:** decision
**Status:** active
**Evidence:** unknown
**Source:** product instruction, 2026-10-23
**Revisit when:** the live C-SSRS item set changes

Legacy registration step 4 and the follow-up C-SSRS section collect five recent
ideation items (`css_i1_recent` through `css_i5_recent`). Each answer is Yes,
No, or Unknown, and the default is Unknown. Lifetime ideation, intensity,
behavior, and lethality are not collected.

**Reason:** the instruction to use this form is confirmed. No clinical reason
for the cut was stated, so that reason is unknown.

**Alternatives:** unknown — no competing clinical alternative was stated.

## New-case identity and defaults do not rewrite legacy evidence

**Id:** cf51258c-59c4-4f6f-a0c0-d5fb26acb97d
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Revisit when:** a migration of legacy clinical evidence is explicitly authorized

The versioned new-case workflow starts only from explicit birth-year New case
registration. Existing encounters stay legacy; no birth year is reverse-calculated
from stored age, and no legacy Unknown answer becomes No. The new-case registration
year and content identities are frozen independently of later patient status.
New-case C-SSRS defaults to five No answers under the requested new contract;
the legacy Unknown defaults described above are not changed.

**Reason:** the November 9 new-case requirements distinguish a new workflow's
deliberate defaults from historical observations. Retrofitting them would fabricate
evidence and make saved results depend on mutable content or calendar year.

**Rejected alternative:** branch existing drafts on the patient's current status,
or derive birth year from age. Both lose the original registration identity.

## Source suggestions do not erase explicit clinical overrides

**Id:** 835151e2-e7ed-472e-8268-58c9e420d266
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Revisit when:** override or recommendation semantics change

New-case manual Yes and No remain independently attributed even after source
measurements or selected medications change. Automatic mode is a separate explicit
choice. The valproate-specific contraception recommendation and the independent
childbearing-potential recommendation remain separate outputs.

**Reason:** the requested new-case contract gives the physician control over an
effective answer while preserving its automatic suggestion and provenance.
Merging the contraception outputs would suppress an independently applicable
recommendation when only one of its conditions changes.

**Rejected alternative:** overwrite a manual answer on each recomputation, or use
one shared contraception flag. Both collapse explicitly distinct clinical inputs.
