from datetime import date

import numpy as np
import pandas as pd
import pytest

from dashboard_v2.engines.pivot_engine import twenty_day_reference
from dashboard_v2.models.schemas import PatternCandidate
from dashboard_v2.services.chronological_replay import replay_symbol
from dashboard_v2.services import screening_service


def _replay_bars() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    dates = pd.bdate_range("2026-01-01", periods=24)
    close = [99.0] * 23 + [101.0]
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [value - 0.25 for value in close],
        "High": [value + 1.0 for value in close],
        "Low": [value - 1.0 for value in close],
        "Close": close,
        "Volume": [1000] * 23 + [1300],
    })
    return frame, dates


def _source_candidate(frame: pd.DataFrame, **kwargs) -> PatternCandidate:
    return PatternCandidate(
        pattern_name="Test Base",
        valid=True,
        candidate_pivot=100.0,
        pattern_start_date=date(2026, 1, 1),
        pattern_upper=101.5,
        candidate_pivot_source="TEST_PATTERN_SOURCE",
    )


def test_replay_preserves_distinct_pattern_upper_candidate_and_twenty_day_reference():
    frame, dates = _replay_bars()
    result = replay_symbol("TEST", frame, pattern_detector=_source_candidate)

    # Before the breakout, source metadata is present on both candidate and next pivot.
    prior = result.iloc[-2]
    assert prior["candidate_pivot"] == pytest.approx(100.0)
    assert prior["pattern_upper"] == pytest.approx(101.5)
    assert prior["candidate_pivot_source"] == "TEST_PATTERN_SOURCE"
    assert prior["next_pivot_candidate"] == pytest.approx(100.0)
    assert prior["next_pivot_candidate_source"] == "TEST_PATTERN_SOURCE"

    # The crossing writes the candidate's source into the historical Breakout Pivot record.
    crossing = result.iloc[-1]
    assert crossing["as_of_date"] == dates[-1].date().isoformat()
    assert crossing["stage"] in {"BREAKOUT", "BREAKOUT CONFIRMED"}
    assert crossing["breakout_pivot"] == pytest.approx(100.0)
    assert crossing["breakout_pivot_source"] == "TEST_PATTERN_SOURCE"
    assert crossing["pattern_upper"] == pytest.approx(101.5)
    assert pd.isna(crossing["next_pivot_candidate"])
    assert pd.isna(crossing["next_pivot_candidate_source"])

    # 20D Reference is an independent max-High calculation, not an alias to either pivot.
    assert twenty_day_reference(frame, as_of_date=dates[-1]) == pytest.approx(102.0)
    assert result.attrs["performance_profile"]["replay_bars_processed"] == len(frame)
    profile = result.attrs["performance_profile"]
    for key in (
        "replay_total_seconds", "replay_normalize_seconds", "replay_pattern_seconds",
        "replay_rebase_seconds", "replay_stage_seconds", "replay_loop_other_seconds",
    ):
        assert key in profile
        assert profile[key] >= 0


def test_screening_result_keeps_source_fields_and_reports_missing_historical_source(monkeypatch):
    dates = pd.bdate_range("2025-01-01", periods=260)
    close = np.linspace(90.0, 110.0, len(dates))
    high = close + 1.0
    high[-1] = 130.0
    raw = pd.DataFrame({
        "Date": dates,
        "Open": close - 0.3,
        "High": high,
        "Low": close - 1.0,
        "Close": close,
        "Volume": [1000] * len(dates),
    })
    latest_date = dates[-1].date().isoformat()
    fake_state = {
        "as_of_date": latest_date,
        "close": float(close[-1]),
        "pattern": "Test Base",
        "pattern_upper": 106.0,
        "candidate_pivot": 105.0,
        "candidate_pivot_source": "TEST_PATTERN_SOURCE",
        "breakout_pivot": 95.0,
        "breakout_pivot_source": "HISTORICAL_SOURCE_NOT_STORED",
        "next_pivot_candidate": 112.0,
        "next_pivot_candidate_source": "TEST_NEXT_SOURCE",
        "stage": "PIVOT APPROACH",
        "rebase_active": False,
        "breakout_cycle_active": False,
        "recent_official_states": [],
    }
    fake_profile = {
        "history_snapshot_seconds": 0.012,
        "history_replay_invoked": True,
        "replay_total_seconds": 0.010,
        "replay_pattern_seconds": 0.003,
        "replay_rebase_seconds": 0.002,
        "replay_stage_seconds": 0.001,
    }
    monkeypatch.setattr(
        screening_service,
        "_history_snapshot",
        lambda symbol, bars, seed: (dict(fake_state), "BUNDLED_REPLAY_SNAPSHOT", dict(fake_profile)),
    )
    monkeypatch.setattr(screening_service, "detect_pattern_candidates", lambda *args, **kwargs: [])

    result = screening_service.analyze_symbol("TEST", raw)

    assert result["pattern_upper"] == pytest.approx(106.0)
    assert result["candidate_pivot"] == pytest.approx(105.0)
    assert result["candidate_pivot_source"] == "TEST_PATTERN_SOURCE"
    assert result["twenty_day_reference"] == pytest.approx(130.0)
    assert result["breakout_pivot"] == pytest.approx(95.0)
    assert result["breakout_pivot_source"] is None
    assert result["next_pivot_candidate"] == pytest.approx(112.0)
    assert result["next_pivot_candidate_source"] == "TEST_NEXT_SOURCE"
    assert result["pivot_source_diagnostics"] == [{
        "field": "breakout_pivot_source",
        "reason": "PRICE_PRESENT_SOURCE_MISSING",
        "price": 95.0,
    }]
    assert result["performance_profile"]["replay_total_seconds"] == pytest.approx(0.010)
    assert result["performance_profile"]["diagnostic_pattern_seconds"] >= 0
