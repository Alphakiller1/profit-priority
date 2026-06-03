"""
Execution-cost model — the correctness core of the whole engine.

The original tracker priced opportunities on midpoints with no fees, so its
"arbs" were phantom: a 1.8% gross edge on mids is a LOSS once you pay the Kalshi
ask, the worse book number, and Kalshi's per-contract fee. Everything here exists
to price the *executable* cost of a position, so a "guaranteed profit" is real.

Kalshi trading fee (taker), per the published schedule:

    fee = ceil( fee_rate * C * P * (1 - P) )   rounded UP to the next cent

where C = contracts, P = price in dollars (0..1). The rate is market-specific
(0.07 is the general default; some series differ), so it's configurable — set the
exact rate for the market you actually trade. There is no separate settlement fee
modeled (Kalshi charges on trades, not settlement, on current series).

Sportsbooks charge no per-bet fee; their "cost" is the vig baked into the price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ── Kalshi ────────────────────────────────────────────────────────────────────
DEFAULT_KALSHI_FEE_RATE = 0.07   # general taker rate; override per market


def kalshi_fee(contracts: float, price: float, fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """Total Kalshi trading fee (dollars) for an order, rounded up to the cent.

    The fee is maximal near $0.50 (p*(1-p)=0.25) and vanishes at the extremes —
    which is exactly where naive ~50/50 'arbs' get eaten alive.
    """
    if contracts <= 0 or not (0.0 < price < 1.0):
        return 0.0
    raw = fee_rate * contracts * price * (1.0 - price)
    # round to a sub-cent before ceiling so float noise (1.75 -> 1.7500…03) doesn't
    # spuriously round the fee up an extra cent.
    return math.ceil(round(raw * 100.0, 6)) / 100.0


def kalshi_exec_cost(contracts: float, price: float, fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """All-in cost to BUY `contracts` at `price` on Kalshi (stake + fee)."""
    return contracts * price + kalshi_fee(contracts, price, fee_rate)


def kalshi_cost_per_payout(price: float, fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """All-in cost per $1 of guaranteed payout (1 contract pays $1 if it wins).

    This is the number to compare against the other leg in an arb: it already
    includes the fee, so summing both legs' cost-per-payout and checking < 1 is a
    true after-fee arbitrage test.
    """
    if not (0.0 < price < 1.0):
        return float("inf")
    return price + kalshi_fee(1.0, price, fee_rate)


# ── Sportsbooks ───────────────────────────────────────────────────────────────
def american_to_decimal(american: int | float) -> float:
    a = float(american)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def decimal_to_implied(decimal_odds: float) -> float:
    """Implied probability (with vig) — also the cost per $1 payout at a book."""
    return 1.0 / decimal_odds if decimal_odds > 0 else float("inf")


def book_cost_per_payout(american: int | float) -> float:
    """Sportsbook cost per $1 of payout for a side (no per-bet fee; vig is in the price)."""
    return decimal_to_implied(american_to_decimal(american))


def devig_two_way(p_a: float, p_b: float) -> tuple[float, float]:
    """Remove vig from a two-way market's implied probs (proportional / multiplicative).

    Use a SHARP book (Pinnacle) for the fair-probability reference, never a soft
    consensus — soft books carry their lean into the 'fair' number.
    """
    total = p_a + p_b
    if total <= 0:
        return (float("nan"), float("nan"))
    return (p_a / total, p_b / total)


@dataclass(frozen=True)
class ExecPrice:
    """One executable side: what it actually costs per $1 payout, all-in."""
    venue: str            # 'kalshi' | book key
    selection: str
    cost_per_payout: float  # all-in cost (fees included) to return $1 if it wins
    raw_price: float        # ask (kalshi $) or implied prob (book)
    age_sec: float | None = None
    liquidity: float | None = None  # contracts/volume available (kalshi); None for books
