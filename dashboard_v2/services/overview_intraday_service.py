"""Legacy-style intraday S&P 500 / NASDAQ / DOW panels for Overview.

This ports the V50.53 Overview cards: regular-session 5-minute candlesticks,
previous-session close reference line, live session change in the card header,
and a compact relative-volume strip. This is a presentation-only feed and is
never passed to the EOD pattern/pivot/stage or Market Regime engines.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import html
import math
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go

NY = ZoneInfo("America/New_York")
INTERVAL = "5m"
MAX_LIVE_BAR_AGE_MINUTES = 15

INDEX_INTRADAY_CONFIGS: tuple[dict[str, str], ...] = (
    {"label": "S&P 500", "symbol": "^GSPC"},
    {"label": "NASDAQ", "symbol": "^IXIC"},
    {"label": "DOW", "symbol": "^DJI"},
)


class OverviewIntradayDataError(RuntimeError):
    """Raised when an intraday index feed is absent or cannot be trusted."""


def _flatten_yfinance_columns(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    x = frame.copy()
    if not isinstance(x.columns, pd.MultiIndex):
        return x

    upper_symbol = str(symbol).strip().upper()
    price_fields = {"open", "high", "low", "close", "adj close", "volume"}
    candidates = []
    for column in x.columns:
        parts = [str(value).strip() for value in column]
        if upper_symbol in {part.upper() for part in parts}:
            candidates.append(column)

    if candidates:
        ticker_level = None
        for level in range(x.columns.nlevels):
            if any(str(column[level]).strip().upper() == upper_symbol for column in candidates):
                ticker_level = level
                break
        if ticker_level is not None:
            try:
                x = x.xs(upper_symbol, axis=1, level=ticker_level, drop_level=True)
            except Exception:
                try:
                    x = x.xs(str(symbol).strip().lower(), axis=1, level=ticker_level, drop_level=True)
                except Exception:
                    pass

    if isinstance(x.columns, pd.MultiIndex):
        flattened = []
        for column in x.columns:
            selected = None
            for part in reversed(column):
                if str(part).strip().lower() in price_fields:
                    selected = str(part).strip().title()
                    break
            flattened.append(selected if selected else str(column[-1]))
        x.columns = flattened
    return x


def normalize_intraday_index_bars(raw: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    """Normalize Yahoo's 5-minute OHLCV with New York timestamps and RTH only."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        raise OverviewIntradayDataError("NO_INTRADAY_BARS")

    x = _flatten_yfinance_columns(raw, symbol)
    rename = {}
    for column in x.columns:
        name = str(column).strip().lower()
        if name in {"open", "high", "low", "close", "volume"}:
            rename[column] = name.title()
        elif name == "adj close":
            rename[column] = "Close"
    x = x.rename(columns=rename)

    missing = [field for field in ("Open", "High", "Low", "Close") if field not in x.columns]
    if missing:
        raise OverviewIntradayDataError(f"MISSING_OHLC_FIELDS:{','.join(missing)}")
    if "Volume" not in x.columns:
        # Price candles remain usable if Yahoo omits volume. The RVOL strip will
        # be empty instead of inventing a zero or substituting ETF volume.
        x["Volume"] = np.nan

    index = pd.DatetimeIndex(pd.to_datetime(x.index, errors="coerce"))
    valid_timestamps = ~index.isna()
    if not valid_timestamps.all():
        x = x.loc[valid_timestamps].copy()
        index = index[valid_timestamps]
    if index.empty:
        raise OverviewIntradayDataError("INVALID_INTRADAY_TIMESTAMPS")
    if index.tz is None:
        # Match the legacy Yahoo handling: naive intraday timestamps are treated
        # as exchange-local New York time.
        index = index.tz_localize(NY)
    else:
        index = index.tz_convert(NY)

    x.index = pd.DatetimeIndex(index)
    for field in ("Open", "High", "Low", "Close", "Volume"):
        x[field] = pd.to_numeric(x[field], errors="coerce")
    x = x.sort_index()
    x = x.loc[~x.index.duplicated(keep="last")].copy()

    # Use regular-session bars only; no premarket or after-hours candles/RVOL.
    local_time = x.index.time
    regular_mask = (
        (local_time >= pd.Timestamp("09:30").time())
        & (local_time < pd.Timestamp("16:00").time())
    )
    x = x.loc[regular_mask].copy()
    if x.empty:
        raise OverviewIntradayDataError("NO_REGULAR_SESSION_BARS")

    prices = x[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)
    finite_prices = np.isfinite(prices).all(axis=1)
    positive_prices = (prices > 0).all(axis=1)
    valid_range = (
        (x["High"] >= x[["Open", "Close", "Low"]].max(axis=1))
        & (x["Low"] <= x[["Open", "Close", "High"]].min(axis=1))
    ).to_numpy()
    x = x.loc[finite_prices & positive_prices & valid_range].copy()
    if x.empty:
        raise OverviewIntradayDataError("NO_VALID_INTRADAY_OHLC")
    x["Volume"] = x["Volume"].where(np.isfinite(x["Volume"]) & (x["Volume"] >= 0), np.nan)
    return x


def fetch_intraday_index_bars(
    symbol: str,
    *,
    yf_module: Any | None = None,
) -> tuple[pd.DataFrame, str, str]:
    """Fetch 1-month 5m bars and use the same fallback order as legacy V50.53."""
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return pd.DataFrame(), "", "EMPTY_SYMBOL"
    try:
        if yf_module is None:
            import yfinance as yf_module  # type: ignore[no-redef]
    except Exception as exc:  # pragma: no cover - dependency/runtime edge
        return pd.DataFrame(), "", f"YFINANCE_IMPORT:{type(exc).__name__}:{exc}"

    attempts: list[tuple[str, Callable[[], pd.DataFrame]]] = [
        ("Ticker.history(1mo)", lambda: yf_module.Ticker(ticker).history(
            period="1mo", interval=INTERVAL, auto_adjust=True, actions=False, prepost=False
        )),
        ("Ticker.history(5d)", lambda: yf_module.Ticker(ticker).history(
            period="5d", interval=INTERVAL, auto_adjust=True, actions=False, prepost=False
        )),
        ("yf.download(1mo)", lambda: yf_module.download(
            ticker, period="1mo", interval=INTERVAL, auto_adjust=True,
            prepost=False, progress=False, threads=False
        )),
        ("yf.download(5d)", lambda: yf_module.download(
            ticker, period="5d", interval=INTERVAL, auto_adjust=True,
            prepost=False, progress=False, threads=False
        )),
    ]
    failures: list[str] = []
    for method, loader in attempts:
        try:
            raw = loader()
            normalized = normalize_intraday_index_bars(raw, ticker)
            if not normalized.empty:
                return normalized, method, ""
            failures.append(f"{method}:EMPTY_RESULT")
        except Exception as exc:
            failures.append(f"{method}:{type(exc).__name__}:{exc}")
    return pd.DataFrame(), "", " | ".join(failures[-4:]) or "NO_INTRADAY_DATA"


def _session_context(now: datetime) -> tuple[Any, bool, Any | None]:
    """Return expected last/current session, regular-session-open flag, and close."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = now.astimezone(NY)
    today = pd.Timestamp(current.date())
    try:
        import exchange_calendars as xcals
        calendar = xcals.get_calendar("XNYS")
        if calendar.is_session(today):
            open_at = pd.Timestamp(calendar.session_open(today)).to_pydatetime().astimezone(NY)
            close_at = pd.Timestamp(calendar.session_close(today)).to_pydatetime().astimezone(NY)
            if current < open_at:
                prior = calendar.date_to_session(today - pd.Timedelta(days=1), direction="previous")
                session = pd.Timestamp(prior)
                session_close = pd.Timestamp(calendar.session_close(session)).to_pydatetime().astimezone(NY)
                return session.date(), False, session_close
            return today.date(), open_at <= current < close_at, close_at
        prior = calendar.date_to_session(today, direction="previous")
        session = pd.Timestamp(prior)
        session_close = pd.Timestamp(calendar.session_close(session)).to_pydatetime().astimezone(NY)
        return session.date(), False, session_close
    except Exception as exc:
        raise OverviewIntradayDataError(f"XNYS_CALENDAR_ERROR:{type(exc).__name__}") from exc


def build_intraday_index_panel(
    bars: pd.DataFrame | None,
    *,
    label: str,
    symbol: str,
    now: datetime | None = None,
    fetch_error: str | None = None,
    max_live_bar_age_minutes: int = MAX_LIVE_BAR_AGE_MINUTES,
) -> dict[str, Any]:
    """Build current-session price/change/RVOL; fail closed for stale live data."""
    current = now or datetime.now(tz=NY)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_ny = current.astimezone(NY)
    try:
        target_date, market_open, session_close = _session_context(current_ny)
        if bars is None or bars.empty:
            raise OverviewIntradayDataError(fetch_error or "NO_INTRADAY_BARS")
        normalized = normalize_intraday_index_bars(bars, symbol)
        normalized = normalized.loc[normalized.index <= pd.Timestamp(current_ny)].copy()
        session_bars = normalized.loc[np.array([stamp.date() == target_date for stamp in normalized.index])].copy()
        if session_bars.empty:
            raise OverviewIntradayDataError(
                f"NO_BARS_FOR_EXPECTED_SESSION:{target_date.isoformat()}"
            )
        latest_ts = pd.Timestamp(session_bars.index[-1]).tz_convert(NY)
        age_minutes = max(0.0, (current_ny - latest_ts.to_pydatetime()).total_seconds() / 60.0)
        if market_open and age_minutes > float(max_live_bar_age_minutes):
            raise OverviewIntradayDataError(
                f"DATA_STALE:{age_minutes:.1f}m>{max_live_bar_age_minutes}m"
            )
        if not market_open and session_close is not None and current_ny >= session_close:
            # During/after close, ensure the feed reached reasonably close to the
            # official session close rather than presenting a partial old session.
            latest_time = latest_ts.to_pydatetime()
            if (session_close - latest_time).total_seconds() > 25 * 60:
                raise OverviewIntradayDataError("DATA_STALE:LAST_BAR_TOO_FAR_BEFORE_CLOSE")

        prior = normalized.loc[np.array([stamp.date() < target_date for stamp in normalized.index])].copy()
        if prior.empty:
            raise OverviewIntradayDataError("PREVIOUS_SESSION_CLOSE_UNAVAILABLE")
        prior_dates = sorted(set(stamp.date() for stamp in prior.index))
        previous_session = prior.loc[np.array([stamp.date() == prior_dates[-1] for stamp in prior.index])]
        previous_close = float(previous_session["Close"].iloc[-1])
        if not math.isfinite(previous_close) or previous_close <= 0:
            raise OverviewIntradayDataError("INVALID_PREVIOUS_SESSION_CLOSE")

        price = float(session_bars["Close"].iloc[-1])
        if not math.isfinite(price) or price <= 0:
            raise OverviewIntradayDataError("INVALID_LATEST_PRICE")
        change_points = price - previous_close
        change_pct = change_points / previous_close * 100.0

        # Legacy V50.53 calculation: compare each 5-minute volume with the
        # median volume at the same clock-time over previous sessions. If no
        # same-slot values exist, fall back to the median prior 5-minute bar.
        rvol = pd.Series(index=session_bars.index, dtype="float64")
        if not prior.empty and prior["Volume"].notna().any():
            slot_medians = prior.groupby(prior.index.strftime("%H:%M"))["Volume"].median()
            for stamp, row in session_bars.iterrows():
                volume = row.get("Volume")
                baseline = float(slot_medians.get(stamp.strftime("%H:%M"), np.nan))
                if pd.notna(volume) and pd.notna(baseline) and baseline > 0:
                    rvol.loc[stamp] = float(volume) / baseline
            if rvol.notna().sum() == 0:
                fallback = float(prior["Volume"].median())
                if math.isfinite(fallback) and fallback > 0:
                    rvol = session_bars["Volume"] / fallback
        rvol = rvol.replace([np.inf, -np.inf], np.nan).clip(lower=0)

        return {
            "available": True,
            "label": label,
            "symbol": symbol,
            "session_date": target_date.isoformat(),
            "date_label": pd.Timestamp(target_date).strftime("%b %d").replace(" 0", " "),
            "price": price,
            "previous_close": previous_close,
            "change_points": change_points,
            "change_pct": change_pct,
            "bars": session_bars,
            "rvol": rvol,
            "rvol_available": bool(rvol.notna().any()),
            "latest_bar_at": latest_ts.isoformat(),
            "age_minutes": age_minutes,
            "market_open": market_open,
            "method": None,
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "label": label,
            "symbol": symbol,
            "session_date": None,
            "date_label": None,
            "price": None,
            "previous_close": None,
            "change_points": None,
            "change_pct": None,
            "bars": pd.DataFrame(),
            "rvol": pd.Series(dtype="float64"),
            "rvol_available": False,
            "latest_bar_at": None,
            "age_minutes": None,
            "market_open": False,
            "method": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def build_intraday_index_figure(panel: dict[str, Any]) -> go.Figure:
    """Build the legacy compact dark candlestick card with RVOL strip."""
    if not panel.get("available"):
        raise OverviewIntradayDataError("Cannot build chart for unavailable panel")
    frame = panel["bars"].copy()
    rvol = pd.to_numeric(panel["rvol"], errors="coerce").reindex(frame.index)
    available_rvol = rvol.dropna()
    rvol_max = float(available_rvol.max()) if not available_rvol.empty else 0.0
    fig = go.Figure()
    if not available_rvol.empty:
        fig.add_trace(go.Bar(
            x=available_rvol.index,
            y=available_rvol.values,
            name="Relative Volume",
            marker_color="#1976d2",
            opacity=0.72,
            width=150000,
            yaxis="y2",
            hovertemplate="RVOL %{y:.2f}×<extra></extra>",
            showlegend=False,
        ))
    fig.add_trace(go.Candlestick(
        x=frame.index,
        open=frame["Open"], high=frame["High"], low=frame["Low"], close=frame["Close"],
        increasing_line_color="#18c75b", decreasing_line_color="#ff5364",
        increasing_fillcolor="#18c75b", decreasing_fillcolor="#ff5364",
        name=str(panel["label"]), showlegend=False,
        hovertemplate="%{x|%H:%M}<br>O %{open:.2f}<br>H %{high:.2f}<br>L %{low:.2f}<br>C %{close:.2f}<extra></extra>",
    ))
    fig.add_hline(y=float(panel["previous_close"]), line_color="#e53935", line_width=1, line_dash="dot", opacity=0.9)
    fig.update_layout(
        height=228,
        margin=dict(l=7, r=42, t=7, b=27),
        paper_bgcolor="#20242b", plot_bgcolor="#20242b",
        font=dict(color="#9aa3b2", family="Arial"),
        hovermode="x unified",
        xaxis=dict(
            type="date", showgrid=True, gridcolor="rgba(105,115,130,.18)",
            zeroline=False, showline=False, tickformat="%-I%p", dtick=60*60*1000,
            tickfont=dict(size=9, color="#9099a8"), rangeslider=dict(visible=False), fixedrange=True,
        ),
        yaxis=dict(
            side="right", domain=[0.18, 1.0], showgrid=True,
            gridcolor="rgba(105,115,130,.16)", zeroline=False,
            tickfont=dict(size=9, color="#8e97a7"), nticks=5, fixedrange=True,
        ),
        yaxis2=dict(
            side="left", domain=[0.0, 0.15], range=[0, max(2.2, rvol_max * 1.15, 1.0)],
            showgrid=False, zeroline=False, showticklabels=False, fixedrange=True,
        ),
        showlegend=False,
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0.005, y=0.01,
        text="RELATIVE<br>VOLUME" if panel.get("rvol_available") else "RELATIVE<br>VOLUME N/A",
        showarrow=False, xanchor="left", yanchor="bottom",
        font=dict(size=6, color="#707989"), align="left",
    )
    fig.add_annotation(
        xref="paper", yref="y", x=1.005, y=float(panel["price"]),
        text=f"{float(panel['price']):,.1f}", showarrow=False,
        xanchor="left", yanchor="middle", bgcolor="#f2c92e", bordercolor="#f2c92e",
        font=dict(size=10, color="#20242b"), borderpad=2,
    )
    return fig


def render_intraday_index_header_html(panel: dict[str, Any]) -> str:
    """Return the compact legacy card header showing date and live change."""
    if not panel.get("available"):
        return ""
    change = float(panel["change_points"])
    percent = float(panel["change_pct"])
    color = "#18c75b" if percent >= 0 else "#ff5364"
    sign = "+" if change >= 0 else ""
    label = html.escape(str(panel["label"]))
    date_label = html.escape(str(panel["date_label"]))
    return (
        "<div style='background:#20242b;border:1px solid #46505f;border-radius:8px;"
        "padding:9px 12px 0 12px;margin-bottom:-1px;'>"
        "<div style='display:flex;align-items:baseline;justify-content:space-between;gap:8px;'>"
        f"<span style='font-size:20px;font-weight:700;color:#a6afbf'>{label}</span>"
        f"<span style='font-size:13px;color:#8f98a8'>{date_label}</span>"
        f"<span style='font-size:14px;font-weight:700;color:{color};white-space:nowrap'>"
        f"{sign}{change:,.2f} ({percent:+.2f}%)</span></div></div>"
    )
