"""Canonical MLB team abbreviations — to match Odds API full names with Kalshi tickers."""

from __future__ import annotations

_FULL = {
    "arizona diamondbacks": "ARI", "atlanta braves": "ATL", "baltimore orioles": "BAL",
    "boston red sox": "BOS", "chicago cubs": "CHC", "chicago white sox": "CHW",
    "cincinnati reds": "CIN", "cleveland guardians": "CLE", "colorado rockies": "COL",
    "detroit tigers": "DET", "houston astros": "HOU", "kansas city royals": "KCR",
    "los angeles angels": "LAA", "los angeles dodgers": "LAD", "miami marlins": "MIA",
    "milwaukee brewers": "MIL", "minnesota twins": "MIN", "new york mets": "NYM",
    "new york yankees": "NYY", "athletics": "ATH", "oakland athletics": "ATH",
    "philadelphia phillies": "PHI", "pittsburgh pirates": "PIT", "san diego padres": "SDP",
    "san francisco giants": "SFG", "seattle mariners": "SEA", "st louis cardinals": "STL",
    "st. louis cardinals": "STL", "tampa bay rays": "TBR", "texas rangers": "TEX",
    "toronto blue jays": "TOR", "washington nationals": "WSN",
}
# Kalshi/odds variant abbreviations -> canonical
_ALIAS = {"AZ": "ARI", "CWS": "CHW", "KC": "KCR", "SD": "SDP", "SF": "SFG",
          "TB": "TBR", "WSH": "WSN", "OAK": "ATH", "CHW": "CHW"}
_CANON = set(_FULL.values())


def to_abbr(name: str) -> str:
    if not name:
        return ""
    n = str(name).strip()
    up = n.upper()
    if up in _CANON:
        return up
    if up in _ALIAS:
        return _ALIAS[up]
    return _FULL.get(n.lower(), up if len(up) <= 3 else "")
