"""Read Genesis preseason context without turning it into a tradeable probability.

The Genesis outlook is a research-only, rating-derived artifact. Profit Priority may display
it alongside division markets for context, but it cannot enter value detection, structural
arbitrage, or staking. Those products require either executable market prices or a separately
promoted model contract.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


OUTLOOK_SCHEMA = "genesis/season-outlook/1"


@dataclass(frozen=True)
class GenesisOutlook:
    season: int
    authority: str
    generated_at: str
    note: str
    week_one: tuple[dict, ...]
    division_projections: tuple[dict, ...]

    @property
    def is_tradeable(self) -> bool:
        """Never tradeable: this schema is published only for research context."""
        return False

    @property
    def refusal_reason(self) -> str:
        return (
            "Genesis season outlook is research-only preseason context; it may not be used "
            "for value detection, structural-arbitrage sizing, or staking."
        )


def load(path: str | Path) -> GenesisOutlook:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != OUTLOOK_SCHEMA:
        raise ValueError(f"Unexpected Genesis outlook schema: {payload.get('schema')!r}")
    if payload.get("authority") != "RESEARCH_ONLY":
        raise ValueError("Genesis outlook authority must be RESEARCH_ONLY")
    week_one = tuple(payload.get("week_one") or ())
    divisions = tuple(payload.get("division_projections") or ())
    if len(week_one) != 16 or len(divisions) != 8:
        raise ValueError("Genesis outlook requires 16 Week 1 games and 8 division projections")
    return GenesisOutlook(
        season=int(payload["season"]),
        authority=str(payload["authority"]),
        generated_at=str(payload.get("generated_at_utc", "")),
        note=str(payload.get("note", "")),
        week_one=week_one,
        division_projections=divisions,
    )


def report(path: str | Path) -> None:
    outlook = load(path)
    print(f"\n  GENESIS NFL OUTLOOK — {outlook.season} · {outlook.generated_at}")
    print(f"  authority : {outlook.authority}")
    print(f"  status    : NOT TRADEABLE — {outlook.refusal_reason}\n")
    print(f"  {'division':<12}{'leader':>9}{'proj W':>9}{'runner-up':>12}{'gap':>8}")
    print("  " + "-" * 52)
    for row in outlook.division_projections:
        print(
            f"  {str(row['division']):<12}{str(row['team']):>9}"
            f"{float(row['projected_wins']):>9.1f}{str(row['runner_up']):>12}"
            f"{float(row['rating_gap']):>+8.2f}"
        )
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Read a research-only Genesis outlook.")
    parser.add_argument("--outlook", required=True, help="path to genesis season-outlook JSON")
    report(parser.parse_args().outlook)


if __name__ == "__main__":
    main()
