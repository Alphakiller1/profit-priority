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
# Maker fees are charged only when a RESTING order ultimately executes, at a
# fraction of the taker rate. This is not a footnote: measured 2026-08-12, pre-game
# price movement on Kalshi runs only ~1.5-3.5 probability points, while a taker
# round trip costs ~3.5c at mid prices. Directional capture therefore does not
# clear costs as a taker and only works as a maker (~0.9c round trip). Any model
# without a maker rate cannot represent the one strategy the data supports.
DEFAULT_KALSHI_MAKER_RATIO = 0.25
# The July 2026 schedule revision added a per-series multiplier defaulting to 1.
DEFAULT_KALSHI_FEE_MULTIPLIER = 1.0


def kalshi_fee(contracts: float, price: float,
               fee_rate: float = DEFAULT_KALSHI_FEE_RATE, *,
               maker: bool = False,
               maker_ratio: float = DEFAULT_KALSHI_MAKER_RATIO,
               multiplier: float = DEFAULT_KALSHI_FEE_MULTIPLIER) -> float:
    """Total Kalshi trading fee (dollars) for an order, rounded up to the cent.

    The fee is maximal near $0.50 (p*(1-p)=0.25) and vanishes at the extremes —
    which is exactly where naive ~50/50 'arbs' get eaten alive.

    `maker=True` prices a resting order that later fills. Note the ceiling is
    applied to the ORDER total, so small orders are penalised: 1 contract at 50c
    pays 4.00% of notional versus 3.50% asymptotic. Size is part of the edge.
    """
    if contracts <= 0 or not (0.0 < price < 1.0):
        return 0.0
    rate = fee_rate * multiplier * (maker_ratio if maker else 1.0)
    raw = rate * contracts * price * (1.0 - price)
    # round to a sub-cent before ceiling so float noise (1.75 -> 1.7500…03) doesn't
    # spuriously round the fee up an extra cent.
    return math.ceil(round(raw * 100.0, 6)) / 100.0


def kalshi_round_trip(price: float, contracts: float = 100, *,
                      entry_maker: bool = False, exit_maker: bool = False,
                      fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """
    Fees to buy AND later sell the same contracts — i.e. flat before settlement.

    This is the bar any price-movement strategy must clear. A position held to
    resolution pays only the entry leg (Kalshi charges no settlement fee).
    """
    return (kalshi_fee(contracts, price, fee_rate, maker=entry_maker)
            + kalshi_fee(contracts, price, fee_rate, maker=exit_maker))


def kalshi_fee_pct_of_notional(price: float, contracts: float = 100, *,
                               maker: bool = False,
                               fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """Fee as a fraction of capital deployed — the number that kills small orders."""
    if contracts <= 0 or not (0.0 < price < 1.0):
        return 0.0
    return kalshi_fee(contracts, price, fee_rate, maker=maker) / (contracts * price)


def two_leg_breakeven(price_a: float, price_b: float, contracts: float = 100, *,
                      maker_a: bool = False, maker_b: bool = False,
                      fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """
    Max combined leg cost (per $1 payout) that still locks a profit when BOTH legs
    are on Kalshi and held to settlement.

    The naive test is `cost_a + cost_b < 1`. The real bar lands near 0.965 at mid
    prices — and near 0.9825 when only one leg is on Kalshi, because a book's vig
    is already inside its price. Testing against 1.00 is what made the original
    tracker's arbs phantom.
    """
    fees = (kalshi_fee(contracts, price_a, fee_rate, maker=maker_a)
            + kalshi_fee(contracts, price_b, fee_rate, maker=maker_b))
    return 1.0 - fees / max(contracts, 1)


def one_leg_breakeven(kalshi_price: float, contracts: float = 100, *,
                      maker: bool = False,
                      fee_rate: float = DEFAULT_KALSHI_FEE_RATE) -> float:
    """Same, for a cross-venue arb where only the Kalshi leg carries an explicit fee."""
    return 1.0 - kalshi_fee(contracts, kalshi_price, fee_rate,
                            maker=maker) / max(contracts, 1)


def prob_to_american(p: float | None) -> int | None:
    """Implied probability -> American odds, for surfaces that display both."""
    if p is None:
        return None
    p = float(p)
    if not 0.0 < p < 1.0:
        return None
    return round(-100.0 * p / (1.0 - p)) if p >= 0.5 else round(100.0 * (1.0 - p) / p)


def fmt_american(p: float | None) -> str:
    a = prob_to_american(p)
    return "–" if a is None else f"{a:+d}"


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
