"""Probability review domain (S48a, seams T1/T5).

Plan.md §9.1 + system-design.md §8.2 (FR-50–52, FR-56, FR-42, NFR-04–05):
author-only CPT review with deterministic redistribution and immutable
revision persistence. Local recalculation/reset/retry arrive in S48b;
freshness regeneration in S48d; signing in S49 — none of those live here.
"""

from x_insight.probability_review import redistribution  # noqa: F401  # public T5 seam
