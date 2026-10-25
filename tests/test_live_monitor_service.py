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


def test_rvol_excludes_in_progress_latest_bar_but_price_uses_it():
    from dashboard_v2.services.live_monitor_service import live_session_metrics

    days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    volumes = {}
    for day in days[:-1]:
        for i in range(6):
            volumes[(day, i)] = 100
    for i in range(6):
        volumes[(days[-1], i)] = 200
    closes = {(days[-1], i): 100.0 + i for i in range(6)}
    frame = bars_for_sessions(days, volumes=volumes, closes=closes, times=("09:30", "09:35", "09:40", "09:45", "09:50", "09:55"))
    now = datetime(2026, 10, 22, 9, 58, tzinfo=NY)  # 09:55 candle is still forming.
    bars = normalize_intraday_bars(frame, now=now)
    metrics = live_session_metrics(bars, now=now)

    assert metrics["last_price"] == pytest.approx(105.0)  # latest observed bar close is still the displayed price.
    assert metrics["session_volume"] == pytest.approx(1200.0)  # full observed session volume is informational.
    assert metrics["live_rvol"] == pytest.approx(2.0)  # only 09:30..09:50 bars counted; prior sessions same time total 500.
    assert str(metrics["rvol_as_of"].strftime("%H:%M")) == "09:50"


def test_live_state_presentation_keeps_intraday_breakout_explicitly_provisional():
    from dashboard_v2.services.live_monitor_service import present_live_state

    assert present_live_state("LIVE PIVOT CROSS (PROVISIONAL)", official_stage="BASE", data_status="LIVE") == "BREAKOUT (LIVE)"
    assert present_live_state("ABOVE ACTIVE BREAKOUT PIVOT (LIVE)", official_stage="BREAKOUT", data_status="LIVE") == "BREAKOUT HOLD (LIVE)"
    assert present_live_state("BELOW ACTIVE BREAKOUT PIVOT (LIVE ONLY)", official_stage="BREAKOUT", data_status="LIVE") == "BELOW BREAKOUT PIVOT (LIVE)"
    assert present_live_state("LIVE PIVOT CROSS (PROVISIONAL)", official_stage="BREAKOUT", data_status="DATA_STALE") == "DATA STALE"


def test_live_entry_quality_uses_v2_live_components_and_never_an_initial_risk_input():
    from dashboard_v2.services.live_monitor_service import compute_live_entry_quality

    dates = pd.bdate_range("2026-06-01", periods=100)
    closes = pd.Series([50.0 + i * 0.5 for i in range(100)])
    daily = pd.DataFrame({"Date": dates, "Open": closes - 0.2, "High": closes + 0.4, "Low": closes - 0.5, "Close": closes, "Volume": 1_000_000})
    result = compute_live_entry_quality(
        live_available=True,
        official_stage="PIVOT APPROACH",
        raw_live_state="PIVOT APPROACH (LIVE)",
        live_price=float(closes.iloc[-1]) * 1.01,
        eod_row={"close": float(closes.iloc[-1]), "candidate_pivot": float(closes.iloc[-1]) * 1.02},
        monitor_row={"_pattern_score": 100.0, "_readiness_score": 80.0, "_rs_score": 85.0, "_momentum_score": 75.0},
        daily_bars=daily,
        live_rvol=1.5,
        candidate_distance_pct=-0.98,
        breakout_distance_pct=None,
        breakout_cycle_active=False,
    )
    assert result["label"] == "🟢 GOOD"
    assert result["score"] is not None and result["score"] >= 75
    assert set(result["components"]) == {"pattern", "setup_quality", "relative_strength", "live_momentum", "live_volume_quality", "ma_structure"}
    assert "risk" not in result["components"]
    assert "initial_risk" not in result["components"]


def test_live_entry_quality_is_disabled_for_stale_data_and_ineligible_base_state():
    from dashboard_v2.services.live_monitor_service import compute_live_entry_quality

    kwargs = dict(
        official_stage="BASE", raw_live_state="LIVE PRICE ONLY · NO VALID CANDIDATE PIVOT",
        live_price=101.0, eod_row={"close": 100.0},
        monitor_row={"_pattern_score": 100.0, "_readiness_score": 80.0, "_rs_score": 90.0, "_momentum_score": 80.0},
        daily_bars=None, live_rvol=1.3, candidate_distance_pct=None,
        breakout_distance_pct=None, breakout_cycle_active=False,
    )
    stale = compute_live_entry_quality(live_available=False, **kwargs)
    ineligible = compute_live_entry_quality(live_available=True, **kwargs)
    assert stale["label"] == "—" and stale["score"] is None
    assert ineligible["label"] == "—" and ineligible["score"] is None


def test_recent_official_state_history_is_newest_first_and_uses_only_supplied_eod_rows():
    from dashboard_v2.services.live_monitor_service import format_recent_official_state_history

    result = format_recent_official_state_history([
        {"as_of_date": "2026-10-21", "stage": "BREAKOUT HOLD"},
        {"as_of_date": "2026-10-23", "stage": "BREAKOUT"},
        {"as_of_date": "2026-10-22", "stage": "BREAKOUT HOLD"},
        {"as_of_date": "2026-10-20", "stage": "PIVOT APPROACH"},
        {"as_of_date": "2026-10-19", "stage": "PIVOT APPROACH"},
    ])
    assert result == "10/23 BREAKOUT → 10/22 BREAKOUT HOLD → 10/21 BREAKOUT HOLD → 10/20 PIVOT APPROACH → 10/19 PIVOT APPROACH"


def test_buy_history_uses_only_user_scoped_v2_journal_payloads_and_shows_open_return():
    from dashboard_v2.services.live_monitor_service import format_buy_history

    trades = [{
        "id": "trade-1", "ticker": "XOM", "initial_entry_date": "2026-09-15",
        "initial_entry_stage": "PIVOT APPROACH", "initial_entry_price": 166.14,
        "initial_shares": 10, "initial_stop_price": 150.0,
    }]
    result = format_buy_history("XOM", 182.75, trades, [], [])
    assert "09/15 PIVOT APPROACH $166.14" in result
    assert "[OPEN]" in result
    assert "+10.0%" in result
    assert format_buy_history("NVDA", 120.0, trades, [], []) == "—"
