"""
Polymarket feed — the second prediction-market venue.

`feeds/kalshi.py` plus `feeds/odds_api.py` gives Kalshi-vs-book. Polymarket adds
Kalshi-vs-Polymarket, which is a different and often better comparison: both are
real-money exchanges quoting the SAME event with no vig baked into the price, so a
disagreement is a disagreement about probability rather than about margin.

Several families mirror Kalshi's partitions exactly, which means `structure.py`'s
K-of-N maths applies to both and the sums can be compared directly:

    Polymarket "MLB: Team to make postseason"  (30)  <->  KXMLBPLAYOFFS (K=12)
    Polymarket "WNBA: 2026 Champion"                 <->  KXWNBA        (K=1)

Measured 2026-08-12 — the venues genuinely disagree:

    WNBA champion     Kalshi sum(ask) 1.200   Polymarket 1.088
    MLB playoffs      Kalshi sum(ask) 12.860  Polymarket 13.144

Discovery traps, each one verified the hard way:
  * `tag_slug=mlb|wnba|nfl` on /markets is SILENTLY IGNORED — all three return the
    same political markets. Filtering that way returns confidently wrong data.
  * `/events` in default order returns stale 2025 rows.
  * `/public-search?q=` is the only reliable discovery path.
  * `outcomes` and `outcomePrices` arrive as JSON-ENCODED STRINGS holding parallel
    arrays. They are not numbers and not keyed by outcome.

Fees: Polymarket charges no per-trade fee on most markets (cost is spread plus
settlement), so its prices are NOT directly comparable to a Kalshi price that still
owes ~1.75c taker. Always bring the Kalshi side through `fees` before comparing.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


@dataclass
class PolyLeg:
    question: str
    outcome: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    liquidity: float | None = None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return round(self.ask - self.bid, 4)


@dataclass
class PolyFamily:
    """A Polymarket event whose markets form a K-of-N partition."""
    label: str
    kalshi_series: str
    sport: str
    k: int
    title: str
    slug: str
    end: str
    legs: list[PolyLeg] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.legs)

    @property
    def sum_price(self) -> float:
        return round(sum(leg.price for leg in self.legs), 4)

    @property
    def sum_ask(self) -> float:
        return round(sum(leg.ask for leg in self.legs if leg.ask is not None), 4)

    @property
    def sum_bid(self) -> float:
        return round(sum(leg.bid for leg in self.legs if leg.bid is not None), 4)

    @property
    def buy_gap(self) -> float:
        return round(self.k - self.sum_ask, 4)

    @property
    def sell_gap(self) -> float:
        return round(self.sum_bid - self.k, 4)


MIRRORED = [
    {"query": "MLB", "title_has": "make postseason", "label": "MLB playoff qualifier",
     "k": 12, "kalshi": "KXMLBPLAYOFFS", "sport": "mlb"},
    {"query": "WNBA", "title_has": "Champion", "label": "WNBA champion",
     "k": 1, "kalshi": "KXWNBA", "sport": "wnba"},
]


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def search_events(q: str, limit: int = 20) -> list[dict]:
    try:
        res = _get(f"{GAMMA}/public-search?q={urllib.parse.quote(q)}"
                   f"&limit_per_type={limit}")
    except Exception:
        return []
    return (res or {}).get("events") or []


def _is_future(ds) -> bool:
    if not ds:
        return False
    try:
        return datetime.fromisoformat(str(ds).replace("Z", "+00:00")) > \
            datetime.now(timezone.utc)
    except ValueError:
        return False


def _decode_prices(m: dict) -> tuple[str | None, float | None]:
    """`outcomes`/`outcomePrices` are JSON strings holding parallel arrays."""
    try:
        outs, prices = m.get("outcomes"), m.get("outcomePrices")
        if isinstance(outs, str):
            outs = json.loads(outs)
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not outs or not prices:
            return None, None
        return str(outs[0]), float(prices[0])   # slot 0 is the affirmative side
    except (ValueError, TypeError, IndexError):
        return None, None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect_family(spec: dict) -> PolyFamily | None:
    for ev in search_events(spec["query"]):
        title = str(ev.get("title") or "")
        if spec["title_has"].lower() not in title.lower():
            continue
        if ev.get("closed") or not _is_future(ev.get("endDate")):
            continue
        legs: list[PolyLeg] = []
        for m in ev.get("markets") or []:
            label, px = _decode_prices(m)
            if px is None or not 0.0 < px < 1.0:
                continue
            legs.append(PolyLeg(
                question=(m.get("question") or "")[:90], outcome=label or "",
                price=round(px, 4), bid=_num(m.get("bestBid")),
                ask=_num(m.get("bestAsk")), volume=_num(m.get("volumeNum")),
                liquidity=_num(m.get("liquidityNum"))))
        if not legs:
            continue
        return PolyFamily(
            label=spec["label"], kalshi_series=spec["kalshi"], sport=spec["sport"],
            k=spec["k"], title=title, slug=str(ev.get("slug") or ""),
            end=str(ev.get("endDate") or ""),
            legs=sorted(legs, key=lambda x: -x.price))
    return None


def fetch_families() -> list[PolyFamily]:
    return [f for f in (collect_family(s) for s in MIRRORED) if f]


def discover() -> None:
    print("\n  POLYMARKET DISCOVERY (open + future only)\n")
    for q in ("MLB", "WNBA", "NFL", "Aces", "Lynx"):
        evs = [e for e in search_events(q)
               if _is_future(e.get("endDate")) and not e.get("closed")]
        print(f"  {q:<7} {len(evs)} open")
        for e in evs[:6]:
            print(f"     {(e.get('title') or '')[:56]:<56} "
                  f"mkts={len(e.get('markets') or []):>3}  {str(e.get('endDate'))[:10]}")
    print()


def report() -> None:
    fams = fetch_families()
    if not fams:
        print("\n  Nothing collected. Run --discover; Polymarket renames events often.\n")
        return
    print("\n  POLYMARKET MIRRORED PARTITIONS\n")
    for f in fams:
        print(f"  {f.label:<24} n={f.n:<3} K={f.k:<3} "
              f"sum(price)={f.sum_price:<9} sum(ask)={f.sum_ask:<9} sum(bid)={f.sum_bid}")
        print(f"  {'':<24} buy gap {f.buy_gap:+.4f}   sell gap {f.sell_gap:+.4f}"
              f"   mirrors {f.kalshi_series}")
        for leg in f.legs[:5]:
            print(f"      {leg.question[:50]:<50} {leg.price:.4f}"
                  f"  vol {float(leg.volume or 0):>11,.0f}")
        print()
    print("  Compare these sums against structure.py's Kalshi sums. Two real-money\n"
          "  venues disagreeing about the SAME partition is a stronger signal than\n"
          "  either venue's internal inconsistency alone.\n")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Polymarket feed.")
    p.add_argument("--discover", action="store_true")
    a = p.parse_args()
    discover() if a.discover else report()


if __name__ == "__main__":
    main()
