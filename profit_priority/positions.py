"""
Position ledger — what you actually bought, what it was worth at the close.

Everything else in this repo scores opportunities. This module is the only part
that knows whether taking them made money. It holds the positions the USER chose
and staked real dollars on, marks them while they are open, captures the closing
line, and computes closing-line value alongside fee-inclusive P&L.

## Why the closing line has to be captured prospectively

CLV compares your entry to the market's final word before settlement. That final
word is not recoverable after the fact: Kalshi's `/markets` feed serves open
markets, and once a market settles the quote that prevailed a minute earlier is
simply gone. No vendor archives it. So the closing line must be *observed while
the position is still open*, which is why `mark` runs on the schedule rather than
being computed once at settlement.

The consequence is stated plainly rather than papered over: a position opened and
settled between two mark runs has NO closing line, and its `clv` is None forever.
Reporting 0.0 there would be a fabricated observation, and it would bias the mean
CLV toward zero exactly on the fastest-moving markets.

## Why CLV leads and P&L follows

On a $350/week budget a season is a few hundred positions. At that sample size
P&L is mostly variance: a 55%-edge bettor loses over 100 bets often enough that
the number carries little information. CLV is measured per position against a
sharp reference and converges far faster, so `report` leads with it. P&L is shown
because it is what lands in the account, not because it is the better evidence.

    python -m profit_priority.positions open --ticker KXMLBGAME-...-TEX --stake 50
    python -m profit_priority.positions import deck-export.json
    python -m profit_priority.positions mark          # on the schedule
    python -m profit_priority.positions report
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import config, fees
from .games import is_pregame

LEDGER = Path(config.DATA_DIR) / "positions.jsonl"

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"Accept": "application/json", "User-Agent": "profit-priority/0.2"}

# A position with no mark taken while it was open has no closing line. It is
# counted and reported, never assigned a CLV of zero.
VERDICTS = ("OPEN", "WIN", "LOSS", "EXITED", "NO_CLOSE", "VOID")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── sizing ────────────────────────────────────────────────────────────────────

def contracts_for_stake(stake: float, price: float, *, maker: bool = False,
                        fee_rate: float | None = None) -> tuple[float, float]:
    """Largest whole contract count whose all-in cost fits inside `stake`.

    Solved rather than divided. Kalshi applies the fee ceiling to the ORDER, not
    per contract, so `stake / (price + per_contract_fee)` overstates size on small
    orders and would have the ledger record a position the user could not afford.
    """
    rate = config.KALSHI_FEE_RATE if fee_rate is None else fee_rate
    if stake <= 0 or not 0.0 < price < 1.0:
        return 0.0, 0.0
    n = math.floor(stake / price)          # ceiling ignoring fees
    while n > 0:
        fee = fees.kalshi_fee(n, price, rate, maker=maker)
        if n * price + fee <= stake + 1e-9:
            return float(n), fee
        n -= 1
    return 0.0, 0.0


# ── the record ────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """One staked position, from entry through the close."""

    id: str
    opened_at: str
    ticker: str
    team: str = ""
    sport: str = ""
    game_date: str = ""
    venue: str = "kalshi"
    entry_price: float = 0.0       # executable price paid, per contract
    stake: float = 0.0             # dollars committed, fees included
    contracts: float = 0.0
    entry_fee: float = 0.0
    maker: bool = False
    source: str = "manual"         # manual | deck
    note: str = ""

    # updated by `mark` while the position is open
    last_mark: float | None = None
    last_mark_at: str | None = None
    marks: int = 0

    # The closing line: the last mid observed while the game was still PRE-GAME.
    # Frozen at first pitch and never overwritten afterwards. For a pre-game
    # trader this is what CLV means -- the market's final word before betting
    # closes. Marking against a price set two innings in is measuring a different
    # market against an entry that could never have been made at it.
    close_line: float | None = None
    close_line_at: str | None = None

    # written once, at the close
    closed_at: str | None = None
    close_price: float | None = None   # final mark observed while open
    settled_to: float | None = None    # 1.0 or 0.0
    clv: float | None = None           # close_price - entry_price, prob points
    pnl: float | None = None
    verdict: str = "OPEN"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @property
    def cost(self) -> float:
        return self.contracts * self.entry_price + self.entry_fee

    def pnl_at(self, price: float, *, taker_exit: bool = True) -> float:
        """P&L if the position were closed out now at `price`."""
        exit_fee = fees.kalshi_fee(self.contracts, price, config.KALSHI_FEE_RATE,
                                   maker=not taker_exit)
        return self.contracts * price - self.cost - exit_fee

    def pnl_settled(self, outcome: float) -> float:
        """P&L held to settlement. Kalshi charges on trades, not settlement,
        so only the entry leg carries a fee."""
        return self.contracts * outcome - self.cost


def _append(p: Position) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(p.to_json() + "\n")


def _load() -> list[Position]:
    """Latest revision of each position id, in insertion order.

    The ledger is append-only: `mark` and `close` append a new full record rather
    than rewriting history, so an interrupted run can never leave a half-written
    file. Replaying keeps the last write per id.
    """
    if not LEDGER.exists():
        return []
    by_id: dict[str, Position] = {}
    fields = {f for f in Position.__dataclass_fields__}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("id"):
            continue
        by_id[row["id"]] = Position(**{k: v for k, v in row.items() if k in fields})
    return list(by_id.values())


def open_positions() -> list[Position]:
    return [p for p in _load() if p.verdict == "OPEN"]


# ── opening ───────────────────────────────────────────────────────────────────

def _market(ticker: str) -> dict | None:
    try:
        return _get(f"{KALSHI}/markets/{urllib.parse.quote(ticker)}").get("market")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def open_position(ticker: str, stake: float, price: float | None = None, *,
                  maker: bool = False, source: str = "manual",
                  note: str = "") -> Position | None:
    """Record a staked position, pricing it at the live ask when not given."""
    m = _market(ticker)
    if m is None and price is None:
        print(f"  cannot reach Kalshi for {ticker} and no --price given; not recorded.")
        return None

    ask = _f((m or {}).get("yes_ask_dollars"))
    entry = price if price is not None else ask
    if entry is None or not 0.0 < entry < 1.0:
        print(f"  no usable entry price for {ticker} (got {entry!r}); not recorded.")
        return None

    if m is not None and str(m.get("status", "")).lower() not in ("active", "open", ""):
        print(f"  {ticker} is {m.get('status')}, not open; not recorded.")
        return None

    n, fee = contracts_for_stake(stake, entry, maker=maker)
    if n <= 0:
        print(f"  ${stake:.2f} does not cover one contract at {entry:.2f}; not recorded.")
        return None

    p = Position(
        id=uuid.uuid4().hex[:12], opened_at=_now(), ticker=ticker,
        team=str((m or {}).get("yes_sub_title") or ticker.rsplit("-", 1)[-1]),
        entry_price=round(entry, 4), stake=round(n * entry + fee, 2),
        contracts=n, entry_fee=round(fee, 2), maker=maker,
        source=source, note=note,
    )
    # The entry is itself the first observation of the line. Without it, a
    # position opened and settled between two scheduled runs would have no mark
    # at all, and its CLV would be unrecoverable rather than merely short-horizon.
    mid = _mid(m) if m else None
    if mid is not None:
        p.last_mark, p.last_mark_at, p.marks = mid, p.opened_at, 1
    _append(p)
    return p


def _mid(m: dict) -> float | None:
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if bid is None or ask is None:
        return None
    if not 0.0 <= bid <= ask <= 1.0:
        return None
    return round((bid + ask) / 2.0, 4)


# ── marking ───────────────────────────────────────────────────────────────────

def mark() -> int:
    """Refresh every open position; close the ones whose market has resolved.

    This is the load-bearing scheduled job. Each run records the prevailing mid,
    so when a market finally settles the most recent mid IS its closing line.
    """
    live = open_positions()
    if not live:
        print("\n  No open positions.\n")
        return 0

    marked = closed = stale = 0
    print(f"\n  MARKING {len(live)} open position(s)\n")
    print(f"  {'team':<10}{'entry':>8}{'mark':>8}{'clv':>9}{'openP&L':>10}  status")
    print("  " + "-" * 62)

    for p in live:
        m = _market(p.ticker)
        if m is None:
            stale += 1
            print(f"  {p.team[:9]:<10}{p.entry_price:>8.2f}{'-':>8}{'-':>9}"
                  f"{'-':>10}  unreachable")
            continue

        status = str(m.get("status", "")).lower()
        mid = _mid(m)

        if status in ("active", "open"):
            if mid is not None:
                p.last_mark, p.last_mark_at = mid, _now()
                p.marks += 1
                marked += 1
                # Freeze the closing line at first pitch. `is_pregame` returns
                # None when the ticker carries no time block, and None must not
                # extend the pre-game window: that would quietly let in-game
                # prices become the "closing line" on exactly the sports whose
                # start time we cannot read.
                if is_pregame(p.ticker, p.game_date) is True:
                    p.close_line, p.close_line_at = mid, p.last_mark_at
            open_pnl = p.pnl_at(mid) if mid is not None else None
            clv = (mid - p.entry_price) if mid is not None else None
            print(f"  {p.team[:9]:<10}{p.entry_price:>8.2f}"
                  f"{(f'{mid:.2f}' if mid is not None else '-'):>8}"
                  f"{(f'{clv:+.3f}' if clv is not None else '-'):>9}"
                  f"{(f'{open_pnl:+.2f}' if open_pnl is not None else '-'):>10}  open")
            _append(p)
            continue

        # Resolved. The last mark taken while open is the closing line.
        result = str(m.get("result", "")).lower()
        outcome = 1.0 if result == "yes" else 0.0 if result == "no" else None
        _close(p, outcome)
        closed += 1
        print(f"  {p.team[:9]:<10}{p.entry_price:>8.2f}"
              f"{(f'{p.close_price:.2f}' if p.close_price is not None else '-'):>8}"
              f"{(f'{p.clv:+.3f}' if p.clv is not None else '-'):>9}"
              f"{(f'{p.pnl:+.2f}' if p.pnl is not None else '-'):>10}  {p.verdict}")

    print(f"\n  {marked} marked, {closed} closed, {stale} unreachable.")
    if closed:
        print("  Closing lines captured from the last mark taken while open.")
    print()
    return 0


def _close(p: Position, outcome: float | None, *, exit_price: float | None = None) -> None:
    p.closed_at = _now()
    # The pre-game close is the correct reference and is preferred whenever it
    # exists. `last_mark` is the fallback only for positions whose start time was
    # never knowable, and it is a weaker measurement -- it may be an in-game price.
    p.close_price = p.close_line if p.close_line is not None else p.last_mark
    p.settled_to = outcome

    if p.close_price is not None:
        p.clv = round(p.close_price - p.entry_price, 4)

    if exit_price is not None:
        p.pnl = round(p.pnl_at(exit_price), 2)
        p.verdict = "EXITED"
    elif outcome is not None:
        p.pnl = round(p.pnl_settled(outcome), 2)
        p.verdict = "WIN" if outcome >= 0.5 else "LOSS"
    else:
        # Market resolved but the result is not published as yes/no. P&L is not
        # knowable, and guessing it would put a fabricated number in the ledger.
        p.pnl = None
        p.verdict = "VOID"

    if p.close_price is None and p.verdict != "VOID":
        p.verdict = p.verdict if p.pnl is not None else "NO_CLOSE"
    _append(p)


def close_position(pos_id: str, outcome: float | None = None,
                   exit_price: float | None = None) -> int:
    for p in _load():
        if p.id.startswith(pos_id) and p.verdict == "OPEN":
            _close(p, outcome, exit_price=exit_price)
            print(f"\n  closed {p.id} {p.team}: {p.verdict}, "
                  f"P&L {('%+.2f' % p.pnl) if p.pnl is not None else 'unknown'}, "
                  f"CLV {('%+.3f' % p.clv) if p.clv is not None else 'no close'}\n")
            return 0
    print(f"\n  no open position matching {pos_id!r}\n")
    return 1


# ── deck import ───────────────────────────────────────────────────────────────

def import_file(path: str) -> int:
    """Ingest tickets exported from the deck.

    The deck is a static page with no backend, so selections live in the browser
    until exported. Re-importing the same file is safe: tickets carry a client id
    and already-imported ones are skipped rather than duplicated.
    """
    f = Path(path)
    if not f.exists():
        print(f"\n  no such file: {path}\n")
        return 1
    try:
        blob = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"\n  {path} is not valid JSON: {e}\n")
        return 1

    tickets = blob.get("tickets", blob if isinstance(blob, list) else [])
    seen = {p.note for p in _load() if p.source == "deck" and p.note}
    added = skipped = failed = 0
    for t in tickets:
        cid = str(t.get("cid") or "")
        if cid and cid in seen:
            skipped += 1
            continue
        p = open_position(
            ticker=str(t.get("ticker") or ""), stake=float(t.get("stake") or 0),
            price=_f(t.get("price")), maker=bool(t.get("maker")),
            source="deck", note=cid,
        )
        if p is None:
            failed += 1
        else:
            added += 1
            seen.add(cid)
    print(f"\n  imported {added}, skipped {skipped} already present, {failed} rejected.\n")
    return 0 if added or skipped else 1


# ── reporting ─────────────────────────────────────────────────────────────────

def summary() -> dict:
    rows = _load()
    closed = [p for p in rows if p.verdict in ("WIN", "LOSS", "EXITED")]
    live = [p for p in rows if p.verdict == "OPEN"]
    with_clv = [p for p in rows if p.clv is not None and p.verdict != "OPEN"]
    no_close = [p for p in rows if p.verdict != "OPEN" and p.clv is None]

    staked = sum(p.cost for p in closed)
    pnl = sum(p.pnl for p in closed if p.pnl is not None)
    clv = [p.clv for p in with_clv]
    return {
        "n_total": len(rows), "n_open": len(live), "n_closed": len(closed),
        "n_with_clv": len(with_clv), "n_no_close": len(no_close),
        "staked": round(staked, 2), "pnl": round(pnl, 2),
        "roi": round(pnl / staked, 4) if staked > 0 else None,
        "wins": sum(1 for p in closed if p.verdict == "WIN"),
        "mean_clv": round(sum(clv) / len(clv), 4) if clv else None,
        "clv_positive": sum(1 for c in clv if c > 0),
        "open_exposure": round(sum(p.cost for p in live), 2),
    }


def report() -> int:
    s = summary()
    rows = _load()
    print("\n  POSITION LEDGER")
    print("  " + "-" * 74)
    if not rows:
        print("  Empty. Open one with:")
        print("    python -m profit_priority.positions open --ticker T --stake 50\n")
        return 0

    live = [p for p in rows if p.verdict == "OPEN"]
    if live:
        print(f"\n  OPEN — {len(live)}, ${s['open_exposure']:.2f} at risk\n")
        print(f"  {'id':<14}{'team':<10}{'entry':>8}{'entryUS':>9}"
              f"{'mark':>8}{'clv':>9}{'stake':>9}")
        for p in live:
            print(f"  {p.id:<14}{p.team[:9]:<10}{p.entry_price:>8.2f}"
                  f"{fees.fmt_american(p.entry_price):>9}"
                  f"{(f'{p.last_mark:.2f}' if p.last_mark else '-'):>8}"
                  f"{(f'{p.last_mark - p.entry_price:+.3f}' if p.last_mark else '-'):>9}"
                  f"{p.cost:>9.2f}")

    print(f"\n  CLOSED — {s['n_closed']}, staked ${s['staked']:.2f}")
    if s["n_closed"]:
        roi = f"{s['roi']:+.2%}" if s["roi"] is not None else "-"
        print(f"    P&L {s['pnl']:+.2f}   ROI {roi}   "
              f"record {s['wins']}-{s['n_closed'] - s['wins']}")

    # CLV leads. It is the metric that separates a good price from a good result,
    # and at this sample size it is the only one carrying much information.
    print("\n  CLOSING LINE VALUE — the signal, ahead of P&L")
    if s["mean_clv"] is None:
        print("    No position has both an entry and an observed closing line yet.")
    else:
        n = s["n_with_clv"]
        print(f"    mean {s['mean_clv']:+.4f} over {n} position(s)   "
              f"beat the close {s['clv_positive']}/{n}")
        print(f"    {'You are buying better than the market closes.' if s['mean_clv'] > 0 else 'You are paying up versus the close.'}")
        if n < 30:
            print(f"    n={n} is too small to conclude anything; 30+ before reading it.")
    if s["n_no_close"]:
        print(f"    {s['n_no_close']} closed position(s) had no mark taken while open,")
        print("    so their closing line is unrecoverable and they are excluded above.")

    if s["n_closed"] and s["n_closed"] < 30:
        print(f"\n  P&L over {s['n_closed']} positions is mostly variance. Judge on CLV.")
    print()
    return 0


def payload() -> dict:
    """Ledger view for the dashboard."""
    rows = _load()
    return {
        "generated_at": _now(),
        "summary": summary(),
        "positions": [{
            "id": p.id, "opened_at": p.opened_at, "ticker": p.ticker, "team": p.team,
            "entry_price": p.entry_price, "entry_american": fees.prob_to_american(p.entry_price),
            "stake": p.stake, "contracts": p.contracts, "cost": round(p.cost, 2),
            "last_mark": p.last_mark, "marks": p.marks,
            "close_price": p.close_price, "clv": p.clv, "pnl": p.pnl,
            "verdict": p.verdict, "source": p.source,
        } for p in sorted(rows, key=lambda x: x.opened_at, reverse=True)[:200]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Staked position ledger with CLV.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="record a staked position")
    o.add_argument("--ticker", required=True)
    o.add_argument("--stake", type=float, required=True)
    o.add_argument("--price", type=float, default=None,
                   help="entry price paid; defaults to the live ask")
    o.add_argument("--maker", action="store_true")
    o.add_argument("--note", default="")

    sub.add_parser("mark", help="refresh open positions and close resolved ones")
    sub.add_parser("report", help="ledger, CLV and P&L")

    i = sub.add_parser("import", help="ingest tickets exported from the deck")
    i.add_argument("file")

    c = sub.add_parser("close", help="close a position by hand")
    c.add_argument("id")
    c.add_argument("--settled", type=float, default=None, help="1 or 0")
    c.add_argument("--exit-price", type=float, default=None)

    a = ap.parse_args()
    if a.cmd == "open":
        p = open_position(a.ticker, a.stake, a.price, maker=a.maker, note=a.note)
        if p is None:
            return 1
        print(f"\n  opened {p.id}  {p.team}  {p.contracts:.0f} @ {p.entry_price:.2f} "
              f"({fees.fmt_american(p.entry_price)})  cost ${p.cost:.2f} "
              f"(fee ${p.entry_fee:.2f})\n")
        return 0
    if a.cmd == "mark":
        return mark()
    if a.cmd == "import":
        return import_file(a.file)
    if a.cmd == "close":
        return close_position(a.id, a.settled, a.exit_price)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
