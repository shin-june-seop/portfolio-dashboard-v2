from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from dashboard_v2.services.overview_intraday_service import (
    build_intraday_index_figure,
    build_intraday_index_panel,
    normalize_intraday_index_bars,
    render_intraday_index_header_html,
)

NY = ZoneInfo("America/New_York")


def _make_intraday_frame():
    rows = []
    # Four prior sessions at identical 5-minute slots establish slot medians.
    prior_days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    for day_no, day in enumerate(prior_days, start=1):
        for minute_no, clock in enumerate(("09:30", "09:35", "09:40")):
            stamp = pd.Timestamp(f"{day} {clock}", tz=NY)
            close = 700.0 + day_no + minute_no * 0.2
            rows.append({
                "Date": stamp, "Open": close - 0.1, "High": close + 0.2,
                "Low": close - 0.2, "Close": close,
                "Volume": float(day_no * (minute_no + 1) * 5),
            })
        stamp = pd.Timestamp(f"{day} 15:55", tz=NY)
        close = 710.0 + day_no
        rows.append({
            "Date": stamp, "Open": close - 0.2, "High": close + 0.1,
            "Low": close - 0.4, "Close": close, "Volume": 1000.0 + day_no,
        })
    for minute_no, clock in enumerate(("09:30", "09:35", "09:40")):
        stamp = pd.Timestamp(f"2026-10-23 {clock}", tz=NY)
        close = 714.0 - minute_no * 1.0
        rows.append({
            "Date": stamp, "Open": close + 0.2, "High": close + 0.4,
            "Low": close - 0.3, "Close": close,
            "Volume": float((minute_no + 1) * 50),
        })
    frame = pd.DataFrame(rows).set_index("Date").sort_index()
    return frame


def test_normalize_legacy_intraday_bars_keeps_timezone_and_regular_session_only():
    raw = _make_intraday_frame()
    # An extended-hours bar is discarded rather than mixed into the mini-chart.
    extra = pd.DataFrame(
        [{"Open": 710, "High": 711, "Low": 709, "Close": 710.5, "Volume": 999}],
        index=[pd.Timestamp("2026-10-23 08:00", tz=NY)],
    )
    normalized = normalize_intraday_index_bars(pd.concat([raw, extra]), "^GSPC")
    assert str(normalized.index.tz) == "America/New_York"
    assert all((normalized.index.time >= pd.Timestamp("09:30").time()))
    assert all((normalized.index.time < pd.Timestamp("16:00").time()))


def test_intraday_panel_computes_live_change_against_previous_session_close_and_slot_rvol():
    panel = build_intraday_index_panel(
        _make_intraday_frame(),
        label="S&P 500",
        symbol="^GSPC",
        now=datetime(2026, 10, 23, 9, 42, tzinfo=NY),
    )
    assert panel["available"] is True
    assert panel["session_date"] == "2026-10-23"
    assert len(panel["bars"]) == 3
    assert panel["previous_close"] == 714.0  # last 15:55 close on Oct 22
    assert panel["price"] == 712.0
    assert panel["change_points"] == -2.0
    assert panel["change_pct"] == pytest.approx(-2.0 / 714.0 * 100.0)
    assert panel["rvol_available"] is True
    assert panel["rvol"].iloc[0] == pytest.approx(50.0 / 12.5)
    assert panel["age_minutes"] == 2.0


def test_intraday_panel_fails_closed_when_live_bar_is_stale_or_today_is_missing():
    stale = build_intraday_index_panel(
        _make_intraday_frame(),
        label="S&P 500",
        symbol="^GSPC",
        now=datetime(2026, 10, 23, 10, 0, tzinfo=NY),
    )
    assert stale["available"] is False
    assert "DATA_STALE" in stale["error"]

    old = _make_intraday_frame().loc[lambda frame: frame.index.date < pd.Timestamp("2026-10-23").date()]
    missing_today = build_intraday_index_panel(
        old,
        label="NASDAQ",
        symbol="^IXIC",
        now=datetime(2026, 10, 23, 10, 0, tzinfo=NY),
    )
    assert missing_today["available"] is False
    assert "NO_BARS_FOR_EXPECTED_SESSION" in missing_today["error"]


def test_intraday_panel_displays_latest_session_after_close():
    raw = _make_intraday_frame()
    stamp = pd.Timestamp("2026-10-23 15:55", tz=NY)
    raw.loc[stamp] = {"Open": 711.0, "High": 712.0, "Low": 710.5, "Close": 711.5, "Volume": 1500.0}
    raw = raw.sort_index()
    panel = build_intraday_index_panel(
        raw,
        label="DOW",
        symbol="^DJI",
        now=datetime(2026, 10, 23, 16, 10, tzinfo=NY),
    )
    assert panel["available"] is True
    assert panel["session_date"] == "2026-10-23"
    assert panel["date_label"] == "Oct 23"


def test_legacy_style_header_and_candlestick_figure_are_rendered():
    panel = build_intraday_index_panel(
        _make_intraday_frame(),
        label="NASDAQ",
        symbol="^IXIC",
        now=datetime(2026, 10, 23, 9, 42, tzinfo=NY),
    )
    assert panel["available"] is True
    header = render_intraday_index_header_html(panel)
    assert "NASDAQ" in header and "Oct 23" in header
    assert "#20242b" in header and "#ff5364" in header
    figure = build_intraday_index_figure(panel)
    assert figure.layout.paper_bgcolor == "#20242b"
    assert figure.layout.plot_bgcolor == "#20242b"
    assert figure.layout.yaxis2.domain == (0.0, 0.15)
    names = [trace.type for trace in figure.data]
    assert "candlestick" in names and "bar" in names
    assert any("RELATIVE" in str(ann.text) for ann in figure.layout.annotations)
