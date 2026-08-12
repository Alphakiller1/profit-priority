"""NFL model feed — consume a forecast WITHOUT laundering its promotion state.

`nfl-model` emits a forecast contract alongside an authority level. This adapter's
job is mostly to refuse: a `RESEARCH_ONLY` forecast may inform a board, and may
never become a value edge or a stake.

That refusal is the entire point. The published NFL evidence
(`nfl-genesis reports/EMPIRICAL_BASELINE_2016_2025.md`) selected a deviation
shrinkage of `lam = 0.000` in all five folds from 2021 onward, meaning the
structural component carries no incremental information over a paired no-vig
closing line. At `lam = 0` the forecast *equals* the market by construction, so any
"edge" computed from it against that same market is arithmetic noise, not signal.

Feeding it into `opportunities.detect_value` unguarded would manufacture exactly
the kind of phantom edge this repo exists to eliminate — the probability analogue
of the midpoint-with-no-fees bug in the original tracker.

    python -m profit_priority.feeds.nfl_model --forecast artifacts/nfl_slate.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import fees

# Only these levels may contribute a tradeable fair probability.
TRADEABLE_AUTHORITY = {"PROMOTED"}
# Below this the forecast is treated as market-equivalent regardless of authority:
# a shrinkage this small cannot move a price enough to matter.
MIN_MEANINGFUL_LAMBDA = 1e-9


@dataclass(frozen=True)
class NflForecast:
    game: str
    home_team: str
    away_team: str
    home_fair: float
    away_fair: float
    home_american: int
    away_american: int
    edge_vs_market: float
    action: str

    def fair_for(self, selection: str) -> float | None:
        if selection == self.home_team:
            return self.home_fair
        if selection == self.away_team:
            return self.away_fair
        return None


@dataclass(frozen=True)
class NflFeed:
    authority: str
    may_bet: bool
    lam: float
    unmet_gates: tuple[str, ...]
    evidence: str
    generated_at: str
    forecasts: tuple[NflForecast, ...]
    skipped: tuple[dict, ...] = ()

    @property
    def is_tradeable(self) -> bool:
        """True only if BOTH the gate passes and the model actually differs from market."""
        return (
            self.may_bet
            and self.authority in TRADEABLE_AUTHORITY
            and self.lam > MIN_MEANINGFUL_LAMBDA
        )

    @property
    def refusal_reason(self) -> str:
        if not self.may_bet or self.authority not in TRADEABLE_AUTHORITY:
            return (f"authority {self.authority} with {len(self.unmet_gates)} unmet "
                    f"production gate(s); forecasts are MONITOR-only")
        if self.lam <= MIN_MEANINGFUL_LAMBDA:
            return ("lam = 0, so the forecast equals the paired no-vig market by "
                    "construction; any edge against that market is arithmetic noise")
        return ""

    def value_candidates(self) -> list[NflForecast]:
        """Forecasts eligible to become value edges. Empty while unpromoted."""
        return list(self.forecasts) if self.is_tradeable else []


def load(path: str | Path) -> NflFeed:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "nfl-model/forecast/1":
        raise ValueError(f"Unexpected forecast schema: {payload.get('schema')!r}")
    games = tuple(
        NflForecast(
            game=str(g["game"]), home_team=str(g["home_team"]),
            away_team=str(g["away_team"]),
            home_fair=float(g["home_fair"]), away_fair=float(g["away_fair"]),
            home_american=int(g["home_american"]),
            away_american=int(g["away_american"]),
            edge_vs_market=float(g.get("edge_vs_market", 0.0)),
            action=str(g.get("action", "MONITOR")),
        )
        for g in payload.get("games", [])
    )
    return NflFeed(
        authority=str(payload.get("authority", "RESEARCH_ONLY")),
        may_bet=bool(payload.get("may_bet", False)),
        lam=float(payload.get("lam", 0.0)),
        unmet_gates=tuple(payload.get("unmet_gates", ())),
        evidence=str(payload.get("evidence", "")),
        generated_at=str(payload.get("generated_at_utc", "")),
        forecasts=games,
        skipped=tuple(payload.get("skipped", ())),
    )


def report(path: str | Path) -> None:
    feed = load(path)
    print(f"\n  NFL MODEL FEED — {feed.generated_at}")
    print(f"  authority : {feed.authority}   may_bet: {feed.may_bet}   lam: {feed.lam}")
    print(f"  games     : {len(feed.forecasts)}   skipped: {len(feed.skipped)}")
    if not feed.is_tradeable:
        print(f"\n  NOT TRADEABLE — {feed.refusal_reason}")
        print("  Forecasts below are shown for monitoring only; they contribute no")
        print("  value edges and cannot be staked.\n")
    print(f"  {'game':<14}{'home':>7}{'home US':>10}{'away':>8}{'away US':>10}"
          f"{'edge':>9}  action")
    print("  " + "-" * 66)
    for f in feed.forecasts[:25]:
        print(f"  {f.game[:13]:<14}{f.home_fair:>7.4f}"
              f"{fees.fmt_american(f.home_fair):>10}{f.away_fair:>8.4f}"
              f"{fees.fmt_american(f.away_fair):>10}{f.edge_vs_market:>+9.4f}"
              f"  {f.action}")
    for s in feed.skipped[:8]:
        print(f"  {str(s.get('game'))[:13]:<14}{'—':>7}{'':>10}{'—':>8}{'':>10}"
              f"{'':>9}  AVOID  ({s.get('reason','')[:40]})")
    if feed.evidence:
        print(f"\n  evidence: {feed.evidence}")
    print()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Read an nfl-model forecast contract.")
    p.add_argument("--forecast", required=True, help="path to nfl-model slate JSON")
    report(p.parse_args().forecast)


if __name__ == "__main__":
    main()
