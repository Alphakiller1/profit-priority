"""Self-learning loop — record, grade, report, and change behaviour.

`logger.py` records every candidate. Nothing has ever graded them, so the record
has been write-only: a log that is never read is a diary, not a learning system.
This module closes the loop.

Four stages, in strict order. Skipping any one breaks the others:

  1. RECORD    every candidate WITH the price it was seen at, before the outcome
               is known. `logger.log_candidate` already does this.
  2. GRADE     revisit each candidate later and ask what the market did. Graded
               mechanically against a rule fixed in advance, never re-chosen after
               seeing the answer.
  3. REPORT    say what changed, what was missed, and -- the part usually omitted --
               WHAT WE STOPPED SEEING.
  4. ADAPT     feed measured hit rates back as calibration, so a detector that has
               never been right stops being trusted.

## Why grading on price rather than outcome

A cross-venue saving is realised the instant you choose the cheaper venue; a
structural lock is realised at settlement, which can be months. Waiting on
settlement to learn anything would make the feedback loop slower than the trading
loop, which is useless.

So candidates are graded on what the MARKET did next, not on who won:

    saving   -> did the cheaper venue stay cheaper? (was the saving real)
    lock     -> did the two venues converge? (was it a real dislocation)
    maker    -> did the spread persist long enough to have been filled?

That resolves in hours and cannot be faked by a confident-looking pipeline.

## The blind-spot rule

Every silent failure in this stack has been indistinguishable from "nothing was
there" unless coverage itself is recorded. `scan_log` exists so that zero
candidates with zero markets scanned can never again be read as an efficient
market. A quiet report is only good news when coverage is non-zero.

    python -m profit_priority.learning record
    python -m profit_priority.learning grade
    python -m profit_priority.learning report
"""

from __future__ import annotations

import argparse
import json
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config, fees

LEARN_DIR = Path(config.DATA_DIR)
CANDIDATES = LEARN_DIR / "learning_candidates.jsonl"
SCANS = LEARN_DIR / "learning_scans.jsonl"

# A candidate is graded no earlier than this: the market needs time to move.
MIN_GRADE_AGE_MIN = 30.0
# Beyond this a candidate is abandoned rather than graded against a stale world.
MAX_GRADE_AGE_H = 72.0
# A detector below this hit rate, with enough samples, is switched off.
TRUST_FLOOR = 0.45
MIN_SAMPLES_TO_JUDGE = 20


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Candidate:
    """One thing the system noticed, priced at the moment it noticed it."""

    id: str
    detected_at: str
    kind: str                      # saving | lock | maker | value
    series: str = ""
    team: str = ""
    venue_buy: str = ""
    venue_sell: str = ""
    price_buy: float | None = None
    price_sell: float | None = None
    edge: float | None = None
    # graded later, never at detection
    graded_at: str | None = None
    price_buy_later: float | None = None
    price_sell_later: float | None = None
    realised: float | None = None
    verdict: str | None = None     # HELD | DECAYED | REVERSED | ABANDONED
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class Scan:
    """Coverage heartbeat: what we looked at, not just what we found."""

    id: str
    scanned_at: str
    scanner: str
    markets_seen: int = 0
    candidates: int = 0
    errors: str = ""
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ── 1. RECORD ─────────────────────────────────────────────────────────────────

def _open_keys() -> set[tuple[str, str, str]]:
    """(kind, series, team) for every candidate still awaiting a grade.

    Deduplication is a correctness requirement, not tidiness. The loop runs every
    few hours against a board that changes slowly, so re-recording the same
    dislocation each cycle would enter one observation dozens of times. `trust()`
    would then treat a single persistent quote as dozens of independent samples
    and report a hit rate with false confidence — the statistical version of the
    same silent-inflation bug this stack keeps producing.
    """
    return {
        (r.get("kind", ""), r.get("series", ""), r.get("team", ""))
        for r in _load(CANDIDATES) if not r.get("graded_at")
    }


def record() -> int:
    """Snapshot today's cross-venue candidates with the prices seen right now."""
    from . import crossvenue

    scan = Scan(id=uuid.uuid4().hex[:12], scanned_at=_now(), scanner="crossvenue")
    try:
        rows = crossvenue.compare()
    except Exception as exc:                    # noqa: BLE001
        scan.errors = f"{type(exc).__name__}: {exc}"
        _append(SCANS, scan.to_json())
        print(f"  scan failed: {scan.errors}")
        return 1

    scan.markets_seen = len(rows)
    already = _open_keys()
    n = 0
    skipped = 0
    for r in rows:
        # A saving is only meaningful if there is one; a lock is rarer still.
        if r.buy_saving > 0 and r.best_buy:
            if ("saving", r.series, r.team) in already:
                skipped += 1
            else:
                already.add(("saving", r.series, r.team))
                cheaper_is_kalshi = r.best_buy[0] == "kalshi"
                c = Candidate(
                    id=uuid.uuid4().hex[:12], detected_at=_now(), kind="saving",
                    series=r.series, team=r.team, venue_buy=r.best_buy[0],
                    price_buy=(r.kalshi.all_in_ask if cheaper_is_kalshi
                               else r.poly.all_in_ask),
                    price_sell=(r.poly.all_in_ask if cheaper_is_kalshi
                                else r.kalshi.all_in_ask),
                    edge=r.buy_saving,
                    note=f"cheaper on {r.best_buy[0]}",
                )
                _append(CANDIDATES, c.to_json())
                n += 1
        if r.lock:
            buy, sell, edge = r.lock
            if ("lock", r.series, r.team) in already:
                skipped += 1
                continue
            already.add(("lock", r.series, r.team))
            c = Candidate(
                id=uuid.uuid4().hex[:12], detected_at=_now(), kind="lock",
                series=r.series, team=r.team, venue_buy=buy, venue_sell=sell,
                price_buy=r.kalshi.all_in_ask if buy == "kalshi" else r.poly.all_in_ask,
                price_sell=r.poly.net_bid if sell == "polymarket" else r.kalshi.net_bid,
                edge=edge, note="cross-venue lock candidate",
            )
            _append(CANDIDATES, c.to_json())
            n += 1

    scan.candidates = n
    if skipped:
        scan.notes.append(f"{skipped} duplicate(s) of still-open candidates skipped")
    _append(SCANS, scan.to_json())
    print(f"  recorded {n} candidate(s) from {len(rows)} matched outcomes"
          f"{f'; skipped {skipped} already-open duplicate(s)' if skipped else ''}.")
    if n == 0:
        # These are three different states and must not share a message. Saying
        # "the venues agreed" when candidates were merely already open would
        # report an efficient market that was never observed.
        if skipped:
            print(f"  Nothing NEW: all {skipped} are still-open candidates awaiting a")
            print("  grade. This is the loop working, not the venues agreeing.")
        elif rows:
            print("  Zero candidates with non-zero coverage is a real answer: the")
            print("  venues agreed on every matched outcome.")
        else:
            print("  [!] Zero candidates AND zero coverage — that is a bug, not an")
            print("      efficient market. Check the feeds before trusting this.")
    return 0


# ── 2. GRADE ──────────────────────────────────────────────────────────────────

def grade() -> int:
    """Re-price each open candidate and record what the market actually did."""
    from . import crossvenue

    rows = _load(CANDIDATES)
    open_rows = [r for r in rows if not r.get("graded_at")]
    if not open_rows:
        print("  nothing open to grade.")
        return 0

    try:
        current = {(c.series, c.team): c for c in crossvenue.compare()}
    except Exception as exc:                    # noqa: BLE001
        print(f"  cannot grade: {type(exc).__name__}: {exc}")
        return 1

    now = datetime.now(UTC)
    graded = 0
    for r in open_rows:
        det = _parse(r.get("detected_at"))
        if det is None:
            continue
        age_min = (now - det).total_seconds() / 60.0
        if age_min < MIN_GRADE_AGE_MIN:
            continue                             # too soon to mean anything
        cmp_now = current.get((r.get("series"), r.get("team")))
        if cmp_now is None or age_min > MAX_GRADE_AGE_H * 60:
            r["graded_at"] = _now()
            r["verdict"] = "ABANDONED"
            r["note"] = f"no longer quoted after {age_min/60:.1f}h"
            graded += 1
            continue

        if r["kind"] == "saving":
            still = cmp_now.buy_saving
            r["realised"] = round(still, 4)
            # Graded against a rule fixed in advance: did most of it survive?
            if still >= (r.get("edge") or 0) * 0.5:
                r["verdict"] = "HELD"
            elif still > 0:
                r["verdict"] = "DECAYED"
            else:
                r["verdict"] = "REVERSED"
        else:  # lock
            still = cmp_now.lock
            r["realised"] = round(still[2], 4) if still else 0.0
            r["verdict"] = "HELD" if still else "DECAYED"
        r["graded_at"] = _now()
        graded += 1

    CANDIDATES.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                          encoding="utf-8")
    print(f"  graded {graded} candidate(s); {len(open_rows) - graded} still too young "
          f"(< {MIN_GRADE_AGE_MIN:.0f} min).")
    return 0


# ── 3. REPORT + 4. ADAPT ──────────────────────────────────────────────────────

def trust() -> dict[str, dict]:
    """Measured reliability per detector — the adaptation signal.

    A detector that has been graded enough times and is right less than
    TRUST_FLOOR of the time is marked untrusted. That is the loop closing:
    the system stops believing a detector its own record contradicts.
    """
    rows = [r for r in _load(CANDIDATES) if r.get("verdict")]
    out: dict[str, dict] = {}
    for kind in sorted({r["kind"] for r in rows}):
        sub = [r for r in rows if r["kind"] == kind]
        judged = [r for r in sub if r["verdict"] != "ABANDONED"]
        held = [r for r in judged if r["verdict"] == "HELD"]
        realised = [r["realised"] for r in judged if r.get("realised") is not None]
        n = len(judged)
        rate = len(held) / n if n else None
        out[kind] = {
            "graded": len(sub),
            "judged": n,
            "abandoned": len(sub) - n,
            "hit_rate": round(rate, 3) if rate is not None else None,
            "mean_realised": round(statistics.fmean(realised), 4) if realised else None,
            "trusted": None if n < MIN_SAMPLES_TO_JUDGE else bool(rate >= TRUST_FLOOR),
            "verdict": ("insufficient evidence" if n < MIN_SAMPLES_TO_JUDGE
                        else ("trusted" if rate >= TRUST_FLOOR else "UNTRUSTED")),
        }
    return out


def report() -> int:
    print(f"\n{'='*78}\n  LEARNING REPORT — {_now()}\n{'='*78}")

    scans = _load(SCANS)
    print("\n  COVERAGE (are we still looking?)")
    if not scans:
        print("    no scans recorded. Run `record` first.")
    else:
        recent = scans[-10:]
        for s in recent[-5:]:
            flag = f"  ERROR {s['errors'][:40]}" if s.get("errors") else ""
            print(f"    {s['scanned_at'][:16]}  {s['scanner']:<12}"
                  f"markets {s.get('markets_seen', 0):>4}  "
                  f"candidates {s.get('candidates', 0):>3}{flag}")
        last = _parse(scans[-1]["scanned_at"])
        if last and (datetime.now(UTC) - last) > timedelta(hours=12):
            print(f"    [!] last scan {(datetime.now(UTC)-last).total_seconds()/3600:.1f}h "
                  f"ago — the loop has stopped running")

    rows = _load(CANDIDATES)
    open_n = sum(1 for r in rows if not r.get("verdict"))
    print(f"\n  CANDIDATES: {len(rows)} recorded, {open_n} awaiting grade")

    t = trust()
    print("\n  DETECTOR RELIABILITY (the adaptation signal)")
    if not t:
        print("    nothing graded yet. Reliability is unknown, not assumed good.")
    else:
        print(f"    {'detector':<10}{'judged':>8}{'abandon':>9}{'hit rate':>10}"
              f"{'mean real':>11}  verdict")
        print("    " + "-" * 62)
        for kind, m in t.items():
            hr = f"{m['hit_rate']*100:.0f}%" if m["hit_rate"] is not None else "-"
            mr = f"{m['mean_realised']:+.4f}" if m["mean_realised"] is not None else "-"
            print(f"    {kind:<10}{m['judged']:>8}{m['abandoned']:>9}{hr:>10}{mr:>11}"
                  f"  {m['verdict']}")
        untrusted = [k for k, m in t.items() if m["trusted"] is False]
        if untrusted:
            print(f"\n    [!] {untrusted} fall below the {TRUST_FLOOR:.0%} floor with")
            print("        enough samples to judge. Stop acting on them.")

    print("\n  BLIND SPOTS")
    warnings = []
    if not scans:
        warnings.append("no coverage recorded at all")
    if rows and open_n == len(rows):
        warnings.append("every candidate is ungraded — `grade` has never run")
    for kind, m in t.items():
        if m["judged"] and m["abandoned"] / max(m["graded"], 1) > 0.5:
            warnings.append(f"{kind}: over half abandoned — quotes vanish before grading")
    if not warnings:
        print("    none detected.")
    for w in warnings:
        print(f"    [!] {w}")

    print(f"\n  fee reference: taker RT @0.50 = {fees.kalshi_round_trip(0.50, 100)/100:.4f}, "
          f"maker RT = {fees.kalshi_round_trip(0.50, 100, entry_maker=True, exit_maker=True)/100:.4f}")
    print("\n  A quiet report is only good news when COVERAGE is non-zero.\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Self-learning loop: record, grade, report.")
    p.add_argument("cmd", choices=["record", "grade", "report", "all"])
    a = p.parse_args()
    rc = 0
    if a.cmd in ("record", "all"):
        rc |= record()
    if a.cmd in ("grade", "all"):
        rc |= grade()
    if a.cmd in ("report", "all"):
        rc |= report()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
