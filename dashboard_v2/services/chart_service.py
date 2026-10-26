"""Read-only legacy-style stock chart helpers for Portfolio Dashboard V2.

The chart consumes the same completed daily bars and canonical EOD analysis as the
Momentum Monitor. Current-session 5-minute bars are a display-only overlay: they never
enter the official pattern, pivot, stage, rebase or Pocket Pivot calculations.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
CHART_UP = "#18c75b"
CHART_DOWN = "#ff5364"
CHART_MA = {20: "#f5c542", 50: "#4fc3f7", 120: "#ff9800", 200: "#e0e0e0"}


def is_xnys_regular_session_open(now: datetime | None = None) -> bool:
    """Return whether `now` is inside an XNYS regular session; fail closed."""
    reference = now or datetime.now(tz=NY)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_ny = reference.astimezone(NY)
    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS")
        session = pd.Timestamp(now_ny.date())
        if not calendar.is_session(session):
            return False
        market_open = pd.Timestamp(calendar.session_open(session)).to_pydatetime().astimezone(NY)
        market_close = pd.Timestamp(calendar.session_close(session)).to_pydatetime().astimezone(NY)
        return market_open <= now_ny < market_close
    except Exception:
        # If the exchange calendar is unavailable, do not treat wall-clock weekday
        # hours as proof that a live market session is open.
        return False


def aggregate_current_session_bar(
    intraday_bars: pd.DataFrame | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Aggregate valid regular-session 5-minute observations into today's live bar.

    Only the current XNYS session is eligible. Naive timestamps, prior-session bars,
    holidays, premarket and postmarket observations are not inferred as live data.
    """
    reference = now or datetime.now(tz=NY)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reference_ny = reference.astimezone(NY)
    if not is_xnys_regular_session_open(reference_ny):
        return None
    if intraday_bars is None or not isinstance(intraday_bars, pd.DataFrame) or intraday_bars.empty:
        return None
    if "Date" not in intraday_bars.columns:
        return None
    missing = [column for column in REQUIRED_COLUMNS if column not in intraday_bars.columns]
    if missing:
        return None

    frame = intraday_bars.copy()
    dates = pd.to_datetime(frame["Date"], errors="coerce")
    try:
        if not isinstance(dates.dtype, pd.DatetimeTZDtype):
            # Do not guess a timezone for intraday bars.
            return None
        dates = dates.dt.tz_convert(NY)
    except Exception:
        return None
    if dates.isna().any():
        return None
    frame["_date_ny"] = dates
    frame = frame.loc[frame["_date_ny"].dt.date == reference_ny.date()].copy()
    local_times = frame["_date_ny"].dt.time
    frame = frame.loc[
        (local_times >= pd.Timestamp("09:30").time())
        & (local_times < pd.Timestamp("16:00").time())
        & (frame["_date_ny"] <= pd.Timestamp(reference_ny))
    ].sort_values("_date_ny")
    if frame.empty:
        return None
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        return None
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any() or (frame["Volume"] < 0).any():
        return None
    if (frame["High"] < frame[["Open", "Low", "Close"]].max(axis=1)).any():
        return None
    if (frame["Low"] > frame[["Open", "High", "Close"]].min(axis=1)).any():
        return None

    first = frame.iloc[0]
    last = frame.iloc[-1]
    return {
        "Open": float(first["Open"]),
        "High": float(frame["High"].max()),
        "Low": float(frame["Low"].min()),
        "Close": float(last["Close"]),
        "Volume": float(frame["Volume"].sum()),
        # Canonical daily session key is timezone-naive midnight; this is used only
        # by the visualization frame and never passed into EOD engines.
        "date": pd.Timestamp(reference_ny.date()),
        "time": pd.Timestamp(last["_date_ny"]).strftime("%H:%M ET"),
    }


def _daily_index(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=[*REQUIRED_COLUMNS], index=pd.DatetimeIndex([], name="Date"))
    data = frame.copy()
    if "Date" in data.columns:
        raw_dates = pd.to_datetime(data["Date"], errors="coerce")
    elif isinstance(data.index, pd.DatetimeIndex):
        raw_dates = pd.Series(pd.to_datetime(data.index, errors="coerce"), index=data.index)
    else:
        raise ValueError("Daily chart data requires a Date column or DatetimeIndex")
    normalized_dates: list[pd.Timestamp] = []
    for value in raw_dates:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError("Daily chart data contains an invalid date")
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(NY).tz_localize(None)
        normalized_dates.append(stamp.normalize())
    data["_chart_date"] = normalized_dates
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError("Daily chart data missing columns: " + ", ".join(missing))
    for column in REQUIRED_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["_chart_date", *REQUIRED_COLUMNS]).sort_values("_chart_date", kind="stable")
    if data["_chart_date"].duplicated().any():
        data = data.drop_duplicates("_chart_date", keep="last")
    data = data.set_index("_chart_date")
    data.index.name = "Date"
    return data.loc[:, list(REQUIRED_COLUMNS)]


def relative_strength_series(
    daily_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None,
) -> pd.Series:
    """Relative price performance vs benchmark, rebased to 100 at first common date."""
    daily = _daily_index(daily_bars)
    benchmark = _daily_index(benchmark_bars)
    if daily.empty or benchmark.empty:
        return pd.Series(dtype="float64", name="QQQ 대비 누적")
    common = daily.index.intersection(benchmark.index)
    if len(common) < 2:
        return pd.Series(dtype="float64", name="QQQ 대비 누적")
    symbol_close = daily.loc[common, "Close"]
    benchmark_close = benchmark.loc[common, "Close"]
    valid = (symbol_close > 0) & (benchmark_close > 0)
    ratio = symbol_close.loc[valid] / benchmark_close.loc[valid]
    if ratio.empty or not math.isfinite(float(ratio.iloc[0])) or float(ratio.iloc[0]) <= 0:
        return pd.Series(dtype="float64", name="QQQ 대비 누적")
    result = ratio / float(ratio.iloc[0]) * 100.0
    result.name = "QQQ 대비 누적"
    return result


def relative_strength_latest(
    daily_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None,
) -> float | None:
    # Match the chart's two-year calculation window so the status line and plotted
    # QQQ-relative line use the same 100-base reference.
    daily = _daily_index(daily_bars)
    if not daily.empty:
        daily = daily.loc[daily.index >= daily.index[-1] - pd.Timedelta(days=730)]
    series = relative_strength_series(daily, benchmark_bars)
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def build_chart_figure(
    ticker: str,
    daily_bars: pd.DataFrame,
    *,
    live_bar: Mapping[str, Any] | None = None,
    benchmark_bars: pd.DataFrame | None = None,
    levels: Mapping[str, Any] | None = None,
):
    """Create a legacy-style one-year price/volume/QQQ-relative/ATR chart."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    daily = _daily_index(daily_bars)
    if daily.empty:
        raise ValueError("No completed daily bars available for chart")
    # Keep two years to establish stable MA/RS context, then display the last year.
    calc_start = daily.index[-1] - pd.Timedelta(days=730)
    daily = daily.loc[daily.index >= calc_start].copy()
    if daily.empty:
        raise ValueError("No daily bars in the chart calculation window")

    full = daily.copy()
    if live_bar is not None:
        live_date = pd.Timestamp(live_bar.get("date"))
        if live_date.tzinfo is not None:
            live_date = live_date.tz_convert(NY).tz_localize(None)
        live_date = live_date.normalize()
        if pd.isna(live_date):
            raise ValueError("Live chart bar has an invalid session date")
        row = {column: pd.to_numeric(pd.Series([live_bar.get(column)]), errors="coerce").iloc[0]
               for column in REQUIRED_COLUMNS}
        if any(pd.isna(row[column]) for column in REQUIRED_COLUMNS):
            raise ValueError("Live chart bar has invalid OHLCV values")
        full = full.loc[full.index != live_date]
        full.loc[live_date, list(REQUIRED_COLUMNS)] = [row[column] for column in REQUIRED_COLUMNS]
        full = full.sort_index()

    close = full["Close"]
    moving_averages = {period: close.rolling(period, min_periods=period).mean() for period in CHART_MA}
    # Reference volume is based only on completed daily bars; the provisional bar
    # must not change its own reference average.
    vol_ma50_daily = daily["Volume"].rolling(50, min_periods=50).mean()
    vol_ma50 = vol_ma50_daily.reindex(full.index).ffill()
    previous_close = close.shift(1)
    true_range = pd.concat([
        full["High"] - full["Low"],
        (full["High"] - previous_close).abs(),
        (full["Low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr_pct = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / close * 100

    start = full.index[-1] - pd.Timedelta(days=366)
    view = full.loc[full.index >= start].copy()
    if view.empty:
        raise ValueError("No bars in the one-year display window")
    last_date = view.index[-1]
    rs = relative_strength_series(daily, benchmark_bars)
    rs_view = rs.loc[rs.index >= start] if not rs.empty else rs

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        row_heights=[0.58, 0.14, 0.14, 0.14], vertical_spacing=0.025,
    )
    fig.add_trace(go.Candlestick(
        x=view.index, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"],
        increasing_line_color=CHART_UP, increasing_fillcolor=CHART_UP,
        decreasing_line_color=CHART_DOWN, decreasing_fillcolor=CHART_DOWN,
        name=str(ticker).upper(), showlegend=False,
    ), row=1, col=1)

    for period, color in CHART_MA.items():
        series = moving_averages[period].loc[moving_averages[period].index >= start]
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values, mode="lines",
            line={"color": color, "width": 1.2}, name=f"MA{period}",
            hovertemplate=f"MA{period} %{{y:.2f}}<extra></extra>",
        ), row=1, col=1)
        valid = moving_averages[period].dropna()
        if not valid.empty:
            fig.add_annotation(
                x=last_date, y=float(valid.iloc[-1]), xanchor="left", xshift=6,
                text=f"<b>{period}</b>", showarrow=False,
                font={"color": color, "size": 11}, bgcolor="rgba(14,17,23,0.6)",
                row=1, col=1,
            )

    for key, line_color, dash, label in (
        ("breakout", "#ffb300", "solid", "Breakout"),
        ("candidate", "#9e9e9e", "dot", "Candidate"),
    ):
        value = (levels or {}).get(key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0:
            continue
        fig.add_hline(
            y=value, line={"color": line_color, "width": 1.6 if key == "breakout" else 1.2,
                           "dash": dash}, row=1, col=1,
            annotation_text=f"{label} {value:.2f}", annotation_position="top left",
            annotation_font_color=line_color,
        )

    volume_colors = [CHART_UP if close_value >= open_value else CHART_DOWN
                     for open_value, close_value in zip(view["Open"], view["Close"])]
    fig.add_trace(go.Bar(
        x=view.index, y=view["Volume"], marker_color=volume_colors, opacity=0.45,
        showlegend=False, hovertemplate="Vol %{y:,.0f}<extra></extra>",
    ), row=2, col=1)
    volume_view = vol_ma50.loc[vol_ma50.index >= start]
    fig.add_trace(go.Scatter(
        x=volume_view.index, y=volume_view.values, mode="lines",
        line={"color": "#e0e0e0", "width": 1.1}, name="Vol MA50",
        hovertemplate="Vol MA50 %{y:,.0f}<extra></extra>",
    ), row=2, col=1)
    volume_valid = vol_ma50.dropna()
    if not volume_valid.empty:
        fig.add_annotation(
            x=last_date, y=float(volume_valid.iloc[-1]), xanchor="left", xshift=6,
            text="<b>Vol 50</b>", showarrow=False,
            font={"color": "#e0e0e0", "size": 10}, row=2, col=1,
        )

    if not rs_view.empty:
        fig.add_trace(go.Scatter(
            x=rs_view.index, y=rs_view.values, mode="lines",
            line={"color": "#ab47bc", "width": 1.4}, name="QQQ 대비 누적",
            hovertemplate="QQQ 대비 %{y:.1f} (100 = QQQ 동일)<extra></extra>",
        ), row=3, col=1)
        fig.add_hline(y=100, line={"color": "#6b7280", "width": 1, "dash": "dot"}, row=3, col=1)
        fig.add_annotation(
            x=rs_view.index[-1], y=float(rs_view.iloc[-1]), xanchor="left", xshift=6,
            text=f"<b>QQQ대비 {rs_view.iloc[-1]:.0f}</b>", showarrow=False,
            font={"color": "#ab47bc", "size": 10}, row=3, col=1,
        )

    atr_view = atr_pct.loc[atr_pct.index >= start].dropna()
    if not atr_view.empty:
        fig.add_trace(go.Scatter(
            x=atr_view.index, y=atr_view.values, mode="lines",
            line={"color": "#26a69a", "width": 1.3}, name="ATR%",
            hovertemplate="ATR %{y:.2f}%<extra></extra>",
        ), row=4, col=1)
        fig.add_annotation(
            x=atr_view.index[-1], y=float(atr_view.iloc[-1]), xanchor="left", xshift=6,
            text=f"<b>ATR {atr_view.iloc[-1]:.1f}%</b>", showarrow=False,
            font={"color": "#26a69a", "size": 10}, row=4, col=1,
        )

    fig.update_layout(
        height=820, margin={"l": 8, "r": 8, "t": 28, "b": 8}, xaxis_rangeslider_visible=False,
        paper_bgcolor="#0e1117", plot_bgcolor="#0e1117", font={"color": "#cfd6e4"},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0,
                "font": {"color": "#cfd6e4", "size": 11}, "bgcolor": "rgba(0,0,0,0)"},
    )
    for row_number in (1, 2, 3, 4):
        fig.update_xaxes(showgrid=False, row=row_number, col=1)
        fig.update_yaxes(side="right", gridcolor="#1f2630", row=row_number, col=1)
    fig.update_yaxes(title_text="", row=3, col=1)
    return fig
