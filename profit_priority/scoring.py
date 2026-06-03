"""
Manufactured-arb scoring — interpretable first, trained later.

Manufactured arb (enter early, hedge after the move) is NOT a lock at entry; its
whole EV rides on whether a hedge actually appears in time. We do NOT trade it
from this engine yet — we *score and log* candidates so the conversion rate is
measured from real data before any capital is risked. The weights are a starting
point and must be refit on logged out-of-sample outcomes (FDR-controlled) before
being trusted.
"""

from __future__ import annotations

from typing import Optional

from . import config
from .fees import kalshi_cost_per_payout
from .opportunities import ManufacturedCandidate
from .pricing import GameMarket

# First-pass interpretable weights (see vault Market-Edge-Engine: enter-at-open on
# steam-up sides was the validated edge). Refit on logged outcomes before trusting.
WEIGHTS = {
    "historical_roi": 2.0,
    "historical_clv": 1.5,
    "sharp_divergence": 1.0,
    "current_move": 0.75,
    "cross_venue_gap": 0.75,
    "book_lag": 0.5,
    "liquidity": 0.25,
    "spread_cost": -1.0,
    "fee_cost": -1.0,
    "stale_risk": -1.5,
    "hedge_failure_risk": -1.0,
}


def score_features(f: dict) -> float:
    return round(sum(WEIGHTS[k] * float(f.get(k, 0.0)) for k in WEIGHTS), 3)


def rank_from_score(score: float) -> str:
    if score >= 3.0:
        return "A"   # enter now, strong hedge probability
    if score >= 1.5:
        return "B"   # monitor; enter only if price improves
    if score >= 0.5:
        return "C"   # value only, no clear hedge path
    return "D"       # reject


def detect_manufactured(gm: GameMarket,
                        signals: Optional[dict] = None,
                        desired_profit: float = 0.02) -> list[ManufacturedCandidate]:
    """Build staged-entry candidates from optional signal inputs.

    signals[sel] = {open_prob, move_toward, sharp_divergence, historical_roi,
                    historical_clv, book_lag, liquidity, hedge_failure_risk}
    Entry rule (vault/PROFIT-PRIORITY): underdog at open<0.45, move toward >=0.02,
    sharp divergence >=0.02, a slow venue still offering the old price.
    """
    signals = signals or {}
    out: list[ManufacturedCandidate] = []
    for sel, side in gm.sides.items():
        sig = signals.get(sel)
        if not sig:
            continue
        # cheapest executable entry cost
        entries = []
        if side.kalshi_yes_ask is not None:
            entries.append(("kalshi", kalshi_cost_per_payout(side.kalshi_yes_ask, config.KALSHI_FEE_RATE)))
        if side.book_decimal is not None:
            entries.append((side.book_key or "book", 1.0 / side.book_decimal))
        if not entries:
            continue
        venue, entry_cost = min(entries, key=lambda x: x[1])

        open_prob = sig.get("open_prob")
        move = sig.get("move_toward")
        div = sig.get("sharp_divergence")

        rejects = []
        if open_prob is not None and open_prob >= 0.45:
            rejects.append("not an underdog at open (>=0.45)")
        if move is not None and move < 0.02:
            rejects.append("insufficient move toward side (<2pts)")
        if div is not None and div < 0.02:
            rejects.append("sharp divergence below threshold (<2pts)")

        target_hedge = round(1.0 - entry_cost - config.SLIPPAGE_BUFFER - desired_profit, 4)

        feats = {
            "historical_roi": sig.get("historical_roi", 0.0),
            "historical_clv": sig.get("historical_clv", 0.0),
            "sharp_divergence": div or 0.0,
            "current_move": move or 0.0,
            "cross_venue_gap": sig.get("cross_venue_gap", 0.0),
            "book_lag": sig.get("book_lag", 0.0),
            "liquidity": min((side.kalshi_liquidity or 0) / 100.0, 1.0),
            "spread_cost": sig.get("spread_cost", 0.0),
            "fee_cost": config.KALSHI_FEE_RATE * entry_cost * (1 - entry_cost),
            "stale_risk": 1.0 if (side.kalshi_age_sec or 0) > config.MAX_PRICE_AGE_SEC else 0.0,
            "hedge_failure_risk": sig.get("hedge_failure_risk", 0.0),
        }
        score = score_features(feats)
        out.append(ManufacturedCandidate(
            gm.game, sel, venue, round(entry_cost, 4), open_prob, move, div,
            target_hedge, score, rank_from_score(score), rejects))
    return out
