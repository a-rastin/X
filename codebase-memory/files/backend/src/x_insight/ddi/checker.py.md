# backend/src/x_insight/ddi/checker.py

Source SHA-256: 11bfcefa07db2b0ed11c44dec5d791bb8a0ee5baccb395f78a7e6477c7e33184
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Coverage-aware drug–drug interaction checking against an immutable, versioned local release.
The reporting rule: absence of evidence is never presented as absence of interaction.

## Important Symbols
- `SEVERITY_ORDER` — contraindicated > serious > monitor_closely > minor > unknown; release
  version regex `ddi-[0-9a-f]{12}`.
- `_aggregate_pair()` — the coverage core: evidence present → `interaction_found` /
  `reviewed_evidence`; `released_complete` with zero rows → `covered_no_listed_interaction` /
  `reviewed_complete_coverage` (the **only** way absence is claimed); otherwise →
  `coverage_unavailable` / `limited_coverage_unavailable`.
- `check()` — one batched `(release_id, pair_key)` query (no N+1); catalog-only input, unknown
  ids → 422, duplicates collapse to one concept, no self-pairs.
- `latest_release_version()` — configured default or newest with deterministic ties;
  `validate_catalog_selection()` validates without producing a report.

## Behavior and Constraints
- In a limited release **every resolved medication** is listed in
  `coverage_unavailable_medications` — limited scope cannot certify any drug.
- A non-released dataset status returns 503 `DATASET_UNAVAILABLE` rather than an empty report.
- The DDInter profile (`ddinter-positive-assertions/1`) keeps native
  Major/Moderate/Minor/Unknown severities and records that repeated exports are not independent
  corroboration.

## Related Files
- `backend/src/x_insight/ddi/publish.py`, `bootstrap.py`, `router.py` (`POST /ddi/check`).
- `backend/src/x_insight/cases/medications.py` — pins the report the encounter refers to.
