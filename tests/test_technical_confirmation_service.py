from __future__ import annotations

import numpy as np
import pandas as pd

from dashboard_v2.services.technical_confirmation_service import (
    calculate_technical_confirmation,
    korean_ma_status,
    korean_technical_status,
    korean_trend_status,
)


def _bars(n: int = 280) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=n)
    close = 100 + np.arange(n) * 0.3 + np.sin(np.arange(n) / 7) * 2.0
    return pd.DataFrame({
        "Date": dates,
        "Open": close * 0.998,
        "High": close * 1.012,
        "Low": close * 0.988,
        "Close": close,
        "Volume": 1_000_000 + (np.arange(n) % 11) * 20_000,
    })


def test_technical_confirmation_calculates_legacy_metric_fields_from_daily_bars():
    bars = _bars()
    result = calculate_technical_confirmation(bars)
    assert result["data_status"] == "OK"
    assert result["price"] == bars["Close"].iloc[-1]
    assert abs(result["ma20"] - bars["Close"].tail(20).mean()) < 1e-8
    assert abs(result["ma50"] - bars["Close"].tail(50).mean()) < 1e-8
    assert 0 <= result["rsi"] <= 100
    assert result["technical_status"] in {"🟢 CONFIRMING", "🔴 WEAKENING", "🟡 MIXED"}
    assert len(result["technical_reasons"]) == 5


def test_technical_confirmation_caps_history_at_requested_eod_anchor():
    bars = _bars(100)
    cutoff = bars["Date"].iloc[79]
    result = calculate_technical_confirmation(bars, as_of_date=cutoff)
    assert result["data_status"] == "OK"
    assert result["as_of_date"] == cutoff.date().isoformat()
    assert result["price"] == bars["Close"].iloc[79]


def test_technical_confirmation_returns_na_instead_of_fabricating_short_history():
    result = calculate_technical_confirmation(_bars(59))
    assert result["data_status"] == "DATA_INSUFFICIENT"
    assert np.isnan(result["price"])
    assert result["technical_status"] == "⚪ N/A"


def test_technical_confirmation_discards_incomplete_ohlc_rows():
    bars = _bars(80)
    bars.loc[79, "High"] = np.nan
    result = calculate_technical_confirmation(bars)
    assert result["data_status"] == "OK"
    assert result["as_of_date"] == bars["Date"].iloc[78].date().isoformat()
    assert result["price"] == bars["Close"].iloc[78]


def test_technical_confirmation_converts_timezone_aware_dates_to_exchange_dates():
    bars = _bars(80)
    bars["Date"] = pd.DatetimeIndex(bars["Date"]).tz_localize("UTC")
    result = calculate_technical_confirmation(bars)
    assert result["data_status"] == "OK"
    assert result["as_of_date"] == bars["Date"].iloc[-1].tz_convert("America/New_York").date().isoformat()


def test_legacy_korean_status_labels_are_preserved():
    assert korean_ma_status("🟢 ALIGNED") == "🟢 정렬 양호"
    assert korean_ma_status("🟡 MIXED") == "🟡 혼합"
    assert korean_trend_status("🟢 IMPROVING") == "🟢 개선"
    assert korean_trend_status("🔴 WEAKENING") == "🔴 약화"
    assert korean_trend_status("→ NEUTRAL") == "→ 중립"
    assert korean_technical_status("🟡 MIXED") == "🟡 혼합"
    assert korean_technical_status("⚪ N/A") == "⚪ 데이터 부족"
