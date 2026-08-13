"""Game events — the daily board, ranked by what the genesis says is actionable.

`structure.py` covers season-long futures: rich in structure, thin in flow, and
resolving in months. That is the wrong primary surface for a trading desk. Games
settle the same day, quote all day, and carry the volume — so they lead, and
futures become the secondary panel.

## What the genesis says about a game, in order

1. CROSS-VENUE SAVING is the only certain gain. If both venues quote the outcome,
   the cheaper one is strictly better and the saving does not depend on being right
   about anything. Ranked first for that reason alone.

2. MAKER ROOM needs width AND flow. A wide spread with no volume is not an
   opportunity, it is a market with no counterparty where a resting order never
   fills. Measured 2026-08-12: the markets wide enough to make in do not trade.
   Both conditions are required here rather than assumed.

3. TAKING NEEDS THE MOVE TO EXCEED THE FEE. A taker round trip costs ~3.5c at mid
   prices while pre-game moves run 1.5-3.5 points, so crossing the spread to chase
   a move is a losing trade before it is a wrong one.

4. NOTHING HERE IS PROMOTED. Every model in the stack is UNPROMOTED, so a game
   card reports what is cheap and what is tradeable structurally. It never asserts
   a side is going to win.

Grouped by Eastern game date, because a slate is a day. A 10pm ET first pitch is
already tomorrow in UTC, and slicing on UTC would silently move late games to the
wrong day — the sort of error that makes a deck quietly wrong rather than loudly
broken.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import fees

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"Accept": "application/json", "User-Agent": "profit-priority/0.2"}

SPORTS: dict[str, str] = {
    "mlb": "KXMLBGAME",
    "wnba": "KXWNBAGAME",
    "nfl": "KXNFLGAME",
}

_MONTHS = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05",
           "JUN": "06", "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10",
           "NOV": "11", "DEC": "12"}

# YYMONDD, optional HHMM (MLB carries a time block, WNBA/NFL do not), matchup, side.
TICKER_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})?([A-Z0-9]+)-([A-Z0-9]+)$")

# Maker room requires BOTH: spread at least this multiple of the maker round trip,
# and at least this much volume. Width alone is a market nobody trades.
MAKER_SPREAD_MULTIPLE = 2.0
MIN_VOLUME_FOR_MAKER = 25.0


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Side:
    team: str
    ticker: str
    bid: float | None
    ask: float | None
    volume: float

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return round(self.ask - self.bid, 4)

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def maker_rt(self) -> float | None:
        m = self.mid
        if m is None or not 0 < m < 1:
            return None
        return round(fees.kalshi_round_trip(m, 100, entry_maker=True,
                                            exit_maker=True) / 100, 4)

    @property
    def taker_rt(self) -> float | None:
        m = self.mid
        if m is None or not 0 < m < 1:
            return None
        return round(fees.kalshi_round_trip(m, 100) / 100, 4)

    @property
    def has_maker_room(self) -> bool:
        s, mrt = self.spread, self.maker_rt
        if s is None or mrt is None:
            return False
        return s >= mrt * MAKER_SPREAD_MULTIPLE and self.volume >= MIN_VOLUME_FOR_MAKER

    @property
    def verdict(self) -> str:
        s, mrt = self.spread, self.maker_rt
        if s is None or mrt is None:
            return "no quote"
        if s < mrt:
            return "too tight"
        if self.volume < MIN_VOLUME_FOR_MAKER:
            return "no volume"
        if s >= mrt * MAKER_SPREAD_MULTIPLE:
            return "maker room"
        return "marginal"

    def as_dict(self) -> dict:
        return {
            "team": self.team, "ticker": self.ticker,
            "bid": self.bid, "ask": self.ask,
            "bid_american": fees.prob_to_american(self.bid),
            "ask_american": fees.prob_to_american(self.ask),
            "spread": self.spread, "mid": self.mid, "volume": self.volume,
            "maker_rt": self.maker_rt, "taker_rt": self.taker_rt,
            "verdict": self.verdict,
        }


@dataclass
class GameEvent:
    sport: str
    game_date: str            # Eastern calendar date
    matchup: str
    sides: list[Side] = field(default_factory=list)

    @property
    def total_volume(self) -> float:
        return sum(s.volume for s in self.sides)

    @property
    def best_spread(self) -> float | None:
        vals = [s.spread for s in self.sides if s.spread is not None]
        return max(vals) if vals else None

    @property
    def overround(self) -> float | None:
        """Sum of both asks. Below 1 is a two-sided arb before fees."""
        asks = [s.ask for s in self.sides if s.ask is not None]
        return round(sum(asks), 4) if len(asks) == 2 else None

    @property
    def maker_sides(self) -> list[Side]:
        return [s for s in self.sides if s.has_maker_room]

    @property
    def rank_score(self) -> float:
        """Ordering only — deliberately not an edge estimate.

        A card ranks high because it is worth LOOKING at (width paired with flow),
        never because the model thinks a side wins. Presenting a rank as an edge is
        exactly the laundering the authority gate exists to prevent.
        """
        if not self.sides:
            return 0.0
        width = self.best_spread or 0.0
        liquid = min(self.total_volume / 1000.0, 1.0)
        return round(width * (0.25 + 0.75 * liquid), 6)

    def as_dict(self) -> dict:
        return {
            "sport": self.sport, "game_date": self.game_date, "matchup": self.matchup,
            "sides": [s.as_dict() for s in self.sides],
            "total_volume": self.total_volume, "best_spread": self.best_spread,
            "overround": self.overround,
            "maker_sides": [s.team for s in self.maker_sides],
            "rank_score": self.rank_score,
        }


def _eastern_date(dt: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (dt - timedelta(hours=4)).date().isoformat()


def _parse_ticker(ticker: str) -> tuple[str, str, str] | None:
    """-> (game_date, event_key, side_code)."""
    m = TICKER_RE.search(ticker or "")
    if not m:
        return None
    yy, mon, dd, _hhmm, _matchup, side = m.groups()
    mm = _MONTHS.get(mon)
    if not mm:
        return None
    return f"20{yy}-{mm}-{dd}", ticker.rsplit("-", 1)[0], side


def fetch_sport(sport: str, pages: int = 8) -> list[GameEvent]:
    series = SPORTS[sport]
    markets: list[dict] = []
    cursor = None
    for _ in range(pages):
        params = {"series_ticker": series, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _get(f"{KALSHI}/markets?{urllib.parse.urlencode(params)}")
        except Exception:
            break
        page = data.get("markets", [])
        markets.extend(page)
        cursor = data.get("cursor")
        if not cursor or not page:
            break

    events: dict[str, GameEvent] = {}
    for m in markets:
        parsed = _parse_ticker(m.get("ticker", ""))
        if not parsed:
            continue
        gdate, key, side = parsed
        title = str(m.get("title") or "")
        # "A vs B ..." is the matchup; fall back to the ticker's event key.
        matchup = key.rsplit("-", 1)[-1]
        if " vs " in title:
            core = title.split(" vs ")
            matchup = f"{core[0].split(':')[-1].strip()} vs {core[1].split()[0]}"
        ev = events.setdefault(key, GameEvent(sport=sport, game_date=gdate,
                                              matchup=matchup))
        ev.sides.append(Side(
            team=side, ticker=m.get("ticker", ""),
            bid=_f(m.get("yes_bid_dollars")), ask=_f(m.get("yes_ask_dollars")),
            volume=_f(m.get("volume_fp")) or 0.0,
        ))
    # Two-way only; anything else is an unmapped market rather than a game.
    return [e for e in events.values() if len(e.sides) == 2]


def board(sports: list[str] | None = None, days: int = 4) -> dict[str, list[GameEvent]]:
    """Game events grouped by Eastern date, nearest first."""
    today = _eastern_date(datetime.now(UTC))
    horizon = (datetime.now(UTC) + timedelta(days=days)).date().isoformat()
    by_date: dict[str, list[GameEvent]] = defaultdict(list)
    for sport in (sports or list(SPORTS)):
        for ev in fetch_sport(sport):
            if today <= ev.game_date <= horizon:
                by_date[ev.game_date].append(ev)
    for d in by_date:
        by_date[d].sort(key=lambda e: -e.rank_score)
    return dict(sorted(by_date.items()))


def payload(days: int = 4) -> dict:
    b = board(days=days)
    today = _eastern_date(datetime.now(UTC))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "today": today,
        "days": [
            {
                "date": d,
                "is_today": d == today,
                "games": len(evs),
                "maker_candidates": sum(len(e.maker_sides) for e in evs),
                "events": [e.as_dict() for e in evs],
            }
            for d, evs in b.items()
        ],
    }


def report(days: int = 3) -> None:
    b = board(days=days)
    today = _eastern_date(datetime.now(UTC))
    if not b:
        print("\n  No game events in the horizon.\n")
        return
    print(f"\n  GAME EVENTS — ET dates, nearest first (today {today})")
    print("  Ranked by width paired with flow. Rank is 'worth looking at',")
    print("  NOT an edge: no model here is promoted to assert a side.\n")
    for d, evs in b.items():
        tag = "   <<< TODAY" if d == today else ""
        makers = sum(len(e.maker_sides) for e in evs)
        print(f"  == {d} ==  {len(evs)} games, {makers} maker candidate(s){tag}")
        print(f"     {'matchup':<22}{'side':<6}{'bid':>7}{'bidUS':>8}"
              f"{'ask':>7}{'askUS':>8}{'sprd':>7}{'mkrRT':>8}{'vol':>10}  verdict")
        for e in evs[:8]:
            for i, s in enumerate(e.sides):
                label = f"{e.matchup[:21]}" if i == 0 else ""
                print(f"     {label:<22}{s.team:<6}"
                      f"{(f'{s.bid:.2f}' if s.bid is not None else '-'):>7}"
                      f"{fees.fmt_american(s.bid):>8}"
                      f"{(f'{s.ask:.2f}' if s.ask is not None else '-'):>7}"
                      f"{fees.fmt_american(s.ask):>8}"
                      f"{(f'{s.spread:.3f}' if s.spread is not None else '-'):>7}"
                      f"{(f'{s.maker_rt:.4f}' if s.maker_rt is not None else '-'):>8}"
                      f"{s.volume:>10,.0f}  {s.verdict}")
        print()
    print("  A wide spread with no volume is not an opportunity - it is a market")
    print("  with no counterparty. Both width and flow are required for 'maker room'.\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Kalshi game-event board by day.")
    p.add_argument("--days", type=int, default=3)
    report(p.parse_args().days)


if __name__ == "__main__":
    main()
