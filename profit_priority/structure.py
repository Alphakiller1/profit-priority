"""
Structural relative value — outcome-neutral by construction.

The three classes in `opportunities.py` all need a second venue or a directional
view. This module needs neither: it finds positions whose payoff is IDENTICAL
across every resolution of the season, using only Kalshi's own board. Nothing here
is a bet on a team.

Two families of no-arbitrage condition:

1. PARTITION (fixed cardinality). Exactly K of N contracts resolve YES.
   MLB: championship (1 of 30), each pennant (1 of 15), each division (1 of 5),
   playoff qualification (12 of 30). NFL: each division (1 of 4). WNBA: title (1 of N).

     buy-all  locks if  sum(ask) < K   -> pay sum(ask), receive K
     sell-all locks if  sum(bid) > K   -> pay N - sum(bid), receive N - K

2. NESTING (implication). If A implies B then P(A) <= P(B) always. A violation is
   `bid(narrow) > ask(broad)`: buy the broad, sell the narrow. Given the
   implication, minimum payout is 1 per set against a cost of
   `ask(broad) + 1 - bid(narrow)`, so guaranteed profit is `bid(narrow) - ask(broad)`
   before fees, with the upside leg as free optionality.

Both are priced through `fees` — at mid prices the fee exceeds almost every
structural gap, which is exactly why an unfee'd version of this would look like a
money printer and be a loss. Longshot legs are the exception: fee scales with
P*(1-P), so a 2c contract costs ~0.14c to trade.

    python -m profit_priority.structure
    python -m profit_priority.structure --discover
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import fees

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
MAX_BANKROLL_FRACTION = 0.25
# A relation whose mid prices invert for more than this share of teams is
# mis-stated, not a market inefficiency. See the caution below.
IMPLICATION_SANITY_THRESHOLD = 0.34


@dataclass(frozen=True)
class Series:
    ticker: str
    label: str
    cardinality: int      # exactly this many contracts resolve YES
    scope: str


# Verified live 2026-08-12. NFL conference / Super Bowl / playoff series returned
# ZERO open markets, so NFL is partition-only for now.
SERIES: list[Series] = [
    Series("KXMLB",          "World Series champion", 1,  "global"),
    Series("KXMLBAL",        "AL pennant",            1,  "AL"),
    Series("KXMLBNL",        "NL pennant",            1,  "NL"),
    Series("KXMLBPLAYOFFS",  "Playoff qualifier",     12, "global"),
    Series("KXMLBALEAST",    "AL East winner",        1,  "AL"),
    Series("KXMLBALCENTRAL", "AL Central winner",     1,  "AL"),
    Series("KXMLBALWEST",    "AL West winner",        1,  "AL"),
    Series("KXMLBNLEAST",    "NL East winner",        1,  "NL"),
    Series("KXMLBNLCENTRAL", "NL Central winner",     1,  "NL"),
    Series("KXMLBNLWEST",    "NL West winner",        1,  "NL"),
    Series("KXNFLAFCEAST",   "AFC East winner",       1,  "AFC"),
    Series("KXNFLAFCNORTH",  "AFC North winner",      1,  "AFC"),
    Series("KXNFLAFCSOUTH",  "AFC South winner",      1,  "AFC"),
    Series("KXNFLAFCWEST",   "AFC West winner",       1,  "AFC"),
    Series("KXNFLNFCEAST",   "NFC East winner",       1,  "NFC"),
    Series("KXNFLNFCNORTH",  "NFC North winner",      1,  "NFC"),
    Series("KXNFLNFCSOUTH",  "NFC South winner",      1,  "NFC"),
    Series("KXNFLNFCWEST",   "NFC West winner",       1,  "NFC"),
    Series("KXWNBA",         "WNBA championship",     1,  "global"),
]

# narrow -> broad: "narrow occurring guarantees broad occurred".
#
# CAUTION. `division winner -> pennant` is FALSE and was in an earlier build,
# where it manufactured six phantom locks with ROIs up to +309%. A team can win
# its division and lose the LCS; a wild card can win the pennant without winning
# any division. Division and pennant are NOT nested in either direction. The same
# trap waits in NFL as `division -> conference`. Only add a pair when the narrow
# event makes the broad one logically unavoidable.
IMPLICATIONS: list[tuple[str, str]] = [
    ("KXMLB", "KXMLBAL"), ("KXMLB", "KXMLBNL"),
    ("KXMLB", "KXMLBPLAYOFFS"),
    ("KXMLBAL", "KXMLBPLAYOFFS"), ("KXMLBNL", "KXMLBPLAYOFFS"),
    ("KXMLBALEAST", "KXMLBPLAYOFFS"), ("KXMLBALCENTRAL", "KXMLBPLAYOFFS"),
    ("KXMLBALWEST", "KXMLBPLAYOFFS"), ("KXMLBNLEAST", "KXMLBPLAYOFFS"),
    ("KXMLBNLCENTRAL", "KXMLBPLAYOFFS"), ("KXMLBNLWEST", "KXMLBPLAYOFFS"),
]

# NOT partitions — never sum these against a K. KXNFLWINS is 544 independent
# season win-total contracts (a team/threshold grid); its ask sum was 285.00.
NOT_PARTITIONS = {"KXNFLWINS", "KXMLBGAME", "KXNFLGAME", "KXWNBAGAME"}


@dataclass
class Contract:
    series: str
    ticker: str
    team: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Lock:
    """A structurally guaranteed profit, independent of every game outcome."""
    kind: str
    label: str
    legs: int
    gross_edge: float
    fee_total: float
    sets: int
    capital: float
    profit: float
    roi: float
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


def _get(path: str, **params) -> dict:
    url = f"{KALSHI_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_series(series_ticker: str, pages: int = 6) -> list[Contract]:
    out: list[Contract] = []
    cursor = None
    for _ in range(pages):
        params = {"series_ticker": series_ticker, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _get("/markets", **params)
        except Exception:
            return out
        page = data.get("markets", [])
        for m in page:
            bid, ask = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
            if bid is None or ask is None:
                continue
            out.append(Contract(series_ticker, m.get("ticker", ""),
                                (m.get("ticker", "").rsplit("-", 1)[-1] or "?"),
                                float(bid), float(ask)))
        cursor = data.get("cursor")
        if not cursor or not page:
            break
    return out


def load_board(series: list[Series] | None = None) -> dict[str, list[Contract]]:
    board: dict[str, list[Contract]] = {}
    for s in (series or SERIES):
        if s.ticker in NOT_PARTITIONS:
            continue
        rows = fetch_series(s.ticker)
        if rows:
            board[s.ticker] = rows
    return board


def partition_locks(board: dict[str, list[Contract]], bankroll: float,
                    min_roi: float) -> tuple[list[Lock], list[dict]]:
    reg = {s.ticker: s for s in SERIES}
    locks: list[Lock] = []
    diagnostics: list[dict] = []

    for ticker, contracts in board.items():
        s = reg.get(ticker)
        if not s or not contracts:
            continue
        n, k = len(contracts), s.cardinality
        sum_ask = sum(c.ask for c in contracts)
        sum_bid = sum(c.bid for c in contracts)
        diagnostics.append({"series": ticker, "label": s.label, "n": n, "k": k,
                            "sum_ask": sum_ask, "sum_bid": sum_bid,
                            "buy_gap": k - sum_ask, "sell_gap": sum_bid - k})

        # BUY-ALL: pay every ask, receive K.
        if k - sum_ask > 0 and sum_ask > 0:
            sets = int((bankroll * MAX_BANKROLL_FRACTION) // sum_ask)
            if sets > 0:
                fee = sum(fees.kalshi_fee(sets, c.ask) for c in contracts)
                capital = sets * sum_ask + fee
                profit = sets * k - capital
                roi = profit / capital if capital > 0 else 0.0
                if profit > 0 and roi >= min_roi:
                    locks.append(Lock("partition-buy", f"{s.label} ({ticker})", n,
                                      k - sum_ask, fee, sets, capital, profit, roi,
                                      f"buy all {n}: sum(ask)={sum_ask:.4f} < K={k}"))

        # SELL-ALL: buy NO on every leg (cost 1-bid each), receive N-K.
        if sum_bid - k > 0:
            per_set = n - sum_bid
            sets = int((bankroll * MAX_BANKROLL_FRACTION) // per_set) if per_set > 0 else 0
            if sets > 0:
                fee = sum(fees.kalshi_fee(sets, 1 - c.bid) for c in contracts)
                capital = sets * per_set + fee
                profit = sets * (n - k) - capital
                roi = profit / capital if capital > 0 else 0.0
                if profit > 0 and roi >= min_roi:
                    locks.append(Lock("partition-sell", f"{s.label} ({ticker})", n,
                                      sum_bid - k, fee, sets, capital, profit, roi,
                                      f"sell all {n}: sum(bid)={sum_bid:.4f} > K={k}",
                                      [f"{n} legs — partial fills leave you exposed"]))
    locks.sort(key=lambda x: -x.roi)
    return locks, diagnostics


def validate_implications(board: dict[str, list[Contract]]) -> list[dict]:
    """
    Sanity-check every declared implication against the live board.

    If `narrow -> broad` holds then P(narrow) <= P(broad) for every team, so only
    spread noise should invert. A relation inverting for a large share of teams is
    mis-stated — this is the check that would have caught `division -> pennant`
    before it printed +309% locks.
    """
    out: list[dict] = []
    for narrow_t, broad_t in IMPLICATIONS:
        narrow = {c.team: c for c in board.get(narrow_t, [])}
        broad = {c.team: c for c in board.get(broad_t, [])}
        shared = sorted(set(narrow) & set(broad))
        if not shared:
            continue
        inv = [t for t in shared if narrow[t].mid > broad[t].mid + 1e-9]
        rate = len(inv) / len(shared)
        out.append({"narrow": narrow_t, "broad": broad_t, "teams": len(shared),
                    "inversions": len(inv), "rate": rate,
                    "suspect": rate > IMPLICATION_SANITY_THRESHOLD})
    return out


def nesting_locks(board: dict[str, list[Contract]], bankroll: float,
                  min_roi: float, near: float = 0.0) -> tuple[list[Lock], list[dict]]:
    locks: list[Lock] = []
    misses: list[dict] = []
    suspect = {(v["narrow"], v["broad"])
               for v in validate_implications(board) if v["suspect"]}

    for narrow_t, broad_t in IMPLICATIONS:
        if (narrow_t, broad_t) in suspect:
            continue                      # relation looks mis-stated; refuse it
        narrow = {c.team: c for c in board.get(narrow_t, [])}
        broad = {c.team: c for c in board.get(broad_t, [])}
        for team in sorted(set(narrow) & set(broad)):
            nc, bc = narrow[team], broad[team]
            gross = nc.bid - bc.ask
            per_set = bc.ask + (1 - nc.bid)
            if per_set <= 0:
                continue
            if gross > 0:
                sets = int((bankroll * MAX_BANKROLL_FRACTION) // per_set)
                if sets > 0:
                    fee = (fees.kalshi_fee(sets, bc.ask)
                           + fees.kalshi_fee(sets, 1 - nc.bid))
                    capital = sets * per_set + fee
                    profit = sets * 1.0 - capital
                    roi = profit / capital if capital > 0 else 0.0
                    if profit > 0 and roi >= min_roi:
                        locks.append(Lock(
                            "nesting", f"{team}: {narrow_t} <= {broad_t}", 2,
                            gross, fee, sets, capital, profit, roi,
                            f"sell {narrow_t} @ {nc.bid:.4f}, buy {broad_t} @ {bc.ask:.4f}",
                            ["upside leg is free optionality"]))
            elif near > 0 and gross > -near:
                misses.append({"team": team, "narrow": narrow_t, "broad": broad_t,
                               "narrow_bid": nc.bid, "broad_ask": bc.ask, "gap": gross})
    locks.sort(key=lambda x: -x.roi)
    misses.sort(key=lambda x: -x["gap"])
    return locks, misses


def run(bankroll: float = 350.0, min_roi: float = 0.005, near: float = 0.0) -> None:
    board = load_board()
    if not board:
        print("\n  No open markets returned. Check connectivity.\n")
        return
    total = sum(len(v) for v in board.values())
    print(f"\n  STRUCTURAL RV (outcome-neutral) — bankroll ${bankroll:,.0f} "
          f"| {total} contracts across {len(board)} series\n")

    p_locks, diags = partition_locks(board, bankroll, min_roi)
    n_locks, misses = nesting_locks(board, bankroll, min_roi, near)

    print(f"  {'series':<18}{'n':>4}{'K':>4}{'sum(ask)':>11}{'sum(bid)':>11}"
          f"{'buy gap':>10}{'sell gap':>10}")
    print("  " + "-" * 68)
    for d in sorted(diags, key=lambda x: -max(x["buy_gap"], x["sell_gap"])):
        print(f"  {d['series']:<18}{d['n']:>4}{d['k']:>4}{d['sum_ask']:>11.4f}"
              f"{d['sum_bid']:>11.4f}{d['buy_gap']:>+10.4f}{d['sell_gap']:>+10.4f}")

    checks = validate_implications(board)
    bad = [c for c in checks if c["suspect"]]
    print(f"\n  IMPLICATION SANITY: {len(checks)} relations, "
          f"{sum(c['inversions'] for c in checks)} inversions, {len(bad)} suspect")
    for c in bad:
        print(f"    [!] {c['narrow']} -> {c['broad']} inverts "
              f"{c['inversions']}/{c['teams']} — SKIPPED")

    locks = sorted(p_locks + n_locks, key=lambda x: -x.roi)
    print(f"\n  LOCKS CLEARING FEES: {len(locks)}")
    if not locks:
        print("    none. Expected - at mid prices the two-leg bar is "
              f"{fees.two_leg_breakeven(0.5, 0.5):.4f}, and efficient markets")
        print("    rarely clear it. Honest-empty is the correct output.")
    for lk in locks[:12]:
        print(f"    - [{lk.kind}] {lk.label}")
        print(f"        {lk.detail}")
        print(f"        {lk.sets} sets x {lk.legs} legs | fees ${lk.fee_total:.2f} | "
              f"capital ${lk.capital:.2f} -> locked ${lk.profit:.2f} ({lk.roi*100:+.2f}%)")
        for w in lk.warnings:
            print(f"        [!] {w}")

    if near > 0:
        print(f"\n  NESTING NEAR-MISSES (within {near:.3f}): {len(misses)}")
        for m in misses[:10]:
            print(f"    {m['team']:<5} {m['narrow']} bid {m['narrow_bid']:.4f} vs "
                  f"{m['broad']} ask {m['broad_ask']:.4f}  gap {m['gap']:+.4f}")
    print()


def discover(tickers: list[str] | None = None) -> None:
    print("\n  SERIES DISCOVERY\n")
    reg = {s.ticker: s for s in SERIES}
    for t in (tickers or [s.ticker for s in SERIES]):
        rows = fetch_series(t)
        if not rows:
            print(f"  {t:<18} (no open markets)")
            continue
        s = reg.get(t)
        k = s.cardinality if s else 1
        sa, sb = sum(c.ask for c in rows), sum(c.bid for c in rows)
        print(f"  {t:<18}{len(rows):>4} mkts  K={k:<3} sum(ask)={sa:>8.4f} "
              f"sum(bid)={sb:>8.4f}  buy{k-sa:+.4f} sell{sb-k:+.4f}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Outcome-neutral structural RV.")
    p.add_argument("--bankroll", type=float, default=350.0)
    p.add_argument("--min-roi", type=float, default=0.005)
    p.add_argument("--near", type=float, default=0.0)
    p.add_argument("--discover", action="store_true")
    a = p.parse_args()
    discover() if a.discover else run(a.bankroll, a.min_roi, a.near)


if __name__ == "__main__":
    main()
