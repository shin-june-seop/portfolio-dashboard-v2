from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from dashboard_v2.services.live_monitor_service import (
    LiveMarketDataError,
    YFinanceIntradayProvider,
    assess_live_data_status,
    classify_live_state,
    crossed_pivot_this_session,
    live_session_metrics,
    normalize_intraday_bars,
)

NY = ZoneInfo("America/New_York")


def bars_for_sessions(sessions, volumes=None, closes=None, times=("09:30", "09:35", "09:40", "09:45")):
    volumes = volumes or {}
    closes = closes or {}
    stamps, rows = [], []
    for day in sessions:
        day_s = str(day)
        for i, time_s in enumerate(times):
            stamps.append(pd.Timestamp(f"{day_s} {time_s}", tz=NY))
            close = closes.get((day_s, i), 100.0 + i)
            rows.append({
                "Open": close - 0.25,
                "High": close + 0.5,
                "Low": close - 0.5,
                "Close": close,
                "Volume": volumes.get((day_s, i), 100),
            })
    return pd.DataFrame(rows, index=pd.DatetimeIndex(stamps))


class FakeTicker:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return self.frame.copy()


def test_provider_uses_5m_regular_session_data_and_auto_adjust_false():
    frame = bars_for_sessions(["2026-10-22", "2026-10-23"])
    # Premarket and after-hours must not enter the live session statistics.
    extra = pd.DataFrame(
        [{"Open": 1, "High": 2, "Low": 1, "Close": 2, "Volume": 999}],
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-23 08:00", tz=NY)]),
    )
    frame = pd.concat([frame, extra]).sort_index()
    ticker = FakeTicker(frame)
    provider = YFinanceIntradayProvider(ticker_factory=lambda _: ticker)
    normalized = provider.intraday_bars("nvda", now=datetime(2026, 10, 23, 10, 0, tzinfo=NY))

    assert ticker.calls[0]["period"] == "10d"
    assert ticker.calls[0]["interval"] == "5m"
    assert ticker.calls[0]["auto_adjust"] is False
    assert ticker.calls[0]["prepost"] is False
    assert normalized["Date"].dt.tz is not None
    assert all(stamp.time() >= pd.Timestamp("09:30").time() for stamp in normalized["Date"])
    assert not any(stamp.time() < pd.Timestamp("09:30").time() for stamp in normalized["Date"])


def test_normalizer_rejects_naive_timestamps_instead_of_guessing_timezone():
    frame = bars_for_sessions(["2026-10-23"])
    frame.index = frame.index.tz_localize(None)
    with pytest.raises(LiveMarketDataError, match="timezone-naive"):
        normalize_intraday_bars(frame, now=datetime(2026, 10, 23, 10, 0, tzinfo=NY))


def test_status_is_live_when_current_session_bar_is_fresh():
    frame = bars_for_sessions(["2026-10-23"], times=("09:30", "09:35", "09:40", "09:45", "09:50", "09:55"))
    now = datetime(2026, 10, 23, 10, 0, tzinfo=NY)
    bars = normalize_intraday_bars(frame, now=now)
    status = assess_live_data_status(bars, now=now)
    assert status.code == "LIVE"
    assert status.signal_available is True
    assert status.market_open is True
    assert status.age_minutes == 5


def test_status_fails_closed_when_open_session_data_is_stale():
    frame = bars_for_sessions(["2026-10-23"], times=("09:30", "09:35"))
    now = datetime(2026, 10, 23, 10, 0, tzinfo=NY)
    bars = normalize_intraday_bars(frame, now=now)
    status = assess_live_data_status(bars, now=now)
    assert status.code == "DATA_STALE"
    assert status.signal_available is False
    assert status.reason == "LATEST_BAR_TOO_OLD"


def test_status_marks_missing_current_session_bars_stale_during_open_session():
    frame = bars_for_sessions(["2026-10-22"])
    now = datetime(2026, 10, 23, 10, 0, tzinfo=NY)
    bars = normalize_intraday_bars(frame, now=now)
    status = assess_live_data_status(bars, now=now)
    assert status.code == "DATA_STALE"
    assert status.reason == "NO_BAR_FOR_CURRENT_SESSION"


def test_status_marks_market_closed_without_enabling_live_signals():
    frame = bars_for_sessions(["2026-10-22"])
    now = datetime(2026, 10, 24, 11, 0, tzinfo=NY)  # Saturday
    bars = normalize_intraday_bars(frame, now=now)
    status = assess_live_data_status(bars, now=now)
    assert status.code == "MARKET_CLOSED"
    assert status.signal_available is False


def test_same_time_live_volume_ratio_is_separate_metric():
    days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    volumes = {}
    for day in days[:-1]:
        for i in range(4):
            volumes[(day, i)] = 100
    for i in range(4):
        volumes[(days[-1], i)] = 200
    frame = bars_for_sessions(days, volumes=volumes)
    now = datetime(2026, 10, 22, 10, 0, tzinfo=NY)
    bars = normalize_intraday_bars(frame, now=now)
    metrics = live_session_metrics(bars)
    assert metrics["session_volume"] == 800
    assert metrics["reference_sessions"] == 3
    assert metrics["live_rvol"] == pytest.approx(2.0)


def test_live_volume_ratio_unavailable_when_insufficient_reference_sessions():
    frame = bars_for_sessions(["2026-10-22", "2026-10-23"])
    metrics = live_session_metrics(normalize_intraday_bars(frame, now=datetime(2026, 10, 23, 10, 0, tzinfo=NY)))
    assert metrics["live_rvol"] is None


def test_candidate_cross_requires_same_session_cross_not_gap_above_pivot():
    frame = bars_for_sessions(
        ["2026-10-22", "2026-10-23"],
        closes={
            ("2026-10-22", 3): 99,
            ("2026-10-23", 0): 104,
            ("2026-10-23", 1): 105,
            ("2026-10-23", 2): 106,
            ("2026-10-23", 3): 107,
        },
    )
    now = datetime(2026, 10, 23, 10, 0, tzinfo=NY)
    bars = normalize_intraday_bars(frame, now=now)
    assert not crossed_pivot_this_session(bars, 103, now=now)  # opened/gapped above; no intraday cross

    cross_frame = bars_for_sessions(
        ["2026-10-23"],
        closes={("2026-10-23", 0): 101, ("2026-10-23", 1): 102, ("2026-10-23", 2): 104, ("2026-10-23", 3): 105},
    )
    cross_bars = normalize_intraday_bars(cross_frame, now=now)
    assert crossed_pivot_this_session(cross_bars, 103, now=now)


def test_live_state_is_not_an_official_breakout_or_failure_stage():
    assert classify_live_state(
        live_price=105,
        candidate_pivot=104,
        breakout_pivot=100,
        breakout_cycle_active=True,
        candidate_crossed_today=True,
    ) == "LIVE PIVOT CROSS (PROVISIONAL)"
    state = classify_live_state(
        live_price=98,
        candidate_pivot=104,
        breakout_pivot=100,
        breakout_cycle_active=True,
        candidate_crossed_today=False,
    )
    assert state == "BELOW ACTIVE BREAKOUT PIVOT (LIVE ONLY)"
    assert "FAILURE" not in state
    assert state != "BREAKOUT"


def test_invalid_ohlcv_range_is_rejected():
    frame = bars_for_sessions(["2026-10-23"])
    frame.iloc[0, frame.columns.get_loc("High")] = 1
    with pytest.raises(LiveMarketDataError, match="range is inconsistent"):
        normalize_intraday_bars(frame, now=datetime(2026, 10, 23, 10, 0, tzinfo=NY))


def test_provider_rejects_non_xnys_instruments_before_fetching():
    ticker = FakeTicker(bars_for_sessions(["2026-10-23"]))
    provider = YFinanceIntradayProvider(ticker_factory=lambda _: ticker)
    with pytest.raises(LiveMarketDataError, match="XNYS-session symbols only"):
        provider.intraday_bars("ES=F", now=datetime(2026, 10, 23, 10, 0, tzinfo=NY))
    assert ticker.calls == []
