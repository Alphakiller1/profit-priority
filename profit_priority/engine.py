"""
Engine — assemble markets, run the three detectors, log every candidate, and rank
the accepted opportunities by what actually matters:
  - Pure arbs       → by guaranteed ROI (no opinion needed; execute now).
  - Cross-venue value → by edge vs the sharp fair price.
  - Manufactured    → by score (R&D; logged, not traded).
"""

from __future__ import annotations

from typing import Optional

from .pricing import GameMarket, assemble_market
from .opportunities import detect_pure_arb, detect_value
from .scoring import detect_manufactured
from . import logger


def run_on_markets(markets: list[GameMarket],
                   signals_by_game: Optional[dict[str, dict]] = None,
                   do_log: bool = True) -> dict:
    signals_by_game = signals_by_game or {}
    pure, value, manu = [], [], []
    for gm in markets:
        pa = detect_pure_arb(gm)
        ve = detect_value(gm)
        mc = detect_manufactured(gm, signals_by_game.get(gm.game))
        if do_log:
            logger.log_all("pure_arb", pa)
            logger.log_all("value", ve)
            logger.log_all("manufactured", mc)
        pure += [p for p in pa if p.accepted]
        value += [v for v in ve if v.accepted]
        manu += [m for m in mc if m.accepted]

    pure.sort(key=lambda p: -p.stake.guaranteed_roi)
    value.sort(key=lambda v: -v.edge)
    manu.sort(key=lambda m: -m.score)
    return {"pure_arbs": pure, "value": value, "manufactured": manu}


def format_report(res: dict) -> str:
    out = []
    pa = res["pure_arbs"]
    out.append("\n  PURE ARBITRAGE — execute both legs now (guaranteed after fees)")
    if pa:
        for p in pa:
            tag = " [THIN]" if p.thin else ""
            out.append(f"   {p.game:10} {p.leg_a} + {p.leg_b}{tag}")
            out.append(f"      lock ROI {p.stake.guaranteed_roi:+.2%}  profit ${p.stake.guaranteed_profit} "
                       f"on ${p.stake.total_cost} risk"
                       + (("  WARN: " + "; ".join(p.warnings)) if p.warnings else ""))
    else:
        out.append("   none above threshold (most 'arbs' die after Kalshi fees — by design)")

    ve = res["value"]
    out.append("\n  CROSS-VENUE VALUE — cheaper than the sharp fair price (+EV, not a lock)")
    if ve:
        for v in ve:
            out.append(f"   {v.game:10} {v.selection} @ {v.venue}  cost {v.exec_cost:.3f} "
                       f"vs fair {v.fair_prob:.3f}  edge {v.edge:+.2%}")
    else:
        out.append("   none above threshold")

    mc = res["manufactured"]
    out.append("\n  MANUFACTURED-ARB CANDIDATES — staged R&D (logged, NOT a lock)")
    if mc:
        for m in mc:
            out.append(f"   [{m.rank}] {m.game:10} {m.selection} enter@{m.entry_venue} {m.entry_cost:.3f} "
                       f"-> hedge <= {m.target_hedge_cost:.3f}  score {m.score}")
    else:
        out.append("   none (need entry signals: dog+steam+sharp divergence)")
    out.append("")
    return "\n".join(out)


# ── Demo (runs with no API keys; proves the math + the fee discipline) ─────────
def demo_markets() -> tuple[list[GameMarket], dict]:
    # 1) A REALISTIC small arb (~1.3% after fees): buy ARI YES on Kalshi @ 0.47, buy
    #    LAD on a book @ +106. A real arb is small — the engine only takes it because
    #    it clears the fee floor AND isn't an implausibly large (stale) gap.
    m1 = assemble_market(
        "ARI@LAD",
        kalshi_by_side={"ARI": {"yes_ask": 0.47, "yes_bid": 0.45, "liquidity": 800, "age_sec": 8},
                        "LAD": {"yes_ask": 0.555, "yes_bid": 0.535, "liquidity": 600, "age_sec": 8}},
        book_americans_by_side={"ARI": {"draftkings": 100, "pinnacle": -105},
                                "LAD": {"draftkings": 106, "pinnacle": -102}},
        seconds_to_first_pitch=5400)
    # 2) A VALUE-only game: SDP cheaper than the sharp fair, no clean hedge.
    m2 = assemble_market(
        "SDP@PHI",
        kalshi_by_side={"SDP": {"yes_ask": 0.47, "yes_bid": 0.45, "liquidity": 120, "age_sec": 15},
                        "PHI": {"yes_ask": 0.56, "yes_bid": 0.54, "liquidity": 150, "age_sec": 15}},
        book_americans_by_side={"SDP": {"draftkings": 120, "pinnacle": 104},
                                "PHI": {"draftkings": -135, "pinnacle": -124}},
        seconds_to_first_pitch=4000)
    # 3) An efficient game: no edge anywhere (the common case).
    m3 = assemble_market(
        "TOR@ATL",
        kalshi_by_side={"TOR": {"yes_ask": 0.46, "yes_bid": 0.45, "liquidity": 300, "age_sec": 10},
                        "ATL": {"yes_ask": 0.57, "yes_bid": 0.56, "liquidity": 300, "age_sec": 10}},
        book_americans_by_side={"TOR": {"draftkings": 118, "pinnacle": 115},
                                "ATL": {"draftkings": -140, "pinnacle": -136}},
        seconds_to_first_pitch=3600)

    signals = {"SDP@PHI": {"SDP": {"open_prob": 0.43, "move_toward": 0.03, "sharp_divergence": 0.025,
                                   "historical_roi": 0.20, "historical_clv": 0.6, "book_lag": 0.4}}}
    return [m1, m2, m3], signals
