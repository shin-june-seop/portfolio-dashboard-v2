"""Opt-in Yahoo/yfinance daily-bar adapter.

This adapter mirrors the legacy app's current price-source convention as a
candidate for parity testing. It is deliberately not wired into Streamlit by
default because provider, adjustment, and completed-bar policy still require
explicit product approval.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Callable, Any

import pandas as pd

from ..engines.data_validation import normalize_ohlcv
from .market_data_service import MarketDataProviderNotConfigured

NY = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class MarketDataFetchError(RuntimeError):
    """Raised when the optional external provider cannot return daily bars."""


def _as_exchange_date(value: Any) -> date:
    """Get the supplied bar's displayed calendar date without UTC date shifting."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Invalid bar date returned by provider")
    return stamp.date()


class YFinanceDailyProvider:
    """Fetch adjusted daily OHLCV with a testable U.S. regular-session cutoff.

    Defaults intentionally mirror the legacy dashboard: `auto_adjust=True`,
    regular session only, and a same-day daily bar is eligible only at/after
    16:00 America/New_York. This is a *candidate parity policy*, not a final
    V2 policy approval. No writes, broker actions, or persistent side effects.

    `as_of_date` means a historical replay cutoff; the bar on that date is
    retained if returned because historical sessions are presumed completed.
    When omitted, the current exchange date is excluded until regular close.
    """

    def __init__(
        self,
        *,
        history_days: int = 5 * 365,
        auto_adjust: bool = True,
        session_close: time = time(16, 0),
        now_fn: Callable[[], datetime] | None = None,
        ticker_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if history_days < 60:
            raise ValueError("history_days must be at least 60 calendar days")
        self.history_days = int(history_days)
        self.auto_adjust = bool(auto_adjust)
        self.session_close = session_close
        self._now_fn = now_fn or (lambda: datetime.now(tz=NY))
        self._ticker_factory = ticker_factory

    def _get_ticker(self, symbol: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(symbol)
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataProviderNotConfigured(
                "yfinance is optional. Install with: pip install -e '.[market-data]'"
            ) from exc
        return yf.Ticker(symbol)

    def daily_bars(self, symbol: str, *, as_of_date: date | str | None = None) -> pd.DataFrame:
        ticker_symbol = str(symbol).upper().strip()
        if not ticker_symbol:
            raise ValueError("symbol must not be empty")

        explicit_as_of = as_of_date is not None
        live_now_ny: datetime | None = None
        if explicit_as_of:
            cutoff = pd.Timestamp(as_of_date).date()
            end_exclusive = cutoff + timedelta(days=1)
        else:
            now = self._now_fn()
            if now.tzinfo is None:
                raise ValueError("now_fn must return a timezone-aware datetime")
            live_now_ny = now.astimezone(NY)
            cutoff = live_now_ny.date()
            end_exclusive = cutoff + timedelta(days=1)

        start = cutoff - timedelta(days=self.history_days)
        ticker = self._get_ticker(ticker_symbol)
        try:
            raw = ticker.history(
                start=start.isoformat(),
                end=end_exclusive.isoformat(),  # yfinance's `end` is exclusive
                interval="1d",
                auto_adjust=self.auto_adjust,
                actions=False,
                prepost=False,
                timeout=15,
            )
        except Exception as exc:
            raise MarketDataFetchError(
                f"Daily OHLCV request failed for {ticker_symbol}: {type(exc).__name__}"
            ) from exc

        if raw is None or raw.empty:
            return pd.DataFrame(columns=["Date", *REQUIRED_COLUMNS])

        # Ticker.history normally returns simple columns, but reject ambiguous
        # multi-index columns rather than guessing the selected field/symbol.
        if isinstance(raw.columns, pd.MultiIndex):
            raise MarketDataFetchError(
                f"Unexpected MultiIndex columns for single-symbol request: {ticker_symbol}"
            )
        missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
        if missing:
            raise MarketDataFetchError(
                f"Provider omitted required fields for {ticker_symbol}: {', '.join(missing)}"
            )

        out = raw.loc[:, list(REQUIRED_COLUMNS)].copy()
        out.insert(0, "Date", [_as_exchange_date(value) for value in raw.index])
        out["Date"] = pd.to_datetime(out["Date"])
        out = out.loc[out["Date"].dt.date <= cutoff].copy()

        # On a live request, exclude today's row until the exchange's scheduled
        # regular-session close. This follows the legacy boundary; the final
        # policy may use a post-close publication buffer after parity testing.
        if not explicit_as_of and live_now_ny is not None:
            if live_now_ny.time().replace(tzinfo=None) < self.session_close:
                out = out.loc[out["Date"].dt.date < live_now_ny.date()].copy()

        try:
            return normalize_ohlcv(out)
        except Exception as exc:
            raise MarketDataFetchError(
                f"Invalid OHLCV returned for {ticker_symbol}: {type(exc).__name__}: {exc}"
            ) from exc
