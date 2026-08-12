"""The learning loop must grade honestly and distrust itself on evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from profit_priority import learning


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never touch the real ledger from a test."""
    monkeypatch.setattr(learning, "CANDIDATES", tmp_path / "cands.jsonl")
    monkeypatch.setattr(learning, "SCANS", tmp_path / "scans.jsonl")


def _write(rows: list[dict]) -> None:
    learning.CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    learning.CANDIDATES.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def _row(kind="saving", verdict=None, realised=None, hours_ago=1.0) -> dict:
    return {
        "id": f"x{abs(hash((kind, verdict, realised, hours_ago))) % 10**6}",
        "detected_at": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
        "kind": kind, "series": "MLBPLAYOFFS", "team": "DET",
        "edge": 0.05, "verdict": verdict, "realised": realised,
        "graded_at": None if verdict is None else datetime.now(UTC).isoformat(),
    }


# ── trust: the adaptation signal ──────────────────────────────────────────────

def test_detector_with_too_few_samples_is_not_judged() -> None:
    """Absence of evidence must not be reported as good performance."""
    _write([_row(verdict="HELD") for _ in range(5)])
    t = learning.trust()["saving"]
    assert t["trusted"] is None
    assert t["verdict"] == "insufficient evidence"


def test_detector_below_the_floor_is_marked_untrusted() -> None:
    """The loop closing: the system stops believing its own failing detector."""
    rows = [_row(verdict="HELD", realised=0.04) for _ in range(6)]
    rows += [_row(verdict="REVERSED", realised=-0.01) for _ in range(20)]
    _write(rows)
    t = learning.trust()["saving"]
    assert t["judged"] == 26
    assert t["hit_rate"] < learning.TRUST_FLOOR
    assert t["trusted"] is False
    assert t["verdict"] == "UNTRUSTED"


def test_detector_above_the_floor_is_trusted() -> None:
    rows = [_row(verdict="HELD", realised=0.04) for _ in range(25)]
    rows += [_row(verdict="DECAYED", realised=0.01) for _ in range(5)]
    _write(rows)
    t = learning.trust()["saving"]
    assert t["trusted"] is True


def test_abandoned_candidates_do_not_count_against_hit_rate() -> None:
    """A quote that vanished is not a wrong call; it is an unanswered question."""
    rows = [_row(verdict="HELD") for _ in range(20)]
    rows += [_row(verdict="ABANDONED") for _ in range(50)]
    _write(rows)
    t = learning.trust()["saving"]
    assert t["judged"] == 20
    assert t["abandoned"] == 50
    assert t["hit_rate"] == pytest.approx(1.0)


# ── grading discipline ────────────────────────────────────────────────────────

def test_young_candidates_are_not_graded(monkeypatch) -> None:
    """Grading immediately would measure noise, not what the market did."""
    _write([_row(hours_ago=0.05)])          # 3 minutes old
    monkeypatch.setattr(learning, "crossvenue", None, raising=False)
    called = {"n": 0}

    class _Fake:
        @staticmethod
        def compare():
            called["n"] += 1
            return []

    import sys
    monkeypatch.setitem(sys.modules, "profit_priority.crossvenue", _Fake)
    learning.grade()
    rows = learning._load(learning.CANDIDATES)
    assert rows[0].get("graded_at") is None


def test_report_flags_a_never_graded_ledger(capsys) -> None:
    """The blind-spot section must catch a write-only log."""
    _write([_row() for _ in range(3)])
    learning.report()
    out = capsys.readouterr().out
    assert "every candidate is ungraded" in out


def test_report_states_unknown_reliability_rather_than_assuming(capsys) -> None:
    _write([_row()])
    learning.report()
    out = capsys.readouterr().out
    assert "unknown, not assumed good" in out


def test_report_warns_when_coverage_has_stopped(capsys) -> None:
    """Zero candidates is only good news if we were still looking."""
    learning.SCANS.parent.mkdir(parents=True, exist_ok=True)
    stale = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    learning.SCANS.write_text(json.dumps({
        "id": "s1", "scanned_at": stale, "scanner": "crossvenue",
        "markets_seen": 10, "candidates": 0}) + "\n", encoding="utf-8")
    learning.report()
    out = capsys.readouterr().out
    assert "the loop has stopped running" in out


# ── deduplication: one dislocation must not become many samples ───────────────

def test_open_keys_tracks_ungraded_candidates_only() -> None:
    """A graded candidate is closed; the same dislocation may be recorded again."""
    _write([
        {**_row(), "kind": "saving", "series": "S", "team": "DET", "graded_at": None},
        {**_row(), "kind": "lock", "series": "S", "team": "COL",
         "graded_at": datetime.now(UTC).isoformat()},
    ])
    keys = learning._open_keys()
    assert ("saving", "S", "DET") in keys
    assert ("lock", "S", "COL") not in keys      # already graded, so re-recordable


def test_duplicate_open_candidate_is_not_recorded_twice(monkeypatch) -> None:
    """The loop runs every few hours against a slow board.

    Without this, one persistent quote enters the ledger dozens of times and
    trust() reports a hit rate with false confidence.
    """
    class _Cmp:
        series, team = "MLBPLAYOFFS", "DET"
        buy_saving = 0.05
        best_buy = ("kalshi", 0.50)
        lock = None

        class kalshi:  # noqa: N801
            all_in_ask = 0.50
            net_bid = 0.48

        class poly:  # noqa: N801
            all_in_ask = 0.55
            net_bid = 0.53

    import sys
    monkeypatch.setitem(sys.modules, "profit_priority.crossvenue",
                        type("M", (), {"compare": staticmethod(lambda: [_Cmp()])}))

    learning.record()
    first = len(learning._load(learning.CANDIDATES))
    learning.record()
    second = len(learning._load(learning.CANDIDATES))
    assert first == 1
    assert second == 1, "the same open candidate was recorded twice"
