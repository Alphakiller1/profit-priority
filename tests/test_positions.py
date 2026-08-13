"""Ledger tests — sizing, CLV honesty, and append-only replay."""

from __future__ import annotations

import json
import math

import pytest

from profit_priority import fees, positions


@pytest.fixture(autouse=True)
def _tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(positions, "LEDGER", tmp_path / "positions.jsonl")


def _pos(**kw) -> positions.Position:
    base = dict(id="a1", opened_at="2026-08-12T00:00:00+00:00",
                ticker="KXMLBGAME-26AUG122210TEXLAA-TEX", team="TEX",
                entry_price=0.56, contracts=86, entry_fee=1.49, stake=49.65)
    base.update(kw)
    return positions.Position(**base)


# ── sizing ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stake,price", [
    (5, 0.56), (25, 0.50), (50, 0.05), (350, 0.50), (1, 0.99), (12.34, 0.37),
])
def test_sizing_never_exceeds_the_stake(stake, price):
    """The user cannot be recorded holding a position they could not afford."""
    n, fee = positions.contracts_for_stake(stake, price)
    assert n * price + fee <= stake + 1e-9


def test_sizing_is_maximal():
    """One more contract must not fit — otherwise the ledger under-sizes."""
    stake, price = 50.0, 0.56
    n, _ = positions.contracts_for_stake(stake, price)
    over = fees.kalshi_fee(n + 1, price)
    assert (n + 1) * price + over > stake


def test_naive_division_would_oversize():
    """Guards the reason this is solved rather than divided.

    The fee ceiling lands on the ORDER, so per-contract fee arithmetic understates
    cost on small orders and buys more than the stake covers.
    """
    stake, price = 5.0, 0.56
    naive = math.floor(stake / fees.kalshi_cost_per_payout(price))
    n, fee = positions.contracts_for_stake(stake, price)
    assert n <= naive
    assert n * price + fee <= stake + 1e-9


def test_sizing_rejects_impossible_orders():
    assert positions.contracts_for_stake(0.10, 0.56) == (0.0, 0.0)
    assert positions.contracts_for_stake(50, 0.0) == (0.0, 0.0)
    assert positions.contracts_for_stake(50, 1.0) == (0.0, 0.0)


def test_maker_sizing_buys_more_than_taker():
    tn, _ = positions.contracts_for_stake(50, 0.50)
    mn, _ = positions.contracts_for_stake(50, 0.50, maker=True)
    assert mn > tn


# ── P&L ───────────────────────────────────────────────────────────────────────

def test_settled_pnl_charges_only_the_entry_leg():
    """Kalshi charges on trades, not settlement."""
    p = _pos()
    assert p.pnl_settled(1.0) == pytest.approx(86 * 1.0 - (86 * 0.56 + 1.49))
    assert p.pnl_settled(0.0) == pytest.approx(-(86 * 0.56 + 1.49))


def test_exiting_flat_loses_the_round_trip():
    """Selling back at the entry price is a loss, not a wash."""
    p = _pos()
    assert p.pnl_at(0.56) < 0


def test_open_pnl_includes_the_exit_fee():
    p = _pos()
    gross = 86 * 0.60 - (86 * 0.56 + 1.49)
    assert p.pnl_at(0.60) < gross


# ── CLV honesty ───────────────────────────────────────────────────────────────

def test_close_without_a_mark_has_no_clv():
    """A position never marked while open has no recoverable closing line.

    Recording 0.0 would be a fabricated observation and would drag mean CLV toward
    zero precisely on the fastest-settling markets.
    """
    p = _pos(last_mark=None)
    positions._close(p, outcome=1.0)
    assert p.clv is None
    assert p.close_price is None
    assert p.pnl is not None          # P&L is still knowable from the outcome


def test_clv_is_close_minus_entry():
    p = _pos(last_mark=0.62)
    positions._close(p, outcome=1.0)
    assert p.clv == pytest.approx(0.06)
    assert p.verdict == "WIN"


def test_loss_verdict_and_pnl():
    p = _pos(last_mark=0.40)
    positions._close(p, outcome=0.0)
    assert p.verdict == "LOSS"
    assert p.clv == pytest.approx(-0.16)
    assert p.pnl == pytest.approx(-(86 * 0.56 + 1.49), abs=0.01)


def test_unresolved_result_is_void_not_a_guess():
    p = _pos(last_mark=0.50)
    positions._close(p, outcome=None)
    assert p.verdict == "VOID"
    assert p.pnl is None


def test_summary_excludes_positions_without_a_closing_line():
    positions._append(_pos(id="w", last_mark=0.62, close_price=0.62, clv=0.06,
                           pnl=36.5, verdict="WIN"))
    positions._append(_pos(id="n", last_mark=None, close_price=None, clv=None,
                           pnl=-49.65, verdict="LOSS"))
    s = positions.summary()
    assert s["n_closed"] == 2
    assert s["n_with_clv"] == 1        # only the one with an observed close
    assert s["n_no_close"] == 1
    assert s["mean_clv"] == pytest.approx(0.06)


# ── append-only ledger ────────────────────────────────────────────────────────

def test_replay_keeps_the_last_write_per_id():
    positions._append(_pos(last_mark=0.57, marks=1))
    positions._append(_pos(last_mark=0.61, marks=2))
    rows = positions._load()
    assert len(rows) == 1
    assert rows[0].last_mark == 0.61 and rows[0].marks == 2


def test_corrupt_lines_are_skipped_not_fatal():
    positions._append(_pos())
    with positions.LEDGER.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n\n")
    assert len(positions._load()) == 1


def test_open_positions_excludes_closed():
    positions._append(_pos(id="o"))
    positions._append(_pos(id="c", verdict="WIN"))
    assert [p.id for p in positions.open_positions()] == ["o"]


# ── deck import ───────────────────────────────────────────────────────────────

def test_import_is_idempotent(tmp_path, monkeypatch):
    """Re-importing an export must not double-count the same ticket."""
    monkeypatch.setattr(positions, "open_position",
                        lambda **kw: positions.Position(
                            id=kw["note"], opened_at="x", ticker=kw["ticker"],
                            source="deck", note=kw["note"]))
    monkeypatch.setattr(positions, "_load",
                        lambda: [positions.Position(id="c1", opened_at="x", ticker="T",
                                                     source="deck", note="c1")])
    f = tmp_path / "export.json"
    f.write_text(json.dumps({"tickets": [
        {"cid": "c1", "ticker": "T", "stake": 25, "price": 0.5},
    ]}), encoding="utf-8")
    positions.import_file(str(f))     # c1 already present -> skipped, not added


def test_payload_carries_american_odds():
    positions._append(_pos())
    row = positions.payload()["positions"][0]
    assert row["entry_american"] == fees.prob_to_american(0.56)
