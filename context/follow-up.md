# Follow-up workflow rationale

## New follow-up rules have their own immutable workflow identity

**Id:** 71450c8e-18c9-467c-a914-cf19fc68e911
**Type:** decision
**Status:** active
**Evidence:** inferred
**Source:** product requirements reconciliation, 2026-11-12; `project-documents/dev/follow-up-specification.md`
**Verification:** corroborated — current `cases/encounters.py`, `follow_up_plans.py` and migration `0032` separate follow-up identity and v5 signatures from the preserved legacy/v4 contracts
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

## Research development does not depend on medical or source approval

**Id:** 2f4778d9-3dc5-456b-bd98-37d12955f814
**Type:** decision
**Status:** active
**Evidence:** confirmed
**Source:** maintainer instruction, 2026-11-12; `project-documents/dev/follow-up-specification.md`, research development policy
**Revisit when:** the maintainer explicitly changes the research-development policy

Follow-up implementation and medical-content changes have no legal confirmation,
author-evidence submission, source-attestation or external medical-approval gate.
The maintainer chose this because the application is a research app and such
requirements obstruct making or changing it. Inherited content-review dossiers
are historical context rather than prerequisites for this work. References and
rationale classifications describe the record without asking its author to prove
or confirm medical knowledge. Automated integrity checks and the selected visit
inputs/signing behavior remain separate engineering and application contracts.

**Rejected alternative:** make each new content version wait for source or
clinical sign-off before implementation or activation.
