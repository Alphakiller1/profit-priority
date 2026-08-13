"""Derivative consistency: the checks must FIRE, or a zero result means nothing."""

from __future__ import annotations

import pytest

from profit_priority.derivatives import (
    GameBook,
    Leg,
    check_half_run_identity,
    check_ladders,
    check_spread_nested_in_moneyline,
    check_total_union_bound,
    expected_from_ladder,
)


def _leg(family, side, threshold, bid, ask, ticker="T") -> Leg:
    return Leg(ticker=f"{ticker}-{side}{threshold}", family=family, event="EVT",
               side=side, threshold=threshold, bid=bid, ask=ask)


# ── ladder monotonicity ───────────────────────────────────────────────────────

def test_inverted_ladder_is_caught() -> None:
    """'over 5.5' cannot be more likely than 'over 4.5'."""
    book = GameBook("EVT", [
        _leg("team_total", "TEX", 4.5, 0.30, 0.32),
        _leg("team_total", "TEX", 5.5, 0.60, 0.62),      # impossible
    ])
    v = check_ladders(book)
    assert len(v) == 1
    assert v[0].gross == pytest.approx(0.60 - 0.32)
    assert v[0].actionable


def test_monotone_ladder_is_clean() -> None:
    book = GameBook("EVT", [
        _leg("team_total", "TEX", 4.5, 0.60, 0.62),
        _leg("team_total", "TEX", 5.5, 0.30, 0.32),
    ])
    assert check_ladders(book) == []


# ── spread nested in moneyline ────────────────────────────────────────────────

def test_spread_priced_above_its_moneyline_is_caught() -> None:
    """Winning by over 4.5 implies winning; it cannot be more likely."""
    book = GameBook("EVT", [
        _leg("moneyline", "CHC", None, 0.50, 0.52),
        _leg("spread", "CHC", 4.5, 0.70, 0.72),          # impossible
    ])
    v = check_spread_nested_in_moneyline(book)
    assert len(v) == 1
    assert v[0].gross == pytest.approx(0.70 - 0.52)


def test_spread_below_moneyline_is_clean() -> None:
    book = GameBook("EVT", [
        _leg("moneyline", "CHC", None, 0.60, 0.62),
        _leg("spread", "CHC", 4.5, 0.20, 0.22),
    ])
    assert check_spread_nested_in_moneyline(book) == []


# ── the 0.5 identity ──────────────────────────────────────────────────────────

def test_half_run_identity_fires_in_both_directions() -> None:
    """Scores are integers: 'wins by over 0.5' IS 'wins'. Either can be rich."""
    rich_spread = GameBook("EVT", [
        _leg("moneyline", "BOS", None, 0.50, 0.52),
        _leg("spread", "BOS", 0.5, 0.60, 0.62),
    ])
    rich_ml = GameBook("EVT", [
        _leg("moneyline", "BOS", None, 0.60, 0.62),
        _leg("spread", "BOS", 0.5, 0.50, 0.52),
    ])
    assert len(check_half_run_identity(rich_spread)) == 1
    assert len(check_half_run_identity(rich_ml)) == 1


# ── union bound linking team totals to the game total ─────────────────────────

def test_union_bound_violation_is_caught() -> None:
    """P(A+B > s) <= P(A > a) + P(B > b) whenever a + b >= s.

    Holds under ANY dependence, which matters because runs in one game are not
    independent.
    """
    book = GameBook("EVT", [
        _leg("team_total", "AAA", 4.5, 0.10, 0.12),
        _leg("team_total", "BBB", 4.5, 0.10, 0.12),
        _leg("total", "", 8.5, 0.80, 0.82),              # 0.80 > 0.12 + 0.12
    ])
    v = check_total_union_bound(book)
    assert len(v) == 1
    assert v[0].gross == pytest.approx(0.80 - 0.12 - 0.12)
    assert v[0].actionable


def test_union_bound_respects_the_split_requirement() -> None:
    """A split with a + b < s proves nothing and must not be used."""
    book = GameBook("EVT", [
        _leg("team_total", "AAA", 1.5, 0.10, 0.12),
        _leg("team_total", "BBB", 1.5, 0.10, 0.12),
        _leg("total", "", 8.5, 0.80, 0.82),              # 1.5+1.5 < 8.5
    ])
    assert check_total_union_bound(book) == []


def test_consistent_total_is_clean() -> None:
    book = GameBook("EVT", [
        _leg("team_total", "AAA", 4.5, 0.40, 0.42),
        _leg("team_total", "BBB", 4.5, 0.40, 0.42),
        _leg("total", "", 8.5, 0.30, 0.32),
    ])
    assert check_total_union_bound(book) == []


# ── the expectation identity ──────────────────────────────────────────────────

def test_expectation_identity_on_a_complete_ladder() -> None:
    """E[X] = sum P(X >= k), exact for non-negative integers."""
    rungs = [_leg("team_total", "A", 0.5, 0.79, 0.81),   # mid 0.80
             _leg("team_total", "A", 1.5, 0.49, 0.51),   # mid 0.50
             _leg("team_total", "A", 2.5, 0.19, 0.21)]   # mid 0.20
    assert expected_from_ladder(rungs) == pytest.approx(1.50)


def test_incomplete_ladder_returns_none_rather_than_understating() -> None:
    """Kalshi ladders start near 'over 2.5'; truncating would understate E[X]."""
    rungs = [_leg("team_total", "A", 2.5, 0.19, 0.21),
             _leg("team_total", "A", 3.5, 0.09, 0.11)]
    assert expected_from_ladder(rungs) is None


def test_gapped_ladder_returns_none() -> None:
    rungs = [_leg("team_total", "A", 0.5, 0.79, 0.81),
             _leg("team_total", "A", 2.5, 0.19, 0.21)]   # missing 1.5
    assert expected_from_ladder(rungs) is None
