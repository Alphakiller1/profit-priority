"""Game board: maker room needs width AND flow; days must not drift on timezone."""

from __future__ import annotations

import pytest

from profit_priority.games import GameEvent, Side, _parse_ticker


def _side(bid, ask, volume, team="AAA") -> Side:
    return Side(team=team, ticker=f"KXMLBGAME-26AUG12X-{team}",
                bid=bid, ask=ask, volume=volume)


# ── the maker-room rule ───────────────────────────────────────────────────────

def test_wide_spread_without_volume_is_not_maker_room() -> None:
    """The measured failure: markets wide enough to make in do not trade.

    Width alone would advertise a market with no counterparty, where a resting
    order simply never fills.
    """
    s = _side(bid=0.10, ask=0.16, volume=3)
    assert s.spread == pytest.approx(0.06)
    assert s.spread > (s.maker_rt or 0) * 2
    assert s.has_maker_room is False
    assert s.verdict == "no volume"


def test_width_with_volume_is_maker_room() -> None:
    s = _side(bid=0.10, ask=0.16, volume=5000)
    assert s.has_maker_room is True
    assert s.verdict == "maker room"


def test_spread_below_the_maker_fee_is_too_tight() -> None:
    """Below the round trip the trade loses before it is even wrong."""
    s = _side(bid=0.499, ask=0.5005, volume=100000)
    assert s.spread < (s.maker_rt or 1)
    assert s.verdict == "too tight"


def test_longshot_prices_clear_the_fee_more_easily() -> None:
    """Fee scales with p(1-p); the 1c tick does not. Extremes have the best ratio.

    This is why maker room clusters at longshots rather than near 50c.
    """
    longshot = _side(bid=0.12, ask=0.13, volume=10000)
    coinflip = _side(bid=0.50, ask=0.51, volume=10000)
    assert longshot.spread == coinflip.spread          # same 1c tick
    assert longshot.maker_rt < coinflip.maker_rt       # cheaper to trade
    assert longshot.has_maker_room and not coinflip.has_maker_room


# ── ranking is ordering, not an edge claim ────────────────────────────────────

def test_rank_rewards_width_paired_with_flow() -> None:
    wide_thin = GameEvent("mlb", "2026-08-12", "A vs B",
                          [_side(0.10, 0.16, 5), _side(0.84, 0.90, 5)])
    wide_deep = GameEvent("mlb", "2026-08-12", "C vs D",
                          [_side(0.10, 0.16, 9000), _side(0.84, 0.90, 9000)])
    assert wide_deep.rank_score > wide_thin.rank_score


def test_overround_is_only_defined_for_a_two_way_market() -> None:
    one = GameEvent("mlb", "2026-08-12", "A vs B", [_side(0.5, 0.51, 100)])
    assert one.overround is None


# ── ticker parsing ────────────────────────────────────────────────────────────

def test_parses_mlb_ticker_with_a_time_block() -> None:
    got = _parse_ticker("KXMLBGAME-26AUG142210MILLAD-MIL")
    assert got is not None
    date, _key, side = got
    assert date == "2026-08-14"
    assert side == "MIL"


def test_parses_nfl_ticker_without_a_time_block() -> None:
    got = _parse_ticker("KXNFLGAME-26AUG13GBPIT-GB")
    assert got is not None
    date, _key, side = got
    assert date == "2026-08-13"
    assert side == "GB"


def test_unparseable_ticker_returns_none_rather_than_guessing() -> None:
    assert _parse_ticker("NOT-A-TICKER") is None
    assert _parse_ticker("") is None
