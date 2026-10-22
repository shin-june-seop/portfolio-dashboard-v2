"""Chronological EOD replay for rebase, candidate pivot and breakout state.

This is a deterministic validation service, not a live-data provider. It consumes
completed daily OHLCV bars in ascending date order and never uses future bars.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import date
from typing import Callable, Any

import pandas as pd

from ..engines.data_validation import normalize_ohlcv
from ..engines.pattern_detectors import detect_primary_pattern
from ..engines.rebase_engine import update_rebase_state
from ..engines.stage_engine import evaluate_official_stage
from ..models.schemas import PatternCandidate, RebaseState, StageInput

PatternDetector = Callable[..., PatternCandidate | None]


def replay_symbol(
    symbol: str,
    raw_frame: pd.DataFrame,
    *,
    pattern_detector: PatternDetector = detect_primary_pattern,
    initial_candidate_pivot: float | None = None,
    initial_breakout_pivot: float | None = None,
    initial_breakout_date: date | str | None = None,
    initial_breakout_active: bool = False,
    start_date: date | str | None = None,
    resume_snapshot: dict[str, Any] | None = None,
    process_from_date: date | str | None = None,
) -> pd.DataFrame:
    """Replay each completed bar, preserving independent candidate/breakout state.

    Initial state is optional and must come from a real prior snapshot/journal.
    If omitted, the replay does not invent a historical breakout event.
    """
    bars = normalize_ohlcv(raw_frame)
    if start_date is not None:
        bars = bars.loc[bars["Date"] >= pd.Timestamp(start_date)].reset_index(drop=True)
    if bars.empty:
        return pd.DataFrame(columns=["ticker", "as_of_date", "pattern", "candidate_pivot", "breakout_pivot", "stage", "rebase_active", "rebase_started_at", "rebase_reason", "breakout_event", "breakout_failure", "validation_status"])

    candidate_pivot = initial_candidate_pivot
    breakout_pivot = initial_breakout_pivot
    breakout_date = pd.Timestamp(initial_breakout_date).date() if initial_breakout_date else None
    breakout_active = bool(initial_breakout_active and breakout_pivot is not None)
    rebase_state = RebaseState(active=False)
    previous_close: float | None = None
    process_cutoff = pd.Timestamp(process_from_date).date() if process_from_date is not None else None

    def _optional_float(value):
        try:
            return float(value) if value is not None and pd.notna(value) else None
        except (TypeError, ValueError):
            return None

    if resume_snapshot is not None:
        candidate_pivot = _optional_float(resume_snapshot.get("candidate_pivot"))
        breakout_pivot = _optional_float(resume_snapshot.get("breakout_pivot"))
        breakout_date = pd.Timestamp(resume_snapshot["breakout_date"]).date() if resume_snapshot.get("breakout_date") and pd.notna(resume_snapshot.get("breakout_date")) else None
        breakout_active = bool(resume_snapshot.get("breakout_cycle_active", False)) and breakout_pivot is not None
        previous_close = _optional_float(resume_snapshot.get("close"))
        started = resume_snapshot.get("rebase_started_at")
        low_date = resume_snapshot.get("rebase_low_date")
        rebase_state = RebaseState(
            active=bool(resume_snapshot.get("rebase_active", False)),
            started_at=pd.Timestamp(started).date() if started is not None and pd.notna(started) else None,
            low=_optional_float(resume_snapshot.get("rebase_low")),
            low_date=pd.Timestamp(low_date).date() if low_date is not None and pd.notna(low_date) else None,
            primary_reason=(str(resume_snapshot.get("rebase_reason")) if resume_snapshot.get("rebase_reason") and pd.notna(resume_snapshot.get("rebase_reason")) else None),
            secondary_reasons=tuple(str(resume_snapshot.get("rebase_secondary_reasons", "") or "").split("|")) if resume_snapshot.get("rebase_secondary_reasons") and pd.notna(resume_snapshot.get("rebase_secondary_reasons")) else (),
        )
        # A rebase closes the old breakout *cycle*, but does not erase the
        # historical Breakout Pivot. Older snapshots may contain the legacy
        # contradictory combination rebase_active=True / breakout_cycle_active=True;
        # normalize that combination when resuming replay.
        if rebase_state.active:
            breakout_active = False

    rows: list[dict[str, Any]] = []

    for idx in range(len(bars)):
        history = bars.iloc[:idx + 1].copy().reset_index(drop=True)
        bar = history.iloc[-1]
        asof = bar["Date"].date()
        if process_cutoff is not None and asof <= process_cutoff:
            continue
        close = float(bar["Close"])
        prior_candidate = candidate_pivot
        prior_breakout = breakout_pivot
        prior_breakout_active = breakout_active

        # RB-01/02/03 are evaluated against state carried from the prior bar.
        # RB-01 only needs the latest 20 completed bars; the active cycle low is
        # carried incrementally by the state machine, so passing the full prefix is
        # both unnecessary and a major historical-replay performance penalty.
        trigger_history = history.tail(20).reset_index(drop=True)
        updated_rebase = update_rebase_state(
            trigger_history, as_of_date=asof, close=close,
            previous_candidate_pivot=prior_candidate,
            active_breakout_pivot=prior_breakout,
            breakout_cycle_active=prior_breakout_active,
            previous_state=rebase_state,
            valid_new_pattern=False,
        )

        # Pattern scan is restricted to bars available as of this date. During a
        # rebase, detector structures must begin on/after the fixed cycle start.
        # Pattern detectors have bounded formation lengths (max 100 bars, with a
        # pre-base lookback / HTF pole). Restrict their scan window to keep
        # a 5-year replay practical; state triggers and low tracking still use the
        # complete history. This also prevents each daily scan from re-evaluating
        # thousands of irrelevant older bars.
        detector_history = history.tail(120).reset_index(drop=True)
        detector_breakout_date = breakout_date
        if detector_breakout_date is not None and detector_breakout_date < detector_history["Date"].iloc[0].date():
            # The first breakout event is outside the scan window. Do not let the
            # detector misinterpret a truncated window as containing that event.
            detector_breakout_date = None
        candidate = pattern_detector(
            detector_history,
            rebase_started_at=updated_rebase.started_at if updated_rebase.active else None,
            prior_breakout_pivot=prior_breakout,
            prior_breakout_date=detector_breakout_date,
        )
        valid_candidate = bool(candidate and candidate.valid and candidate.candidate_pivot and candidate.pattern_start_date)

        # A valid new-cycle structure ends a rebase only when no fresh trigger is
        # present today; the rebase engine owns that precedence decision.
        if updated_rebase.active and valid_candidate:
            updated_rebase = update_rebase_state(
                trigger_history, as_of_date=asof, close=close,
                previous_candidate_pivot=prior_candidate,
                active_breakout_pivot=prior_breakout,
                breakout_cycle_active=prior_breakout_active,
                previous_state=updated_rebase,
                valid_new_pattern=True,
            )
            # If the cycle ended, this candidate is allowed to become active.
            # If a new trigger kept it active, it remains diagnostic only.
            valid_candidate = not updated_rebase.active

        if updated_rebase.active:
            active_candidate_pivot = None
            pattern_name = "BASE FORMING / NO VALID PATTERN"
            candidate_start = None
            candidate_source = "REBASE_FORMING"
        elif valid_candidate:
            active_candidate_pivot = float(candidate.candidate_pivot)
            pattern_name = candidate.pattern_name
            candidate_start = candidate.pattern_start_date
            candidate_source = candidate.candidate_pivot_source
        else:
            active_candidate_pivot = None
            pattern_name = "BASE FORMING / NO VALID PATTERN"
            candidate_start = None
            candidate_source = None

        # Use detector output only when it is valid for the current state. A
        # historical Breakout Pivot is never replaced by a newly scanned pivot.
        stage = evaluate_official_stage(StageInput(
            as_of_date=asof,
            close=close,
            previous_close=previous_close,
            candidate_pivot=active_candidate_pivot,
            candidate_valid=active_candidate_pivot is not None,
            candidate_source=candidate_source,
            rebase_forming=updated_rebase.active,
            active_breakout_pivot=breakout_pivot,
            breakout_cycle_active=breakout_active,
            next_pivot=active_candidate_pivot,
            next_pivot_valid=active_candidate_pivot is not None,
        ))

        if stage.breakout_failure:
            # Preserve the historical pivot as a record, but close its active cycle.
            breakout_active = False
            rebase_state = updated_rebase
            # Failure invalidates the current candidate even if a detector found one.
            candidate_pivot = None
            pattern_name = "BASE FORMING / NO VALID PATTERN"
            candidate_start = None
            candidate_source = "REBASE_FORMING"
            active_candidate_pivot = None
        else:
            rebase_state = updated_rebase
            candidate_pivot = active_candidate_pivot
            if stage.breakout_event:
                # The Breakout Pivot changes only on an actual upward cross of a
                # valid candidate pivot. Until then, keep the last historical value.
                breakout_pivot = float(stage.activate_breakout_pivot)
                breakout_date = asof
                breakout_active = True
            elif updated_rebase.active:
                # Invalidate the prior breakout cycle as soon as a rebase is active,
                # while retaining breakout_pivot / breakout_date as historical data.
                # A subsequent candidate breakout starts a new cycle and replaces
                # the displayed Breakout Pivot at that event only.
                breakout_active = False

        rows.append({
            "ticker": symbol.upper(),
            "as_of_date": asof.isoformat(),
            "open": float(bar["Open"]), "high": float(bar["High"]),
            "low": float(bar["Low"]), "close": close, "volume": int(bar["Volume"]),
            "pattern": pattern_name,
            "pattern_start_date": candidate_start.isoformat() if candidate_start else None,
            "candidate_pivot": candidate_pivot,
            "breakout_pivot": breakout_pivot,
            "breakout_date": breakout_date.isoformat() if breakout_date else None,
            "breakout_cycle_active": breakout_active,
            "stage": stage.stage.value,
            "rebase_active": rebase_state.active,
            "rebase_started_at": rebase_state.started_at.isoformat() if rebase_state.started_at else None,
            "rebase_low": rebase_state.low,
            "rebase_low_date": rebase_state.low_date.isoformat() if rebase_state.low_date else None,
            "rebase_reason": rebase_state.primary_reason,
            "rebase_secondary_reasons": "|".join(rebase_state.secondary_reasons),
            "breakout_event": stage.breakout_event,
            "breakout_failure": stage.breakout_failure,
            "prior_candidate_pivot": prior_candidate,
            "prior_breakout_pivot": prior_breakout,
            "validation_status": "OBSERVED_NOT_VALIDATED",
        })
        previous_close = close

    return pd.DataFrame(rows)
