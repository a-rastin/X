"""Deterministic CPT redistribution (S48a, seam T5).

System-design.md §8.2 (FR-52) + plan.md §9.1: integer-unit math only
(no float drift), decimal strings, state-order tie break.

For a row with units ``u[1..n]``, selected state ``k``, selected target
``X``, total ``T=100_000_000``:

1. Validate ``0 <= X <= T`` (422, never repaired).
2. ``n == 1``: only ``T`` is valid.
3. ``R = T - X``, ``S = sum(u[j] for j != k)`` from the immediately
   preceding committed row.
4. ``S > 0``: ideal ``R*u[j]/S``; ``S == 0``: ``R/(n-1)``.
5. Floor ideals, allocate remaining single units by descending fractional
   remainder, ties by pinned network state order (declared ``states``
   order, i.e. ascending row index — the row order already equals that
   order via S23 validation).
6. Selected state stays exactly ``X``; row total is exactly ``T``.

Examples: ``[20,30,50]`` first to 40 → ``[40,22.5,37.5]``;
``[100,0,0]`` first to 40 → ``[40,30,30]``; four-state ``[100,0,0,0]``
first to 0 → ``[0,33.333334,33.333333,33.333333]`` in declared order.
"""

from __future__ import annotations

import re
from typing import Any

from x_insight.models.inference import MAX_DECIMAL_PLACES, UNITS_FOR_100_PCT

#: Redistribution rule version (persisted on every revision for audit).
REDISTRIBUTION_RULE_VERSION = "redistribution-v1"

#: Alias kept local so callers need not import the models package.
UNITS_FOR_100 = UNITS_FOR_100_PCT

_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_NONFINITE_TOKENS = frozenset(
    {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
)


class RedistributionError(ValueError):
    """Invalid redistribution input (caller maps to 422, never repaired)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message[:500]


def parse_percentage_to_units(value: Any) -> int:
    """Parse a decimal-string percentage to integer units (no float coercion).

    Up to six decimal places, ``0``..``100`` inclusive. Booleans, nulls,
    floats, nonfinite tokens, malformed decimals, excess precision and
    out-of-range values raise :class:`RedistributionError` (422).
    """
    if isinstance(value, bool):
        raise RedistributionError("boolean_percentage", f"Value {value!r} is not a decimal string.")
    if value is None:
        raise RedistributionError("null_percentage", "Percentage must be a decimal string.")
    if isinstance(value, float):
        import math as _math

        if not _math.isfinite(value):
            raise RedistributionError("nonfinite_percentage", f"Value {value!r} is nonfinite.")
        raise RedistributionError(
            "malformed_percentage", f"Value {value!r} is not a decimal string."
        )
    if not isinstance(value, str):
        raise RedistributionError(
            "malformed_percentage", f"Value {value!r} is not a decimal string."
        )
    token = value
    if not token:
        raise RedistributionError("malformed_percentage", "Percentage must not be empty.")
    if token.lower() in _NONFINITE_TOKENS:
        raise RedistributionError("nonfinite_percentage", f"Value {token!r} is nonfinite.")
    if not _DECIMAL_RE.match(token):
        raise RedistributionError(
            "malformed_percentage", f"Value {token!r} is not a decimal string."
        )
    body = token[1:] if token.startswith("-") else token
    if "." in body:
        frac = body.split(".", 1)[1]
        if len(frac) > MAX_DECIMAL_PLACES:
            raise RedistributionError("excess_precision", f"Value {token!r} exceeds six decimals.")
    negative = token.startswith("-")
    digits = body.replace(".", "")
    decimals = len(body.split(".", 1)[1]) if "." in body else 0
    try:
        raw = int(digits) if digits else 0
    except ValueError as exc:
        raise RedistributionError("malformed_percentage", f"Value {token!r} is malformed.") from exc
    units = raw * (10 ** (MAX_DECIMAL_PLACES - decimals))
    if negative:
        units = -units
    if units < 0 or units > UNITS_FOR_100_PCT:
        raise RedistributionError("percentage_out_of_range", f"Value {token!r} is outside 0..100.")
    return units


def format_units_to_percentage(units: int) -> str:
    """Format integer units as a canonical decimal-string percentage.

    Minimal form (trailing zeros trimmed, up to six decimals):
    ``40_000_000`` → ``"40"``, ``22_500_000`` → ``"22.5"``,
    ``33_333_334`` → ``"33.333334"``.
    """
    if units < 0 or units > UNITS_FOR_100_PCT:
        raise RedistributionError("percentage_out_of_range", f"Units {units} out of range.")
    whole = units // 1_000_000
    rest = units % 1_000_000
    if rest == 0:
        return str(whole)
    frac = f"{rest:06d}".rstrip("0")
    return f"{whole}.{frac}"


def redistribute_row(
    preceding_units: list[int],
    selected_index: int,
    target_units: int,
) -> list[int]:
    """Redistribute one row deterministically (integer math only).

    ``preceding_units`` are the immediately preceding committed row units
    in declared state order; ``selected_index`` is the edited state;
    ``target_units`` is the validated selected target ``X``. Returns the
    new units (selected exactly ``X``, total exactly ``T``). Other rows
    are untouched by the caller. Ties break by ascending index, which
    equals pinned declared state order (S23 preserves it).
    """
    n = len(preceding_units)
    if n == 0:
        raise RedistributionError("empty_row", "Row must hold at least one state.")
    if not 0 <= selected_index < n:
        raise RedistributionError("unknown_state", "Selected state is outside the row.")
    if target_units < 0 or target_units > UNITS_FOR_100_PCT:
        raise RedistributionError("percentage_out_of_range", "Target is outside 0..100.")
    if n == 1:
        if target_units != UNITS_FOR_100_PCT:
            raise RedistributionError(
                "single_state_locked",
                "Single-state rows remain at 100%.",
            )
        return [UNITS_FOR_100_PCT]
    for units in preceding_units:
        if units < 0 or units > UNITS_FOR_100_PCT:
            raise RedistributionError("percentage_out_of_range", "Preceding row is out of range.")
    total = UNITS_FOR_100_PCT
    remaining = total - target_units
    others = [i for i in range(n) if i != selected_index]
    preceding_others = [preceding_units[i] for i in others]
    other_total = sum(preceding_others)
    floors: list[int] = []
    remainders: list[int] = []
    if other_total > 0:
        for units in preceding_others:
            numerator = remaining * units
            floors.append(numerator // other_total)
            remainders.append(numerator % other_total)
        # Descending remainder, ties by declared order (ascending index).
        order = sorted(range(len(others)), key=lambda pos: (-remainders[pos], others[pos]))
    else:
        count = n - 1
        floor = remaining // count
        rem = remaining % count
        floors = [floor] * count
        remainders = [rem] * count
        # All remainders equal → declared order decides.
        order = sorted(range(len(others)), key=lambda pos: others[pos])
    leftover = remaining - sum(floors)
    if leftover < 0 or leftover > len(others):
        raise RedistributionError("inexact_total", "Redistribution cannot total exactly 100%.")
    extra = [0] * len(others)
    for pos in order[:leftover]:
        extra[pos] = 1
    new_units = [0] * n
    new_units[selected_index] = target_units
    for pos, idx in enumerate(others):
        new_units[idx] = floors[pos] + extra[pos]
    if sum(new_units) != total:
        raise RedistributionError("inexact_total", "Redistributed row does not total 100%.")
    for units in new_units:
        if units < 0 or units > total:
            raise RedistributionError(
                "percentage_out_of_range", "Redistributed value out of range."
            )
    return new_units
