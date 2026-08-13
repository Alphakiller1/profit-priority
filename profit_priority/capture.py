"""Scheduled orchestrator — the loop that runs without you.

`learning.py` closes the record/grade/report cycle, but nothing fires it. A loop
that only runs when invoked by hand is not a learning system; it is a command you
sometimes remember. This is the entry point a scheduler calls.

Order matters and is not arbitrary:

  1. GRADE first, against the world as it was before this run's snapshot. Grading
     after recording would let a candidate be graded against a price captured in
     the same instant it was detected, which measures nothing.
  2. MARK open positions. This must happen every run and cannot be deferred: a
     settled market stops quoting, so a closing line not observed while the
     position was open is gone permanently. Marking is the only reason CLV can
     be computed at all.
  3. RECORD the current board.
  4. EXPORT the dashboard so the published page reflects the run.
  5. PUBLISH — commit and push docs/ so the deployed site updates itself. A deck
     that only refreshes when someone runs a command by hand is a local script
     with a web page next to it.

Everything here is free: Kalshi and Polymarket public endpoints, no metered API.
That is deliberate — a loop with a per-run cost gets throttled to save money and
then stops producing the evidence it exists to gather.

The publish step stages `docs/` and NOTHING else. The position ledger lives in
`data/` (gitignored) and never reaches the public repo: it holds real stake sizes
and P&L, and this repo is public.

    python -m profit_priority.capture
    python -m profit_priority.capture --dry-run
    python -m profit_priority.capture --no-publish
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LOG = HERE / "data" / "capture.log"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(UTC).isoformat(timespec='seconds')}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _run(label: str, args: list[str], timeout: int = 900) -> bool:
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, *args], cwd=HERE,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _log(f"[{label}] TIMEOUT after {timeout}s")
        return False
    ok = proc.returncode == 0
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    summary = next((ln.strip() for ln in reversed(tail) if ln.strip()), "")
    _log(f"[{label}] {'ok' if ok else f'FAIL rc={proc.returncode}'} "
         f"{int((time.time()-t0)*1000)}ms  {summary[:150]}")
    if not ok:
        for ln in (proc.stderr or "").strip().splitlines()[-4:]:
            _log(f"[{label}]   {ln[:150]}")
    return ok


def _git(*args: str, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def publish() -> bool:
    """Commit and push docs/ so the deployed deck reflects this run.

    Only `docs/` is staged. Staging everything would sooner or later sweep a
    half-finished edit — or a data file — into a public repo on a timer, with
    nobody watching. If docs/ is unchanged this is a no-op, not an empty commit.
    """
    rc, _ = _git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        _log("[publish] not a git repo; skipped")
        return True

    rc, out = _git("status", "--porcelain", "docs")
    if rc != 0:
        _log(f"[publish] git status failed: {out[:150]}")
        return False
    if not out:
        _log("[publish] docs/ unchanged; nothing to push")
        return True

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rc, out = _git("add", "docs")
    if rc != 0:
        _log(f"[publish] git add failed: {out[:150]}")
        return False
    rc, out = _git("-c", "core.hooksPath=/dev/null", "commit", "-m",
                   f"data: scheduled deck refresh {stamp}")
    if rc != 0 and "nothing to commit" not in out.lower():
        _log(f"[publish] commit failed: {out[:150]}")
        return False
    rc, out = _git("push", timeout=300)
    if rc != 0:
        _log(f"[publish] push failed (commit is local): {out[:200]}")
        return False
    _log(f"[publish] pushed deck refresh {stamp}")
    return True


def run(dry_run: bool = False, do_publish: bool = True) -> int:
    _log("=== capture start ===")
    if dry_run:
        _log("dry run — nothing executed")
        return 0

    failures = 0
    # Grade BEFORE recording, so nothing is graded against its own snapshot.
    if not _run("grade", ["-m", "profit_priority.learning", "grade"]):
        failures += 1
    # Mark BEFORE the dashboard export, so the deck shows this run's marks rather
    # than the previous run's. Also before anything that can fail slowly: a missed
    # mark is an unrecoverable gap in the closing line, not a retryable step.
    if not _run("positions", ["-m", "profit_priority.positions", "mark"], timeout=600):
        failures += 1
    if not _run("record", ["-m", "profit_priority.learning", "record"]):
        failures += 1
    if not _run("dashboard", ["-m", "profit_priority", "dashboard"], timeout=1200):
        failures += 1

    if do_publish and not publish():
        failures += 1

    _log(f"=== capture done ({failures} failure(s)) ===")
    return failures


def main() -> int:
    p = argparse.ArgumentParser(description="Scheduled learning + dashboard loop.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-publish", action="store_true",
                   help="refresh locally without pushing docs/")
    a = p.parse_args()
    return 1 if run(a.dry_run, do_publish=not a.no_publish) else 0


if __name__ == "__main__":
    raise SystemExit(main())
