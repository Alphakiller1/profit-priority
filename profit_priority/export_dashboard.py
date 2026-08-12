"""
Export a run to docs/data.json for the live dashboard (Phase 6 panels:
Pure Arbs, Cross-Venue Value, Manufactured Candidates, Funnel/Postmortem).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import config

DOCS = Path(__file__).resolve().parent.parent / "docs"
DATA_JSON = DOCS / "data.json"


def _funnel_from_log() -> dict:
    counts, accepted, rejects = Counter(), Counter(), Counter()
    if config.LOG_PATH.exists():
        with open(config.LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                k = r.get("kind", "?")
                counts[k] += 1
                if r.get("accepted"):
                    accepted[k] += 1
                else:
                    for reason in (r.get("candidate", {}) or {}).get("reject_reasons", []) or []:
                        # bucket by the leading phrase, not the exact numbers
                        rejects[reason.split("(")[0].split("<")[0].strip()] += 1
    return {
        "by_kind": {k: {"logged": counts[k], "accepted": accepted[k]} for k in sorted(counts)},
        "top_reject_reasons": [{"reason": r, "n": n} for r, n in rejects.most_common(8)],
    }


def _structure_payload() -> dict:
    """
    Outcome-neutral panel: Kalshi partition/nesting state plus the Polymarket
    mirror. Every sub-block is guarded — a venue being down must degrade one panel,
    never blank the dashboard.
    """
    out: dict = {"partitions": [], "locks": [], "implications": [],
                 "polymarket": [], "board": [], "fee_ref": {}, "errors": []}
    try:
        from . import fees, structure
        board = structure.load_board()
        locks, diags = structure.partition_locks(board, 350.0, 0.005)
        nlocks, _ = structure.nesting_locks(board, 350.0, 0.005)
        out["partitions"] = [
            {"series": d["series"], "label": d["label"], "n": d["n"], "k": d["k"],
             "sum_ask": round(d["sum_ask"], 4), "sum_bid": round(d["sum_bid"], 4),
             "buy_gap": round(d["buy_gap"], 4), "sell_gap": round(d["sell_gap"], 4)}
            for d in sorted(diags, key=lambda x: -max(x["buy_gap"], x["sell_gap"]))]
        out["locks"] = [
            {"kind": l.kind, "label": l.label, "legs": l.legs, "sets": l.sets,
             "capital": round(l.capital, 2), "profit": round(l.profit, 2),
             "roi": round(l.roi, 4), "detail": l.detail}
            for l in (locks + nlocks)]
        out["implications"] = structure.validate_implications(board)

        # Per-contract board with the fee bar each spread must clear.
        rows = []
        for series, contracts in board.items():
            for c in contracts:
                mid = c.mid
                if not 0.0 < mid < 1.0:
                    continue
                rows.append({
                    "series": series, "team": c.team, "ticker": c.ticker,
                    "bid": round(c.bid, 4), "ask": round(c.ask, 4),
                    "spread": round(c.spread, 4), "mid": round(mid, 4),
                    "bid_american": fees.prob_to_american(c.bid),
                    "ask_american": fees.prob_to_american(c.ask),
                    "taker_rt": round(fees.kalshi_round_trip(mid, 100) / 100, 4),
                    "maker_rt": round(fees.kalshi_round_trip(
                        mid, 100, entry_maker=True, exit_maker=True) / 100, 4),
                })
        out["board"] = sorted(rows, key=lambda r: -r["spread"])[:120]
        out["fee_ref"] = {
            "taker_rt_at_50": round(fees.kalshi_round_trip(0.50, 100) / 100, 4),
            "maker_rt_at_50": round(fees.kalshi_round_trip(
                0.50, 100, entry_maker=True, exit_maker=True) / 100, 4),
            "two_leg_breakeven": round(fees.two_leg_breakeven(0.5, 0.5), 4),
            "one_leg_breakeven": round(fees.one_leg_breakeven(0.5), 4),
        }
    except Exception as e:                       # noqa: BLE001 - panel must not break export
        out["errors"].append(f"structure: {type(e).__name__}: {e}")

    try:
        from .feeds import polymarket
        out["polymarket"] = [{
            "label": f.label, "kalshi_series": f.kalshi_series, "sport": f.sport,
            "n": f.n, "k": f.k, "sum_ask": f.sum_ask, "sum_bid": f.sum_bid,
            "buy_gap": f.buy_gap, "sell_gap": f.sell_gap,
            "legs": [{"q": l.question, "price": l.price, "bid": l.bid,
                      "ask": l.ask, "volume": l.volume} for l in f.legs[:12]],
        } for f in polymarket.fetch_families()]
    except Exception as e:                       # noqa: BLE001
        out["errors"].append(f"polymarket: {type(e).__name__}: {e}")

    return out


def build_payload(res: dict, source: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "kalshi_fee_rate": config.KALSHI_FEE_RATE,
        "structure": _structure_payload(),
        "thresholds": {
            "pure_arb_roi": config.MIN_PURE_ARB_ROI,
            "thin_arb_roi": config.MIN_THIN_ARB_ROI,
            "value_edge": config.MIN_VALUE_EDGE,
        },
        "pure_arbs": [{
            "game": p.game, "leg_a": p.leg_a, "leg_b": p.leg_b, "thin": p.thin,
            "roi": p.stake.guaranteed_roi, "profit": p.stake.guaranteed_profit,
            "risk": p.stake.total_cost, "warnings": p.warnings,
        } for p in res["pure_arbs"]],
        "value": [{
            "game": v.game, "selection": v.selection, "venue": v.venue,
            "exec_cost": v.exec_cost, "fair_prob": v.fair_prob, "edge": v.edge,
        } for v in res["value"]],
        "manufactured": [{
            "game": m.game, "selection": m.selection, "entry_venue": m.entry_venue,
            "entry_cost": m.entry_cost, "target_hedge_cost": m.target_hedge_cost,
            "score": m.score, "rank": m.rank, "open_prob": m.open_prob,
            "move_toward": m.move_toward, "sharp_divergence": m.sharp_divergence,
        } for m in res["manufactured"]],
        "funnel": _funnel_from_log(),
    }


def write_dashboard(res: dict, source: str) -> Path:
    DOCS.mkdir(parents=True, exist_ok=True)
    DATA_JSON.write_text(json.dumps(build_payload(res, source), indent=2), encoding="utf-8")
    return DATA_JSON
