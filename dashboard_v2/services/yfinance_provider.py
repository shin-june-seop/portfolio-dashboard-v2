"""Yahoo Finance daily OHLCV adapter under the approved EOD data policy.

Technical patterns/pivots use Yahoo OHLC with auto_adjust=False (Yahoo's
split-normalized traded-price series). Multi-period returns use Adj Close,
which Yahoo adjusts for splits and cash distributions. Only the latest
completed XNYS session is accepted; the current session is withheld until a
15-minute post-close finalization buffer has elapsed. This provider is for
read-only analysis and can fail closed when data is stale or incomplete.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
import math
from zoneinfo import ZoneInfo
from typing import Callable, Any

import pandas as pd

from ..engines.data_validation import normalize_ohlcv
from .market_data_service import MarketDataProviderNotConfigured

NY = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
ADJUSTED_CLOSE = "Adj Close"
DEFAULT_FINALIZATION_CUTOFF = time(16, 15)


class MarketDataFetchError(RuntimeError):
    """Raised when the provider cannot return a trustworthy completed daily bar."""


def _as_exchange_date(value: Any) -> date:
    """Get the supplied bar's displayed calendar date without UTC date shifting."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Invalid bar date returned by provider")
    return stamp.date()


@lru_cache(maxsize=1)
def _xnys_calendar():
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - dependency is installed in app
        raise MarketDataProviderNotConfigured(
            "exchange-calendars is required for the completed-session check"
        ) from exc
    return xcals.get_calendar("XNYS")


def _latest_xnys_session_on_or_before(day: date) -> date:
    """Return the latest NYSE session on/before a date, including holidays/weekends."""
    try:
        session = _xnys_calendar().date_to_session(pd.Timestamp(day), direction="previous")
    except Exception as exc:
        raise MarketDataFetchError(
            f"Could not determine the expected XNYS session for {day}: {type(exc).__name__}"
        ) from exc
    return pd.Timestamp(session).date()


class YFinanceDailyProvider:
    """Fetch split-normalized OHLCV plus a separate dividend-adjusted close.

    `auto_adjust=False` is fixed by policy: patterns/pivots must not inherit the
    dividend-adjustment factor applied to Adj Close. `as_of_date` is for an
    explicit historical replay cutoff; its latest expected XNYS session must be
    present. Live requests withhold the current date until 16:15 New York time,
    and then require that session's bar to be present or fail closed.
    """

    def __init__(
        self,
        *,
        history_days: int = 5 * 365,
        finalization_cutoff: time = DEFAULT_FINALIZATION_CUTOFF,
        now_fn: Callable[[], datetime] | None = None,
        ticker_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if history_days < 60:
            raise ValueError("history_days must be at least 60 calendar days")
        self.history_days = int(history_days)
        self.finalization_cutoff = finalization_cutoff
        self._now_fn = now_fn or (lambda: datetime.now(tz=NY))
        self._ticker_factory = ticker_factory

    def _get_ticker(self, symbol: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(symbol)
        try:
            import yfinance as yf
        except ImportError as exc:
            raise MarketDataProviderNotConfigured(
                "yfinance is required for Yahoo Finance EOD data."
            ) from exc
        return yf.Ticker(symbol)

    def daily_bars(self, symbol: str, *, as_of_date: date | str | None = None) -> pd.DataFrame:
        ticker_symbol = str(symbol).upper().strip()
        if not ticker_symbol:
            raise ValueError("symbol must not be empty")

        explicit_as_of = as_of_date is not None
        now = self._now_fn()
        if now.tzinfo is None:
            raise ValueError("now_fn must return a timezone-aware datetime")
        live_now_ny = now.astimezone(NY)
        if explicit_as_of:
            cutoff = pd.Timestamp(as_of_date).date()
            if cutoff > live_now_ny.date():
                raise MarketDataFetchError(
                    f"as_of_date {cutoff} is in the future relative to New York date {live_now_ny.date()}"
                )
        else:
            cutoff = live_now_ny.date()
        end_exclusive = cutoff + timedelta(days=1)

        # Explicit historical cutoffs can include their bar only if that
        # session is already complete in real time. If the requested cutoff is
        # today before the buffer, behave like an EOD scan and withhold today.
        current_session_not_final = (
            cutoff == live_now_ny.date()
            and live_now_ny.time().replace(tzinfo=None) < self.finalization_cutoff
        )
        if current_session_not_final:
            expected_reference_date = cutoff - timedelta(days=1)
        else:
            expected_reference_date = cutoff

        expected_session = _latest_xnys_session_on_or_before(expected_reference_date)
        start = cutoff - timedelta(days=self.history_days)
        ticker = self._get_ticker(ticker_symbol)
        try:
            raw = ticker.history(
                start=start.isoformat(),
                end=end_exclusive.isoformat(),  # yfinance's end date is exclusive
                interval="1d",
                auto_adjust=False,
                actions=False,
                prepost=False,
                timeout=15,
            )
        except Exception as exc:
            raise MarketDataFetchError(
                f"Daily OHLCV request failed for {ticker_symbol}: {type(exc).__name__}"
            ) from exc

        if raw is None or raw.empty:
            raise MarketDataFetchError(f"No daily bars returned for {ticker_symbol}")

        if isinstance(raw.columns, pd.MultiIndex):
            raise MarketDataFetchError(
                f"Unexpected MultiIndex columns for single-symbol request: {ticker_symbol}"
            )
        missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
        if missing:
            raise MarketDataFetchError(
                f"Provider omitted required fields for {ticker_symbol}: {', '.join(missing)}"
            )
        if ADJUSTED_CLOSE not in raw.columns:
            raise MarketDataFetchError(
                f"Provider omitted {ADJUSTED_CLOSE} required for total-return calculations: {ticker_symbol}"
            )

        dates = [_as_exchange_date(value) for value in raw.index]
        out = raw.loc[:, list(REQUIRED_COLUMNS)].copy()
        out.insert(0, "Date", pd.to_datetime(dates))
        out[ADJUSTED_CLOSE] = pd.to_numeric(raw[ADJUSTED_CLOSE], errors="coerce").to_numpy()
        out = out.loc[out["Date"].dt.date <= cutoff].copy()

        # Never expose a still-forming current-session bar to EOD analysis.
        if current_session_not_final:
            out = out.loc[out["Date"].dt.date < cutoff].copy()

        # OHLCV and Adj Close are both required. Fail closed rather than emit
        # partial or mixed-basis data that can create misleading pivots/signals.
        try:
            normalized = normalize_ohlcv(out[["Date", *REQUIRED_COLUMNS]])
        except Exception as exc:
            raise MarketDataFetchError(
                f"Invalid OHLCV returned for {ticker_symbol}: {type(exc).__name__}: {exc}"
            ) from exc

        adjusted = out[["Date", ADJUSTED_CLOSE]].copy().reset_index(drop=True)
        adjusted_values = adjusted[ADJUSTED_CLOSE].astype(float).tolist()
        if any(not math.isfinite(value) for value in adjusted_values):
            raise MarketDataFetchError(f"Missing/non-finite Adj Close for {ticker_symbol}")
        if any(value <= 0 for value in adjusted_values):
            raise MarketDataFetchError(f"Non-positive Adj Close for {ticker_symbol}")
        if not normalized.empty:
            # Enforce non-zero reported volume on the latest signal bar. A zero
            # value can otherwise silently make RVOL unavailable or misleading.
            if float(normalized.iloc[-1]["Volume"]) <= 0:
                raise MarketDataFetchError(
                    f"Latest completed bar has missing/zero volume for {ticker_symbol}"
                )
            actual_latest = normalized.iloc[-1]["Date"].date()
            if actual_latest != expected_session:
                raise MarketDataFetchError(
                    f"Latest completed XNYS session missing/incomplete for {ticker_symbol}: "
                    f"expected {expected_session}, received {actual_latest}"
                )
        else:
            raise MarketDataFetchError(f"No completed XNYS-session bars available for {ticker_symbol}")

        # Normalize the adjusted-close dates separately, then merge so the
        # OHLCV columns and total-return series stay aligned but not conflated.
        try:
            result = normalized.merge(adjusted, on="Date", how="left", validate="one_to_one")
        except Exception as exc:
            raise MarketDataFetchError(
                f"Could not align Adj Close for {ticker_symbol}: {type(exc).__name__}"
            ) from exc
        if result[ADJUSTED_CLOSE].isna().any():
            raise MarketDataFetchError(f"Adj Close dates do not align to OHLCV for {ticker_symbol}")
        return result[["Date", *REQUIRED_COLUMNS, ADJUSTED_CLOSE]].reset_index(drop=True)
