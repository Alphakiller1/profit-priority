from __future__ import annotations

import json

import pytest

from profit_priority.feeds.genesis_outlook import load


def _outlook(tmp_path, *, authority="RESEARCH_ONLY"):
    payload = {
        "schema": "genesis/season-outlook/1",
        "season": 2026,
        "authority": authority,
        "generated_at_utc": "2026-08-17T22:50:00+00:00",
        "note": "research only",
        "week_one": [{"game": f"G{index}"} for index in range(16)],
        "division_projections": [{"division": f"D{index}"} for index in range(8)],
    }
    path = tmp_path / "outlook.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_genesis_outlook_is_context_only(tmp_path):
    outlook = load(_outlook(tmp_path))
    assert outlook.is_tradeable is False
    assert "may not be used" in outlook.refusal_reason
    assert len(outlook.week_one) == 16
    assert len(outlook.division_projections) == 8


def test_promoted_genesis_outlook_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="RESEARCH_ONLY"):
        load(_outlook(tmp_path, authority="PROMOTED"))
