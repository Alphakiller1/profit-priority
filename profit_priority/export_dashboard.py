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


def build_payload(res: dict, source: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "kalshi_fee_rate": config.KALSHI_FEE_RATE,
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
