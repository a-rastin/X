# backend/src/x_insight/cases/follow_up_plans.py

Source SHA-256: 44a9226fb8f3deb4d6830e8b70c2a8b0d8f80baeb1591600023b2bef37704154

## Purpose
Immutable follow-up proposal persistence, current freshness and replay-safe atomic v5 signing without a worker/provider.

## Behavior and constraints
`verified_inputs` re-hashes pinned content, installed model and assessment
bytes and checks DDI/catalog identities. Its fingerprint includes raw/effective
inputs, baseline, demographics and physician availability, excluding final text
and presentation language. `generate_proposal` deduplicates by immutable input
and content hashes and stores the full distribution, exclusions and sections.
`context` locks account before patient/encounter, matching deactivation discard;
`sign` then locks final text, rechecks completeness/freshness/outcome and writes
one v5 signature plus lifecycle/audit atomically. Identical confirmations replay.
`read_proposal` prefers a matching current fingerprint and exposes
`signed_patient_id` only for owned signed encounters to recover lost responses.

## Evidence
Current source and `backend/tests/http/test_follow_up_plans.py`; see the B/F/G progress checkpoint.
