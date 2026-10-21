"""Canonical official EOD stage state machine. No UI or database dependencies."""
from __future__ import annotations
from ..config.settings import DEFAULT_SETTINGS
from ..models.schemas import OfficialStage, StageInput, StageResult
from .pivot_engine import is_pivot_approach


def _crossed_up(previous_close: float | None, close: float, pivot: float | None) -> bool:
    return previous_close is not None and pivot is not None and previous_close <= pivot < close


def evaluate_official_stage(item: StageInput) -> StageResult:
    """Evaluate one completed daily bar; callers persist returned events idempotently."""
    # Active cycle failure is top priority and strictly EOD-close based.
    if item.breakout_cycle_active and item.active_breakout_pivot is not None and item.close < item.active_breakout_pivot:
        return StageResult(
            as_of_date=item.as_of_date,
            stage=OfficialStage.BREAKOUT_FAILURE,
            reason_codes=("EOD_CLOSE_BELOW_ACTIVE_BREAKOUT_PIVOT", "START_REBASE"),
            breakout_failure=True,
        )

    # Rebase invalidates the active candidate setup. The historical Breakout Pivot
    # is retained as a reference, but the screen returns to BASE until a new
    # post-rebase pattern is valid. An EOD close below the active Breakout Pivot
    # was already handled above and remains the highest-priority failure event.
    if item.rebase_forming:
        return StageResult(item.as_of_date, OfficialStage.BASE, ("REBASE_FORMING",))

    # A later valid Next Pivot can create a new breakout event in an existing cycle.
    if item.breakout_cycle_active and item.next_pivot_valid and _crossed_up(item.previous_close, item.close, item.next_pivot):
        confirmed = item.eod_rvol is not None and item.eod_rvol >= DEFAULT_SETTINGS.breakout_confirmed_min_rvol
        return StageResult(
            as_of_date=item.as_of_date,
            stage=OfficialStage.BREAKOUT_CONFIRMED if confirmed else OfficialStage.BREAKOUT,
            reason_codes=("NEXT_PIVOT_CROSSED_UP", "EOD_RVOL_CONFIRMED" if confirmed else "EOD_RVOL_BELOW_CONFIRM_THRESHOLD"),
            breakout_event=True,
            breakout_event_pivot=float(item.next_pivot),
            breakout_confirmed=confirmed,
            activate_breakout_pivot=float(item.next_pivot),
        )

    # Existing breakout remains a state after the event day. High volume cannot
    # repeat BREAKOUT CONFIRMED without a new valid pivot cross.
    if item.breakout_cycle_active and item.active_breakout_pivot is not None and item.close > item.active_breakout_pivot:
        return StageResult(item.as_of_date, OfficialStage.BREAKOUT, ("ACTIVE_BREAKOUT_PIVOT_HELD",))

    # Do not issue a candidate-pivot stage while rebase is active or pattern is invalid.
    if item.rebase_forming or not item.candidate_valid or item.candidate_pivot is None:
        return StageResult(item.as_of_date, OfficialStage.BASE, ("REBASE_FORMING" if item.rebase_forming else "NO_VALID_CANDIDATE_PIVOT",))

    if _crossed_up(item.previous_close, item.close, item.candidate_pivot):
        confirmed = item.eod_rvol is not None and item.eod_rvol >= DEFAULT_SETTINGS.breakout_confirmed_min_rvol
        return StageResult(
            as_of_date=item.as_of_date,
            stage=OfficialStage.BREAKOUT_CONFIRMED if confirmed else OfficialStage.BREAKOUT,
            reason_codes=("CANDIDATE_PIVOT_CROSSED_UP", "EOD_RVOL_CONFIRMED" if confirmed else "EOD_RVOL_BELOW_CONFIRM_THRESHOLD"),
            breakout_event=True,
            breakout_event_pivot=float(item.candidate_pivot),
            breakout_confirmed=confirmed,
            activate_breakout_pivot=float(item.candidate_pivot),
        )

    if is_pivot_approach(item.close, item.candidate_pivot, candidate_valid=item.candidate_valid, rebase_forming=item.rebase_forming):
        return StageResult(item.as_of_date, OfficialStage.PIVOT_APPROACH, ("CANDIDATE_DISTANCE_BETWEEN_MINUS_5_AND_0",))

    if item.base_context_valid and item.pocket_pivot_valid:
        return StageResult(item.as_of_date, OfficialStage.BASE_PLUS_PP, ("VALID_PP_WITH_BASE_CONTEXT",))
    return StageResult(item.as_of_date, OfficialStage.BASE, ("NO_STAGE_GATE_PASSED",))
