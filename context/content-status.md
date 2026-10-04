# Content status

## Bayesian networks are CPT templates, not executable networks

**Id:** 375d527a-d589-4558-9eef-ac9400655080
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** progress-tracker.md S00, 2026-10-04
**Revisit when:** a network is supplied with complete reviewed CPTs, or registry activation rules change

The 11 supplied Bayesian networks are templates requiring complete
CPTs, not executable networks. All declare
`validation_status=EXPERIMENTAL_TEMPLATE_REQUIRES_CPTS` and
`inference_mode=after_complete_cpt_validation`; BN-04's 13 DEFINITION
blocks are deterministic 0/1 source-lookup tables
(`parameter_status=deterministic_not_patient_risk`), and its own
parameter inventory declares 19 run-specific distributions to supply.
Intended parents survive only as `proposed_parent` properties, which
an ordinary XMLBIF importer ignores. Observed via grep and stdlib
parse in the S00 session; no namespaces assumed.

**Reason:** the networks cannot execute as supplied, so runtime
requires complete LLM-estimated CPTs for every node — no invented
priors or uniform defaults may be added to make them pass.

**Rejected alternative:** filling missing values with uniform
probabilities so a network activates. Rejected — reference tables in
a released artifact must be explicitly reviewed, and owner review
must resolve draft restrictions in a new version rather than flipping
an `inference_enabled` property.

## blockers.md §5.1 DEFINITION counts are stale

**Id:** ca004ade-5e0c-45a9-8c7a-b50325270c95
**Type:** incident
**Status:** active
**Evidence:** confirmed
**Source:** progress-tracker.md S00, 2026-10-04
**Revisit when:** blockers.md is regenerated or the BNs change again

The partial DEFINITION counts reported in blockers.md §5.1 (BN-04
17/15 missing, BN-05 6/14, and so on) do not reproduce against the
current files by either grep or stdlib parse. Root cause recorded in
the progress tracker: blockers.md was committed at 13:33 and the BNs
were re-edited afterwards ("edit BNs", 15:30) — the table describes
the pre-edit revision. The current XML also contains no literal
`inference_allowed=false` / `deployment_allowed=false` flags; gating
is via `EXPERIMENTAL_TEMPLATE_REQUIRES_CPTS` plus the execution
contract.

**Reason:** a reader consulting blockers.md §5.1 for current network
status would be misled; the verified structural status is recorded
above instead.

**Rejected alternative:** treating the §5.1 counts as current.
Rejected — they could not be reproduced by two independent methods
against the files as they exist.
