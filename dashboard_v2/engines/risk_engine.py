"""Initial risk, monotonic trailing-stop state, and EOD-only exit events."""
from __future__ import annotations
from dataclasses import replace
from datetime import date
from math import isfinite
from ..config.settings import DEFAULT_SETTINGS
from ..models.schemas import ExitSignal, InitialRiskResult, StopState


def _positive(value: float | None) -> bool:
    return value is not None and isfinite(float(value)) and float(value) > 0


def compute_initial_risk(
    reference_price: float | None,
    *,
    atr14: float | None,
    swing_low_10d: float | None = None,
    sma20: float | None = None,
) -> InitialRiskResult:
    """Use ATR(14)*1.5 first, then a valid 10-day swing low, then SMA20-2%."""
    if not _positive(reference_price):
        return InitialRiskResult(reference_price, None, None, None, "INVALID_REFERENCE_PRICE")
    ref = float(reference_price)
    stop: float | None = None
    source: str | None = None
    if _positive(atr14):
        stop = ref - DEFAULT_SETTINGS.initial_stop_atr_multiple * float(atr14)
        source = "ATR14_X_1_5"
    elif _positive(swing_low_10d) and float(swing_low_10d) < ref:
        stop = float(swing_low_10d)
        source = "10D_SWING_LOW_FALLBACK"
    elif _positive(sma20) and float(sma20) * 0.98 < ref:
        stop = float(sma20) * 0.98
        source = "SMA20_MINUS_2_PCT_FALLBACK"
    else:
        return InitialRiskResult(ref, None, None, None, "NO_VALID_STOP_BASIS")

    if stop >= ref or stop <= 0:
        return InitialRiskResult(ref, None, None, source, "STOP_NOT_BELOW_REFERENCE")
    risk_pct = (stop / ref - 1.0) * 100.0
    return InitialRiskResult(ref, round(stop, 6), round(risk_pct, 4), source, "OK")


def create_stop_state(entry_date: date, entry_price: float, initial_stop_price: float) -> StopState:
    """Create immutable per-fill stop state. Invalid entries fail loudly."""
    if not _positive(entry_price) or not _positive(initial_stop_price) or initial_stop_price >= entry_price:
        raise ValueError("entry_price and initial_stop_price must be positive, with stop below entry")
    return StopState(
        entry_date=entry_date,
        entry_price=float(entry_price),
        initial_stop_price=float(initial_stop_price),
        trailing_stop_price=None,
        trailing_active=False,
        effective_stop_price=float(initial_stop_price),
        highest_high_since_entry=None,
    )


def update_trailing_stop(
    state: StopState,
    *,
    close: float,
    high: float,
    atr22: float | None,
    observed_high_since_entry: float | None = None,
) -> StopState:
    """Update one fill's stop state using completed daily bar inputs only.

    Activation is close >= entry + 1R. Once activated, trailing_stop_price can
    rise or stay unchanged but is never lowered. Missing ATR prevents a new
    trailing calculation but does not erase prior stop state.
    """
    if not _positive(close) or not _positive(high) or high < close:
        raise ValueError("close/high must be positive and high must be >= close")
    high_candidates = [float(high)]
    if state.highest_high_since_entry is not None and _positive(state.highest_high_since_entry):
        high_candidates.append(float(state.highest_high_since_entry))
    # Optional deterministic high-water mark reconstructed from all completed
    # bars since this fill. This prevents missed dashboard sessions from losing
    # highs merely because the app was not opened every day.
    if _positive(observed_high_since_entry):
        high_candidates.append(float(observed_high_since_entry))
    high_water = max(high_candidates)
    risk_per_share = state.entry_price - state.initial_stop_price
    activated_now = float(close) >= state.entry_price + DEFAULT_SETTINGS.trailing_activation_r * risk_per_share
    active = state.trailing_active or activated_now
    trail = state.trailing_stop_price
    if active and _positive(atr22):
        raw = high_water - DEFAULT_SETTINGS.trailing_stop_atr_multiple * float(atr22)
        trail = raw if trail is None else max(float(trail), raw)
    effective = max(state.initial_stop_price, trail) if active and trail is not None else state.initial_stop_price
    return replace(
        state,
        trailing_stop_price=trail,
        trailing_active=active,
        effective_stop_price=float(effective),
        highest_high_since_entry=high_water,
    )


def evaluate_eod_exit_signal(state: StopState, *, as_of_date: date, close: float) -> ExitSignal:
    """Emit a signal only when a completed daily close is strictly below stop.

    This returns an informational signal only: it never submits an order or
    changes the recorded position/transaction status.
    """
    stop = state.effective_stop_price or state.initial_stop_price
    triggered = float(close) < float(stop)
    reason = None
    if triggered:
        if state.trailing_active and state.trailing_stop_price is not None and state.trailing_stop_price >= state.initial_stop_price:
            reason = "TRAILING_STOP_EOD_CLOSE_BELOW"
        else:
            reason = "INITIAL_STOP_EOD_CLOSE_BELOW"
    return ExitSignal(
        as_of_date=as_of_date,
        triggered=triggered,
        close=float(close),
        effective_stop_price=float(stop),
        reason_code=reason,
        order_submitted=False,
        position_closed=False,
    )
