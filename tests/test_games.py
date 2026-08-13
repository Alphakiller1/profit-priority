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


# ── scope constraint: pre-game only ───────────────────────────────────────────

def test_started_game_is_not_tradeable() -> None:
    """In-game markets are out of scope, so the board must exclude them."""
    from datetime import UTC, datetime
    from profit_priority.games import is_pregame
    assert is_pregame("KXMLBGAME-20AUG121840CLEDET-DET", "2020-08-12",
                      now=datetime(2020, 8, 13, 3, 0, tzinfo=UTC)) is False


def test_future_game_is_tradeable() -> None:
    from datetime import UTC, datetime
    from profit_priority.games import is_pregame
    assert is_pregame("KXMLBGAME-20AUG122210TEXLAA-TEX", "2020-08-12",
                      now=datetime(2020, 8, 12, 20, 0, tzinfo=UTC)) is True


def test_game_inside_the_buffer_is_not_tradeable() -> None:
    """A market minutes from first pitch is effectively live."""
    from datetime import UTC, datetime
    from profit_priority.games import is_pregame
    # 22:10 ET == 02:10 UTC next day; 5 minutes before that.
    assert is_pregame("KXMLBGAME-20AUG122210TEXLAA-TEX", "2020-08-12",
                      now=datetime(2020, 8, 13, 2, 5, tzinfo=UTC)) is False


def test_unknown_start_returns_none_not_true() -> None:
    """WNBA/NFL tickers carry no time block.

    Returning True would silently admit a live market and break the constraint
    in exactly the way it was set to prevent.
    """
    from datetime import UTC, datetime
    from profit_priority.games import is_pregame
    assert is_pregame("KXNFLGAME-20AUG13GBPIT-GB", "2020-08-13",
                      now=datetime(2020, 8, 13, 18, 0, tzinfo=UTC)) is None


def test_unknown_start_is_excluded_from_the_board() -> None:
    from profit_priority.games import GameEvent
    unknown = GameEvent("nfl", "2026-08-13", "A vs B", [], pregame=None)
    started = GameEvent("mlb", "2026-08-12", "C vs D", [], pregame=False)
    ok = GameEvent("mlb", "2026-08-12", "E vs F", [], pregame=True)
    assert unknown.tradeable is False
    assert started.tradeable is False
    assert ok.tradeable is True
