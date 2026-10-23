from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard_v2.engines.quality_recovery_engine import (
    MIN_BARS_FOR_FULL_SCAN,
    QualityRecoveryDataError,
    analyze_quality_recovery,
)


def _bars(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "Date": pd.bdate_range("2024-01-02", periods=len(close)),
        "Close": close,
        "Open": close,
        "High": [v * 1.01 for v in close],
        "Low": [v * 0.99 for v in close],
        "Volume": [1_000_000] * len(close),
        "Adj Close": [v * 0.98 for v in close],
    })


def test_ma_values_use_technical_close_not_adj_close():
    values = np.linspace(50.0, 150.0, MIN_BARS_FOR_FULL_SCAN).tolist()
    bars = _bars(values)
    out = analyze_quality_recovery("nvda", bars)
    close = pd.Series(values)
    assert out["ticker"] == "NVDA"
    assert out["close"] == pytest.approx(values[-1])
    assert out["sma20"] == pytest.approx(close.rolling(20).mean().iloc[-1])
    assert out["sma50"] == pytest.approx(close.rolling(50).mean().iloc[-1])
    assert out["sma120"] == pytest.approx(close.rolling(120).mean().iloc[-1])
    assert out["sma200"] == pytest.approx(close.rolling(200).mean().iloc[-1])
    assert out["technical_price_basis"] == "YAHOO_OHLC_AUTO_ADJUST_FALSE_CLOSE"


def test_current_ma_stack_is_not_mislabelled_as_cross_today():
    values = np.linspace(50.0, 150.0, 260).tolist()
    out = analyze_quality_recovery("META", _bars(values))
    assert out["sma20"] > out["sma50"] > out["sma200"]
    assert out["recovery_cross_today"] is False
    assert out["golden_cross_today"] is False
    assert out["recovery_state"] == "ABOVE_200D_CONFIRMED_TREND"


def test_recovery_setup_detects_early_technical_recovery():
    values = [100.0] * 210
    values += np.linspace(99.0, 52.0, 45).tolist()
    values += np.linspace(53.0, 92.0, 45).tolist()
    out = analyze_quality_recovery("ABC", _bars(values))
    assert out["close"] > out["sma20"] > out["sma50"]
    assert out["sma50"] < out["sma200"]
    assert out["sma20_rising_5d"] is True
    assert out["recovery_setup"] is True
    assert out["recovery_state"] == "RECOVERY_SETUP"


def test_recovery_cross_and_golden_cross_fields_match_latest_bar_crosses_only():
    values = [100.0] * 230 + np.linspace(99.0, 52.0, 35).tolist() + np.linspace(53.0, 100.0, 35).tolist()
    bars = _bars(values)
    out = analyze_quality_recovery("ABC", bars)
    close = bars["Close"]
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    assert out["recovery_cross_today"] == bool(sma20.iloc[-2] <= sma50.iloc[-2] and sma20.iloc[-1] > sma50.iloc[-1])
    assert out["golden_cross_today"] == bool(sma50.iloc[-2] <= sma200.iloc[-2] and sma50.iloc[-1] > sma200.iloc[-1])


def test_120d_and_200d_reclaim_events_are_one_day_crosses():
    # Verify the returned flags against exact previous/current close-vs-MA transitions.
    values = [100.0] * 205 + np.linspace(99.0, 65.0, 35).tolist() + np.linspace(66.0, 115.0, 50).tolist()
    bars = _bars(values)
    out = analyze_quality_recovery("ABC", bars)
    close = bars["Close"]
    sma120 = close.rolling(120).mean()
    sma200 = close.rolling(200).mean()
    expected_120 = close.iloc[-2] <= sma120.iloc[-2] and close.iloc[-1] > sma120.iloc[-1]
    expected_200 = close.iloc[-2] <= sma200.iloc[-2] and close.iloc[-1] > sma200.iloc[-1]
    assert out["reclaimed_120d_today"] == bool(expected_120)
    assert out["reclaimed_200d_today"] == bool(expected_200)


def test_insufficient_bars_fail_closed():
    with pytest.raises(QualityRecoveryDataError, match="INSUFFICIENT_COMPLETED_DAILY_BARS"):
        analyze_quality_recovery("ABC", _bars([100.0] * 200))


def test_bad_or_duplicate_daily_data_fail_closed():
    bars = _bars([100.0] * 205)
    bars.loc[3, "Close"] = float("nan")
    with pytest.raises(QualityRecoveryDataError, match="INVALID_CLOSE_VALUES"):
        analyze_quality_recovery("ABC", bars)

    bars = _bars([100.0] * 205)
    bars.loc[5, "Date"] = bars.loc[4, "Date"]
    with pytest.raises(QualityRecoveryDataError, match="DUPLICATE_DAILY_DATES"):
        analyze_quality_recovery("ABC", bars)


def test_intraday_multiple_bars_per_session_are_rejected():
    bars = _bars([100.0] * 205)
    bars.loc[1, "Date"] = pd.Timestamp(bars.loc[1, "Date"]) + pd.Timedelta(hours=1)
    with pytest.raises(QualityRecoveryDataError, match="INTRADAY_OR_DUPLICATE_SESSION_BARS"):
        analyze_quality_recovery("ABC", bars)


def test_fundamental_gate_is_explicitly_not_assessed_and_never_creates_pivot():
    out = analyze_quality_recovery("ABC", _bars(np.linspace(50.0, 100.0, 220).tolist()))
    assert out["fundamental_gate_status"] == "NOT_ASSESSED"
    assert "candidate_pivot" not in out
    assert "stage" not in out
    assert out["signal_provenance"] == "COMPLETED_DAILY_BARS_ONLY"
