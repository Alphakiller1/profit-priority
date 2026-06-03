"""
Opportunity detection — the three classes, all priced on EXECUTABLE cost after
fees and slippage. Every candidate (accepted or rejected) carries its reject
reasons so the learning loop can log the whole funnel, not just the winners.

  A. PURE ARB           — both legs executable now → guaranteed profit after fees.
  B. CROSS-VENUE VALUE  — one side cheap vs the de-vigged SHARP fair price (+EV, not a lock).
  C. MANUFACTURED ARB   — staged: enter early, hedge after the expected move (R&D; logged, not a lock).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config
from .fees import kalshi_cost_per_payout, book_cost_per_payout
from .pricing import GameMarket, SideQuote, is_stale
from .staking import equalized_lock, ArbStake


# ── A. Pure arbitrage ─────────────────────────────────────────────────────────
@dataclass
class PureArb:
    game: str
    market_type: str
    leg_a: str                 # "SEL @ venue (price)"
    leg_b: str
    stake: ArbStake
    thin: bool                 # used the higher thin/stale ROI threshold
    warnings: list[str] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.reject_reasons


def _leg_cost(side: SideQuote, venue: str) -> Optional[float]:
    """All-in cost-per-payout for buying `side` on `venue`, incl. fees + slippage."""
    if venue == "kalshi":
        if side.kalshi_yes_ask is None:
            return None
        return kalshi_cost_per_payout(side.kalshi_yes_ask, config.KALSHI_FEE_RATE) + config.SLIPPAGE_BUFFER
    if side.book_decimal is None:
        return None
    return book_cost_per_payout_from_decimal(side.book_decimal) + config.SLIPPAGE_BUFFER


def book_cost_per_payout_from_decimal(decimal_odds: float) -> float:
    return 1.0 / decimal_odds if decimal_odds > 0 else float("inf")


def detect_pure_arb(gm: GameMarket) -> list[PureArb]:
    """Buy one side on the cheaper venue, the other on the other venue; lock if
    total executable cost-per-payout < 1 by enough to clear the ROI threshold."""
    pair = gm.two_sides()
    if not pair:
        return []
    a, b = pair
    out: list[PureArb] = []

    # Two ways to split the legs across venues.
    for (sa, va), (sb, vb) in (((a, "kalshi"), (b, "book")), ((a, "book"), (b, "kalshi"))):
        ca, cb = _leg_cost(sa, va), _leg_cost(sb, vb)
        if ca is None or cb is None:
            continue
        stake = equalized_lock(ca, cb, target_payout=100.0)

        # liquidity / staleness drive the threshold + warnings
        kal_side = sa if va == "kalshi" else sb
        thin = (kal_side.kalshi_liquidity is not None
                and kal_side.kalshi_liquidity < config.MIN_KALSHI_LIQUIDITY)
        min_roi = config.MIN_THIN_ARB_ROI if thin else config.MIN_PURE_ARB_ROI

        rejects, warns = [], []
        if stake.guaranteed_roi < min_roi:
            rejects.append(f"roi {stake.guaranteed_roi:.3%} < min {min_roi:.1%}")
        if abs((ca - config.SLIPPAGE_BUFFER) + (cb - config.SLIPPAGE_BUFFER) - 1.0) > config.MAX_PLAUSIBLE_GAP:
            rejects.append("cross-venue gap implausibly large (likely stale/mismatched line)")
        for s, v in ((sa, va), (sb, vb)):
            age = s.kalshi_age_sec if v == "kalshi" else s.book_age_sec
            if is_stale(age):
                rejects.append(f"{s.selection}@{v} price stale ({age:.0f}s)")
        if gm.seconds_to_first_pitch is not None and gm.seconds_to_first_pitch < config.MIN_SECONDS_TO_FIRST_PITCH:
            rejects.append("too close to first pitch to execute both legs")
        if thin:
            warns.append("thin Kalshi liquidity — fill risk")

        la = f"{sa.selection}@{va}({sa.kalshi_yes_ask if va=='kalshi' else sa.book_decimal})"
        lb = f"{sb.selection}@{vb}({sb.kalshi_yes_ask if vb=='kalshi' else sb.book_decimal})"
        out.append(PureArb(gm.game, gm.market_type, la, lb, stake, thin, warns, rejects))
    return out


# ── B. Cross-venue value ──────────────────────────────────────────────────────
@dataclass
class ValueEdge:
    game: str
    selection: str
    venue: str
    exec_cost: float          # all-in cost per $1 payout
    fair_prob: float          # de-vigged sharp fair probability
    edge: float               # fair_prob - exec_cost  (>0 = +EV)
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.reject_reasons


def detect_value(gm: GameMarket) -> list[ValueEdge]:
    """A single side whose cheapest executable cost is below the SHARP fair price."""
    out: list[ValueEdge] = []
    for sel, side in gm.sides.items():
        if side.fair_prob is None:
            continue
        options = []
        if side.kalshi_yes_ask is not None:
            options.append(("kalshi", kalshi_cost_per_payout(side.kalshi_yes_ask, config.KALSHI_FEE_RATE) + config.SLIPPAGE_BUFFER))
        if side.book_decimal is not None:
            options.append((side.book_key or "book", 1.0 / side.book_decimal + config.SLIPPAGE_BUFFER))
        if not options:
            continue
        venue, cost = min(options, key=lambda x: x[1])
        edge = side.fair_prob - cost
        rejects = []
        if edge < config.MIN_VALUE_EDGE:
            rejects.append(f"edge {edge:.3%} < min {config.MIN_VALUE_EDGE:.1%}")
        if gm.seconds_to_first_pitch is not None and gm.seconds_to_first_pitch < config.MIN_SECONDS_TO_FIRST_PITCH:
            rejects.append("too close to first pitch")
        out.append(ValueEdge(gm.game, sel, venue, round(cost, 4), round(side.fair_prob, 4),
                             round(edge, 4), rejects))
    return out


# ── C. Manufactured-arb candidate (staged; logged, never a lock) ─────────────
@dataclass
class ManufacturedCandidate:
    game: str
    selection: str
    entry_venue: str
    entry_cost: float
    open_prob: Optional[float]
    move_toward: Optional[float]      # observed move toward this side (prob pts)
    sharp_divergence: Optional[float]
    target_hedge_cost: float          # buy the other side <= this to lock desired profit
    score: float = 0.0
    rank: str = "D"
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.reject_reasons
