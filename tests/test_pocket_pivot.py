from dashboard_v2.engines.pocket_pivot_engine import detect_pocket_pivot
from .helpers import bars_from_closes


def _pp_history(no_down_days=False):
    if no_down_days:
        closes = [100.0 + i * 0.02 for i in range(60)]
    else:
        closes = [100.0] * 50 + [99, 101, 98, 100, 99, 101, 99, 101, 99, 100, 102]
    volumes = [100.0] * len(closes)
    # Make prior 10-day down-day volume max explicit but lower than signal volume.
    for i in range(max(0, len(closes) - 11), len(closes) - 1):
        volumes[i] = 150.0 if closes[i] < closes[i - 1] else 100.0
    volumes[-1] = 500.0
    lows = [c - 0.2 for c in closes]
    lows[-1] = closes[-1] - 5.0  # low tests MA while close reclaims it; no fixed distance rule
    return bars_from_closes(closes, volumes=volumes, lows=lows)


def test_pocket_pivot_valid_with_down_day_volume_and_ma_test():
    result = detect_pocket_pivot(_pp_history(), constructive_context=True)
    assert result.valid_signal
    assert result.reason_codes == ("POCKET_PIVOT_VALID",)
    assert result.signal_volume == 500
    assert result.max_prior_down_day_volume == 150
    assert result.reference_ma in {"MA10", "MA50"}


def test_no_prior_down_days_does_not_auto_pass():
    result = detect_pocket_pivot(_pp_history(no_down_days=True), constructive_context=True)
    assert not result.valid_signal
    assert "PP_VOLUME_REFERENCE_UNAVAILABLE" in result.reason_codes


def test_invalid_wider_context_rejects_signal_even_if_volume_passes():
    result = detect_pocket_pivot(_pp_history(), constructive_context=False)
    assert not result.valid_signal
    assert "PP_CONTEXT_INVALID" in result.reason_codes


def test_prior_max_down_day_volume_must_be_strictly_exceeded():
    df = _pp_history()
    # Match the strongest down-day's volume, which is enough to fail the strict > rule.
    df.loc[len(df) - 1, "Volume"] = 150.0
    result = detect_pocket_pivot(df, constructive_context=True)
    assert not result.valid_signal
    assert "SIGNAL_VOLUME_NOT_ABOVE_PRIOR_10D_DOWN_DAY_MAX" in result.reason_codes
