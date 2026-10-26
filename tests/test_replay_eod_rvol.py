from datetime import date

import pandas as pd
import pytest

from dashboard_v2.models.schemas import PatternCandidate
from dashboard_v2.services.chronological_replay import replay_symbol


def _bars(cross_volume: int) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    dates = pd.bdate_range("2026-01-01", periods=24)
    close = [170.0] * 22 + [179.0, 181.0]
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [value - 0.5 for value in close],
        "High": [value + 1.0 for value in close],
        "Low": [value - 1.0 for value in close],
        "Close": close,
        "Volume": [1000] * 23 + [cross_volume],
    })
    return frame, dates


def _candidate(frame, **kwargs):
    return PatternCandidate(
        "Flat Base", True, 180.0, date(2026, 1, 1), 180.0,
    )


@pytest.mark.parametrize(
    ("cross_volume", "expected_rvol", "expected_stage"),
    [
        (1200, 1.20, "BREAKOUT"),
        (1300, 1.30, "BREAKOUT CONFIRMED"),
    ],
)
def test_replay_computes_prior_20_day_eod_rvol_and_uses_confirm_threshold(
    cross_volume, expected_rvol, expected_stage
):
    frame, dates = _bars(cross_volume)
    result = replay_symbol("TEST", frame, pattern_detector=_candidate)
    crossing = result.iloc[-1]

    assert crossing["as_of_date"] == dates[-1].date().isoformat()
    assert crossing["eod_rvol"] == pytest.approx(expected_rvol)
    assert crossing["stage"] == expected_stage
    assert bool(crossing["breakout_confirmed"]) is (cross_volume >= 1300)
    assert crossing["breakout_pivot"] == pytest.approx(180.0)


def test_rvol_date_scoped_replay_uses_volume_context_before_start_date():
    frame, dates = _bars(1300)
    result = replay_symbol(
        "TEST", frame, pattern_detector=_candidate,
        start_date=dates[-1].date(),
    )
    assert len(result) == 1
    assert result.iloc[0]["eod_rvol"] == pytest.approx(1.30)


def test_rvol_is_missing_until_twenty_prior_completed_bars_exist():
    frame, _ = _bars(1300)
    result = replay_symbol("TEST", frame.iloc[:20], pattern_detector=_candidate)
    assert result["eod_rvol"].isna().all()


def test_high_volume_following_day_does_not_retroactively_confirm_breakout():
    frame, dates = _bars(1200)
    # The next bar remains above the new breakout pivot but has high volume. Since
    # there is no fresh upward cross, confirmation must remain false.
    next_date = dates[-1] + pd.offsets.BDay(1)
    followup = pd.DataFrame({
        "Date": [next_date], "Open": [181.0], "High": [184.0],
        "Low": [180.5], "Close": [183.0], "Volume": [2000],
    })
    frame = pd.concat([frame, followup], ignore_index=True)

    result = replay_symbol("TEST", frame, pattern_detector=_candidate)
    crossing = result.iloc[-2]
    following = result.iloc[-1]

    assert crossing["stage"] == "BREAKOUT"
    assert crossing["eod_rvol"] == pytest.approx(1.20)
    assert not bool(crossing["breakout_confirmed"])
    assert following["stage"] == "BREAKOUT"
    assert not bool(following["breakout_event"])
    assert not bool(following["breakout_confirmed"])
