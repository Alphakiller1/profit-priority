"""Scheduled orchestrator — the loop that runs without you.

`learning.py` closes the record/grade/report cycle, but nothing fires it. A loop
that only runs when invoked by hand is not a learning system; it is a command you
sometimes remember. This is the entry point a scheduler calls.

Order matters and is not arbitrary:

  1. GRADE first, against the world as it was before this run's snapshot. Grading
     after recording would let a candidate be graded against a price captured in
     the same instant it was detected, which measures nothing.
  2. RECORD the current board.
  3. EXPORT the dashboard so the published page reflects the run.

Everything here is free: Kalshi and Polymarket public endpoints, no metered API.
That is deliberate — a loop with a per-run cost gets throttled to save money and
then stops producing the evidence it exists to gather.

    python -m profit_priority.capture
    python -m profit_priority.capture --dry-run
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


def run(dry_run: bool = False) -> int:
    _log("=== capture start ===")
    if dry_run:
        _log("dry run — nothing executed")
        return 0

    failures = 0
    # Grade BEFORE recording, so nothing is graded against its own snapshot.
    if not _run("grade", ["-m", "profit_priority.learning", "grade"]):
        failures += 1
    if not _run("record", ["-m", "profit_priority.learning", "record"]):
        failures += 1
    if not _run("dashboard", ["-m", "profit_priority", "dashboard"], timeout=1200):
        failures += 1

    _log(f"=== capture done ({failures} failure(s)) ===")
    return failures


def main() -> int:
    p = argparse.ArgumentParser(description="Scheduled learning + dashboard loop.")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    return 1 if run(a.dry_run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
