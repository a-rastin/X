"""Persist complete CPT adjustments: deterministic redistribution (S48a, seam T5).

Pure seam T5 only: ``redistribute_row`` + ``parse/format`` from
``x_insight.probability_review.redistribution`` (integer units, ``T=100_000_000``,
floor + largest-remainder, ties by declared state order). No HTTP, no DB,
no provider/MCP. Expected values are independent worked literals from
tasks.md S48a §§2-3, never implementation output, never mirrored
private helpers.

Covers tasks.md S48a §§2-3:
- [20,30,50] first to 40 -> [40,22.5,37.5] (proportional from preceding).
- [100,0,0] first to 40 -> [40,30,30] (zero-other-total equal split).
- Four-state [100,0,0,0] first to 0 -> [0,33.333334,33.333333,33.333333]
  in declared order (largest-remainder tie determinism).
- Single-state 100% locked; 0/100 endpoints; equal split; tie determinism;
  invalid target/precision rejected (never repaired).
"""

from __future__ import annotations

import pytest

from x_insight.probability_review.redistribution import (
    RedistributionError,
    format_units_to_percentage,
    parse_percentage_to_units,
    redistribute_row,
)

T = 100_000_000


def units(*percentages: str) -> list[int]:
    return [parse_percentage_to_units(p) for p in percentages]


def strings(values: list[int]) -> list[str]:
    return [format_units_to_percentage(v) for v in values]


def test_proportional_20_30_50_first_to_40() -> None:
    """S48a §2: [20,30,50] edited first to 40 yields [40,22.5,37.5]."""
    preceding = units("20", "30", "50")
    assert preceding == [20_000_000, 30_000_000, 50_000_000]
    target = parse_percentage_to_units("40")
    assert target == 40_000_000
    result = redistribute_row(preceding, 0, target)
    assert result == [40_000_000, 22_500_000, 37_500_000]
    assert strings(result) == ["40", "22.5", "37.5"]
    assert sum(result) == T
    assert result[0] == target


def test_zero_other_total_equal_split_100_0_0_first_to_40() -> None:
    """S48a §2: [100,0,0] first to 40 yields [40,30,30] (equal split)."""
    preceding = units("100", "0", "0")
    result = redistribute_row(preceding, 0, parse_percentage_to_units("40"))
    assert result == [40_000_000, 30_000_000, 30_000_000]
    assert strings(result) == ["40", "30", "30"]
    assert sum(result) == T


def test_four_state_first_to_zero_declared_order() -> None:
    """S48a §3: [100,0,0,0] first to 0 yields ordered tie-break split."""
    preceding = units("100", "0", "0", "0")
    result = redistribute_row(preceding, 0, parse_percentage_to_units("0"))
    assert result == [0, 33_333_334, 33_333_333, 33_333_333]
    assert strings(result) == ["0", "33.333334", "33.333333", "33.333333"]
    assert sum(result) == T
    # Declared order decides: the first other state holds the single extra unit.
    assert result[1] == result[2] + 1
    assert result[2] == result[3]


def test_single_state_locked_at_100() -> None:
    """S48a §3: single-state rows remain exactly at 100%."""
    assert redistribute_row([T], 0, T) == [T]
    assert strings([T]) == ["100"]
    for bad in ("0", "99", "50", "99.999999"):
        with pytest.raises(RedistributionError):
            redistribute_row([T], 0, parse_percentage_to_units(bad))


def test_endpoints_zero_and_100() -> None:
    """S48a §3: 0/100 endpoints keep the selected value and total exactly."""
    preceding = units("20", "30", "50")
    to_zero = redistribute_row(preceding, 0, parse_percentage_to_units("0"))
    assert to_zero == [0, 37_500_000, 62_500_000]
    assert strings(to_zero) == ["0", "37.5", "62.5"]
    to_full = redistribute_row(preceding, 0, parse_percentage_to_units("100"))
    assert to_full == [T, 0, 0]
    assert strings(to_full) == ["100", "0", "0"]
    # Two-state endpoints on the last state.
    two = units("80", "20")
    assert redistribute_row(two, 1, parse_percentage_to_units("0")) == [T, 0]
    assert redistribute_row(two, 1, parse_percentage_to_units("100")) == [0, T]
    for row in (to_zero, to_full):
        assert sum(row) == T


def test_zero_other_total_equal_split_last_state() -> None:
    """S48a §3: zero-other-total splits evenly regardless of selected index."""
    preceding = units("0", "0", "100")
    result = redistribute_row(preceding, 2, parse_percentage_to_units("40"))
    assert result == [30_000_000, 30_000_000, 40_000_000]
    assert strings(result) == ["30", "30", "40"]
    assert sum(result) == T


def test_largest_remainder_tie_determinism_proportional() -> None:
    """S48a §3: equal remainders break by declared order, deterministically."""
    preceding = units("60", "20", "20")
    target = parse_percentage_to_units("33.333333")
    first = redistribute_row(preceding, 0, target)
    second = redistribute_row(list(preceding), 0, target)
    assert first == second
    assert first == [33_333_333, 33_333_334, 33_333_333]
    assert strings(first) == ["33.333333", "33.333334", "33.333333"]
    assert sum(first) == T
    # The equal-remainder losers stay in declared order on repeat.
    for _ in range(3):
        assert redistribute_row(list(preceding), 0, target) == first


def test_largest_remainder_tie_determinism_equal_split() -> None:
    """S48a §3: all-equal remainders allocate extras in declared order."""
    preceding = units("100", "0", "0", "0")
    first = redistribute_row(preceding, 0, parse_percentage_to_units("0"))
    assert redistribute_row(list(preceding), 0, parse_percentage_to_units("0")) == first
    # Middle-state selection keeps the selected value and totals exactly.
    middle = redistribute_row(preceding, 2, parse_percentage_to_units("40"))
    assert middle[2] == 40_000_000
    assert sum(middle) == T
    assert middle == [60_000_000, 0, 40_000_000, 0]


def test_selected_value_always_exact_and_total_exact() -> None:
    """S48a §§2-3: selected stays exactly X and every row totals exactly T."""
    cases: list[tuple[list[str], int, str]] = [
        (["20", "30", "50"], 1, "40"),
        (["80", "20"], 0, "60"),
        (["90", "10"], 1, "40"),
        (["100", "0", "0"], 1, "25"),
    ]
    for raw, index, target_raw in cases:
        preceding = units(*raw)
        target = parse_percentage_to_units(target_raw)
        result = redistribute_row(preceding, index, target)
        assert result[index] == target
        assert sum(result) == T
        assert all(0 <= v <= T for v in result)


def test_invalid_target_rejected_not_repaired() -> None:
    """S48a §3: invalid targets fail rather than being repaired silently."""
    preceding = units("20", "30", "50")
    for bad_target in (-1, T + 1, -100, 100_000_001):
        with pytest.raises(RedistributionError):
            redistribute_row(preceding, 0, bad_target)
    with pytest.raises(RedistributionError):
        redistribute_row([], 0, parse_percentage_to_units("40"))
    with pytest.raises(RedistributionError):
        redistribute_row(preceding, 3, parse_percentage_to_units("40"))
    with pytest.raises(RedistributionError):
        redistribute_row(preceding, -1, parse_percentage_to_units("40"))


def test_invalid_precision_and_shape_rejected() -> None:
    """S48a §3: excess precision, malformed, boolean/null/float/nonfinite fail."""
    for bad in ("22.1234567", "0.0000001", "200", "-1", "", "abc", " 40", "40 "):
        with pytest.raises(RedistributionError):
            parse_percentage_to_units(bad)
    for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "+Infinity"):
        with pytest.raises(RedistributionError):
            parse_percentage_to_units(bad)
    for bad in (True, False, None, 40, 40.0, ["40"], {"v": "40"}):  # type: ignore[list-item]
        with pytest.raises(RedistributionError):
            parse_percentage_to_units(bad)  # type: ignore[arg-type]
    # Canonical formatting is minimal (no trailing zeros, up to six decimals).
    assert format_units_to_percentage(40_000_000) == "40"
    assert format_units_to_percentage(22_500_000) == "22.5"
    assert format_units_to_percentage(33_333_334) == "33.333334"
    assert format_units_to_percentage(33_333_333) == "33.333333"
    with pytest.raises(RedistributionError):
        format_units_to_percentage(-1)
    with pytest.raises(RedistributionError):
        format_units_to_percentage(T + 1)
