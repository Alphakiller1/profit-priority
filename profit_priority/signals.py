"""
Sharp-signal ingestion — turn the sharp-money-tracker's live steam/divergence
output into the feature dict the manufactured-arb scorer consumes.

The tracker writes `sharp_signals.csv` keyed by a deterministic game_pk
(crc32 of "date|away|home"); we replicate that key to join its rows to our
'AWAY@HOME' markets without coupling the two codebases. Selection for the ML
market is the team abbreviation, which already matches our side keys.

These are the validated edge per the vault (Market-Edge-Engine): enter-at-open on
steam-up underdog sides survived FDR at +20–48% ROI/u. The scorer rewards exactly
that pattern (dog + steam + sharp divergence).
"""

from __future__ import annotations

import csv
import zlib
from datetime import date
from pathlib import Path
from typing import Optional

from . import config
from .fees import american_to_decimal, decimal_to_implied


def game_pk(d: str, away: str, home: str) -> int:
    """Must match the sharp tracker's key exactly."""
    return zlib.crc32(f"{d}|{away}|{home}".encode())


def _implied(american) -> Optional[float]:
    try:
        return decimal_to_implied(american_to_decimal(int(float(american))))
    except (TypeError, ValueError):
        return None


def _signals_path() -> Optional[Path]:
    p = getattr(config, "SHARP_SIGNALS_CSV", None)
    if not p:
        return None
    p = Path(p)
    return p if p.exists() else None


def load_sharp_signals(market_games: list[str],
                       today: Optional[str] = None,
                       csv_path: Optional[str] = None) -> dict[str, dict]:
    """Build { 'AWAY@HOME': { sel: {sharp_divergence, move_toward, open_prob, steam,
    book_lag} } } for the given markets, from the tracker's sharp_signals.csv.

    move_toward = implied(line_current) - implied(line_open)  (prob pts toward the side).
    """
    today = today or date.today().isoformat()
    path = Path(csv_path) if csv_path else _signals_path()
    if not path or not path.exists():
        return {}

    # game_pk -> 'AWAY@HOME' for the markets we care about
    pk_to_game = {}
    for g in market_games:
        if "@" in g:
            a, h = g.split("@", 1)
            pk_to_game[game_pk(today, a, h)] = g

    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("market_type", "ml")).lower() not in ("ml", "h2h", "moneyline"):
                continue
            try:
                pk = int(float(row.get("game_pk")))
            except (TypeError, ValueError):
                continue
            game = pk_to_game.get(pk)
            if not game:
                continue
            sel = str(row.get("selection", "")).strip().upper()
            if not sel:
                continue
            op = _implied(row.get("line_open"))
            cur = _implied(row.get("line_current"))
            move = round((cur - op), 4) if (op is not None and cur is not None) else None
            div = row.get("divergence")
            try:
                div = float(div) if div not in (None, "") else None
            except ValueError:
                div = None
            steam = str(row.get("steam_flag", "")).strip().lower() in ("true", "1", "yes")
            out.setdefault(game, {})[sel] = {
                "sharp_divergence": div,
                "move_toward": move,
                "open_prob": op,
                "steam": steam,
                "book_lag": 0.0,  # filled when lead-lag book intel is available
            }
    return out
