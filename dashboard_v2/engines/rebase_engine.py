"""Reset-aware rebase state machine; previous candidate and active breakout are inputs."""
from __future__ import annotations
from datetime import date
import pandas as pd
from .data_validation import normalize_ohlcv
from ..models.schemas import RebaseState


def update_rebase_state(
    frame: pd.DataFrame,
    *,
    as_of_date: date | str,
    close: float,
    previous_candidate_pivot: float | None,
    active_breakout_pivot: float | None,
    breakout_cycle_active: bool,
    previous_state: RebaseState | None = None,
    valid_new_pattern: bool = False,
) -> RebaseState:
    """Evaluate RB-01/02/03 and preserve a single cycle until valid pattern found.

    `previous_candidate_pivot` must be the candidate that was active before the
    current day's pattern scan, preventing circular comparison to a new pivot.
    The caller's input history must contain completed bars only.
    """
    df = normalize_ohlcv(frame, as_of_date=as_of_date)
    target_date = pd.Timestamp(as_of_date).date()
    is_rebasing = bool(previous_state and previous_state.active)

    # A valid new pattern ends the active rebase cycle. RB-01 is commonly still
    # true throughout a prolonged drawdown because the rolling 20-day high remains
    # above price; treating that persistent condition as a fresh trigger would make
    # the cycle impossible to close. Only a same-day official breakout failure or
    # a drawdown against a still-active prior candidate can retain trigger priority.
    if is_rebasing and valid_new_pattern:
        current_trigger_reasons = _trigger_reasons(
            df,
            close=close,
            previous_candidate_pivot=previous_candidate_pivot,
            active_breakout_pivot=active_breakout_pivot,
            breakout_cycle_active=breakout_cycle_active,
        )
        blocking_reasons = {"BREAKOUT_FAILURE", "CANDIDATE_PIVOT_DRAWDOWN"}
        if not blocking_reasons.intersection(current_trigger_reasons):
            return RebaseState(active=False)

    trigger_reasons = _trigger_reasons(
        df,
        close=close,
        previous_candidate_pivot=previous_candidate_pivot,
        active_breakout_pivot=active_breakout_pivot,
        breakout_cycle_active=breakout_cycle_active,
    )

    # Existing cycle remains stable even if price bounces above a threshold.
    if is_rebasing:
        start_date = previous_state.started_at
        if start_date is None:
            start_date = target_date
        # Carry the cycle low forward incrementally. `previous_state.low` already
        # represents the minimum low through the previous completed bar, so rescanning
        # the full cycle on every EOD replay is unnecessary and quadratic.
        prior_low = previous_state.low if previous_state is not None else None
        prior_low_date = previous_state.low_date if previous_state is not None else None
        current_low = float(df.iloc[-1]["Low"])
        if prior_low is None or current_low < float(prior_low):
            low, low_date = current_low, target_date
        else:
            low, low_date = float(prior_low), prior_low_date or target_date
        secondary = tuple(r for r in trigger_reasons if r != previous_state.primary_reason)
        return RebaseState(
            active=True,
            started_at=start_date,
            low=low,
            low_date=low_date,
            primary_reason=previous_state.primary_reason or (trigger_reasons[0] if trigger_reasons else "REBASE_IN_PROGRESS"),
            secondary_reasons=tuple(dict.fromkeys((*previous_state.secondary_reasons, *secondary))),
        )

    if not trigger_reasons:
        return RebaseState(active=False)

    # Precedence: official Breakout Failure, then Candidate Pivot drawdown,
    # then rolling 20-day-high drawdown. Preserve all other same-day reasons.
    priority = {"BREAKOUT_FAILURE": 0, "CANDIDATE_PIVOT_DRAWDOWN": 1, "20D_HIGH_DRAWDOWN": 2}
    reasons = sorted(set(trigger_reasons), key=lambda r: priority[r])
    start_date = target_date
    cycle_bars = df.loc[df["Date"] >= pd.Timestamp(start_date)]
    if cycle_bars.empty:
        low, low_date = float(close), target_date
    else:
        idx = cycle_bars["Low"].idxmin()
        low, low_date = float(cycle_bars.loc[idx, "Low"]), cycle_bars.loc[idx, "Date"].date()
    return RebaseState(
        active=True,
        started_at=start_date,
        low=low,
        low_date=low_date,
        primary_reason=reasons[0],
        secondary_reasons=tuple(reasons[1:]),
    )


def _trigger_reasons(
    df: pd.DataFrame,
    *,
    close: float,
    previous_candidate_pivot: float | None,
    active_breakout_pivot: float | None,
    breakout_cycle_active: bool,
) -> list[str]:
    reasons: list[str] = []
    if breakout_cycle_active and active_breakout_pivot is not None and close < float(active_breakout_pivot):
        reasons.append("BREAKOUT_FAILURE")
    if previous_candidate_pivot is not None and previous_candidate_pivot > 0 and close <= float(previous_candidate_pivot) * 0.90:
        reasons.append("CANDIDATE_PIVOT_DRAWDOWN")
    if len(df) >= 20:
        rolling_20d_high = float(df["High"].tail(20).max())
        if rolling_20d_high > 0 and close <= rolling_20d_high * 0.90:
            reasons.append("20D_HIGH_DRAWDOWN")
    return reasons
