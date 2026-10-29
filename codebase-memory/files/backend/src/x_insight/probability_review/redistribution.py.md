# backend/src/x_insight/probability_review/redistribution.py

Source SHA-256: 26e235317038d477350130d026fc8ced77954bac5ede227a17f686f648a3b4bb
Source commit: bad418cecdf013317df56751fec2c4a0158b305d

## Purpose
Deterministic integer-only redistribution of a CPT row after a slider change, so the result is
reproducible and exactly totals 100 %.

## Important Symbols
- `REDISTRIBUTION_RULE_VERSION = "redistribution-v1"`; units `UNITS_FOR_100_PCT` (1e8),
  ≤6 decimal places.
- `parse_percentage_to_units()` — booleans, floats, nulls, non-finite tokens and excess
  precision all raise (422); values are never quietly repaired.
- `redistribute_row()` — floors the ideal allocation, then distributes leftover single units by
  descending fractional remainder with **ties broken by pinned declared state order**;
  single-state rows are locked at 100; the result must total exactly `T`.

## Behavior and Constraints
- This is the T5 seam referenced across the reasoning docs; the same integer policy is what
  `models.inference.validate_cpts` enforces, so an accepted table always satisfies inference
  validation.

## Related Files
- `backend/src/x_insight/probability_review/service.py` — persists the resulting revision.
- `backend/src/x_insight/models/inference.py` — validates every row at exactly 100 000 000 units.
