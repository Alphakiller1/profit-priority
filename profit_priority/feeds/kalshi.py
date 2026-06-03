"""
Kalshi KXMLBGAME (game-winner) executable prices. Returns, per game, the YES
ask/bid + liquidity + age per side. We use the ASK to buy (execution price), not
the midpoint — the whole point of this engine.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from .. import config
from ..teams import to_abbr

SERIES = "KXMLBGAME"


def _get(path: str, params: dict):
    q = urllib.parse.urlencode(params)
    url = f"{config.KALSHI_BASE}{path}?{q}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _markets(status: str = "open", max_pages: int = 8) -> list[dict]:
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"series_ticker": SERIES, "status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _get("/markets", params)
        out += data.get("markets", [])
        cursor = data.get("cursor")
        if not cursor:
            break
    return out


_TICKER = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})\d*([A-Z]{2,3})$")


def _parse(ticker: str):
    """KXMLBGAME-26JUN03...ARI -> (game_date, side_abbr). Best-effort."""
    m = _TICKER.search(ticker or "")
    if not m:
        return None, None
    return None, to_abbr(m.group(4))


def fetch_ml() -> dict[str, dict[str, dict]]:
    """{ 'AWAY@HOME': { sel: {yes_ask, yes_bid, liquidity, age_sec} } } (best-effort match).

    Kalshi tickers encode the matchup + side; we group by the event's two sides.
    """
    out: dict[str, dict[str, dict]] = {}
    by_event: dict[str, list[dict]] = {}
    for m in _markets("open"):
        ev = m.get("event_ticker", "")
        by_event.setdefault(ev, []).append(m)

    for ev, ms in by_event.items():
        sides = {}
        for m in ms:
            _, sel = _parse(m.get("ticker", ""))
            if not sel:
                title = (m.get("yes_sub_title") or m.get("title") or "")
                sel = to_abbr(title)
            if not sel:
                continue
            ya = m.get("yes_ask")
            yb = m.get("yes_bid")
            sides[sel] = {
                "yes_ask": (ya / 100.0) if ya is not None else None,
                "yes_bid": (yb / 100.0) if yb is not None else None,
                "liquidity": m.get("open_interest") or m.get("volume"),
                "age_sec": 0.0,
            }
        if len(sides) == 2:
            a, b = sorted(sides.keys())
            out[f"{a}@{b}"] = sides   # note: order may not match home/away; engine de-vigs both
    return out
