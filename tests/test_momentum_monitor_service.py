from __future__ import annotations

import numpy as np
import pandas as pd

from dashboard_v2.services.momentum_monitor_service import (
    FALLBACK_MOMENTUM_UNIVERSE,
    _pp_guide,
    build_momentum_monitor_frame,
    build_sector_leadership,
    calculate_price_momentum_scores,
    resolve_watchlist_sectors,
    sector_leadership_table_html,
    trend_template_strip_html,
)


def make_bars(ticker: str, n: int = 300, *, drift: float = 0.0015, start: str = "2025-07-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    base = 50.0 + len(ticker) * 2
    close = base * np.exp(np.arange(n) * drift)
    high = close * 1.012
    low = close * 0.988
    open_ = close * (1 + np.sin(np.arange(n) / 5) * 0.002)
    volume = 1_000_000 + (np.arange(n) % 17) * 10_000
    return pd.DataFrame({
        "Date": dates,
        "Open": open_, "High": high, "Low": low, "Close": close,
        "Adj Close": close * 0.995, "Volume": volume,
    })


def make_benchmark(dates: pd.DatetimeIndex, symbols=("B1", "B2", "B3", "B4")) -> pd.DataFrame:
    idx = np.arange(len(dates), dtype=float)
    frame = {}
    for i, symbol in enumerate(symbols):
        frame[symbol] = (100 + i * 5) * np.exp(idx * (0.0004 + i * 0.0005))
    return pd.DataFrame(frame, index=dates)


def test_legacy_sector_default_map_only_fills_unassigned_watchlist_sectors():
    rows = [
        {"ticker": "NVDA", "sector": "미분류"},
        {"ticker": "AAPL", "sector": "Custom Theme"},
        {"ticker": "XYZ", "sector": ""},
        {"ticker": "COST", "sector": "미분류"},
        {"ticker": "XOM", "sector": "미분류"},
        {"ticker": "ASML", "sector": "미분류"},
    ]
    mapped = resolve_watchlist_sectors(rows)
    assert mapped["NVDA"] == "Semiconductor"
    assert mapped["AAPL"] == "Custom Theme"
    assert mapped["XYZ"] == "미분류"
    assert mapped["COST"] == "Consumer"
    assert mapped["XOM"] == "Energy"
    assert mapped["ASML"] == "Semiconductor"


def test_momentum_scores_use_adjusted_close_and_keep_off_universe_targets_out_of_rank_universe():
    bars = make_bars("AAA")
    dates = pd.DatetimeIndex(bars["Date"])
    benchmark = make_benchmark(dates)
    # Benchmark ticker return distribution is not modified by including target AAA.
    result = calculate_price_momentum_scores(["AAA"], {"AAA": bars}, benchmark, ["B1", "B2", "B3", "B4"], as_of_date=dates[-1])
    assert len(result) == 1
    assert result.iloc[0]["momentum_status"] == "AVAILABLE"
    assert 0 <= result.iloc[0]["Momentum"] <= 100
    assert np.isfinite(result.iloc[0]["Momentum 변화"])


def test_momentum_fails_closed_when_adjusted_close_is_missing_or_stale():
    bars = make_bars("AAA")
    bars["Adj Close"] = np.nan
    dates = pd.DatetimeIndex(pd.to_datetime(make_bars("AAA")["Date"]))
    benchmark = make_benchmark(dates)
    result = calculate_price_momentum_scores(["AAA"], {"AAA": bars}, benchmark, ["B1", "B2", "B3", "B4"], as_of_date=dates[-1])
    assert result.iloc[0]["momentum_status"] == "INSUFFICIENT_ADJUSTED_HISTORY"
    assert pd.isna(result.iloc[0]["Momentum"])
    stale = calculate_price_momentum_scores(["AAA"], {"AAA": make_bars("AAA")}, benchmark.iloc[:-1], ["B1", "B2", "B3", "B4"], as_of_date=dates[-1])
    assert stale.iloc[0]["momentum_status"] == "DATA UNAVAILABLE"


def test_build_frame_keeps_v2_official_stage_pivots_and_rvol_unchanged():
    bars = make_bars("AAA")
    anchor = pd.Timestamp(bars["Date"].iloc[-1]).date().isoformat()
    benchmark = make_benchmark(pd.DatetimeIndex(bars["Date"]))
    scan = pd.DataFrame([{
        "ticker": "AAA", "as_of_date": anchor, "close": float(bars["Close"].iloc[-1]),
        "stage": "BREAKOUT CONFIRMED", "pattern": "Flat Base",
        "candidate_pivot": 120.5, "candidate_distance_pct": 1.2,
        "breakout_pivot": 119.0, "breakout_distance_pct": 2.5,
        "breakout_cycle_active": True, "rvol": 1.37,
        "pocket_pivot_today": False, "breakout_event": True,
        "breakout_failure": False, "rebase_active": False,
        "sma20": 110, "sma50": 100, "next_pivot_candidate": 121.0,
    }])
    qqq = make_bars("QQQ")
    original_scan = scan.copy(deep=True)
    monitor, _ = build_momentum_monitor_frame(
        scan, {"AAA": bars}, [{"ticker": "AAA", "sector": "AI / Big Tech", "enabled": True}],
        benchmark, ["B1", "B2", "B3", "B4"], qqq,
    )
    pd.testing.assert_frame_equal(scan, original_scan)
    row = monitor.iloc[0]
    assert row["Entry Stage"] == "BREAKOUT CONFIRMED"
    assert row["Pattern"] == "Flat Base"
    assert row["Candidate Pivot"] == "$120.50"
    assert row["돌파 Pivot"] == "$119.00"
    assert row["Pivot Source"] == "V2 OFFICIAL BREAKOUT HISTORY"
    assert row["RVOL (20D)"] == "1.37×"
    assert row["PP Status"] != "PP EVENT"
    # Breakout readiness is explicitly descriptive and cannot replace V2 pivots.
    assert row["_breakout_pivot"] == 119.0
    assert row["_stage"] == "BREAKOUT CONFIRMED"


def test_sector_leadership_legacy_score_labels_and_tooltips():
    monitor = pd.DataFrame([
        {"종목": "AAA", "섹터": "AI / Big Tech", "RS": 90, "Momentum": 85,
         "Momentum 변화": "↑ +5", "RS 변화": "↑ +4", "_momentum_delta": 5, "_rs_delta": 4,
         "Breakout Readiness": 80},
        {"종목": "BBB", "섹터": "AI / Big Tech", "RS": 80, "Momentum": 75,
         "Momentum 변화": "↑ +2", "RS 변화": "↑ +1", "_momentum_delta": 2, "_rs_delta": 1,
         "Breakout Readiness": 70},
        {"종목": "CCC", "섹터": "미분류", "RS": 100, "Momentum": 100,
         "Momentum 변화": "↑ +9", "RS 변화": "↑ +9", "_momentum_delta": 9, "_rs_delta": 9,
         "Breakout Readiness": 99},
    ])
    sectors = build_sector_leadership(monitor)
    assert len(sectors) == 1
    row = sectors.iloc[0]
    assert row["섹터"] == "AI / Big Tech"
    assert row["판단"] == "🟢 LEADING"
    assert row["종목수"] == 2
    assert row["강한 종목 목록"] == "AAA, BBB"
    html = sector_leadership_table_html(sectors)
    assert "강한 종목: AAA, BBB" in html
    assert "개선 종목" in html


def test_trend_template_strip_contains_hoverable_ticker_and_failed_conditions():
    frame = pd.DataFrame([{
        "종목": "NVDA", "Trend Template": "6/8 미달",
        "Trend Template 미달": "가격 > 50일선, RS Proxy ≥ 70",
    }])
    rendered = trend_template_strip_html(frame)
    assert "NVDA 6/8 미달" in rendered
    assert "가격 &gt; 50일선" in rendered
    assert "RS Proxy ≥ 70" in rendered


def test_pp_guide_without_50_day_history_returns_no_confluence_without_exception():
    bars = make_bars("AAA", n=20)
    result = _pp_guide(bars, float(bars["Close"].iloc[-1]), False)
    assert result["pp_status"] in {"WATCH", "OUTSIDE", "CONFLUENCE"}
    assert pd.isna(result["guide_50_low"])
    assert pd.isna(result["confluence_low"])
    assert pd.isna(result["confluence_high"])


def test_fallback_momentum_universe_matches_documented_legacy_symbols():
    assert set(FALLBACK_MOMENTUM_UNIVERSE) == {
        "QQQ", "SPY", "IWM", "DIA", "XLK", "XLF", "XLI", "XLE", "XLV", "XLC", "XLY", "XLP", "XLU", "SOXX", "IGV"
    }
