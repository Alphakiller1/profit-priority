"""
CLI:

    python -m profit_priority demo            # run the math on a built-in snapshot (no keys)
    python -m profit_priority scan            # live: Kalshi + The Odds API (needs ODDS_API_KEY)
    python -m profit_priority report          # summarize the candidate log (the learning loop)
    python -m profit_priority fees 0.40 100   # show Kalshi all-in cost for a price/size
    python -m profit_priority dashboard       # write docs/data.json (live feeds when keyed)
    python -m profit_priority dashboard live  # force live feeds (needs ODDS_API_KEY)
    python -m profit_priority dashboard demo  # force the built-in fixture snapshot
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
    from .fees import fmt_american
    print(f"  Kalshi @ {price:.2f}/{fmt_american(price)} x {contracts:g} contracts "
          f"(rate {config.KALSHI_FEE_RATE})")
    print(f"   fee total           ${kalshi_fee(contracts, price, config.KALSHI_FEE_RATE):.2f}")
    _cpp = kalshi_cost_per_payout(price, config.KALSHI_FEE_RATE)
    print(f"   cost per $1 payout  {_cpp:.4f}/{fmt_american(_cpp)}  "
          f"(vs raw {price:.2f}/{fmt_american(price)})")


def _dashboard(argv):
    """
    Write docs/data.json. The sportsbook-backed panels (pure arb / value /
    manufactured) need a metered Odds API key; the structural panels do not.

    Mode defaults to `auto`, which publishes those panels ONLY from live feeds.
    Without a key it publishes them EMPTY rather than falling back to the demo
    snapshot: a scheduled run that quietly ships three fixture games as if they
    were today's board is worse than a blank panel, because the page gives no
    sign that the run never happened. `demo` stays available explicitly, for
    showing the math offline.
    """
    from . import export_dashboard
    mode = (argv[0] if argv else "auto").lower()
    note = ""

    if mode == "demo":
        markets, signals = engine.demo_markets()
        source = "demo"
        note = "Built-in fixture snapshot — not today's board."
    elif mode == "live" or (mode == "auto" and config.ODDS_API_KEY):
        try:
            from .feeds import build_live_markets
            markets, signals = build_live_markets()
            source = "live"
        except Exception as e:                   # noqa: BLE001 - one dead feed must not blank the deck
            markets, signals = [], {}
            source = "unavailable"
            note = f"Sportsbook feed failed: {type(e).__name__}: {e}"
    else:
        markets, signals = [], {}
        source = "unavailable"
        note = ("No ODDS_API_KEY — the sportsbook feed these three panels price "
                "against is not configured. The structural panels above are live.")

    # Only a live run is evidence. Logging demo candidates would seed the
    # learning ledger (and the funnel panel) with fixtures that never traded.
    res = engine.run_on_markets(markets, signals, do_log=(source == "live"))
    path = export_dashboard.write_dashboard(res, source, note)
    print(engine.format_report(res))
    if note:
        print(f"  note: {note}")
    print(f"  dashboard data -> {path}\n  open docs/index.html (or serve docs/)")


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
    elif cmd == "dashboard":
        _dashboard(sys.argv[2:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
