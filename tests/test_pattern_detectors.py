from __future__ import annotations
from datetime import date, timedelta

import numpy as np
import pandas as pd

from dashboard_v2.engines.pattern_detectors import (
    detect_cup_handle,
    detect_cup_without_handle,
    detect_double_bottom,
    detect_flat_base,
    detect_pattern_candidates,
    detect_primary_pattern,
)


def make_frame(closes, *, highs=None, lows=None, volumes=None):
    n = len(closes)
    close = np.asarray(closes, dtype=float)
    high = np.asarray(highs, dtype=float) if highs is not None else close + 1.5
    low = np.asarray(lows, dtype=float) if lows is not None else close - 1.5
    vol = np.asarray(volumes, dtype=float) if volumes is not None else np.full(n, 100.0)
    return pd.DataFrame({
        "Date": [date(2025, 1, 1) + timedelta(days=i) for i in range(n)],
        "Open": close,
        "High": np.maximum(high, close),
        "Low": np.minimum(low, close),
        "Close": close,
        "Volume": vol,
    })


def flat_base_fixture():
    # A prior advance from ~55 into a 30-bar, shallow consolidation around 100.
    pre = np.linspace(55, 100, 45)
    x = np.arange(30)
    base = 100 + 1.2 * np.sin(x / 2) + 0.3 * np.sin(x * 1.7)
    closes = np.concatenate([pre, base])
    return make_frame(closes, highs=closes + 2.0, lows=closes - 2.0)


def double_bottom_fixture():
    n = 60
    x = np.arange(n)
    # Two troughs ~27 sessions apart with a ~20% middle-rally high.
    closes = np.interp(x, [0, 14, 29, 41, 59], [90, 76.5, 89, 76.6, 88])
    closes[12:17] = [80, 77, 76.5, 77, 80]
    closes[25:34] = [82, 85, 88, 89, 89, 89, 88, 85, 82]
    closes[39:45] = [80, 77, 76.6, 77, 79, 82]
    closes[45:] = np.linspace(82, 88, n - 45)
    lows = closes - 1.0
    lows[14] = 75.0
    lows[41] = 75.2
    highs = closes + 1.0
    highs[29] = 90.0
    return make_frame(closes, highs=highs, lows=lows)


def cup_without_handle_fixture():
    n = 60
    x = np.linspace(0, 1, n)
    # Rounded U-shaped cup with a deeper-than-saucer drawdown (~30%).
    closes = 100 - 28 * 4 * x * (1 - x)
    return make_frame(closes, highs=closes + 1.5, lows=closes - 1.5)


def cup_handle_fixture():
    n_cup, n_handle = 42, 8
    x = np.linspace(0, 1, n_cup)
    cup = 100 - 25 * 4 * x * (1 - x)
    handle = np.array([97.5, 96.0, 94.0, 94.8, 95.6, 96.5, 97.4, 98.3])
    closes = np.concatenate([cup, handle])
    highs = closes + 1.5
    lows = closes - 1.5
    return make_frame(closes, highs=highs, lows=lows)


def test_flat_base_detector_requires_shallow_structure_and_prior_advance():
    result = detect_flat_base(flat_base_fixture())
    assert result is not None
    assert result.pattern_name == "Flat Base"
    assert result.candidate_pivot > 100
    assert result.diagnostics["duration_bars"] >= 25
    assert result.diagnostics["prior_advance_pct"] >= 20


def test_double_bottom_detector_finds_two_close_troughs_and_midpoint_pivot():
    result = detect_double_bottom(double_bottom_fixture())
    assert result is not None
    assert result.pattern_name == "Double Bottom"
    assert abs(result.diagnostics["low2"] / result.diagnostics["low1"] - 1) <= 0.03
    assert result.candidate_pivot >= 90
    assert result.diagnostics["middle_rebound_pct"] >= 10


def test_cup_without_handle_detector_finds_rounded_cup():
    result = detect_cup_without_handle(cup_without_handle_fixture())
    assert result is not None
    assert result.pattern_name == "Cup Without Handle"
    assert 15 <= result.diagnostics["cup_depth_pct"] <= 33
    assert result.candidate_pivot > 95


def test_cup_handle_detector_finds_handle_and_uses_handle_high_as_pivot():
    frame = cup_handle_fixture()
    result = detect_cup_handle(frame)
    assert result is not None
    assert result.pattern_name == "Cup & Handle"
    assert result.diagnostics["handle_bars"] >= 5
    assert result.diagnostics["handle_depth_pct"] <= 15
    assert result.candidate_pivot == float(frame.tail(8)["High"].max())


def test_rebase_cycle_prevents_old_pattern_being_reused():
    frame = flat_base_fixture()
    result = detect_flat_base(frame, rebase_started_at=date(2025, 3, 1))
    assert result is None


def test_short_history_returns_no_pattern_without_exception():
    result = detect_primary_pattern(make_frame(np.linspace(10, 12, 15)))
    assert result is None


def test_full_detector_returns_single_primary_pattern_separately_from_candidates():
    candidates = detect_pattern_candidates(flat_base_fixture())
    assert candidates
    primary = detect_primary_pattern(flat_base_fixture())
    assert primary is not None
    assert primary.pattern_name == "Flat Base"


def high_tight_flag_fixture():
    pole = np.linspace(50, 110, 40)
    flag = np.array([109, 108, 107, 106, 108, 109, 107, 105, 104, 106,
                     108, 107, 105, 103, 101, 102, 104, 103, 101, 102], dtype=float)
    closes = np.concatenate([pole, flag])
    volumes = np.concatenate([np.full(len(pole), 100.0), np.full(len(flag), 70.0)])
    return make_frame(closes, highs=closes + 0.1, lows=closes - 0.1, volumes=volumes)


def vcp_fixture():
    # Pole followed by three progressively shallower contractions and volume dry-up.
    knots = [(0, 48), (10, 100), (15, 80), (23, 96), (28, 86),
             (36, 98), (41, 92), (55, 95)]
    x = np.arange(56)
    closes = np.interp(x, [a for a, _ in knots], [b for _, b in knots])
    closes += 0.15 * np.sin(x * 1.7)
    volumes = np.full(56, 100.0)
    volumes[16:29] = 80.0
    volumes[29:42] = 60.0
    volumes[42:] = 50.0
    return make_frame(closes, highs=closes + 0.05, lows=closes - 0.05, volumes=volumes)


def test_high_tight_flag_requires_big_pole_and_shallow_flag():
    from dashboard_v2.engines.pattern_detectors import detect_high_tight_flag
    result = detect_high_tight_flag(high_tight_flag_fixture())
    assert result is not None
    assert result.pattern_name == "High Tight Flag"
    assert result.diagnostics["pole_gain_pct"] >= 100
    assert result.diagnostics["flag_depth_pct"] <= 25
    assert result.diagnostics["flag_bars"] >= 15


def test_vcp_requires_descending_contractions_prior_advance_and_volume_dryup():
    import json
    from dashboard_v2.engines.pattern_detectors import detect_vcp
    result = detect_vcp(vcp_fixture())
    assert result is not None
    assert result.pattern_name == "VCP"
    contractions = result.diagnostics["contractions_pct"]
    assert 2 <= len(contractions) <= 6
    assert contractions[0] >= 10
    assert contractions[-1] <= 8
    assert all(contractions[i] <= contractions[i - 1] * 0.75 for i in range(1, len(contractions)))
    assert result.candidate_pivot_source == "VCP"
    json.dumps(result.diagnostics)


def test_base_on_base_requires_known_first_pivot_and_majority_of_bars_above_it():
    from dashboard_v2.engines.pattern_detectors import detect_base_on_base
    pre = np.linspace(90, 105, 20)
    second = 103 + 1 * np.sin(np.arange(30) / 2)
    frame = make_frame(np.concatenate([pre, second]), highs=np.concatenate([pre + 1, second + 1]),
                       lows=np.concatenate([pre - 1, second - 1]))
    assert detect_base_on_base(frame, prior_breakout_pivot=None) is None
    result = detect_base_on_base(frame, prior_breakout_pivot=100)
    assert result is not None
    assert result.pattern_name == "Base-on-Base"
    assert result.diagnostics["second_base_days_above_first_pivot_pct"] >= 50
    assert result.diagnostics["max_advance_since_first_pivot_pct"] < 20


def test_vcp_wins_primary_selection_when_classic_pattern_also_exists():
    from dashboard_v2.engines.pattern_detectors import detect_primary_pattern
    result = detect_primary_pattern(vcp_fixture())
    assert result is not None
    assert result.pattern_name == "VCP"


def test_saucer_base_requires_long_rounded_structure():
    from dashboard_v2.engines.pattern_detectors import detect_saucer_base
    x = np.linspace(0, 1, 70)
    closes = 100 - 15 * 4 * x * (1 - x)
    result = detect_saucer_base(make_frame(closes, highs=closes + 1.5, lows=closes - 1.5))
    assert result is not None
    assert result.pattern_name == "Saucer Base"
    assert result.diagnostics["duration_bars"] >= 40
    assert result.diagnostics["depth_pct"] <= 25


def test_ascending_base_pullback_diagnostics_are_percentages_not_prices():
    import json
    from dashboard_v2.engines.pattern_detectors import detect_ascending_base
    knots = [(0, 50), (8, 70), (14, 63), (22, 80), (28, 72), (36, 91), (42, 82), (58, 100)]
    x = np.arange(59)
    closes = np.interp(x, [a for a, _ in knots], [b for _, b in knots]) + 0.2 * np.sin(x * 1.4)
    frame = make_frame(closes, highs=closes + 0.1, lows=closes - 0.1)
    result = detect_ascending_base(frame)
    assert result is not None
    pullbacks = result.diagnostics["pullbacks"]
    assert len(pullbacks) == 3
    assert all(6 <= item["depth_pct"] <= 25 for item in pullbacks)
    json.dumps(result.diagnostics)


def test_base_on_base_rejects_when_price_already_advanced_20pct_after_first_breakout():
    from dashboard_v2.engines.pattern_detectors import detect_base_on_base
    before = np.linspace(90, 99, 10)
    break_and_rally = np.array([102, 108, 115, 123, 126, 120, 114, 110], dtype=float)
    later_base = 103 + 0.8 * np.sin(np.arange(30) / 2)
    closes = np.concatenate([before, break_and_rally, later_base])
    frame = make_frame(closes, highs=closes + 1, lows=closes - 1)
    assert detect_base_on_base(frame, prior_breakout_pivot=100) is None


def test_base_on_base_requires_observable_breakout_or_explicit_event_date():
    from dashboard_v2.engines.pattern_detectors import detect_base_on_base
    closes = 103 + 0.6 * np.sin(np.arange(35) / 2)
    frame = make_frame(closes, highs=closes + 1, lows=closes - 1)
    assert detect_base_on_base(frame, prior_breakout_pivot=100) is None
    result = detect_base_on_base(frame, prior_breakout_pivot=100, prior_breakout_date=date(2025, 1, 5))
    assert result is not None
    assert result.pattern_name == "Base-on-Base"


def test_base_on_base_handles_candidate_ends_before_first_breakout_without_crashing():
    from dashboard_v2.engines.pattern_detectors import detect_base_on_base
    closes = np.linspace(90, 105, 25)
    frame = make_frame(closes, highs=closes + 1, lows=closes - 1)
    # Event occurs at the final bar; formation-end lookback includes prior bars.
    # Those earlier ends must be rejected, not passed to np.max on an empty slice.
    event_date = pd.Timestamp(frame.iloc[-1]["Date"]).date()
    result = detect_base_on_base(frame, prior_breakout_pivot=100, prior_breakout_date=event_date)
    assert result is None
