from dashboard_v2.engines.data_validation import atr_wilder
from .helpers import bars_from_closes


def test_atr_returns_none_when_history_is_insufficient():
    df = bars_from_closes([100.0] * 10)
    assert atr_wilder(df, 14) is None


def test_atr_returns_positive_value_with_enough_completed_bars():
    df = bars_from_closes([100.0 + (i % 2) for i in range(30)])
    value = atr_wilder(df, 14)
    assert value is not None
    assert value > 0
