"""
Configuration — thresholds, venues, fee rate. Override via environment.

Thresholds are deliberately conservative and tied to the cost floor: a 1% gross
"arb" is a loss after Kalshi fees near 50/50, so the minimum guaranteed ROI is set
ABOVE the realistic fee+slippage floor, not at a round number.
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Secrets / endpoints ──────────────────────────────────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT_KEY = "baseball_mlb"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"  # public read endpoints

DATA_DIR = Path(os.getenv("PP_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
LOG_PATH = DATA_DIR / "candidates.jsonl"

# Optional: the sharp-money-tracker's live steam/divergence output, joined into the
# manufactured-arb scorer. Defaults to the sibling repo's data dir if present.
SHARP_SIGNALS_CSV = os.getenv(
    "PP_SHARP_SIGNALS",
    str(Path(__file__).resolve().parent.parent.parent / "sharp-money-tracker" / "data" / "sharp_signals.csv"),
)

# ── Fees / execution realism ─────────────────────────────────────────────────
KALSHI_FEE_RATE = float(os.getenv("KALSHI_FEE_RATE", "0.07"))   # set per market!
SLIPPAGE_BUFFER = float(os.getenv("PP_SLIPPAGE", "0.005"))      # per-leg cushion on price

# ── Opportunity thresholds (after fees + slippage) ───────────────────────────
MIN_PURE_ARB_ROI = float(os.getenv("PP_MIN_ARB_ROI", "0.010"))     # 1.0% guaranteed
MIN_THIN_ARB_ROI = float(os.getenv("PP_MIN_THIN_ARB_ROI", "0.025"))  # 2.5% on thin/stale
MIN_VALUE_EDGE = float(os.getenv("PP_MIN_VALUE_EDGE", "0.03"))     # 3pts vs sharp fair

# ── Sanity / risk gates ──────────────────────────────────────────────────────
MAX_PLAUSIBLE_GAP = float(os.getenv("PP_MAX_GAP", "0.12"))   # cross-venue gaps above = stale/mismatch
MAX_PRICE_AGE_SEC = float(os.getenv("PP_MAX_AGE", "120"))    # reject stale legs
MIN_SECONDS_TO_FIRST_PITCH = float(os.getenv("PP_MIN_TTFP", "600"))  # 10 min execution buffer
MIN_KALSHI_LIQUIDITY = float(os.getenv("PP_MIN_LIQ", "20"))  # contracts available to fill

# Sharp reference book(s) for the fair-probability anchor — de-vig these, NOT soft consensus.
SHARP_BOOKS = ("pinnacle", "betonlineag", "lowvig")
# Soft/commercial books we'd actually execute the sportsbook leg against.
EXECUTION_BOOKS = ("draftkings", "fanduel", "betmgm", "caesars", "betrivers", "pointsbetus")
