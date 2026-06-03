"""profit_priority — executable, fee-aware MLB cross-venue profit engine.

Pure arb + cross-venue value are priced on EXECUTABLE cost after real Kalshi fees
and slippage (so a 'guaranteed profit' is actually guaranteed). Manufactured arb
is scored and logged as R&D, never traded blind.
"""
__version__ = "0.1.0"
