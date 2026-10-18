# Registration flow

## Registration opens on the wizard

**Id:** c6dd360a-d80a-45a2-aa17-7239c4368829
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** product instruction, 2026-10-18
**Revisit when:** registration entry or where demographics notes are saved changes
**See:** registration-flow.md#adverse-effects-belong-to-follow-up — 70b5da8e-c401-4b56-97e3-5c11b6d787da — as of 2026-10-18

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

## Adverse effects belong to follow-up

**Id:** 70b5da8e-c401-4b56-97e3-5c11b6d787da
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** product instruction, 2026-10-18
**Revisit when:** the registration or follow-up wizard step list changes
**See:** registration-flow.md#registration-opens-on-the-wizard — c6dd360a-d80a-45a2-aa17-7239c4368829 — as of 2026-10-18

Adverse effects are recorded on a follow-up encounter, not while adding
a new patient. The registration wizard is Register patient, Diagnosis,
PANSS, C-SSRS, History, Medications, Proposal review. Follow-up keeps
Adverse effects between History and Medications. The server refuses a
new effects read, status change, effects-page note, or changed effects
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
