"""Best execution across Kalshi and Polymarket on the SAME event.

Two real-money exchanges quote several identical partitions. Neither carries vig,
so a price difference is a disagreement about probability — and whichever side you
want, one of them is cheaper. Trading the wrong venue is a pure, avoidable cost.

Three things this reports, in increasing order of value:

  1. BEST BUY  — the venue with the lower all-in ask for a side.
  2. BEST SELL — the venue with the higher bid for a side.
  3. CROSS-VENUE LOCK — one venue's ask below the other's bid on the same
     outcome. Buy there, sell here, keep the difference, outcome irrelevant.

Fees are asymmetric and must not be ignored. Kalshi charges
``ceil(0.07 * C * P * (1-P))`` per order; Polymarket charges no per-trade fee on
most markets (its cost is spread plus settlement). Comparing raw prices would
therefore flatter Kalshi systematically, so the Kalshi side is brought through
``fees`` before any comparison.

Matching is by team, and the two venues name teams completely differently —
Kalshi uses short codes (``DET``, ``CONN``) inside a ticker, Polymarket uses a
full club name inside a question sentence. The mapping below is explicit rather
than fuzzy: a wrong join here would compare two different teams' prices and
report the mismatch as an edge, which is the most dangerous failure available in
this module.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

from . import fees, structure
from .feeds import polymarket

# Club name -> venue-neutral code. Keyed on the distinctive nickname so it works
# against a full sentence ("Will the Detroit Tigers clinch...").
MLB_NICKNAMES = {
    "diamondbacks": "AZ", "braves": "ATL", "orioles": "BAL", "red sox": "BOS",
    "cubs": "CHC", "white sox": "CWS", "reds": "CIN", "guardians": "CLE",
    "rockies": "COL", "tigers": "DET", "astros": "HOU", "royals": "KC",
    "angels": "LAA", "dodgers": "LAD", "marlins": "MIA", "brewers": "MIL",
    "twins": "MIN", "mets": "NYM", "yankees": "NYY", "athletics": "ATH",
    "phillies": "PHI", "pirates": "PIT", "padres": "SD", "giants": "SF",
    "mariners": "SEA", "cardinals": "STL", "rays": "TB", "rangers": "TEX",
    "blue jays": "TOR", "nationals": "WSH",
}

WNBA_NICKNAMES = {
    "dream": "ATL", "sky": "CHI", "sun": "CONN", "wings": "DAL",
    "valkyries": "GS", "fever": "IND", "aces": "LV", "sparks": "LA",
    "lynx": "MIN", "liberty": "NY", "mercury": "PHX", "storm": "SEA",
    "mystics": "WSH", "fire": "PDX", "tempo": "TOR",
}

NICKNAMES = {"KXMLBPLAYOFFS": MLB_NICKNAMES, "KXWNBA": WNBA_NICKNAMES}

# A gap this large across two liquid exchanges is far more likely a bad join or a
# stale quote than a real disagreement. Surface it as suspect, never as an edge.
MAX_PLAUSIBLE_CROSS_VENUE_GAP = 0.20


def team_from_question(question: str, nicknames: dict[str, str]) -> str | None:
    """Resolve a club code from a Polymarket question sentence.

    Longest nickname first so 'white sox' is not shadowed by 'sox', and word
    boundaries so 'sun' does not match inside 'Sunday'. Returns None rather than
    guessing — an unmatched leg is dropped, never approximated.
    """
    text = question.lower()
    for nickname in sorted(nicknames, key=len, reverse=True):
        if re.search(rf"\b{re.escape(nickname)}\b", text):
            return nicknames[nickname]
    return None


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    bid: float | None
    ask: float | None
    fee_per_contract: float = 0.0

    @property
    def all_in_ask(self) -> float | None:
        """Cost per $1 of payout, fees included."""
        return None if self.ask is None else self.ask + self.fee_per_contract

    @property
    def net_bid(self) -> float | None:
        """Proceeds per contract sold, fees deducted.

        Floored at zero: a 0.00 bid minus a fee is arithmetically negative, but
        you cannot be paid less than nothing to sell. Left unfloored it produced
        -0.0007 rows that would read as sellable liquidity where there is none.
        """
        if self.bid is None or self.bid <= 0:
            return None
        return max(self.bid - self.fee_per_contract, 0.0)


@dataclass
class SideComparison:
    series: str
    team: str
    kalshi: VenueQuote
    poly: VenueQuote

    @property
    def best_buy(self) -> tuple[str, float] | None:
        options = [(q.venue, q.all_in_ask) for q in (self.kalshi, self.poly)
                   if q.all_in_ask is not None]
        return min(options, key=lambda x: x[1]) if options else None

    @property
    def best_sell(self) -> tuple[str, float] | None:
        options = [(q.venue, q.net_bid) for q in (self.kalshi, self.poly)
                   if q.net_bid is not None]
        return max(options, key=lambda x: x[1]) if options else None

    @property
    def buy_saving(self) -> float:
        """What choosing the wrong venue would cost per $1 of payout."""
        asks = [q.all_in_ask for q in (self.kalshi, self.poly) if q.all_in_ask is not None]
        return round(max(asks) - min(asks), 4) if len(asks) == 2 else 0.0

    @property
    def lock(self) -> tuple[str, str, float] | None:
        """(buy_venue, sell_venue, profit) when one ask sits below the other's bid."""
        for buy, sell in ((self.kalshi, self.poly), (self.poly, self.kalshi)):
            if buy.all_in_ask is None or sell.net_bid is None:
                continue
            edge = sell.net_bid - buy.all_in_ask
            if 0 < edge <= MAX_PLAUSIBLE_CROSS_VENUE_GAP:
                return (buy.venue, sell.venue, round(edge, 4))
        return None

    @property
    def suspect(self) -> bool:
        if self.kalshi.ask is None or self.poly.ask is None:
            return False
        return abs(self.kalshi.ask - self.poly.ask) > MAX_PLAUSIBLE_CROSS_VENUE_GAP


def compare(contracts: int = 100) -> list[SideComparison]:
    board = structure.load_board()
    out: list[SideComparison] = []
    for family in polymarket.fetch_families():
        series = family.kalshi_series
        nicknames = NICKNAMES.get(series)
        kalshi_legs = {c.team: c for c in board.get(series, [])}
        if not nicknames or not kalshi_legs:
            continue
        for leg in family.legs:
            team = team_from_question(leg.question, nicknames)
            if team is None or team not in kalshi_legs:
                continue          # unmatched: drop rather than approximate
            k = kalshi_legs[team]
            # Kalshi fee at this price, amortised per contract.
            k_fee = fees.kalshi_fee(contracts, k.ask) / contracts if 0 < k.ask < 1 else 0.0
            out.append(SideComparison(
                series=series, team=team,
                kalshi=VenueQuote("kalshi", k.bid, k.ask, k_fee),
                poly=VenueQuote("polymarket", leg.bid, leg.ask),
            ))
    out.sort(key=lambda c: -c.buy_saving)
    return out


def report(contracts: int = 100) -> None:
    rows = compare(contracts)
    print(f"\n  CROSS-VENUE BEST EXECUTION — {len(rows)} matched outcomes")
    print("  Kalshi asks include its per-contract fee; Polymarket charges none.\n")
    if not rows:
        print("  No outcomes matched across both venues.\n")
        return

    locks = [r for r in rows if r.lock]
    print(f"  {'series':<16}{'team':<6}{'K ask':>9}{'P ask':>9}{'BUY':>12}"
          f"{'save':>8}{'K bid':>9}{'P bid':>9}{'SELL':>12}")
    print("  " + "-" * 92)
    for r in rows[:28]:
        bb, bs = r.best_buy, r.best_sell
        flag = "  [!] suspect" if r.suspect else ""
        print(f"  {r.series.replace('KX',''):<16}{r.team:<6}"
              f"{(f'{r.kalshi.all_in_ask:.4f}' if r.kalshi.all_in_ask else '—'):>9}"
              f"{(f'{r.poly.all_in_ask:.4f}' if r.poly.all_in_ask else '—'):>9}"
              f"{(bb[0] if bb else '—'):>12}{r.buy_saving:>8.4f}"
              f"{(f'{r.kalshi.net_bid:.4f}' if r.kalshi.net_bid else '—'):>9}"
              f"{(f'{r.poly.net_bid:.4f}' if r.poly.net_bid else '—'):>9}"
              f"{(bs[0] if bs else '—'):>12}{flag}")

    print(f"\n  CROSS-VENUE LOCKS (CANDIDATES, not confirmed): {len(locks)}")
    if not locks:
        print("    none — no venue's ask sits below the other's bid after fees.")
        print("    Expected: both books are real money and broadly agree.")
    for r in locks:
        buy, sell, edge = r.lock
        print(f"    {r.team} ({r.series}): buy {buy}, sell {sell} -> +{edge:.4f}/contract")

    if locks:
        print("\n    [!] VERIFY BEFORE TRADING ANY OF THESE. A cross-venue lock is only")
        print("        a lock if BOTH markets resolve identically. Mismatched resolution")
        print("        is the classic way this trade becomes a loss:")
        print("          - Kalshi 'playoff qualifier' vs Polymarket 'clinch a spot' may")
        print("            differ on tiebreakers, play-in games, or settlement timing.")
        print("          - Polymarket bestBid/bestAsk carry no depth guarantee; the size")
        print("            you need may not exist at that price.")
        print("          - Capital is tied up on both venues in different currencies")
        print("            (USD vs USDC) until resolution, which can be months.")
        print("        Read both rulebooks on the specific market before sizing.")

    worst = max(rows, key=lambda r: r.buy_saving)
    print(f"\n  Largest avoidable cost: {worst.team} {worst.series} — "
          f"{worst.buy_saving:.4f}/contract by using the wrong venue.")
    print("  Unlike the locks above, this saving needs no resolution match: if you")
    print("  were going to take that side anyway, the cheaper venue is strictly")
    print("  better and the difference does not depend on being right.\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Kalshi vs Polymarket best execution.")
    p.add_argument("--contracts", type=int, default=100)
    report(p.parse_args().contracts)


if __name__ == "__main__":
    main()
