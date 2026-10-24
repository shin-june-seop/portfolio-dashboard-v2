from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from dashboard_v2.services.momentum_market_map_service import (
    build_market_map_figure,
    fetch_market_map_data,
    market_map_status_caption,
    normalize_market_map_bars,
    quote_from_bars,
)

NY = ZoneInfo("America/New_York")


def _intraday(rows):
    """rows: (timestamp, open, high, low, close, volume), timestamp in ET."""
    frame = pd.DataFrame(
        [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": v} for _, o, h, l, c, v in rows],
        index=pd.DatetimeIndex([pd.Timestamp(ts, tz=NY) for ts, *_ in rows]),
    )
    return frame


def test_regular_session_uses_live_price_vs_previous_completed_close():
    bars = _intraday([
        ("2026-10-21 15:55", 98, 101, 97, 100, 1200),
        ("2026-10-22 15:55", 100, 103, 99, 102, 1400),
        ("2026-10-23 10:00", 102, 104, 101, 103, 1500),
    ])
    result = quote_from_bars("NVDA", bars, now=datetime(2026, 10, 23, 10, 5, tzinfo=NY))
    assert result["quote_mode"] == "LIVE"
    assert result["price"] == pytest.approx(103)
    assert result["daily_pct"] == pytest.approx((103 / 102 - 1) * 100)
    assert result["eod_date"] == "2026-10-22"
    assert result["quote_time"] == "10:00 ET"


def test_after_close_uses_latest_completed_session_close():
    bars = _intraday([
        ("2026-10-22 15:55", 99, 101, 98, 100, 1000),
        ("2026-10-23 15:55", 100, 104, 99, 103, 1300),
    ])
    result = quote_from_bars("AAPL", bars, now=datetime(2026, 10, 23, 16, 10, tzinfo=NY))
    assert result["quote_mode"] == "EOD"
    assert result["price"] == pytest.approx(103)
    assert result["daily_pct"] == pytest.approx(3.0)
    assert result["eod_date"] == "2026-10-23"


def test_stale_live_bar_does_not_masquerade_as_live_quote():
    bars = _intraday([
        ("2026-10-20 15:55", 98, 101, 97, 100, 1000),
        ("2026-10-21 15:55", 100, 102, 99, 101, 1100),
        ("2026-10-22 15:55", 101, 103, 100, 102, 1200),
        ("2026-10-23 09:45", 102, 103, 101, 102.5, 300),
    ])
    result = quote_from_bars("META", bars, now=datetime(2026, 10, 23, 10, 15, tzinfo=NY))
    assert result["quote_mode"] == "EOD"
    assert result["eod_date"] == "2026-10-22"
    assert result["price"] == pytest.approx(102)


def test_daily_fallback_uses_completed_close_pair_and_excludes_partial_today_bar():
    intraday = pd.DataFrame()
    daily = pd.DataFrame(
        {"Close": [98.0, 100.0, 103.0]},
        index=pd.to_datetime(["2026-10-21", "2026-10-22", "2026-10-23"]),
    )
    result = quote_from_bars("MSFT", intraday, now=datetime(2026, 10, 23, 10, 10, tzinfo=NY), daily_fallback=daily)
    assert result["quote_mode"] == "EOD"
    assert result["price"] == pytest.approx(100.0)
    assert result["daily_pct"] == pytest.approx((100 / 98 - 1) * 100)
    assert result["eod_date"] == "2026-10-22"


def test_market_map_keeps_equal_tile_values_alphabetical_order_and_stage_labels():
    quotes = pd.DataFrame([
        {"ticker": "NVDA", "price": 125.0, "daily_pct": 2.1, "quote_mode": "LIVE", "quote_time": "10:00 ET", "eod_date": "2026-10-22", "sector": "Semiconductors"},
        {"ticker": "AAPL", "price": 230.0, "daily_pct": -0.5, "quote_mode": "LIVE", "quote_time": "10:00 ET", "eod_date": "2026-10-22", "sector": "Technology"},
        {"ticker": "META", "price": 710.0, "daily_pct": 0.3, "quote_mode": "EOD", "quote_time": "2026-10-22", "eod_date": "2026-10-22", "sector": "Communication Services"},
    ])
    fig = build_market_map_figure(
        quotes,
        stage_by_ticker={"AAPL": "BREAKOUT FAILURE", "META": "BASE + PP", "NVDA": "BASE"},
    )
    trace = fig.data[0]
    assert list(trace.values) == [1.0, 1.0, 1.0]
    assert [str(label).split("</b>")[0].replace("<b>", "") for label in trace.labels] == ["AAPL", "META", "NVDA"]
    assert "BREAKOUT FAILURE" in trace.labels[0]
    assert "BASE+PP" in trace.labels[1]
    assert trace.marker.colorscale[0][1] == "#8b1e1e"
    assert trace.marker.colorscale[-1][1] == "#16803c"


def test_status_caption_discloses_live_and_eod_basis():
    live = pd.DataFrame([{"quote_mode": "LIVE", "quote_time": "11:05 ET", "eod_date": "2026-10-22"}])
    eod = pd.DataFrame([{"quote_mode": "EOD", "quote_time": "2026-10-23", "eod_date": "2026-10-23"}])
    assert "LIVE" in market_map_status_caption(live)
    assert "현재가 / 직전 완료 정규장 종가" in market_map_status_caption(live)
    assert "EOD · 2026-10-23" in market_map_status_caption(eod)


def test_fetch_market_map_data_sorts_symbols_and_uses_watchlist_sector_metadata(monkeypatch):
    import dashboard_v2.services.momentum_market_map_service as service

    def fake_quote(ticker, **kwargs):
        return {
            "ticker": ticker, "price": 10.0, "daily_pct": 1.0,
            "quote_mode": "EOD", "quote_time": "2026-10-22", "eod_date": "2026-10-22", "error": "",
        }

    monkeypatch.setattr(service, "fetch_market_map_quote", fake_quote)
    frame = fetch_market_map_data(["NVDA", "aapl", "META", "AAPL"], sector_by_ticker={"AAPL": "Technology"})
    assert frame["ticker"].tolist() == ["AAPL", "META", "NVDA"]
    assert frame.loc[frame["ticker"] == "AAPL", "sector"].iloc[0] == "Technology"
