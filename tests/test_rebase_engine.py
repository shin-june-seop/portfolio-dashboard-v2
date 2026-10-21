from datetime import date
from dashboard_v2.engines.rebase_engine import update_rebase_state
from dashboard_v2.models.schemas import RebaseState
from .helpers import bars_from_closes


def test_rb01_starts_rebase_when_close_ten_percent_below_20d_high():
    closes = [100.0] * 19 + [89.0]
    highs = [102.0] * 19 + [90.0]
    df = bars_from_closes(closes, highs=highs)
    asof = df.iloc[-1]["Date"] if isinstance(df.iloc[-1]["Date"], date) else df.iloc[-1]["Date"].date()
    result = update_rebase_state(df, as_of_date=asof, close=89, previous_candidate_pivot=None, active_breakout_pivot=None, breakout_cycle_active=False)
    assert result.active
    assert result.primary_reason == "20D_HIGH_DRAWDOWN"
    assert result.started_at == asof


def test_breakout_failure_reason_has_priority_and_keeps_secondary_triggers():
    closes = [100.0] * 19 + [80.0]
    highs = [110.0] * 19 + [81.0]
    df = bars_from_closes(closes, highs=highs)
    asof = df.iloc[-1]["Date"] if isinstance(df.iloc[-1]["Date"], date) else df.iloc[-1]["Date"].date()
    result = update_rebase_state(df, as_of_date=asof, close=80, previous_candidate_pivot=100, active_breakout_pivot=90, breakout_cycle_active=True)
    assert result.active
    assert result.primary_reason == "BREAKOUT_FAILURE"
    assert "CANDIDATE_PIVOT_DRAWDOWN" in result.secondary_reasons
    assert "20D_HIGH_DRAWDOWN" in result.secondary_reasons


def test_existing_rebase_cycle_keeps_original_start_and_updates_low():
    df = bars_from_closes([100.0] * 20 + [85.0, 80.0], lows=[99.0] * 20 + [84.0, 78.0])
    asof = df.iloc[-1]["Date"] if isinstance(df.iloc[-1]["Date"], date) else df.iloc[-1]["Date"].date()
    old_start = df.iloc[-2]["Date"] if isinstance(df.iloc[-2]["Date"], date) else df.iloc[-2]["Date"].date()
    prior = RebaseState(active=True, started_at=old_start, low=84, low_date=old_start, primary_reason="20D_HIGH_DRAWDOWN")
    result = update_rebase_state(df, as_of_date=asof, close=80, previous_candidate_pivot=None, active_breakout_pivot=None, breakout_cycle_active=False, previous_state=prior)
    assert result.active
    assert result.started_at == old_start
    assert result.low == 78
    assert result.low_date == asof


def test_rebase_cycle_ends_when_valid_new_pattern_is_accepted_without_new_trigger():
    # Last 20-bar high is 100 and close is 96 (>90%); no candidate or breakout failure.
    closes = [100.0] * 20 + [96.0]
    highs = [100.0] * 20 + [97.0]
    df = bars_from_closes(closes, highs=highs)
    asof = df.iloc[-1]["Date"] if isinstance(df.iloc[-1]["Date"], date) else df.iloc[-1]["Date"].date()
    prior = RebaseState(active=True, started_at=date(2026, 1, 1), low=80, low_date=date(2026, 1, 5), primary_reason="20D_HIGH_DRAWDOWN")
    result = update_rebase_state(df, as_of_date=asof, close=96, previous_candidate_pivot=None, active_breakout_pivot=None, breakout_cycle_active=False, previous_state=prior, valid_new_pattern=True)
    assert not result.active



def test_valid_pattern_can_end_rebase_while_original_rb01_threshold_remains_true():
    # RB-01 remains true during the drawdown, but that persistent condition is not a
    # fresh invalidation event that should trap the state machine in BASE FORMING.
    closes = [100.0] * 19 + [85.0]
    highs = [102.0] * 19 + [87.0]
    df = bars_from_closes(closes, highs=highs)
    asof = df.iloc[-1]["Date"] if isinstance(df.iloc[-1]["Date"], date) else df.iloc[-1]["Date"].date()
    prior = RebaseState(active=True, started_at=date(2026, 1, 1), low=80, low_date=date(2026, 1, 5), primary_reason="20D_HIGH_DRAWDOWN")
    result = update_rebase_state(df, as_of_date=asof, close=85, previous_candidate_pivot=None, active_breakout_pivot=None, breakout_cycle_active=False, previous_state=prior, valid_new_pattern=True)
    assert not result.active
