# Follow-up workflow rationale

## New follow-up rules have their own immutable workflow identity

**Id:** 71450c8e-18c9-467c-a914-cf19fc68e911
**Type:** decision
**Status:** active
**Evidence:** inferred
**Source:** product requirements reconciliation, 2026-11-12; `project-documents/dev/follow-up-specification.md`
**Verification:** corroborated — current `cases/encounters.py` and New case signing already separate workflow identity and immutable historical signatures; the new follow-up extension is planned, not implemented
**Revisit when:** migration of an existing draft or historical signature to the new follow-up workflow is explicitly requested

The agreed follow-up redesign applies to newly created encounters through a
distinct version; old drafts and signatures retain their original contracts.
The compatibility choice is confirmed. Its preservation rationale is inferred:
new No defaults, simple side-effect questions and fixed-model decisions have
different meanings from legacy Unknown answers, required questionnaires and
LLM/CPT acceptance. Retrofitting would change what existing records assert.

**Alternative:** apply the new defaults/rules to existing records. The confirmed
scope excludes that migration. No additional product reason was supplied.

Behavior and execution steps live in the
[specification](../project-documents/dev/follow-up-specification.md) and
[implementation plan](../project-documents/dev/follow-up-implementation-plan.md).

## Replacement interaction checks retain the outgoing antipsychotic

**Id:** 2a344eb1-163f-4fe2-83fb-3d64e9e1ba23
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** product clarification, 2026-11-12; `project-documents/dev/follow-up-specification.md`, section 6
**Revisit when:** structured medication discontinuation or a prescribing/cross-taper protocol is introduced

New follow-up replacement candidates are checked against all currently listed
medications, including the main antipsychotic being replaced.

**Reason:** treatment overlap may occur. Excluding the outgoing drug from the
comparison would hide a relevant D/X pair during that overlap.

**Rejected alternative:** compare only with the other ongoing drugs. The chosen
policy keeps the outgoing-drug pair visible rather than assuming discontinuation
is immediate. It does not invent a cross-taper schedule or numeric dose.
