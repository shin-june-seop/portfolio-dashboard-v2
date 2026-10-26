from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dashboard_v2.services.chart_service import (
    aggregate_current_session_bar,
    build_chart_figure,
    is_xnys_regular_session_open,
    relative_strength_latest,
)

NY = ZoneInfo("America/New_York")


def _daily_frame(n=560, *, ticker_slope=0.12, offset=100.0):
    dates = pd.bdate_range(end="2026-10-23", periods=n)
    x = np.arange(n, dtype=float)
    close = offset + x * ticker_slope + np.sin(x / 8.0) * 2.0
    open_ = close - np.cos(x / 5.0) * 0.8
    high = np.maximum(open_, close) + 1.2
    low = np.minimum(open_, close) - 1.1
    volume = 1_000_000 + (x.astype(int) % 17) * 20_000
    return pd.DataFrame({
        "Date": dates,
        "Open": open_, "High": high, "Low": low,
        "Close": close, "Volume": volume,
    })


def _intraday_fixture():
    idx = pd.DatetimeIndex([
        pd.Timestamp("2026-10-26 08:30", tz=NY),
        pd.Timestamp("2026-10-26 15:45", tz=NY),
        pd.Timestamp("2026-10-26 15:50", tz=NY),
        pd.Timestamp("2026-10-26 15:55", tz=NY),
        pd.Timestamp("2026-10-23 15:55", tz=NY),
    ])
    return pd.DataFrame({
        "Date": idx,
        "Open": [95, 100, 102, 101, 98],
        "High": [97, 103, 104, 104, 99],
        "Low": [94, 99, 101, 100, 97],
        "Close": [96, 102, 101, 103, 98],
        "Volume": [777, 10, 20, 30, 99],
    })


def test_live_bar_aggregates_only_current_open_regular_session():
    now = datetime(2026, 10, 26, 15, 58, tzinfo=NY)
    assert is_xnys_regular_session_open(now)
    bar = aggregate_current_session_bar(_intraday_fixture(), now=now)
    assert bar is not None
    assert bar["Open"] == 100
    assert bar["High"] == 104
    assert bar["Low"] == 99
    assert bar["Close"] == 103
    assert bar["Volume"] == 60  # previous session's 99 is excluded
    assert str(bar["date"].date()) == "2026-10-26"
    assert bar["time"] == "15:55 ET"


def test_live_bar_fails_closed_when_market_is_closed_or_session_data_missing():
    after_close = datetime(2026, 10, 26, 16, 5, tzinfo=NY)
    before_open = datetime(2026, 10, 26, 9, 0, tzinfo=NY)
    holiday = datetime(2026, 11, 26, 12, 0, tzinfo=NY)
    assert aggregate_current_session_bar(_intraday_fixture(), now=after_close) is None
    assert aggregate_current_session_bar(_intraday_fixture(), now=before_open) is None
    assert aggregate_current_session_bar(_intraday_fixture(), now=holiday) is None
    assert aggregate_current_session_bar(pd.DataFrame(), now=datetime(2026, 10, 26, 15, 58, tzinfo=NY)) is None


def test_chart_figure_contains_four_legacy_style_panels_and_pivot_levels():
    daily = _daily_frame()
    qqq = _daily_frame(ticker_slope=0.10, offset=250.0)
    live = {
        "date": pd.Timestamp("2026-10-26"),
        "Open": 166, "High": 169, "Low": 164, "Close": 168,
        "Volume": 3_000_000, "time": "15:55 ET",
    }
    fig = build_chart_figure(
        "AAPL", daily, live_bar=live, benchmark_bars=qqq,
        levels={"breakout": 167.5, "candidate": 166.2},
    )
    names = {str(trace.name) for trace in fig.data if trace.name is not None}
    assert "AAPL" in names
    assert {"MA20", "MA50", "MA120", "MA200", "Vol MA50", "QQQ 대비 누적", "ATR%"}.issubset(names)
    assert len(fig.layout.shapes) >= 3  # breakout, candidate, QQQ=100
    assert fig.layout.height == 820
    assert fig.layout.xaxis.matches == "x4"  # all four panels share the same trading-date axis
    assert relative_strength_latest(daily, qqq) is not None


def test_chart_without_benchmark_still_renders_price_volume_moving_average_and_atr():
    daily = _daily_frame()
    fig = build_chart_figure("TEST", daily, benchmark_bars=None, levels={})
    names = {str(trace.name) for trace in fig.data if trace.name is not None}
    assert {"MA20", "MA50", "MA120", "MA200", "Vol MA50", "ATR%"}.issubset(names)
    assert "QQQ 대비 누적" not in names
    assert relative_strength_latest(daily, None) is None
