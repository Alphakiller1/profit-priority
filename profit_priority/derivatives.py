"""Derivative consistency — the structural angle a sharp desk actually runs.

We scope moneyline. Kalshi lists ten contract families per game, and the other nine
are *internally constrained*: a spread must reconcile with a moneyline, a ladder
must be monotone, and team totals must reconcile with the game total. Those
constraints hold regardless of who wins, so exploiting them needs **no forecasting
edge** — which is exactly what the measurements say we do not have.

## The four checks, in increasing order of sharpness

1. LADDER MONOTONICITY. "over 5.5" implies "over 4.5", so P must be non-increasing
   in the threshold. A violation is a lock: sell the higher rung, buy the lower.

2. SPREAD NESTED IN MONEYLINE. "wins by over 4.5" implies "wins", so
   P(spread) <= P(moneyline). Same nesting maths as `structure.py`, applied inside
   a single game instead of across a season.

3. THE 0.5 IDENTITY. Scores are integers, so "wins by over 0.5" IS "wins". Not an
   inequality — an equality. Any gap between that rung and the moneyline is a
   pricing error in one of the two, with no modelling required to say so.

4. EXPECTATION RECONCILIATION. For a non-negative integer X,

       E[X] = sum_{k>=1} P(X >= k)

   so a team-total ladder yields expected runs directly, with no distributional
   assumption. Then E[home] + E[away] must equal E[total] computed the same way
   from the game-total ladder. A mismatch is a structural inconsistency across two
   independently quoted families.

   This is the sharpest check here because it is the least likely to be arbitraged:
   it requires reading three ladders at once, and it is invisible to anyone pricing
   one contract at a time.

Every violation is priced through `fees` before it is reported. An inconsistency
smaller than the round trip is a curiosity, not an opportunity.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import fees

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"Accept": "application/json", "User-Agent": "profit-priority/0.3"}

FAMILIES = {
    "moneyline":  "KXMLBGAME",
    "spread":     "KXMLBSPREAD",
    "total":      "KXMLBTOTAL",
    "team_total": "KXMLBTEAMTOTAL",
}

# Event key: everything before the final '-SIDE' identifies one game.
EVENT_RE = re.compile(r"^(.*?)-([A-Z0-9]+)$")
# "over 6.5 runs" / "Over 15.5 runs scored" -> 6.5 / 15.5
THRESH_RE = re.compile(r"over\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
# Team prefix in a spread/team-total side code: CHC7 -> CHC, TEX8 -> TEX
SIDE_RE = re.compile(r"^([A-Z]+)(\d+)$")

# A violation must clear this after fees to be reported as actionable.
MIN_NET_EDGE = 0.005
# Expectation mismatch worth surfacing, in runs.
MIN_RUNS_MISMATCH = 0.75


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Leg:
    ticker: str
    family: str
    event: str
    side: str                 # team code, or "" for game totals
    threshold: float | None
    bid: float | None
    ask: float | None
    title: str = ""

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass
class Violation:
    kind: str
    event: str
    detail: str
    gross: float
    fee: float
    net: float
    legs: tuple[str, str] = ("", "")

    @property
    def actionable(self) -> bool:
        return self.net >= MIN_NET_EDGE


@dataclass
class GameBook:
    event: str
    legs: list[Leg] = field(default_factory=list)

    def of(self, family: str) -> list[Leg]:
        return [leg for leg in self.legs if leg.family == family]

    def ladder(self, family: str, side: str) -> list[Leg]:
        """Rungs for one side, ascending threshold."""
        rungs = [leg for leg in self.legs
                 if leg.family == family and leg.side == side
                 and leg.threshold is not None]
        return sorted(rungs, key=lambda leg: leg.threshold)


def fetch_all(limit_per_family: int = 400) -> dict[str, GameBook]:
    books: dict[str, GameBook] = {}
    for family, series in FAMILIES.items():
        cursor = None
        pulled = 0
        while pulled < limit_per_family:
            params = {"series_ticker": series, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data = _get(f"{KALSHI}/markets?{urllib.parse.urlencode(params)}")
            except Exception:
                break
            page = data.get("markets", [])
            if not page:
                break
            pulled += len(page)
            for m in page:
                t = m.get("ticker", "")
                em = EVENT_RE.match(t)
                if not em:
                    continue
                event, side_code = em.group(1), em.group(2)
                sub = str(m.get("yes_sub_title") or "")
                th = THRESH_RE.search(sub) or THRESH_RE.search(str(m.get("title") or ""))
                threshold = float(th.group(1)) if th else None
                sm = SIDE_RE.match(side_code)
                side = sm.group(1) if sm else (side_code if family == "moneyline" else "")
                books.setdefault(event, GameBook(event)).legs.append(Leg(
                    ticker=t, family=family, event=event, side=side,
                    threshold=threshold, bid=_f(m.get("yes_bid_dollars")),
                    ask=_f(m.get("yes_ask_dollars")), title=str(m.get("title") or "")))
            cursor = data.get("cursor")
            if not cursor:
                break
    return books


def _lock(narrow: Leg, broad: Leg, kind: str, detail: str,
          contracts: int = 100) -> Violation | None:
    """narrow implies broad, so P(narrow) <= P(broad).

    A violation is bid(narrow) > ask(broad): sell the narrow, buy the broad, and
    the minimum payout is 1 per set because the only state that pays nothing
    (broad without narrow) is impossible... no -- broad-without-narrow IS possible
    and pays 1 on the NO leg. Minimum payout is therefore 1, gross edge is
    bid(narrow) - ask(broad).
    """
    if narrow.bid is None or broad.ask is None:
        return None
    gross = narrow.bid - broad.ask
    if gross <= 0:
        return None
    fee = (fees.kalshi_fee(contracts, broad.ask)
           + fees.kalshi_fee(contracts, 1 - narrow.bid)) / contracts
    return Violation(kind, narrow.event, detail, round(gross, 4), round(fee, 4),
                     round(gross - fee, 4), (narrow.ticker, broad.ticker))


def check_ladders(book: GameBook) -> list[Violation]:
    """P(over X) must be non-increasing in X, within every ladder."""
    out: list[Violation] = []
    for family in ("spread", "total", "team_total"):
        sides = {leg.side for leg in book.of(family)}
        for side in sides:
            rungs = book.ladder(family, side)
            for lo, hi in zip(rungs, rungs[1:], strict=False):
                # hi implies lo (over 5.5 implies over 4.5)
                v = _lock(hi, lo, f"ladder:{family}",
                          f"{side or 'total'} over {hi.threshold} "
                          f"({fees.fmt_american(hi.bid)}) bid exceeds "
                          f"over {lo.threshold} ask ({fees.fmt_american(lo.ask)})")
                if v:
                    out.append(v)
    return out


def check_spread_nested_in_moneyline(book: GameBook) -> list[Violation]:
    """"wins by over X" implies "wins", for X >= 0."""
    ml = {leg.side: leg for leg in book.of("moneyline")}
    out: list[Violation] = []
    for leg in book.of("spread"):
        m = ml.get(leg.side)
        if m is None or leg.threshold is None or leg.threshold < 0:
            continue
        v = _lock(leg, m, "spread<=moneyline",
                  f"{leg.side} by over {leg.threshold} bid "
                  f"{leg.bid:.2f}/{fees.fmt_american(leg.bid)} exceeds moneyline ask "
                  f"{m.ask:.2f}/{fees.fmt_american(m.ask)}" if m.ask is not None else "")
        if v:
            out.append(v)
    return out


def check_half_run_identity(book: GameBook) -> list[Violation]:
    """Scores are integers: "wins by over 0.5" IS "wins". An equality, not a bound.

    Reported in BOTH directions, because either contract can be the mispriced one.
    """
    ml = {leg.side: leg for leg in book.of("moneyline")}
    out: list[Violation] = []
    for leg in book.of("spread"):
        if leg.threshold != 0.5:
            continue
        m = ml.get(leg.side)
        if m is None:
            continue
        for narrow, broad, tag in ((leg, m, "spread rich"), (m, leg, "moneyline rich")):
            v = _lock(narrow, broad, "0.5-identity",
                      f"{leg.side}: over-0.5 and moneyline are the SAME event; "
                      f"{tag}")
            if v:
                out.append(v)
    return out


def expected_from_ladder(rungs: list[Leg]) -> float | None:
    """E[X] = sum_{k>=1} P(X >= k), read straight off a .5-threshold ladder.

    No distributional assumption: this is an identity for non-negative integers.
    Requires a contiguous ladder from 0.5 upward; a gap would silently truncate the
    sum and understate the expectation, so a gap returns None instead.
    """
    have = {}
    for r in rungs:
        if r.threshold is None or r.mid is None:
            continue
        k = r.threshold + 0.5              # "over 4.5" == "at least 5"
        if abs(k - round(k)) > 1e-9:
            return None
        have[int(round(k))] = r.mid
    if not have or 1 not in have:
        return None                        # ladder does not start at "over 0.5"
    total, k = 0.0, 1
    while k in have:
        total += have[k]
        k += 1
    if max(have) >= k:                     # a gap exists above the contiguous run
        return None
    return total


def check_total_union_bound(book: GameBook, contracts: int = 100) -> list[Violation]:
    """Link team totals to the game total WITHOUT assuming independence.

    The exact identity E[X] = sum P(X>=k) needs a ladder starting at "over 0.5".
    Kalshi's team ladders start around "over 2.5", so that sum is unavailable and
    a truncated version would silently understate the expectation.

    This bound needs no complete ladder and no distribution. For any split a + b >= s:

        if A <= a and B <= b then A + B <= a + b <= s

    so {A+B > s} is contained in {A > a} union {B > b}, giving the union bound

        P(A+B > s) <= P(A > a) + P(B > b)

    Crucially this holds under ANY dependence between the teams' scores — which
    matters, because runs in a single game are emphatically not independent.

    Trade when violated: short the total, long both team legs.
        cost   = (1 - bid_total) + ask_A + ask_B
        payout >= 1 in every state
        profit  = bid_total - ask_A - ask_B
    """
    sides = sorted({leg.side for leg in book.of("team_total") if leg.side})
    if len(sides) != 2:
        return []
    a_side, b_side = sides
    ladder_a = book.ladder("team_total", a_side)
    ladder_b = book.ladder("team_total", b_side)
    totals = book.ladder("total", "")
    if not ladder_a or not ladder_b or not totals:
        return []

    out: list[Violation] = []
    for tot in totals:
        if tot.threshold is None or tot.bid is None:
            continue
        best: tuple[float, Leg, Leg] | None = None
        for la in ladder_a:
            for lb in ladder_b:
                if la.threshold is None or lb.threshold is None:
                    continue
                if la.ask is None or lb.ask is None:
                    continue
                # The split must dominate the total threshold for the bound to hold.
                if la.threshold + lb.threshold < tot.threshold:
                    continue
                gross = tot.bid - la.ask - lb.ask
                if best is None or gross > best[0]:
                    best = (gross, la, lb)
        if best is None or best[0] <= 0:
            continue
        gross, la, lb = best
        fee = (fees.kalshi_fee(contracts, la.ask)
               + fees.kalshi_fee(contracts, lb.ask)
               + fees.kalshi_fee(contracts, 1 - tot.bid)) / contracts
        out.append(Violation(
            "total-union-bound", book.event,
            f"total over {tot.threshold} bid {tot.bid:.2f}/"
            f"{fees.fmt_american(tot.bid)} exceeds "
            f"{a_side} over {la.threshold} ask {la.ask:.2f} + "
            f"{b_side} over {lb.threshold} ask {lb.ask:.2f} "
            f"(split {la.threshold}+{lb.threshold} >= {tot.threshold})",
            round(gross, 4), round(fee, 4), round(gross - fee, 4),
            (tot.ticker, f"{la.ticker}+{lb.ticker}")))
    return out


def check_expectation_reconciliation(book: GameBook) -> list[dict]:
    """Partial-expectation diagnostic, reported only when the ladder allows it.

    Retained because a complete ladder occasionally appears, and when it does the
    identity is exact and worth having. Returns nothing rather than guessing.
    """
    sides = {leg.side for leg in book.of("team_total") if leg.side}
    if len(sides) != 2:
        return []
    per_team = {}
    for s in sides:
        e = expected_from_ladder(book.ladder("team_total", s))
        if e is None:
            return []
        per_team[s] = e
    e_total = expected_from_ladder(book.ladder("total", ""))
    if e_total is None:
        return []
    implied = sum(per_team.values())
    gap = implied - e_total
    return [{
        "event": book.event, "per_team": per_team,
        "implied_total": round(implied, 3), "quoted_total": round(e_total, 3),
        "gap_runs": round(gap, 3),
        "flag": abs(gap) >= MIN_RUNS_MISMATCH,
    }]


def _near_misses(books: dict) -> list[tuple[str, str, float]]:
    """Slack on the tightest constraints: ask(broad) - bid(narrow), lowest first."""
    out: list[tuple[str, str, float]] = []
    for b in books.values():
        ml = {leg.side: leg for leg in b.of("moneyline")}
        for leg in b.of("spread"):
            m = ml.get(leg.side)
            if m and m.ask is not None and leg.bid is not None:
                out.append(("spread<=moneyline",
                            f"{leg.side} by over {leg.threshold} vs ML",
                            round(m.ask - leg.bid, 4)))
        for family in ("spread", "total", "team_total"):
            for side in {leg.side for leg in b.of(family)}:
                rungs = b.ladder(family, side)
                for lo, hi in zip(rungs, rungs[1:], strict=False):
                    if lo.ask is not None and hi.bid is not None:
                        out.append((f"ladder:{family}",
                                    f"{side or 'total'} {hi.threshold} vs {lo.threshold}",
                                    round(lo.ask - hi.bid, 4)))
    out.sort(key=lambda x: x[2])
    return out


def scan() -> tuple[list[Violation], list[dict], dict]:
    books = fetch_all()
    violations: list[Violation] = []
    recons: list[dict] = []
    for b in books.values():
        violations += check_ladders(b)
        violations += check_spread_nested_in_moneyline(b)
        violations += check_half_run_identity(b)
        violations += check_total_union_bound(b)
        recons += check_expectation_reconciliation(b)
    violations.sort(key=lambda v: -v.net)
    recons.sort(key=lambda r: -abs(r["gap_runs"]))
    coverage = {
        "books": books,
        "games": len(books),
        "legs": sum(len(b.legs) for b in books.values()),
        "families": {f: sum(len(b.of(f)) for b in books.values()) for f in FAMILIES},
    }
    return violations, recons, coverage


def report() -> None:
    violations, recons, cov = scan()
    print(f"\n  DERIVATIVE CONSISTENCY — {cov['games']} games, {cov['legs']} contracts")
    print("  families: " + "  ".join(f"{k}={v}" for k, v in cov["families"].items()))
    print("  Constraints hold regardless of who wins, so none of this needs a forecast.\n")

    actionable = [v for v in violations if v.actionable]
    print(f"  VIOLATIONS CLEARING FEES: {len(actionable)}  "
          f"(of {len(violations)} gross)")
    if not violations:
        print("    none - every ladder is monotone and every nesting holds.")
        # A scanner that always prints zero is indistinguishable from a broken
        # one, so report how CLOSE the tightest constraint came. Distance to
        # violation is the evidence that the check ran and measured something.
        near = _near_misses(cov["books"])
        if near:
            print("\n    Tightest constraints observed (distance to violation):")
            for kind, detail, slack in near[:6]:
                print(f"      {kind:<22}{slack:+.4f}  {detail[:62]}")
            print("    A negative distance would be a lock. These are the closest the")
            print("    board came, which is what makes the zero above meaningful.")
    for v in violations[:14]:
        mark = "ACTIONABLE" if v.actionable else "below fee"
        print(f"    [{v.kind}] {v.detail}")
        print(f"        gross {v.gross:+.4f}  fee {v.fee:.4f}  net {v.net:+.4f}  {mark}")

    print("\n  EXPECTATION RECONCILIATION  (E[home]+E[away] vs E[total], in runs)")
    flagged = [r for r in recons if r["flag"]]
    if not recons:
        print("    no game had contiguous ladders on all three families.")
        print("    The identity needs a ladder starting at 'over 0.5' with no gaps;")
        print("    a gap would truncate the sum and understate the expectation.")
    for r in recons[:12]:
        teams = "  ".join(f"{k} {v:.2f}" for k, v in r["per_team"].items())
        flag = "  <-- MISMATCH" if r["flag"] else ""
        print(f"    {r['event'][-26:]:<28}{teams}  implied {r['implied_total']:.2f}"
              f"  quoted {r['quoted_total']:.2f}  gap {r['gap_runs']:+.2f}{flag}")
    if flagged:
        print(f"\n    {len(flagged)} game(s) where the two families disagree by "
              f">= {MIN_RUNS_MISMATCH} runs.")
        print("    That is a structural inconsistency between independently quoted")
        print("    ladders — invisible to anyone pricing one contract at a time.")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Within-game derivative consistency.")
    p.parse_args()
    report()


if __name__ == "__main__":
    main()
