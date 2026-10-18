# Registration flow

## Registration opens on the wizard

**Id:** c6dd360a-d80a-45a2-aa17-7239c4368829
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** product instruction, 2026-10-18
**Revisit when:** registration entry or where demographics notes are saved changes

Register new patient opens the eight-step wizard immediately. Demographics
are entered on step 1, and the demographics page notes and working note are
on that same step. A valid submit still creates the patient and registration
draft together, then opens `#/encounters/:id#register` — the same step, with
the saved demographics and notes that can actually save. There is no second
notes page and no control that opens the draft separately.

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
