"""
CLI:

    python -m profit_priority demo            # run the math on a built-in snapshot (no keys)
    python -m profit_priority scan            # live: Kalshi + The Odds API (needs ODDS_API_KEY)
    python -m profit_priority report          # summarize the candidate log (the learning loop)
    python -m profit_priority fees 0.40 100   # show Kalshi all-in cost for a price/size
"""

from __future__ import annotations

import sys

from . import engine
from .fees import kalshi_fee, kalshi_cost_per_payout
from . import config


def _demo():
    markets, signals = engine.demo_markets()
    res = engine.run_on_markets(markets, signals, do_log=True)
    print(engine.format_report(res))
    print(f"  (logged every candidate -> {config.LOG_PATH})")


def _scan():
    try:
        from .feeds import build_live_markets
    except Exception as e:  # pragma: no cover
        print(f"live feeds unavailable: {e}")
        return
    markets, signals = build_live_markets()
    if not markets:
        print("No markets assembled (check ODDS_API_KEY / Kalshi availability).")
        return
    res = engine.run_on_markets(markets, signals, do_log=True)
    print(engine.format_report(res))


def _report():
    import json
    if not config.LOG_PATH.exists():
        print("No candidate log yet — run `demo` or `scan` first.")
        return
    counts, accepted = {}, {}
    with open(config.LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get("kind", "?")
            counts[k] = counts.get(k, 0) + 1
            if r.get("accepted"):
                accepted[k] = accepted.get(k, 0) + 1
    print("  CANDIDATE LOG SUMMARY (the learning loop)")
    for k in sorted(counts):
        print(f"   {k:14} {counts[k]:5} logged · {accepted.get(k,0):4} accepted")
    print("   (refit scoring weights on settled outcomes before trusting manufactured arb)")


def _fees(argv):
    price = float(argv[0]) if argv else 0.40
    contracts = float(argv[1]) if len(argv) > 1 else 100.0
    print(f"  Kalshi @ {price:.2f} x {contracts:g} contracts (rate {config.KALSHI_FEE_RATE})")
    print(f"   fee total           ${kalshi_fee(contracts, price, config.KALSHI_FEE_RATE):.2f}")
    print(f"   cost per $1 payout  {kalshi_cost_per_payout(price, config.KALSHI_FEE_RATE):.4f}  (vs raw {price:.2f})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        _demo()
    elif cmd == "scan":
        _scan()
    elif cmd == "report":
        _report()
    elif cmd == "fees":
        _fees(sys.argv[2:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
