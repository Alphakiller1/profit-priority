"""
Sportsbook prices via The Odds API (h2h / moneyline). Returns, per game, the raw
American odds for every book seen on each side — pricing.assemble_market then
picks the best EXECUTION book and de-vigs the SHARP books for the fair anchor.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .. import config
from ..teams import to_abbr

_FETCHED_AT = None


def _get(path: str, params: dict):
    if not config.ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY not set")
    q = urllib.parse.urlencode({**params, "apiKey": config.ODDS_API_KEY})
    url = f"{config.ODDS_API_BASE}{path}?{q}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_ml() -> dict[str, dict[str, dict[str, int]]]:
    """{ 'AWAY@HOME': { 'AWAY': {book: american}, 'HOME': {book: american} }, ... }"""
    data = _get(f"/sports/{config.ODDS_SPORT_KEY}/odds",
                {"regions": "us,eu", "markets": "h2h", "oddsFormat": "american"})
    out: dict[str, dict[str, dict[str, int]]] = {}
    for ev in data:
        away = to_abbr(ev.get("away_team", ""))
        home = to_abbr(ev.get("home_team", ""))
        if not away or not home:
            continue
        game = f"{away}@{home}"
        sides = out.setdefault(game, {away: {}, home: {}})
        for bk in ev.get("bookmakers", []):
            key = bk.get("key")
            for mk in bk.get("markets", []):
                if mk.get("key") != "h2h":
                    continue
                for o in mk.get("outcomes", []):
                    sel = to_abbr(o.get("name", ""))
                    price = o.get("price")
                    if sel in sides and price is not None:
                        sides[sel][key] = int(price)
    return out


def now_age(_fetched_at: str | None = None) -> float:
    return 0.0  # odds fetched fresh each scan; refine with per-book timestamps if needed
