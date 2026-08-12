"""The NFL feed must refuse to turn an unpromoted forecast into a value edge."""

from __future__ import annotations

import json

import pytest

from profit_priority.feeds.nfl_model import MIN_MEANINGFUL_LAMBDA, load


def _slate(tmp_path, *, authority="RESEARCH_ONLY", may_bet=False, lam=0.0,
           unmet=("probability_space_clv_above_zero",), skipped=()):
    payload = {
        "schema": "nfl-model/forecast/1",
        "generated_at_utc": "2026-08-12T08:00:00+00:00",
        "authority": authority,
        "may_bet": may_bet,
        "lam": lam,
        "unmet_gates": list(unmet),
        "evidence": "nfl-genesis baseline",
        "games": [{
            "game": "LAC@KC", "home_team": "KC", "away_team": "LAC",
            "home_fair": 0.62, "away_fair": 0.38,
            "home_american": -163, "away_american": 163,
            "edge_vs_market": 0.0, "action": "MONITOR",
        }],
        "skipped": list(skipped),
    }
    path = tmp_path / "slate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_research_only_forecast_yields_no_value_candidates(tmp_path) -> None:
    """The central guard: an unpromoted model cannot become an edge."""
    feed = load(_slate(tmp_path))
    assert feed.is_tradeable is False
    assert feed.value_candidates() == []
    assert "unmet production gate" in feed.refusal_reason


def test_promoted_but_zero_lambda_is_still_refused(tmp_path) -> None:
    """lam=0 means the forecast IS the market; an edge against it is arithmetic noise.

    Promotion alone is not sufficient. Without this the feed would happily compare
    the market to itself and report the rounding as opportunity.
    """
    feed = load(_slate(tmp_path, authority="PROMOTED", may_bet=True, lam=0.0, unmet=()))
    assert feed.may_bet is True
    assert feed.is_tradeable is False
    assert "equals the paired no-vig market" in feed.refusal_reason


def test_promoted_with_real_shrinkage_is_tradeable(tmp_path) -> None:
    feed = load(_slate(tmp_path, authority="PROMOTED", may_bet=True, lam=0.30, unmet=()))
    assert feed.is_tradeable is True
    assert len(feed.value_candidates()) == 1


def test_lambda_at_the_threshold_is_not_meaningful(tmp_path) -> None:
    feed = load(_slate(tmp_path, authority="PROMOTED", may_bet=True,
                       lam=MIN_MEANINGFUL_LAMBDA, unmet=()))
    assert feed.is_tradeable is False


def test_fair_lookup_by_selection(tmp_path) -> None:
    forecast = load(_slate(tmp_path)).forecasts[0]
    assert forecast.fair_for("KC") == pytest.approx(0.62)
    assert forecast.fair_for("LAC") == pytest.approx(0.38)
    assert forecast.fair_for("DEN") is None      # unknown team must not guess


def test_skipped_games_are_preserved_not_dropped(tmp_path) -> None:
    path = _slate(tmp_path, skipped=[{"game": "NYJ@BUF", "action": "AVOID",
                                      "reason": "KeyError: home_american"}])
    feed = load(path)
    assert len(feed.skipped) == 1
    assert feed.skipped[0]["action"] == "AVOID"


def test_unknown_schema_is_rejected(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "nfl-model/forecast/999"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected forecast schema"):
        load(path)
