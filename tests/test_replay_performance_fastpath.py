from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from dashboard_v2.engines.pattern_detectors import _local_extrema, detect_primary_pattern
from dashboard_v2.services.chronological_replay import replay_symbol

ROOT = Path(__file__).resolve().parents[1]


def _legacy_local_extrema(values: np.ndarray, *, kind: str, order: int = 2) -> list[int]:
    """Reference implementation used before the vectorized extrema fast path."""
    points: list[int] = []
    if len(values) < order * 2 + 1:
        return points
    for i in range(order, len(values) - order):
        value = values[i]
        if kind == "max":
            if all(value > values[j] for j in range(i - order, i)) and all(
                value >= values[j] for j in range(i + 1, i + order + 1)
            ):
                points.append(i)
        elif kind == "min":
            if all(value < values[j] for j in range(i - order, i)) and all(
                value <= values[j] for j in range(i + 1, i + order + 1)
            ):
                points.append(i)
    return points


def test_vectorized_local_extrema_matches_legacy_tie_and_plateau_rules() -> None:
    rng = np.random.default_rng(20261026)
    cases = [
        np.array([1, 2, 2, 1, 3, 2, 1, 1, 2, 3, 2], dtype=float),
        np.array([4, 3, 2, 1, 2, 3, 4, 3, 2, 1], dtype=float),
    ]
    cases.extend(rng.integers(0, 8, size=n).astype(float) for n in (5, 9, 17, 33, 70, 121))
    for values in cases:
        for order in (1, 2, 3):
            for kind in ("max", "min"):
                assert _local_extrema(values, kind=kind, order=order) == _legacy_local_extrema(
                    values, kind=kind, order=order
                )


def test_replay_fast_path_matches_validated_detector_path_exactly() -> None:
    bars = pd.read_csv(ROOT / "data" / "regression" / "ohlcv" / "GOOG.csv").tail(120)

    # Normal replay calls the internal _normalized fast path. The wrapper below
    # intentionally invokes the same detector through its public validation path.
    fast = replay_symbol("GOOG", bars)

    def validated_detector(frame: pd.DataFrame, **kwargs):
        return detect_primary_pattern(frame, **kwargs)

    validated = replay_symbol("GOOG", bars, pattern_detector=validated_detector)
    pd.testing.assert_frame_equal(fast, validated, check_exact=True)


def test_normalized_and_public_pattern_detection_return_identical_candidates() -> None:
    raw = pd.read_csv(ROOT / "data" / "regression" / "ohlcv" / "STX.csv").tail(120)
    normalized = raw.copy()
    normalized["Date"] = pd.to_datetime(normalized["Date"])
    public_result = detect_primary_pattern(raw)
    fast_result = detect_primary_pattern(normalized, _normalized=True)
    assert fast_result == public_result
