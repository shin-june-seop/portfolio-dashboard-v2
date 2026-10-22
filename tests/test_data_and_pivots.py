from datetime import date
import pandas as pd
import pytest
from dashboard_v2.engines.data_validation import MarketDataError, normalize_ohlcv
from dashboard_v2.engines.pivot_engine import price_distance_pct, is_pivot_approach, twenty_day_reference
from .helpers import bars_from_closes


def test_normalize_preserves_latest_row_and_does_not_drop_last_bar():
    df = bars_from_closes([100, 101, 102])
    out = normalize_ohlcv(df)
    assert len(out) == 3
    assert out.iloc[-1]["Close"] == 102


def test_normalize_asof_includes_asof_day_and_excludes_later_days():
    df = bars_from_closes([100, 101, 102])
    out = normalize_ohlcv(df, as_of_date="2026-01-02")
    assert len(out) == 2
    assert out.iloc[-1]["Close"] == 101


def test_duplicate_daily_bars_fail_loudly():
    df = bars_from_closes([100, 101])
    df.loc[1, "Date"] = df.loc[0, "Date"]
    with pytest.raises(MarketDataError, match="Duplicate"):
        normalize_ohlcv(df)


def test_price_distance_and_invalid_values():
    assert price_distance_pct(95, 100) == pytest.approx(-5.0)
    assert price_distance_pct(105, 100) == pytest.approx(5.0)
    assert price_distance_pct(100, None) is None
    assert price_distance_pct(100, 0) is None


def test_pivot_approach_boundaries_and_rebase_gate():
    assert is_pivot_approach(95, 100, candidate_valid=True)  # exactly -5% is included
    assert is_pivot_approach(99.999, 100, candidate_valid=True)
    assert not is_pivot_approach(100, 100, candidate_valid=True)  # zero is excluded
    assert not is_pivot_approach(94.99, 100, candidate_valid=True)
    assert not is_pivot_approach(99, 100, candidate_valid=False)
    assert not is_pivot_approach(99, 100, candidate_valid=True, rebase_forming=True)


def test_20d_reference_includes_last_bar_high():
    df = bars_from_closes([100.0] * 21, highs=[101.0] * 20 + [120.0])
    assert twenty_day_reference(df) == pytest.approx(120.0)
    assert twenty_day_reference(df.iloc[:-1]) == pytest.approx(101.0)


def test_20d_reference_requires_20_bars():
    assert twenty_day_reference(bars_from_closes([100.0] * 19)) is None


def test_market_bar_date_calendar_day_is_not_shifted_by_timezone_normalization():
    df = pd.DataFrame([{
        "Date": "2026-10-19T23:30:00-04:00",
        "Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000,
    }])
    out = normalize_ohlcv(df)
    assert out.iloc[0]["Date"].date() == date(2026, 10, 19)


def test_normalize_rejects_non_finite_ohlcv_values():
    df = bars_from_closes([100.0, 101.0, 102.0])
    df.loc[2, "Volume"] = float("inf")
    with pytest.raises(MarketDataError, match="non-finite"):
        normalize_ohlcv(df)
