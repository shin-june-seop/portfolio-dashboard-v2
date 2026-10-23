"""Read-only, completed-daily-bar quality/recovery technical scan.

This module is intentionally independent of pattern, candidate-pivot, breakout,
and PP engines. It reports moving-average recovery context and events only; it
never creates a pivot or changes an official entry stage.
"""
from __future__ import annotations

from typing import Any
import math

import pandas as pd


REQUIRED_COLUMNS = ("Date", "Close")
MIN_BARS_FOR_FULL_SCAN = 201  # current + prior SMA200 values are both required


class QualityRecoveryDataError(ValueError):
    """Raised when completed daily bars cannot support a trustworthy scan."""


def _finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def _pct_distance(price: float, average: float) -> float:
    return (price / average - 1.0) * 100.0


def analyze_quality_recovery(symbol: str, bars: pd.DataFrame) -> dict[str, Any]:
    """Analyze technical recovery context on completed daily Close prices.

    Definitions (intentionally transparent):
    * Recovery Setup: Close > SMA20, SMA20 > SMA50, SMA20 rising versus five
      completed sessions ago, and SMA50 < SMA200. This is an early technical
      recovery context, not a buy signal or a fundamental-quality conclusion.
    * Recovery Cross: the completed-bar SMA20 crosses from <= SMA50 to > SMA50
      on the latest bar.
    * Golden Cross: the completed-bar SMA50 crosses from <= SMA200 to > SMA200
      on the latest bar.
    * 120D/200D Reclaim: Close crosses from <= the same-day SMA to > that SMA
      on the latest completed bar.

    At least 201 unique, valid daily observations are required so the current
    and previous SMA200 can both be computed. OHLC's `Close` series is used,
    not dividend-adjusted `Adj Close`. This function must not receive intraday bars.
    """
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise QualityRecoveryDataError("SYMBOL_EMPTY")
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise QualityRecoveryDataError("NO_DAILY_BARS")
    missing = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise QualityRecoveryDataError("MISSING_COLUMNS:" + ",".join(missing))

    frame = bars.loc[:, ["Date", "Close"]].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    if frame["Date"].isna().any():
        raise QualityRecoveryDataError("INVALID_DAILY_DATES")
    if frame["Date"].duplicated().any():
        raise QualityRecoveryDataError("DUPLICATE_DAILY_DATES")
    if frame["Close"].isna().any() or not frame["Close"].map(_finite_positive).all():
        raise QualityRecoveryDataError("INVALID_CLOSE_VALUES")

    # The EOD provider emits date-normalized bars. Reject timestamps with a
    # time component or multiple observations on one session instead of quietly
    # turning intraday observations into misleading SMA values.
    if (frame["Date"] != frame["Date"].dt.normalize()).any() or frame["Date"].dt.normalize().duplicated().any():
        raise QualityRecoveryDataError("INTRADAY_OR_DUPLICATE_SESSION_BARS")
    frame = frame.sort_values("Date", kind="stable").reset_index(drop=True)
    if len(frame) < MIN_BARS_FOR_FULL_SCAN:
        raise QualityRecoveryDataError(
            f"INSUFFICIENT_COMPLETED_DAILY_BARS:{len(frame)}<{MIN_BARS_FOR_FULL_SCAN}"
        )

    close = frame["Close"].astype(float)
    frame["SMA20"] = close.rolling(20, min_periods=20).mean()
    frame["SMA50"] = close.rolling(50, min_periods=50).mean()
    frame["SMA120"] = close.rolling(120, min_periods=120).mean()
    frame["SMA200"] = close.rolling(200, min_periods=200).mean()
    current = frame.iloc[-1]
    previous = frame.iloc[-2]
    sma20, sma50, sma120, sma200 = (float(current[name]) for name in ("SMA20", "SMA50", "SMA120", "SMA200"))
    prev_sma20, prev_sma50, prev_sma120, prev_sma200 = (
        float(previous[name]) for name in ("SMA20", "SMA50", "SMA120", "SMA200")
    )
    price = float(current["Close"])
    rising_sma20_5d = sma20 > float(frame.iloc[-6]["SMA20"])

    recovery_cross = prev_sma20 <= prev_sma50 and sma20 > sma50
    golden_cross = prev_sma50 <= prev_sma200 and sma50 > sma200
    reclaim_120 = float(previous["Close"]) <= prev_sma120 and price > sma120
    reclaim_200 = float(previous["Close"]) <= prev_sma200 and price > sma200
    recovery_setup = (
        price > sma20
        and sma20 > sma50
        and rising_sma20_5d
        and sma50 < sma200
    )

    # The state describes where price/trend sits, not an entry stage.
    if recovery_setup:
        state = "RECOVERY_SETUP"
    elif price > sma200 and sma50 > sma200:
        state = "ABOVE_200D_CONFIRMED_TREND"
    elif price > sma200:
        state = "PRICE_ABOVE_200D_MA50_NOT_CROSSED"
    elif price > sma120:
        state = "ABOVE_120D_BELOW_200D"
    elif price > sma50:
        state = "ABOVE_50D_BELOW_120D"
    else:
        state = "BELOW_50D"

    return {
        "ticker": ticker,
        "as_of_date": current["Date"].date().isoformat(),
        "close": price,
        "sma20": sma20,
        "sma50": sma50,
        "sma120": sma120,
        "sma200": sma200,
        "distance_sma20_pct": _pct_distance(price, sma20),
        "distance_sma50_pct": _pct_distance(price, sma50),
        "distance_sma120_pct": _pct_distance(price, sma120),
        "distance_sma200_pct": _pct_distance(price, sma200),
        "sma20_rising_5d": bool(rising_sma20_5d),
        "recovery_setup": bool(recovery_setup),
        "recovery_cross_today": bool(recovery_cross),
        "golden_cross_today": bool(golden_cross),
        "reclaimed_120d_today": bool(reclaim_120),
        "reclaimed_200d_today": bool(reclaim_200),
        "recovery_state": state,
        "technical_price_basis": "YAHOO_OHLC_AUTO_ADJUST_FALSE_CLOSE",
        "signal_provenance": "COMPLETED_DAILY_BARS_ONLY",
        # No fundamentals provider/contract exists in this phase. Keep this
        # explicit rather than implying a fundamental quality pass.
        "fundamental_gate_status": "NOT_ASSESSED",
        "validation_status": "OBSERVED_NOT_VALIDATED",
    }
