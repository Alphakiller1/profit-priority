"""
Executable price assembly.

Turns raw venue data into per-side EXECUTABLE quotes:
  - Kalshi: the YES *ask* (what you pay to buy), plus available liquidity + age.
  - Sportsbooks: the BEST decimal odds across the books you can actually bet, by side.
  - A fair-probability anchor de-vigged from SHARP books only (never soft consensus).

This is the layer the original tracker got wrong (midpoints + median consensus).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config
from .fees import american_to_decimal, decimal_to_implied, devig_two_way


@dataclass
class SideQuote:
    selection: str
    # Kalshi (binary, $ per contract)
    kalshi_yes_ask: Optional[float] = None
    kalshi_yes_bid: Optional[float] = None
    kalshi_liquidity: Optional[float] = None
    kalshi_age_sec: Optional[float] = None
    # Best executable sportsbook for this side
    book_key: Optional[str] = None
    book_decimal: Optional[float] = None
    book_age_sec: Optional[float] = None
    # Fair anchor (de-vigged sharp)
    fair_prob: Optional[float] = None


@dataclass
class GameMarket:
    game: str                       # 'AWAY@HOME'
    market_type: str = "ml"
    sides: dict[str, SideQuote] = field(default_factory=dict)
    seconds_to_first_pitch: Optional[float] = None

    def two_sides(self):
        keys = list(self.sides.keys())
        return (self.sides[keys[0]], self.sides[keys[1]]) if len(keys) == 2 else None


def fair_reference(sharp_implied: dict[str, float]) -> dict[str, float]:
    """De-vig a two-way sharp market into fair probabilities per selection."""
    keys = list(sharp_implied.keys())
    if len(keys) != 2:
        return {}
    fa, fb = devig_two_way(sharp_implied[keys[0]], sharp_implied[keys[1]])
    return {keys[0]: fa, keys[1]: fb}


def best_execution_book(book_americans: dict[str, int | float]) -> tuple[Optional[str], Optional[float]]:
    """Best (highest decimal = cheapest cost-per-payout) book we can actually bet."""
    best_key, best_dec = None, None
    for key, american in book_americans.items():
        if key not in config.EXECUTION_BOOKS:
            continue
        dec = american_to_decimal(american)
        if best_dec is None or dec > best_dec:
            best_key, best_dec = key, dec
    return best_key, best_dec


def assemble_market(game: str,
                    kalshi_by_side: dict[str, dict],
                    book_americans_by_side: dict[str, dict[str, int | float]],
                    seconds_to_first_pitch: Optional[float] = None) -> GameMarket:
    """Build a GameMarket from raw feed pieces.

    kalshi_by_side[sel] = {yes_ask, yes_bid, liquidity, age_sec}
    book_americans_by_side[sel] = {book_key: american_odds, ...}  (all books seen)
    """
    gm = GameMarket(game=game, seconds_to_first_pitch=seconds_to_first_pitch)

    # Fair anchor from sharp books across the two sides.
    sharp_implied: dict[str, float] = {}
    for sel, books in book_americans_by_side.items():
        sharp = [american_to_decimal(o) for k, o in books.items() if k in config.SHARP_BOOKS]
        if sharp:
            # use the tightest (lowest implied) sharp price per side as the anchor input
            sharp_implied[sel] = min(decimal_to_implied(d) for d in sharp)
    fair = fair_reference(sharp_implied) if len(sharp_implied) == 2 else {}

    for sel in book_americans_by_side:
        bk, bd = best_execution_book(book_americans_by_side.get(sel, {}))
        k = kalshi_by_side.get(sel, {})
        gm.sides[sel] = SideQuote(
            selection=sel,
            kalshi_yes_ask=k.get("yes_ask"),
            kalshi_yes_bid=k.get("yes_bid"),
            kalshi_liquidity=k.get("liquidity"),
            kalshi_age_sec=k.get("age_sec"),
            book_key=bk, book_decimal=bd, book_age_sec=k.get("book_age_sec"),
            fair_prob=fair.get(sel),
        )
    return gm


def is_stale(age_sec: Optional[float]) -> bool:
    return age_sec is not None and age_sec > config.MAX_PRICE_AGE_SEC
