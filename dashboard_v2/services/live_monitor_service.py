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

from ..engines.entry_quality_engine import apply_price_extension_gate, score_entry_quality

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
    now: datetime | None = None,
) -> dict[str, float | int | None]:
    """Compute live price and same-time cumulative RVOL using completed bars only.

    Price may use the newest observed bar, but RVOL excludes a still-forming
    5-minute bar. The ratio is a LIVE-only diagnostic, never official EOD RVOL
    and never a trigger for official BREAKOUT CONFIRMED.
    """
    if bars is None or bars.empty:
        return {"last_price": None, "session_volume": None, "live_rvol": None, "reference_sessions": 0,
                "rvol_as_of": None}
    data = bars.copy().sort_values("Date").reset_index(drop=True)
    latest_stamp = pd.Timestamp(data["Date"].iloc[-1]).tz_convert(NY)
    use_date = session_date or latest_stamp.date()
    data = data.loc[data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() <= use_date)].copy()
    today = data.loc[data["Date"].map(lambda value: pd.Timestamp(value).tz_convert(NY).date() == use_date)].copy()
    if today.empty:
        return {"last_price": float(data["Close"].iloc[-1]), "session_volume": None, "live_rvol": None,
                "reference_sessions": 0, "rvol_as_of": None}
    reference_now = (now or datetime.now(tz=NY)).astimezone(NY)
    stamps = pd.to_datetime(today["Date"], errors="coerce")
    # Provider timestamps mark the start of 5-minute candles. Count a candle
    # toward RVOL only after its five-minute interval has completed.
    completed_mask = stamps.map(lambda stamp: pd.notna(stamp) and (pd.Timestamp(stamp) + pd.Timedelta(minutes=5)).to_pydatetime() <= reference_now)
    completed_today = today.loc[completed_mask].copy()
    session_volume = float(today["Volume"].sum())
    if completed_today.empty:
        return {"last_price": float(today["Close"].iloc[-1]), "session_volume": session_volume,
                "live_rvol": None, "reference_sessions": 0, "rvol_as_of": None}

    latest_completed_stamp = pd.Timestamp(completed_today["Date"].iloc[-1]).tz_convert(NY)
    latest_time = latest_completed_stamp.time()
    completed_volume = float(completed_today["Volume"].sum())

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
            live_rvol = completed_volume / average
    return {
        "last_price": float(today["Close"].iloc[-1]),
        "session_volume": session_volume,
        "live_rvol": live_rvol,
        "reference_sessions": len(reference_totals),
        "rvol_as_of": latest_completed_stamp,
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


def present_live_state(
    raw_state: str,
    *,
    official_stage: str = "BASE",
    rebase_active: bool = False,
    data_status: str = "LIVE",
) -> str:
    """Translate detailed V2 live diagnostics to compact legacy-style labels.

    Display labels explicitly say LIVE when they resemble an official EOD stage.
    This function is presentation-only; it never updates the official stage.
    """
    status = str(data_status or "").upper()
    if status == "DATA_STALE":
        return "DATA STALE"
    if status == "DATA_INCOMPLETE":
        return "DATA INCOMPLETE"
    if status == "MARKET_CLOSED":
        return "MARKET CLOSED"

    raw = str(raw_state or "").upper()
    stage = str(official_stage or "BASE").upper()
    if raw == "LIVE PIVOT CROSS (PROVISIONAL)":
        return "BREAKOUT (LIVE)"
    if raw == "ABOVE CANDIDATE PIVOT (PROVISIONAL)":
        return "BREAKOUT (LIVE)"
    if raw == "PIVOT APPROACH (LIVE)":
        return "PIVOT APPROACH (LIVE)"
    if raw == "BELOW ACTIVE BREAKOUT PIVOT (LIVE ONLY)":
        return "BELOW BREAKOUT PIVOT (LIVE)"
    if raw == "ABOVE ACTIVE BREAKOUT PIVOT (LIVE)":
        return "BREAKOUT HOLD (LIVE)"
    if raw == "BELOW CANDIDATE PIVOT (LIVE)":
        return "BASE + PP" if stage == "BASE + PP" else ("BASE FORMING" if rebase_active else "BASE")
    if raw.startswith("LIVE PRICE ONLY"):
        if stage == "BASE + PP":
            return "BASE + PP"
        return "BASE FORMING" if rebase_active else "BASE"
    if "INCOMPLETE" in raw or "STALE" in raw:
        return "DATA INCOMPLETE"
    return raw_state or "DATA INCOMPLETE"


def _finite_number(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def live_volume_quality(rvol_at_time: float | None) -> float | None:
    """Map same-time LIVE RVOL to a 0..100 score component; missing remains missing."""
    value = _finite_number(rvol_at_time)
    if value is None:
        return None
    if value >= 1.50:
        return 100.0
    if value >= 1.30:
        return 92.0
    if value >= 1.15:
        return 82.0
    if value >= 1.00:
        return 72.0
    if value >= 0.80:
        return 58.0
    return 40.0


def live_momentum_score(eod_momentum: float | None, eod_price: float | None, live_price: float | None) -> float | None:
    """Apply a bounded intraday nudge to the canonical EOD Momentum percentile."""
    base, close, price = map(_finite_number, (eod_momentum, eod_price, live_price))
    if base is None:
        return None
    if close is None or price is None or close <= 0:
        return float(max(0.0, min(100.0, base)))
    day_move_pct = (price / close - 1.0) * 100.0
    adjustment = max(-10.0, min(10.0, day_move_pct * 2.5))
    return float(max(0.0, min(100.0, base + adjustment)))


def live_breakout_quality(pivot_distance_pct: float | None, rvol_at_time: float | None) -> float | None:
    """V2 live setup-quality component for a provisional pivot-side observation."""
    distance = _finite_number(pivot_distance_pct)
    volume_score = live_volume_quality(rvol_at_time)
    if volume_score is None:
        volume_score = 55.0
    if distance is None:
        price_score = 60.0
    elif distance < 0:
        price_score = 55.0
    elif distance <= 3:
        price_score = 100.0
    elif distance <= 5:
        price_score = 82.0
    elif distance < 7:
        price_score = 55.0
    else:
        price_score = 25.0
    return float(0.60 * volume_score + 0.40 * price_score)


def live_ma_structure(daily_bars: pd.DataFrame | None, live_price: float | None) -> dict[str, Any]:
    """Evaluate MA structure using only completed EOD closes plus current live price."""
    price = _finite_number(live_price)
    if price is None or price <= 0 or daily_bars is None or daily_bars.empty or "Close" not in daily_bars:
        return {"status": "N/A", "score": None, "sma20": None, "sma50": None, "slope20_pct": None, "slope50_pct": None}
    closes = pd.to_numeric(daily_bars["Close"], errors="coerce").dropna().astype(float)
    if len(closes) < 60:
        return {"status": "N/A", "score": None, "sma20": None, "sma50": None, "slope20_pct": None, "slope50_pct": None}
    sma20_series = closes.rolling(20, min_periods=20).mean()
    sma50_series = closes.rolling(50, min_periods=50).mean()
    sma20 = _finite_number(sma20_series.iloc[-1])
    sma50 = _finite_number(sma50_series.iloc[-1])
    prior20 = _finite_number(sma20_series.iloc[-11])
    prior50 = _finite_number(sma50_series.iloc[-11])
    slope20 = ((sma20 / prior20) - 1.0) * 100.0 if sma20 and prior20 else None
    slope50 = ((sma50 / prior50) - 1.0) * 100.0 if sma50 and prior50 else None
    if sma20 is None or sma50 is None or slope20 is None or slope50 is None:
        status, score = "N/A", None
    elif price > sma20 > sma50 and slope20 > 0 and slope50 >= 0:
        status, score = "ALIGNED", 100.0
    elif price > sma50:
        status, score = "MIXED", 60.0
    else:
        status, score = "WEAK", 20.0
    return {"status": status, "score": score, "sma20": sma20, "sma50": sma50,
            "slope20_pct": slope20, "slope50_pct": slope50}


def compute_live_entry_quality(
    *,
    live_available: bool,
    official_stage: str,
    raw_live_state: str,
    live_price: float | None,
    eod_row: dict[str, Any] | None,
    monitor_row: dict[str, Any] | None,
    daily_bars: pd.DataFrame | None,
    live_rvol: float | None,
    candidate_distance_pct: float | None,
    breakout_distance_pct: float | None,
    breakout_cycle_active: bool,
) -> dict[str, Any]:
    """Compute V2 LIVE Entry Quality; Initial Risk is deliberately not a component.

    The component weights and 75/60 labels come from the V2 Entry Quality engine.
    Legacy-style intraday nudges are applied only to the display calculation; the
    canonical EOD score, stage, pattern, and pivots are never changed.
    """
    stage = str(official_stage or "BASE").upper().strip()
    state = str(raw_live_state or "").upper()
    quality_states = {
        "PIVOT APPROACH (LIVE)", "LIVE PIVOT CROSS (PROVISIONAL)",
        "ABOVE CANDIDATE PIVOT (PROVISIONAL)", "ABOVE ACTIVE BREAKOUT PIVOT (LIVE)",
    }
    eligible_stage = stage in {"PIVOT APPROACH", "BREAKOUT", "BREAKOUT CONFIRMED"}
    if (not live_available or stage == "BREAKOUT FAILURE" or (not eligible_stage and state not in quality_states)):
        return {"label": "—", "score": None, "detail": "", "components": {}, "reason_codes": ()}

    eod = eod_row or {}
    monitor = monitor_row or {}
    live_price_n = _finite_number(live_price)
    eod_price = _finite_number(eod.get("close"))
    pattern_score = _finite_number(monitor.get("_pattern_score"))
    readiness = _finite_number(monitor.get("_readiness_score", monitor.get("Breakout Readiness")))
    rs_score = _finite_number(monitor.get("_rs_score", monitor.get("RS")))
    eod_momentum = _finite_number(monitor.get("_momentum_score", monitor.get("Momentum")))
    ma_info = live_ma_structure(daily_bars, live_price_n)
    ma_score = _finite_number(ma_info.get("score"))
    live_momentum = live_momentum_score(eod_momentum, eod_price, live_price_n)
    volume_score = live_volume_quality(live_rvol)

    breakout_like = state in {
        "LIVE PIVOT CROSS (PROVISIONAL)", "ABOVE CANDIDATE PIVOT (PROVISIONAL)",
        "ABOVE ACTIVE BREAKOUT PIVOT (LIVE)",
    } or (eligible_stage and (stage.startswith("BREAKOUT") or breakout_cycle_active))
    effective_distance = breakout_distance_pct if breakout_cycle_active else candidate_distance_pct
    setup_quality = live_breakout_quality(effective_distance, live_rvol) if breakout_like else readiness

    components = {
        "pattern": pattern_score,
        "setup_quality": setup_quality,
        "relative_strength": rs_score,
        "live_momentum": live_momentum,
        "live_volume_quality": volume_score,
        "ma_structure": ma_score,
    }
    quality = score_entry_quality(components, mode="LIVE", stage=stage, required_components=("pattern",))
    score = _finite_number(quality.score)
    raw_label = str(getattr(quality.label, "value", quality.label))
    display_label = {
        "GOOD": "🟢 GOOD", "CAUTION": "🟡 CAUTION", "WAIT": "🔴 WAIT",
        "DATA INCOMPLETE": "⚪ DATA INCOMPLETE",
    }.get(raw_label, "—")
    extension_label, gate_action, gate_reasons = apply_price_extension_gate(
        quality.label,
        candidate_distance_pct=(None if breakout_cycle_active else candidate_distance_pct),
        breakout_distance_pct=(breakout_distance_pct if breakout_cycle_active else None),
    )
    extension_value = str(getattr(gate_action, "value", gate_action))
    if extension_value == "WAIT":
        display_label = "🔴 WAIT (EXTENDED)"
    elif extension_value == "CAUTION" and display_label == "🟢 GOOD":
        display_label = "🟡 CAUTION (EXTENDED)"
    detail = " · ".join(
        f"{name} {value:.0f}" for name, value in components.items() if value is not None
    )
    if ma_info.get("status") and ma_info["status"] != "N/A":
        detail = (detail + " · " if detail else "") + f"LIVE MA {ma_info['status']}"
    if live_rvol is not None and math.isfinite(float(live_rvol)):
        detail = (detail + " · " if detail else "") + f"LIVE RVOL AT TIME {float(live_rvol):.2f}×"
    if gate_reasons:
        detail = (detail + " · " if detail else "") + ",".join(gate_reasons)
    return {
        "label": display_label, "score": score, "detail": detail,
        "components": components,
        "reason_codes": tuple(quality.reason_codes) + tuple(gate_reasons),
        "ma_status": ma_info.get("status"),
        "sma20": ma_info.get("sma20"), "sma50": ma_info.get("sma50"),
    }


def format_recent_official_state_history(states: list[dict[str, Any]] | None, *, limit: int = 5) -> str:
    """Format replayed completed-EOD stages newest-first for the Live Monitor table."""
    rows = list(states or [])
    cleaned: list[tuple[pd.Timestamp, str]] = []
    for row in rows:
        try:
            stamp = pd.Timestamp(row.get("as_of_date")).normalize()
            stage = str(row.get("stage") or "").strip().upper()
            if pd.isna(stamp) or not stage:
                continue
            cleaned.append((stamp, stage))
        except (TypeError, ValueError):
            continue
    if not cleaned:
        return "—"
    cleaned.sort(key=lambda item: item[0], reverse=True)
    parts = []
    for stamp, stage in cleaned[:max(1, int(limit))]:
        parts.append(f"{stamp.strftime('%m/%d')} {stage}")
    return " → ".join(parts)


def format_buy_history(
    ticker: str,
    live_price: float | None,
    trades: list[dict[str, Any]] | None,
    adds: list[dict[str, Any]] | None,
    exits: list[dict[str, Any]] | None,
    *,
    max_events: int = 3,
) -> str:
    """Format user-owned V2 PP Journal buy fills; never reads legacy holdings/trades."""
    from .pp_journal_analytics import summarize_trade

    symbol = str(ticker or "").strip().upper()
    current = _finite_number(live_price)
    selected_trades = [row for row in (trades or []) if str(row.get("ticker") or "").strip().upper() == symbol]
    if not selected_trades:
        return "—"
    trade_ids = {str(row.get("id") or "") for row in selected_trades}
    relevant_adds = [row for row in (adds or []) if str(row.get("trade_id") or "") in trade_ids]
    relevant_exits = [row for row in (exits or []) if str(row.get("trade_id") or "") in trade_ids]
    events: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for trade in selected_trades:
        trade_id = str(trade.get("id") or "")
        own_adds = [row for row in relevant_adds if str(row.get("trade_id") or "") == trade_id]
        own_exits = [row for row in relevant_exits if str(row.get("trade_id") or "") == trade_id]
        summary = summarize_trade(trade, own_adds, own_exits)
        summaries[trade_id] = summary
        events.append({
            "date": str(trade.get("initial_entry_date") or "")[:10],
            "stage": str(trade.get("initial_entry_stage") or "BASE").upper(),
            "price": _finite_number(trade.get("initial_entry_price")),
            "trade_id": trade_id,
            "kind": "BUY",
        })
        for add in own_adds:
            events.append({
                "date": str(add.get("entry_date") or "")[:10],
                "stage": str(add.get("entry_stage") or "BASE").upper(),
                "price": _finite_number(add.get("entry_price")),
                "trade_id": trade_id,
                "kind": "ADD",
            })
    events = [event for event in events if event["date"] and event["price"] is not None and event["price"] > 0]
    events.sort(key=lambda item: (item["date"], item["kind"]), reverse=True)
    if not events:
        return "—"
    parts: list[str] = []
    for event in events[:max(1, int(max_events))]:
        summary = summaries.get(event["trade_id"], {})
        status = str(summary.get("status") or "OPEN")
        price = float(event["price"])
        date_label = pd.Timestamp(event["date"]).strftime("%m/%d")
        kind_label = "ADD " if event["kind"] == "ADD" else ""
        if status == "CLOSED":
            ret = _finite_number(summary.get("realized_return_pct"))
            suffix = f" 실현 {ret:+.1f}%" if ret is not None else ""
            status_label = "CLOSED"
        else:
            avg_cost = _finite_number(summary.get("average_remaining_cost"))
            ret = ((current / avg_cost) - 1.0) * 100.0 if current and avg_cost and avg_cost > 0 else None
            suffix = f" 잔여 {ret:+.1f}%" if ret is not None else ""
            status_label = "PARTIAL" if status == "PARTIAL" else "OPEN"
        parts.append(f"{kind_label}{date_label} {event['stage']} ${price:,.2f}{suffix} [{status_label}]")
    return " · ".join(parts)
