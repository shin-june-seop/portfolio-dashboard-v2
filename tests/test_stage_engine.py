from datetime import date
from dashboard_v2.engines.stage_engine import evaluate_official_stage
from dashboard_v2.models.schemas import OfficialStage, StageInput

D = date(2026, 10, 19)


def test_pivot_approach_at_minus_five_percent():
    result = evaluate_official_stage(StageInput(D, close=95, previous_close=94, candidate_pivot=100, candidate_valid=True))
    assert result.stage == OfficialStage.PIVOT_APPROACH


def test_breakout_confirmed_only_on_actual_cross_with_volume():
    result = evaluate_official_stage(StageInput(D, close=101, previous_close=99, candidate_pivot=100, candidate_valid=True, eod_rvol=1.30))
    assert result.stage == OfficialStage.BREAKOUT_CONFIRMED
    assert result.breakout_event
    assert result.activate_breakout_pivot == 100


def test_actual_cross_without_volume_is_breakout_not_confirmed():
    result = evaluate_official_stage(StageInput(D, close=101, previous_close=99, candidate_pivot=100, candidate_valid=True, eod_rvol=1.29))
    assert result.stage == OfficialStage.BREAKOUT
    assert result.breakout_event
    assert not result.breakout_confirmed


def test_no_backdated_breakout_when_price_already_above_candidate():
    result = evaluate_official_stage(StageInput(D, close=105, previous_close=104, candidate_pivot=100, candidate_valid=True, eod_rvol=2.0))
    assert result.stage == OfficialStage.BASE
    assert not result.breakout_event


def test_breakout_confirmed_does_not_repeat_on_following_day():
    result = evaluate_official_stage(StageInput(D, close=105, previous_close=104, active_breakout_pivot=100, breakout_cycle_active=True, eod_rvol=2.0))
    assert result.stage == OfficialStage.BREAKOUT
    assert not result.breakout_confirmed
    assert not result.breakout_event


def test_eod_close_below_active_breakout_pivot_is_failure():
    result = evaluate_official_stage(StageInput(D, close=99.99, previous_close=103, active_breakout_pivot=100, breakout_cycle_active=True))
    assert result.stage == OfficialStage.BREAKOUT_FAILURE
    assert result.breakout_failure


def test_eod_close_equal_to_active_pivot_is_not_failure():
    result = evaluate_official_stage(StageInput(D, close=100, previous_close=101, active_breakout_pivot=100, breakout_cycle_active=True, candidate_pivot=104, candidate_valid=True))
    assert not result.breakout_failure
    assert result.stage != OfficialStage.BREAKOUT_FAILURE


def test_rebase_blocks_pivot_approach():
    result = evaluate_official_stage(StageInput(D, close=99, previous_close=98, candidate_pivot=100, candidate_valid=True, rebase_forming=True))
    assert result.stage == OfficialStage.BASE
    assert "REBASE_FORMING" in result.reason_codes


def test_active_breakout_failure_has_priority_over_new_next_pivot_cross():
    result = evaluate_official_stage(StageInput(D, close=95, previous_close=94, active_breakout_pivot=100, breakout_cycle_active=True, next_pivot=96, next_pivot_valid=True, eod_rvol=2.0))
    assert result.stage == OfficialStage.BREAKOUT_FAILURE
    assert not result.breakout_event


def test_rebase_forming_suppresses_breakout_hold_but_preserves_failure_priority():
    from dashboard_v2.models.schemas import OfficialStage, StageInput
    from dashboard_v2.engines.stage_engine import evaluate_official_stage

    base = StageInput(
        as_of_date=D,
        close=110,
        previous_close=109,
        candidate_pivot=120,
        candidate_valid=False,
        rebase_forming=True,
        active_breakout_pivot=100,
        breakout_cycle_active=True,
    )
    assert evaluate_official_stage(base).stage == OfficialStage.BASE
    failed = StageInput(
        as_of_date=D,
        close=99,
        previous_close=101,
        rebase_forming=True,
        active_breakout_pivot=100,
        breakout_cycle_active=True,
    )
    result = evaluate_official_stage(failed)
    assert result.stage == OfficialStage.BREAKOUT_FAILURE
    assert result.breakout_failure is True
