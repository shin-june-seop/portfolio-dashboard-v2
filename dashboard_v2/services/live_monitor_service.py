"""Provisional intraday monitoring, isolated from the official EOD engine.

Yahoo Finance 5-minute bars are only a provisional observation feed. This module
never writes EOD stage history and never marks an official breakout confirmed or
failed. Official pattern/pivot/stage values must come from the completed-daily-bar
screening service and be passed in as read-only context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
DEFAULT_STALE_MINUTES = 15
MIN_REFERENCE_SESSIONS_FOR_LIVE_RVOL = 3


class LiveMarketDataError(RuntimeError):
    """Raised when intraday data cannot be trusted even for provisional display."""


@dataclass(frozen=True, slots=True)
class LiveDataStatus:
    code: str
    market_open: bool
    signal_available: bool
    latest_bar_at: pd.Timestamp | None
    age_minutes: float | None
    reason: str


def _calendar():
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise LiveMarketDataError("exchange-calendars is required for intraday freshness checks") from exc
    return xcals.get_calendar("XNYS")


def _market_window(now: datetime) -> tuple[bool, bool]:
    """Return (is_XNYS_session, is_regular_market_open) using the exchange calendar."""
    now_ny = now.astimezone(NY)
    cal = _calendar()
    session = pd.Timestamp(now_ny.date())
    try:
        is_session = bool(cal.is_session(session))
    except Exception as exc:
        raise LiveMarketDataError(f"Cannot determine XNYS session: {type(exc).__name__}") from exc
    if not is_session:
        return False, False
    try:
        market_open = pd.Timestamp(cal.session_open(session)).to_pydatetime().astimezone(NY)
        market_close = pd.Timestamp(cal.session_close(session)).to_pydatetime().astimezone(NY)
    except Exception as exc:
        raise LiveMarketDataError(f"Cannot determine XNYS trading hours: {type(exc).__name__}") from exc
    return True, market_open <= now_ny < market_close


def normalize_intraday_bars(raw: pd.DataFrame, *, now: datetime | None = None) -> pd.DataFrame:
    """Validate and normalize provider bars without converting their timestamps to dates."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        raise LiveMarketDataError("No intraday bars returned")
    if isinstance(raw.columns, pd.MultiIndex):
        raise LiveMarketDataError("Unexpected MultiIndex columns for a single-symbol intraday request")
    missing = [name for name in REQUIRED_COLUMNS if name not in raw.columns]
    if missing:
        raise LiveMarketDataError(f"Provider omitted required intraday fields: {', '.join(missing)}")

    index = pd.DatetimeIndex(pd.to_datetime(raw.index, errors="coerce"))
    if index.isna().any():
        raise LiveMarketDataError("Invalid intraday timestamps returned by provider")
    if index.tz is None:
        raise LiveMarketDataError("Intraday timestamps are timezone-naive; refusing to guess market time zone")
    index = index.tz_convert(NY)

    frame = raw.loc[:, list(REQUIRED_COLUMNS)].copy().reset_index(drop=True)
    frame.insert(0, "Date", index)
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        raise LiveMarketDataError("Intraday OHLCV contains missing or non-numeric values")
    numeric = frame[list(REQUIRED_COLUMNS)].to_numpy(dtype=float)
    if not all(math.isfinite(float(value)) for value in numeric.ravel()):
        raise LiveMarketDataError("Intraday OHLCV contains non-finite values")
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise LiveMarketDataError("Intraday OHLC prices must be positive")
    if (frame["Volume"] < 0).any():
        raise LiveMarketDataError("Intraday volume must not be negative")
    invalid_range = (
        (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1))
        | (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1))
    )
    if invalid_range.any():
        raise LiveMarketDataError("Intraday OHLC high/low range is inconsistent")
    if frame["Date"].duplicated().any():
        raise LiveMarketDataError("Duplicate intraday bar timestamps")

    frame = frame.sort_values("Date").reset_index(drop=True)
    reference_now = now or datetime.now(tz=NY)
    if reference_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_ny = reference_now.astimezone(NY)
    frame = frame.loc[frame["Date"] <= pd.Timestamp(now_ny)].copy()
    # Use regular-session bars only. The app must not silently mix premarket or after-hours volume.
    stamps = frame["Date"].dt.tz_convert(NY)
    times = stamps.dt.time
    cal = _calendar()
    session_mask = []
    for stamp in stamps:
        try:
            session_mask.append(bool(cal.is_session(pd.Timestamp(stamp.date()))))
        except Exception:
            session_mask.append(False)
    frame = frame.loc[
        pd.Series(session_mask, index=frame.index)
        & (times >= pd.Timestamp("09:30").time())
        & (times < pd.Timestamp("16:00").time())
    ].copy()
    if frame.empty:
        raise LiveMarketDataError("No regular-session intraday bars remain after validation")
    return frame.reset_index(drop=True)


class YFinanceIntradayProvider:
    """Thin Yahoo Finance adapter; source is provisional, not guaranteed real-time."""

    def __init__(self, *, ticker_factory: Callable[[str], Any] | None = None):
        self._ticker_factory = ticker_factory

    def _get_ticker(self, symbol: str):
        if self._ticker_factory is not None:
            return self._ticker_factory(symbol)
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - dependency is in requirements
            raise LiveMarketDataError("yfinance is required for Yahoo Finance intraday data") from exc
        return yf.Ticker(symbol)

    def intraday_bars(self, symbol: str, *, now: datetime | None = None) -> pd.DataFrame:
        ticker_symbol = str(symbol or "").strip().upper()
        if not ticker_symbol:
            raise ValueError("symbol must not be empty")
        # This first Live Monitor release assumes US equity/index regular sessions
        # governed by XNYS hours. Futures, FX and 24-hour crypto symbols would need
        # an instrument-specific session/volume profile and must not be mis-scored.
        if ticker_symbol.endswith(("=F", "=X", "-USD")):
            raise LiveMarketDataError(
                "This Live Monitor version supports XNYS-session symbols only; futures/FX/crypto need a separate session profile"
            )
        ticker = self._get_ticker(ticker_symbol)
        try:
            raw = ticker.history(
                period="10d",
                interval="5m",
                auto_adjust=False,
                actions=False,
                prepost=False,
                timeout=15,
            )
        except Exception as exc:
            raise LiveMarketDataError(
                f"Yahoo intraday request failed for {ticker_symbol}: {type(exc).__name__}"
            ) from exc
        return normalize_intraday_bars(raw, now=now)


def assess_live_data_status(
    bars: pd.DataFrame,
    *,
    now: datetime,
    stale_after_minutes: int = DEFAULT_STALE_MINUTES,
) -> LiveDataStatus:
    """Fail closed during an open session when a current/fresh bar is not available."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if bars is None or bars.empty or "Date" not in bars.columns:
        return LiveDataStatus("DATA_INCOMPLETE", False, False, None, None, "NO_VALID_BARS")
    now_ny = now.astimezone(NY)
    latest = pd.Timestamp(bars["Date"].iloc[-1])
    if latest.tzinfo is None:
        return LiveDataStatus("DATA_INCOMPLETE", False, False, None, None, "LATEST_TIMESTAMP_HAS_NO_TIMEZONE")
    latest = latest.tz_convert(NY)
    age = max(0.0, (now_ny - latest.to_pydatetime()).total_seconds() / 60.0)
    try:
        _, market_open = _market_window(now_ny)
    except LiveMarketDataError:
        return LiveDataStatus("DATA_INCOMPLETE", False, False, latest, age, "EXCHANGE_CALENDAR_UNAVAILABLE")

    if not market_open:
        return LiveDataStatus("MARKET_CLOSED", False, False, latest, age, "REGULAR_SESSION_CLOSED")
    if latest.date() != now_ny.date():
        return LiveDataStatus("DATA_STALE", True, False, latest, age, "NO_BAR_FOR_CURRENT_SESSION")
    # Intraday indexes commonly stamp the start of the 5-minute bar. A 15-minute
    # threshold allows a normal bar cadence plus brief provider latency.
    if age > float(stale_after_minutes):
        return LiveDataStatus("DATA_STALE", True, False, latest, age, "LATEST_BAR_TOO_OLD")
    return LiveDataStatus("LIVE", True, True, latest, age, "FRESH_REGULAR_SESSION_BAR")


def live_session_metrics(
    bars: pd.DataFrame,
    *,
    session_date=None,
    reference_sessions: int = 5,
    min_reference_sessions: int = MIN_REFERENCE_SESSIONS_FOR_LIVE_RVOL,
) -> dict[str, float | int | None]:
    """Compute latest bar price and same-time cumulative intraday volume ratio.

    The ratio is a LIVE intraday diagnostic only, not official EOD RVOL and not a
    trigger for BREAKOUT CONFIRMED.
    """
    if bars is None or bars.empty:
        return {"last_price": None, "session_volume": None, "live_rvol": None, "reference_sessions": 0}
    data = bars.copy().sort_values("Date").reset_index(drop=True)
    latest_stamp = pd.Timestamp(data["Date"].iloc[-1]).tz_convert(NY)
    use_date = session_date or latest_stamp.date()
    data = data.loc[data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() <= use_date)].copy()
    today = data.loc[data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() == use_date)].copy()
    if today.empty:
        return {"last_price": float(data["Close"].iloc[-1]), "session_volume": None, "live_rvol": None, "reference_sessions": 0}
    latest_time = pd.Timestamp(today["Date"].iloc[-1]).tz_convert(NY).time()
    session_volume = float(today["Volume"].sum())

    day_dates = sorted({pd.Timestamp(value).tz_convert(NY).date() for value in data["Date"] if pd.Timestamp(value).tz_convert(NY).date() < use_date})
    day_dates = day_dates[-max(0, int(reference_sessions)):]
    reference_totals: list[float] = []
    for day in day_dates:
        day_data = data.loc[data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() == day)].copy()
        through_same_time = day_data.loc[day_data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).time() <= latest_time)]
        if not through_same_time.empty:
            reference_totals.append(float(through_same_time["Volume"].sum()))
    live_rvol = None
    if len(reference_totals) >= min_reference_sessions:
        average = sum(reference_totals) / len(reference_totals)
        if math.isfinite(average) and average > 0:
            live_rvol = session_volume / average
    return {
        "last_price": float(today["Close"].iloc[-1]),
        "session_volume": session_volume,
        "live_rvol": live_rvol,
        "reference_sessions": len(reference_totals),
    }


def crossed_pivot_this_session(bars: pd.DataFrame, pivot: float | None, *, now: datetime) -> bool:
    """Detect a true intraday close cross using same-session bars only."""
    if pivot is None or not math.isfinite(float(pivot)) or float(pivot) <= 0 or bars is None or bars.empty:
        return False
    day = now.astimezone(NY).date()
    today = bars.loc[bars["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() == day)]
    closes = pd.to_numeric(today["Close"], errors="coerce").dropna().tolist() if not today.empty else []
    return any(float(previous) < float(pivot) <= float(current) for previous, current in zip(closes, closes[1:]))


def classify_live_state(
    *,
    live_price: float | None,
    candidate_pivot: float | None,
    breakout_pivot: float | None,
    breakout_cycle_active: bool,
    candidate_crossed_today: bool,
) -> str:
    """Read-only provisional state. Never returns official BREAKOUT/FAILURE stages."""
    if live_price is None or not math.isfinite(float(live_price)) or float(live_price) <= 0:
        return "DATA INCOMPLETE"
    price = float(live_price)
    candidate = float(candidate_pivot) if candidate_pivot is not None and pd.notna(candidate_pivot) and float(candidate_pivot) > 0 else None
    breakout = float(breakout_pivot) if breakout_pivot is not None and pd.notna(breakout_pivot) and float(breakout_pivot) > 0 else None

    if breakout_cycle_active and breakout is not None and price < breakout:
        return "BELOW ACTIVE BREAKOUT PIVOT (LIVE ONLY)"
    if candidate is not None and price >= candidate:
        if candidate_crossed_today:
            return "LIVE PIVOT CROSS (PROVISIONAL)"
        return "ABOVE CANDIDATE PIVOT (PROVISIONAL)"
    if candidate is not None:
        distance = (price / candidate - 1.0) * 100.0
        if -5.0 <= distance < 0.0:
            return "PIVOT APPROACH (LIVE)"
        return "BELOW CANDIDATE PIVOT (LIVE)"
    if breakout_cycle_active and breakout is not None:
        return "ABOVE ACTIVE BREAKOUT PIVOT (LIVE)"
    return "LIVE PRICE ONLY · NO VALID CANDIDATE PIVOT"
