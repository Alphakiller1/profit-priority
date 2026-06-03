"""
Stake sizing — size two legs so the net profit is the SAME whichever side wins
(a true lock), after the all-in cost of each leg.

We work in "cost per $1 of payout" space (see fees.ExecPrice.cost_per_payout),
which unifies Kalshi contracts and sportsbook decimal odds: buy enough of each
side to return the same target payout, and the guaranteed profit is
`target_payout - total_cost`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArbStake:
    target_payout: float       # the equal $ each side returns if it wins
    cost_a: float              # all-in $ outlay on side A
    cost_b: float              # all-in $ outlay on side B
    total_cost: float          # cost_a + cost_b (the bankroll at risk)
    guaranteed_profit: float   # target_payout - total_cost
    guaranteed_roi: float      # guaranteed_profit / total_cost


def equalized_lock(cost_pp_a: float, cost_pp_b: float, target_payout: float = 100.0) -> ArbStake:
    """Lock both sides to return `target_payout` each.

    cost_pp_* = all-in cost per $1 payout for each side (fees already inside).
    Because each side returns the same payout, whichever wins yields the same net,
    so guaranteed_profit/roi are outcome-independent.
    """
    cost_a = cost_pp_a * target_payout
    cost_b = cost_pp_b * target_payout
    total = cost_a + cost_b
    profit = target_payout - total
    roi = profit / total if total > 0 else 0.0
    return ArbStake(target_payout, round(cost_a, 2), round(cost_b, 2),
                    round(total, 2), round(profit, 2), round(roi, 4))


def kalshi_contracts_for_payout(target_payout: float) -> int:
    """Kalshi contracts needed to return `target_payout` (1 contract pays $1)."""
    return max(1, round(target_payout))


def book_stake_for_payout(target_payout: float, decimal_odds: float) -> float:
    """Sportsbook stake to return `target_payout` total (stake * decimal = payout)."""
    return round(target_payout / decimal_odds, 2) if decimal_odds > 0 else 0.0
