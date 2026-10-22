"""End-to-end regression tests for candidate-pivot to confirmed-breakout lifecycle.

These tests use synthetic OHLCV bars. They validate engine-state transitions,
not market ground truth or pattern-detection accuracy.
"""
from datetime import date

import pandas as pd
import pytest

from dashboard_v2.models.schemas import PatternCandidate
from dashboard_v2.services.chronological_replay import replay_symbol


def _candidate_180(frame, **kwargs):
    """Deterministic test candidate representing a valid post-rebase base."""
    return PatternCandidate(
        pattern_name="Flat Base",
        valid=True,
        candidate_pivot=180.0,
        pattern_start_date=date(2026, 10, 14),
        pattern_upper=180.0,
        candidate_pivot_source="PATTERN",
    )


def _fixture(cross_volume: float, *, post_breakout_volume: float | None = None):
    dates = pd.bdate_range("2026-09-18", periods=25)
    closes = [170.0] * 21 + [177.0, 179.0, 181.0, 183.0]
    highs = [172.0] * 21 + [178.0, 180.0, 182.0, 184.0]
    lows = [168.0] * 21 + [176.0, 178.0, 180.0, 182.0]
    lows[16] = 150.0  # previously tracked cycle low before the snapshot
    volumes = [1000.0] * 25
    volumes[23] = float(cross_volume)
    if post_breakout_volume is not None:
        volumes[24] = float(post_breakout_volume)

    frame = pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })
    seed_date = dates[20].date()
    snapshot = {
        "as_of_date": seed_date.isoformat(),
        "close": 170.0,
        "candidate_pivot": None,
        "breakout_pivot": 300.0,
        "breakout_date": "2026-06-01",
        "breakout_cycle_active": False,
        "stage": "BASE",
        "rebase_active": True,
        "rebase_started_at": "2026-10-09",
        "rebase_low": 150.0,
        "rebase_low_date": "2026-10-12",
        "rebase_reason": "CANDIDATE_PIVOT_DRAWDOWN",
        "rebase_secondary_reasons": "20D_HIGH_DRAWDOWN",
    }
    result = replay_symbol(
        "TEST",
        frame,
        pattern_detector=_candidate_180,
        resume_snapshot=snapshot,
        process_from_date=seed_date,
    )
    return result, dates


def test_rebase_candidate_pivot_approach_and_low_volume_breakout_replaces_pivot():
    result, dates = _fixture(cross_volume=1200.0, post_breakout_volume=2000.0)

    assert len(result) == 4
    before_cross = result.iloc[0:2]
    assert list(before_cross["stage"]) == ["PIVOT APPROACH", "PIVOT APPROACH"]
    assert list(before_cross["candidate_pivot"]) == [180.0, 180.0]
    assert list(before_cross["breakout_pivot"]) == [300.0, 300.0]
    assert not bool(before_cross.iloc[0]["rebase_active"])
    assert not bool(before_cross.iloc[0]["breakout_cycle_active"])

    crossing = result.iloc[2]
    assert crossing["as_of_date"] == dates[23].date().isoformat()
    assert crossing["stage"] == "BREAKOUT"
    assert crossing["eod_rvol"] == pytest.approx(1.20)
    assert bool(crossing["breakout_event"])
    assert not bool(crossing["breakout_failure"])
    assert crossing["breakout_pivot"] == pytest.approx(180.0)
    assert crossing["breakout_date"] == dates[23].date().isoformat()
    assert bool(crossing["breakout_cycle_active"])

    # High RVOL on the following day cannot retroactively upgrade a prior
    # low-volume breakout to BREAKOUT CONFIRMED without a fresh upward cross.
    following = result.iloc[3]
    assert following["stage"] == "BREAKOUT"
    assert not bool(following["breakout_event"])
    assert not bool(following["breakout_confirmed"])
    assert following["breakout_pivot"] == pytest.approx(180.0)
    assert following["breakout_date"] == dates[23].date().isoformat()


def test_rvol_exactly_at_1_30_candidate_cross_is_breakout_confirmed_and_replaces_pivot():
    result, dates = _fixture(cross_volume=1300.0)
    crossing = result.iloc[2]

    assert crossing["as_of_date"] == dates[23].date().isoformat()
    assert crossing["stage"] == "BREAKOUT CONFIRMED"
    assert crossing["eod_rvol"] == pytest.approx(1.30)
    assert bool(crossing["breakout_event"])
    assert crossing["breakout_pivot"] == pytest.approx(180.0)
    assert crossing["breakout_date"] == dates[23].date().isoformat()
    assert bool(crossing["breakout_cycle_active"])
    assert not bool(crossing["rebase_active"])
