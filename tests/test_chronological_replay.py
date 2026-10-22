from datetime import date
import pandas as pd
from dashboard_v2.models.schemas import PatternCandidate
from dashboard_v2.services.chronological_replay import replay_symbol


def bars(closes, highs=None, lows=None):
    highs = highs or [x + 1 for x in closes]
    lows = lows or [x - 1 for x in closes]
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame({"Date": dates, "Open": closes, "High": highs, "Low": lows,
                         "Close": closes, "Volume": [1000] * len(closes)})


def no_pattern(*args, **kwargs):
    return None


def always_pattern(frame, **kwargs):
    # Artificial candidate for state-machine tests only; never market ground truth.
    return PatternCandidate("Flat Base", True, 100.0, date(2026, 1, 1), 100.0)


def test_replay_is_chronological_and_does_not_use_future_bars():
    frame = bars([100, 101, 102, 103, 104])
    result = replay_symbol("TEST", frame, pattern_detector=no_pattern)
    assert list(result["as_of_date"]) == sorted(result["as_of_date"])
    assert len(result) == len(frame)
    assert result.iloc[0]["close"] == 100
    assert result.iloc[0]["high"] == 101


def test_candidate_pivot_is_not_created_from_pattern_while_rebase_active():
    # 20th bar closes 10% below the previous 20-day high; candidate scan is present
    # but rebase reset must win on the trigger bar.
    closes = [100.0] * 19 + [89.0]
    highs = [102.0] * 19 + [90.0]
    frame = bars(closes, highs=highs)
    result = replay_symbol("TEST", frame, pattern_detector=always_pattern)
    last = result.iloc[-1]
    assert last["rebase_active"]
    assert pd.isna(last["candidate_pivot"])
    assert last["pattern"] == "BASE FORMING / NO VALID PATTERN"


def test_historical_breakout_pivot_is_preserved_when_candidate_is_reset():
    closes = [100.0] * 19 + [80.0]
    highs = [101.0] * 19 + [81.0]
    frame = bars(closes, highs=highs)
    result = replay_symbol("TEST", frame, pattern_detector=no_pattern,
                           initial_candidate_pivot=100.0,
                           initial_breakout_pivot=90.0,
                           initial_breakout_date=date(2026, 1, 2),
                           initial_breakout_active=True)
    last = result.iloc[-1]
    assert last["breakout_failure"]
    assert last["breakout_pivot"] == 90.0
    assert not last["breakout_cycle_active"]
    assert pd.isna(last["candidate_pivot"])


def test_resume_from_snapshot_preserves_breakout_pivot_and_processes_new_eod_failure():
    import pandas as pd
    from dashboard_v2.services.chronological_replay import replay_symbol

    dates = pd.bdate_range("2026-10-01", periods=7)
    close = [100, 102, 104, 106, 108, 110, 104]
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [x - 0.5 for x in close],
        "High": [x + 1.0 for x in close],
        "Low": [x - 1.0 for x in close],
        "Close": close,
        "Volume": [100000] * len(close),
    })
    seed = {
        "as_of_date": dates[-2].date().isoformat(),
        "close": 110.0,
        "candidate_pivot": 115.0,
        "breakout_pivot": 105.0,
        "breakout_date": dates[2].date().isoformat(),
        "breakout_cycle_active": True,
        "stage": "BREAKOUT",
        "rebase_active": False,
        "rebase_started_at": None,
        "rebase_low": None,
        "rebase_low_date": None,
        "rebase_reason": None,
        "rebase_secondary_reasons": "",
    }
    result = replay_symbol(
        "TEST", frame,
        pattern_detector=lambda *args, **kwargs: None,
        resume_snapshot=seed,
        process_from_date=dates[-2].date(),
    )
    assert len(result) == 1
    latest = result.iloc[-1]
    assert latest["breakout_failure"]
    assert latest["stage"] == "BREAKOUT FAILURE"
    assert latest["breakout_pivot"] == 105.0
    assert latest["rebase_active"]
    assert pd.isna(latest["candidate_pivot"])


def test_rebase_closes_old_breakout_cycle_but_preserves_historical_pivot():
    # Candidate drawdown starts a rebase while price remains well above the old
    # Breakout Pivot. The old cycle ends, but its pivot/date stay as history.
    closes = [100.0] * 19 + [89.0]
    frame = bars(closes)
    result = replay_symbol(
        "TEST", frame, pattern_detector=always_pattern,
        initial_candidate_pivot=100.0,
        initial_breakout_pivot=70.0,
        initial_breakout_date=date(2026, 1, 2),
        initial_breakout_active=True,
    )
    last = result.iloc[-1]
    assert bool(last["rebase_active"])
    assert last["rebase_reason"] == "CANDIDATE_PIVOT_DRAWDOWN"
    assert not bool(last["breakout_failure"])
    assert last["breakout_pivot"] == 70.0
    assert last["breakout_date"] == "2026-01-02"
    assert not bool(last["breakout_cycle_active"])


def test_new_candidate_replaces_breakout_pivot_only_when_crossed():
    # Rebase ends with a candidate at 180 while price is still below it. The old
    # historical pivot (300) remains displayed until the next day's actual cross.
    dates = pd.bdate_range("2026-10-20", periods=3)
    closes = [170.0, 175.0, 181.0]
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [x - 0.5 for x in closes],
        "High": [x + 1.0 for x in closes],
        "Low": [x - 1.0 for x in closes],
        "Close": closes,
        "Volume": [1000] * len(closes),
    })

    def candidate_180(*args, **kwargs):
        return PatternCandidate("Flat Base", True, 180.0, date(2026, 10, 1), 180.0)

    snapshot = {
        "as_of_date": dates[0].date().isoformat(),
        "close": 170.0,
        "candidate_pivot": None,
        "breakout_pivot": 300.0,
        "breakout_date": "2026-10-01",
        "breakout_cycle_active": True,  # legacy contradictory state
        "stage": "BASE",
        "rebase_active": True,
        "rebase_started_at": "2026-10-10",
        "rebase_low": 150.0,
        "rebase_low_date": "2026-10-15",
        "rebase_reason": "CANDIDATE_PIVOT_DRAWDOWN",
        "rebase_secondary_reasons": "",
    }
    result = replay_symbol(
        "TEST", frame, pattern_detector=candidate_180,
        resume_snapshot=snapshot,
        process_from_date=dates[0].date(),
    )
    assert len(result) == 2
    candidate_day = result.iloc[0]
    assert not bool(candidate_day["rebase_active"])
    assert candidate_day["candidate_pivot"] == 180.0
    assert candidate_day["breakout_pivot"] == 300.0
    assert not bool(candidate_day["breakout_event"])
    assert not bool(candidate_day["breakout_cycle_active"])

    breakout_day = result.iloc[1]
    assert bool(breakout_day["breakout_event"])
    assert breakout_day["stage"] == "BREAKOUT"
    assert breakout_day["breakout_pivot"] == 180.0
    assert breakout_day["breakout_date"] == dates[2].date().isoformat()
    assert bool(breakout_day["breakout_cycle_active"])
