"""Live feed assembly: combine sportsbook + Kalshi into GameMarket objects."""

from __future__ import annotations

from ..pricing import assemble_market
from . import odds_api, kalshi


def build_live_markets():
    """Returns (markets, signals). Sportsbook is the spine (full slate, sharp anchor);
    Kalshi prices are merged in by game key where they match."""
    books = odds_api.fetch_ml()           # {game: {sel: {book: american}}}
    try:
        kal = kalshi.fetch_ml()           # {game: {sel: {...}}}
    except Exception:
        kal = {}

    markets = []
    for game, book_sides in books.items():
        markets.append(assemble_market(
            game,
            kalshi_by_side=kal.get(game, {}),
            book_americans_by_side=book_sides,
            seconds_to_first_pitch=None,
        ))
    # Signals (steam/divergence) would be injected from the sharp tracker; empty here.
    return markets, {}
