from __future__ import annotations

import numpy as np
import pandas as pd

from dashboard_v2.services.quality_recovery_service import (
    analyze_legacy_quality_recovery,
    canonical_legacy_rs,
    fetch_yfinance_fundamentals,
    fundamentals_from_income_statement,
    ma_recovery_event,
    quality_recovery_table,
)


def income_stmt(revenue=(300, 250, 200, 150), net_income=(60, 40, 20, 10)):
    return pd.DataFrame(
        [revenue, net_income],
        index=["Total Revenue", "Net Income"],
        columns=pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]),
    )


def test_legacy_fundamentals_use_annual_revenue_and_net_income_growth():
    result = fundamentals_from_income_statement(income_stmt())
    assert result["quality_gate"] is True
    assert result["fundamental_damage"] is False
    assert np.isclose(result["revenue_growth"], 20.0)
    assert np.isclose(result["earnings_growth"], 50.0)
    assert result["fundamental_data_status"] == "AVAILABLE"
    assert result["fundamental_direction"] == "둔화"


def test_negative_growth_fails_gate_and_marks_damage():
    result = fundamentals_from_income_statement(
        income_stmt(revenue=(240, 250, 200, 150), net_income=(20, 40, 20, 10))
    )
    assert result["quality_gate"] is False
    assert result["fundamental_damage"] is True
    assert result["fundamental_data_status"] == "AVAILABLE"


def test_missing_fundamental_data_cannot_pass_gate():
    result = fundamentals_from_income_statement(None)
    assert result["quality_gate"] is False
    assert result["fundamental_data_status"] == "UNAVAILABLE"
    assert result["fundamental_error"] == "YFINANCE_INCOME_STATEMENT_EMPTY"


def test_fetch_yfinance_fundamentals_is_injectable_and_uses_statement():
    class FakeTicker:
        income_stmt = income_stmt()

    result = fetch_yfinance_fundamentals("nvda", ticker_factory=lambda ticker: FakeTicker())
    assert result["quality_gate"] is True
    assert result["fundamental_source"] == "YAHOO_FINANCE_ANNUAL_INCOME_STATEMENT"


def test_canonical_rs_aligns_dates_and_returns_legacy_0_to_100_rating():
    dates = pd.date_range("2024-01-01", periods=270, freq="B")
    stock = pd.DataFrame({"Date": dates, "Close": np.linspace(100, 180, len(dates))})
    qqq = pd.DataFrame({"Date": dates, "Close": np.linspace(100, 140, len(dates))})
    result = canonical_legacy_rs(stock, qqq)
    assert 0 <= result["rs"] <= 100
    assert np.isfinite(result["rs_delta"])


def test_canonical_rs_requires_full_253_aligned_observations():
    dates = pd.date_range("2025-01-01", periods=252, freq="B")
    stock = pd.DataFrame({"Date": dates, "Close": np.linspace(100, 120, len(dates))})
    result = canonical_legacy_rs(stock, stock)
    assert np.isnan(result["rs"])
    assert np.isnan(result["rs_delta"])


def test_ma_event_marks_two_consecutive_closes_below_average_as_breakdown():
    close = pd.Series([105.0] * 18 + [95.0, 94.0])
    low = close - 1.0
    ma = pd.Series([100.0] * len(close))
    event = ma_recovery_event(close, low, ma, "120MA")
    assert event["state"] == "120MA 무너짐"
    assert event["distance"] < 0


def test_quality_recovery_uses_technical_bars_and_keeps_fundamental_gate_separate():
    dates = pd.date_range("2025-01-01", periods=260, freq="B")
    close = np.linspace(80, 120, len(dates))
    bars = pd.DataFrame({"Date": dates, "Close": close, "Low": close - 1.0})
    adjusted = pd.DataFrame({"Date": dates, "Close": close})
    benchmark = pd.DataFrame({"Date": dates, "Close": np.linspace(80, 110, len(dates))})
    fundamentals = fundamentals_from_income_statement(income_stmt())
    result = analyze_legacy_quality_recovery(
        "NVDA",
        bars,
        fundamentals=fundamentals,
        adjusted_close=adjusted,
        benchmark_adjusted_close=benchmark,
        benchmark_bars=benchmark,
    )
    assert result["ticker"] == "NVDA"
    assert result["quality_gate"] is True
    assert result["ma120"] > 0 and result["ma200"] > 0
    assert result["as_of_date"] == dates[-1].date().isoformat()


def test_quality_table_matches_legacy_columns_and_sorts_by_recovery_priority():
    rows = [
        {"ticker": "AAA", "quality_gate": True, "eps_growth": 10, "sales_growth": 15,
         "fundamental_direction": "유지", "correction": -20,
         "e120": {"distance": -10, "state": "120MA 정상"},
         "e200": {"distance": -15, "state": "200MA 무너짐"}, "rs": 40, "rs_delta": -5,
         "status": "200MA 무너짐"},
        {"ticker": "BBB", "quality_gate": True, "eps_growth": 20, "sales_growth": 25,
         "fundamental_direction": "가속", "correction": -10,
         "e120": {"distance": 1, "state": "120MA 지지 반등"},
         "e200": {"distance": 2, "state": "200MA 정상"}, "rs": 80, "rs_delta": 2,
         "status": "120MA 지지 반등"},
    ]
    table = quality_recovery_table(rows)
    assert list(table.columns) == [
        "종목", "Quality", "EPS Growth", "Sales Growth", "Fundamental", "Correction",
        "120MA", "200MA", "RS", "RS 변화", "현재 상태",
    ]
    assert table.iloc[0]["종목"] == "AAA"
