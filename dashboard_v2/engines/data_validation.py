"""Canonical OHLCV preparation. This module never silently removes the last row."""
from __future__ import annotations
from datetime import date
from typing import Iterable
import pandas as pd
import numpy as np

REQUIRED_OHLCV = ("Open", "High", "Low", "Close", "Volume")


class MarketDataError(ValueError):
    """Raised when source market data cannot safely be used by an engine."""


def normalize_ohlcv(frame: pd.DataFrame, *, as_of_date: date | str | None = None) -> pd.DataFrame:
    """Normalize OHLCV columns and dates without guessing which row is incomplete.

    The market-data service must pass only completed daily bars for EOD work, or
    supply `as_of_date` to cap the history. The latest eligible row is retained.
    Any incomplete-bar exclusion happens in the service layer, not here.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", *REQUIRED_OHLCV])
    df = frame.copy()
    # A source such as yfinance may name its DatetimeIndex "Date" while the
    # normalized frame also contains a Date column. Pandas treats sort_values("Date")
    # as ambiguous in that case, so clear index level names: the canonical Date
    # column is the single source of truth after normalization.
    if isinstance(df.index, pd.MultiIndex):
        df.index = df.index.set_names([None] * df.index.nlevels)
    else:
        df.index = df.index.rename(None)
    lower_to_original = {str(c).strip().lower(): c for c in df.columns}
    date_col = next((lower_to_original[k] for k in ("date", "datetime", "timestamp") if k in lower_to_original), None)
    if date_col is None:
        if isinstance(df.index, pd.DatetimeIndex):
            df["Date"] = df.index
        else:
            raise MarketDataError("A Date/Datetime column or DatetimeIndex is required")
    else:
        df["Date"] = df[date_col]
    rename: dict[object, str] = {}
    for canonical in REQUIRED_OHLCV:
        original = lower_to_original.get(canonical.lower())
        if original is not None:
            rename[original] = canonical
    df = df.rename(columns=rename)
    missing = [c for c in REQUIRED_OHLCV if c not in df.columns]
    if missing:
        raise MarketDataError(f"Missing OHLCV columns: {', '.join(missing)}")

    # Preserve the calendar-date component supplied by the source rather than
    # coercing every timestamp to UTC (which can shift exchange-local dates).
    # The provider adapter is responsible for converting raw intraday timestamps
    # to the correct exchange-local daily-session date before this function.
    normalized_dates = []
    for raw_date in df["Date"]:
        try:
            stamp = pd.Timestamp(raw_date)
            if pd.isna(stamp):
                raise ValueError("NaT")
            normalized_dates.append(pd.Timestamp(stamp.date()))
        except Exception as exc:
            raise MarketDataError("One or more bar dates are invalid") from exc
    df["Date"] = normalized_dates
    for col in REQUIRED_OHLCV:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    numeric = df[list(REQUIRED_OHLCV)].to_numpy(dtype="float64", copy=False)
    if not np.isfinite(numeric).all():
        raise MarketDataError("OHLCV contains missing/non-numeric/non-finite values")
    if (df["Volume"] < 0).any():
        raise MarketDataError("Volume must not be negative")
    if (df["High"] < df[["Open", "Close", "Low"]].max(axis=1)).any():
        raise MarketDataError("High is below Open/Close/Low on at least one bar")
    if (df["Low"] > df[["Open", "Close", "High"]].min(axis=1)).any():
        raise MarketDataError("Low is above Open/Close/High on at least one bar")

    df = df[["Date", *REQUIRED_OHLCV]].sort_values("Date", kind="stable")
    # A duplicated daily bar is an upstream-data ambiguity, not something to
    # hide via an arbitrary first/last choice.
    if df["Date"].duplicated().any():
        duplicates = df.loc[df["Date"].duplicated(keep=False), "Date"].dt.date.astype(str).unique()
        raise MarketDataError(f"Duplicate daily bars found: {', '.join(duplicates[:5])}")
    if as_of_date is not None:
        cutoff = pd.Timestamp(as_of_date).normalize()
        df = df.loc[df["Date"] <= cutoff]
    return df.reset_index(drop=True)


def bars_through_date(frame: pd.DataFrame, as_of_date: date | str) -> pd.DataFrame:
    """Date-boundary helper that includes the as-of date when present."""
    return normalize_ohlcv(frame, as_of_date=as_of_date)


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Calculate true range for already validated daily OHLCV bars."""
    if frame.empty:
        return pd.Series(dtype="float64")
    prev_close = frame["Close"].shift(1)
    ranges = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - prev_close).abs(),
        (frame["Low"] - prev_close).abs(),
    ], axis=1)
    return ranges.max(axis=1)


def atr_wilder(frame: pd.DataFrame, period: int) -> float | None:
    """Return latest Wilder ATR for completed bars only; None if too little data."""
    if period < 1 or len(frame) < period + 1:
        return None
    tr = true_range(frame)
    # Wilder's moving average is an EWM with alpha=1/period.
    result = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    value = result.iloc[-1]
    return float(value) if pd.notna(value) and value > 0 else None
