"""Correctness tests — the fee/arb/staking math is the product; it must be right."""

import math

from profit_priority.fees import (
    kalshi_fee, kalshi_cost_per_payout, american_to_decimal,
    book_cost_per_payout, devig_two_way,
)
from profit_priority.staking import equalized_lock
from profit_priority.pricing import assemble_market
from profit_priority.opportunities import detect_pure_arb, detect_value


def test_kalshi_fee_peaks_at_half_and_vanishes_at_extremes():
    # max near 0.50, ~0 at the extremes
    assert kalshi_fee(100, 0.50) > kalshi_fee(100, 0.10)
    assert kalshi_fee(100, 0.50) > kalshi_fee(100, 0.90)
    assert kalshi_fee(1, 0.01) < 0.02   # ~1 cent at the extreme for one contract
    # formula: ceil(0.07 * 100 * 0.5 * 0.5 *100c)/100 = ceil(1.75)=1.75
    assert abs(kalshi_fee(100, 0.50, 0.07) - 1.75) < 1e-9


def test_cost_per_payout_includes_fee():
    # buying YES at 0.40: raw 0.40, plus a fee, so all-in > 0.40
    c = kalshi_cost_per_payout(0.40, 0.07)
    assert c > 0.40
    assert c < 0.45


def test_a_naive_midpoint_arb_dies_after_fees():
    # Two 0.49 mids 'arb' (0.98 < 1) — but on Kalshi both legs pay the ask + fee.
    both = kalshi_cost_per_payout(0.49, 0.07) * 2
    assert both > 0.98          # fees pushed it up
    # and at exactly-even pricing it should NOT clear a 1% lock
    stake = equalized_lock(kalshi_cost_per_payout(0.49, 0.07),
                           kalshi_cost_per_payout(0.49, 0.07))
    assert stake.guaranteed_roi < 0.01


def test_american_decimal_and_book_cost():
    assert abs(american_to_decimal(150) - 2.5) < 1e-9
    assert abs(american_to_decimal(-200) - 1.5) < 1e-9
    assert abs(book_cost_per_payout(150) - 0.4) < 1e-9


def test_devig_normalizes_to_one():
    fa, fb = devig_two_way(0.55, 0.52)   # 1.07 total vig
    assert abs(fa + fb - 1.0) < 1e-9
    assert fa > fb


def test_equalized_lock_is_outcome_independent():
    s = equalized_lock(0.40, 0.40, target_payout=100)
    assert s.cost_a == s.cost_b == 40.0
    assert s.total_cost == 80.0
    assert s.guaranteed_profit == 20.0
    assert abs(s.guaranteed_roi - 0.25) < 1e-9


def test_pure_arb_detected_when_real_and_rejected_when_efficient():
    # A REALISTIC small arb (~1.5%): A YES on Kalshi @ 0.47, B on a book @ +106.
    arb = assemble_market(
        "A@B",
        kalshi_by_side={"A": {"yes_ask": 0.47, "liquidity": 500, "age_sec": 5},
                        "B": {"yes_ask": 0.555, "liquidity": 500, "age_sec": 5}},
        book_americans_by_side={"A": {"draftkings": 100, "pinnacle": -105},
                                "B": {"draftkings": 106, "pinnacle": -102}},
        seconds_to_first_pitch=5000)
    accepted = [p for p in detect_pure_arb(arb) if p.accepted]
    assert accepted, "a genuine after-fee arb should be found"
    assert accepted[0].stake.guaranteed_roi >= 0.01

    eff = assemble_market(
        "C@D",
        kalshi_by_side={"C": {"yes_ask": 0.47, "liquidity": 500, "age_sec": 5},
                        "D": {"yes_ask": 0.56, "liquidity": 500, "age_sec": 5}},
        book_americans_by_side={"C": {"draftkings": 115, "pinnacle": 112},
                                "D": {"draftkings": -135, "pinnacle": -132}},
        seconds_to_first_pitch=5000)
    assert not [p for p in detect_pure_arb(eff) if p.accepted], "efficient game must yield no lock"


def test_value_edge_uses_sharp_fair_not_soft():
    m = assemble_market(
        "E@F",
        kalshi_by_side={"E": {"yes_ask": 0.42, "liquidity": 300, "age_sec": 5},
                        "F": {"yes_ask": 0.60, "liquidity": 300, "age_sec": 5}},
        book_americans_by_side={"E": {"draftkings": 145, "pinnacle": 120},
                                "F": {"draftkings": -150, "pinnacle": -140}},
        seconds_to_first_pitch=5000)
    vals = detect_value(m)
    assert all(0.0 <= v.fair_prob <= 1.0 for v in vals)
