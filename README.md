# profit-priority

An **executable, fee-aware** MLB cross-venue profit engine — the corrected build of the
`PROFIT-PRIORITY` roadmap.

The original sharp-tracker priced opportunities on **midpoints with no fees**, so its
"arbs" were phantom: a 1.8% gross edge on mids is a *loss* once you pay the Kalshi ask,
the worse book number, and Kalshi's per-contract fee. This engine fixes that — every
opportunity is priced on **executable cost after real fees + slippage**, so a
"guaranteed profit" is actually guaranteed.

> Analysis infrastructure, not betting advice.

## The three opportunity classes
| Class | What it is | Status |
|---|---|---|
| **A. Pure arb** | Both legs executable now → guaranteed profit **after fees** | Tradeable |
| **B. Cross-venue value** | One side cheaper than the **de-vigged sharp** fair price (+EV, not a lock) | Tradeable |
| **C. Manufactured arb** | Staged: enter early, hedge after the move | **R&D — scored & logged, never traded blind** |

Manufactured arb is *not* a lock at entry — its whole EV rides on whether a hedge
actually appears in time. So we **log every candidate** (the cheapest, highest-value
thing here) and measure the real hedge-conversion rate before risking a dollar.

## Why it's built to actually be profitable
1. **Real Kalshi fee model** (`fees.py`): `ceil(rate · C · p · (1−p))`, max near $0.50 — exactly where naive 50/50 "arbs" die. Thresholds sit **above** the fee floor, not at round numbers.
2. **Executable prices** (`pricing.py`): Kalshi **ask** (not mid), **best execution book by side** (not median consensus).
3. **Sharp fair anchor**: value is measured vs **de-vigged Pinnacle/sharp**, never soft consensus.
4. **Hard risk gates**: price-age, time-to-first-pitch, liquidity, implausible-gap, single-sharp-book.
5. **Outcome-independent staking** (`staking.py`): both legs sized to the same payout, so the lock is real whichever side wins.
6. **Log-everything learning loop** (`logger.py`): refit the scoring weights on settled outcomes (FDR-controlled) before trusting Class C.

## Quick start
```bash
pip install -r requirements.txt
python -m profit_priority demo      # runs the full math on a built-in snapshot (no API keys)
python -m profit_priority fees 0.49 # show how fees kill a 0.49/0.49 "arb"
pytest -q                           # the fee/arb/staking math is the product — it's tested
```

Live (needs a free key from the-odds-api.com):
```bash
export ODDS_API_KEY=...             # PowerShell: $env:ODDS_API_KEY="..."
export KALSHI_FEE_RATE=0.07         # set the EXACT rate for the market you trade
python -m profit_priority scan
python -m profit_priority report    # summarize the candidate funnel
python -m profit_priority dashboard # write docs/data.json, then serve docs/ (or GitHub Pages)
```

`dashboard` takes a mode: `auto` (default — live feeds when `ODDS_API_KEY` is set),
`live` (force), `demo` (force the built-in fixture snapshot). Without a key, `auto`
publishes the three sportsbook-priced panels **empty with the reason attached** rather
than falling back to fixtures, and logs nothing: a scheduled run that ships three
fixture games as today's board is worse than a blank panel, because the page gives no
sign the run never happened. The structural panels (Kalshi + Polymarket, free
endpoints) are live in every mode.

### Live sharp signals
`scan` auto-joins the **sharp-money-tracker's** `sharp_signals.csv` (steam + sharp-vs-soft
divergence) into the manufactured-arb scorer, matched by the same `crc32(date|away|home)`
game key. Point at it with `PP_SHARP_SIGNALS=/path/to/sharp_signals.csv` (defaults to the
sibling repo). The validated edge (vault Market-Edge-Engine): enter-at-open on **steam-up
underdog** sides survived FDR at +20–48% ROI/u — the scorer rewards exactly that pattern.

### NFL Genesis context
`profit_priority.feeds.nfl_model` reads the market-forecast contract and refuses to turn an
unpromoted or zero-lambda forecast into a value edge. The companion Genesis outlook is a
separate, read-only research artifact: inspect it with
`python -m profit_priority.feeds.genesis_outlook --outlook path/to/genesis_outlook_2026.json`.
It is deliberately excluded from value detection, structural-arbitrage sizing, and staking.

### Dashboard (Phase 6)
`docs/index.html` renders four panels from `docs/data.json`: **Pure Arbs**, **Cross-Venue
Value**, **Manufactured Candidates**, and a **Funnel/Postmortem** (logged vs accepted per
class + top reject reasons). `python -m profit_priority dashboard` regenerates the data;
serve `docs/` over HTTP or enable GitHub Pages (the `fetch` needs HTTP, not `file://`).

Nothing type-checks that page, so `tests/test_dashboard_js.py` lints its inline script
for helpers that are called but never defined and for element ids it reaches for that
are not in the markup. Both are render-time faults: a missing helper once blanked the
whole deck for six scheduled runs while `data.json` was perfectly correct.

## Layout
```
profit_priority/
  fees.py          # Kalshi fee model + de-vig + American/decimal  (the correctness core)
  pricing.py       # executable quotes, best-book-by-side, sharp fair anchor, freshness
  staking.py       # equalized-payout stake sizing (binary + decimal)
  opportunities.py # pure arb / cross-venue value detectors (after fees) + reject reasons
  scoring.py       # manufactured-arb interpretable score (refit before trusting)
  logger.py        # log EVERY candidate (accepted or not) → candidates.jsonl
  engine.py        # orchestrate + rank + report + built-in demo snapshot
  feeds/           # odds_api (sportsbook) + kalshi (binary) live feeds
  teams.py         # canonical abbreviations to match Kalshi ↔ Odds API
tests/             # fee/arb/staking correctness
```

## Honest limits
- **Manufactured-arb backtest needs tick-level bid/ask on both venues** you mostly don't have — so it's **forward-tested via the candidate log**, not backtested.
- **Set `KALSHI_FEE_RATE` to the real market rate** — the default 0.07 is the general taker rate; sports series can differ, and the threshold math depends on it.
- Success degrades access (books limit winners; Kalshi has position/liquidity caps). "Profit per bankroll-hour" matters more than ROI because the qualifying MLB universe is small.
