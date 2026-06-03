"""
Learning loop — log EVERY candidate (accepted or rejected), so the funnel and the
manufactured-arb hedge-conversion rate are measured from real data, not guessed.
This is the cheapest, highest-value part of the whole system: start banking the
record now and the scoring weights can be refit honestly later.

Append-only JSONL so it is safe to tail, replay, and analyze.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

from . import config


def _serialize(obj):
    if dataclasses.is_dataclass(obj):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    return obj


def log_candidate(kind: str, candidate, extra: dict | None = None) -> None:
    """Append one candidate record. kind in {pure_arb, value, manufactured}."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "accepted": bool(getattr(candidate, "accepted", False)),
        "candidate": _serialize(candidate),
    }
    if extra:
        rec.update(extra)
    with open(config.LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def log_all(kind: str, candidates) -> int:
    for c in candidates:
        log_candidate(kind, c)
    return len(candidates)
