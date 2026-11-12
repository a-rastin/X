# Follow-up Package D partial checkpoint — 2026-11-12

Base revision `cd9115b7bf0b50a11c10259be767835e2e31f185`; changes are
uncommitted. Pure follow-up decisions now cover oral recovery/adherence paths,
effect priority and adjuncts, unsupported treatment review, active questions,
16 factor values and source details, and independent plan sections. Preview and
step validation use the same decision path and require unresolved model factors
only on an oral-replacement branch. Current pregnancy answers now contribute to
the effective flag. No model, provider or database is used by these rules.

Verification: 64 database-free tests passed across follow-up rules, state,
baseline and content; Ruff and mypy passed. No database was migrated. This is a
partial checkpoint: the complete prescribed behavior matrix has not been
individually exercised, and D's decisions have not been integrated with E's
availability/DDI exclusions or F's proposal/signature persistence. In particular,
E must recompute timing after an exclusion or no eligible candidate, and retain
the distinction between the original and final action. The activation gate stays
off until F/G. Packages A–C remain partial as their checkpoints describe; E–H
remain open. No commit or deployment was made.
