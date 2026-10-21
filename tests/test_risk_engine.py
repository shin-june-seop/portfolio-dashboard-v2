from datetime import date
import pytest
from dashboard_v2.engines.risk_engine import compute_initial_risk, create_stop_state, update_trailing_stop, evaluate_eod_exit_signal

D = date(2026, 10, 19)


def test_initial_risk_uses_atr14_first():
    result = compute_initial_risk(100, atr14=4.0, swing_low_10d=95, sma20=98)
    assert result.stop_price == pytest.approx(94.0)
    assert result.source == "ATR14_X_1_5"
    assert result.risk_pct == pytest.approx(-6.0)


def test_initial_risk_falls_back_to_swing_low_then_sma20():
    swing = compute_initial_risk(100, atr14=None, swing_low_10d=92, sma20=98)
    assert swing.stop_price == 92
    assert swing.source == "10D_SWING_LOW_FALLBACK"
    sma = compute_initial_risk(100, atr14=None, swing_low_10d=100, sma20=98)
    assert sma.stop_price == pytest.approx(96.04)
    assert sma.source == "SMA20_MINUS_2_PCT_FALLBACK"
    none = compute_initial_risk(100, atr14=None, swing_low_10d=101, sma20=103)
    assert none.stop_price is None
    assert none.reason_code == "NO_VALID_STOP_BASIS"


def test_invalid_initial_risk_reference_fails_explicitly():
    result = compute_initial_risk(0, atr14=2)
    assert result.stop_price is None
    assert result.reason_code == "INVALID_REFERENCE_PRICE"


def test_trailing_stop_not_activated_by_intraday_high_alone():
    state = create_stop_state(D, 100, 94)  # 1R is 6, activation close is 106
    state = update_trailing_stop(state, close=104, high=110, atr22=2)
    assert not state.trailing_active
    assert state.trailing_stop_price is None
    assert state.effective_stop_price == 94


def test_trailing_stop_activates_at_plus_one_r_and_uses_high_water_minus_three_atr():
    state = create_stop_state(D, 100, 94)
    state = update_trailing_stop(state, close=106, high=108, atr22=2)
    assert state.trailing_active
    assert state.highest_high_since_entry == 108
    assert state.trailing_stop_price == pytest.approx(102)
    assert state.effective_stop_price == pytest.approx(102)


def test_trailing_stop_never_moves_down():
    state = create_stop_state(D, 100, 94)
    state = update_trailing_stop(state, close=110, high=115, atr22=2)
    first = state.trailing_stop_price
    state = update_trailing_stop(state, close=108, high=109, atr22=5)
    assert state.trailing_stop_price >= first
    assert state.highest_high_since_entry == 115


def test_missing_atr_does_not_erase_existing_trailing_stop():
    state = create_stop_state(D, 100, 94)
    state = update_trailing_stop(state, close=110, high=115, atr22=2)
    old = state.trailing_stop_price
    state = update_trailing_stop(state, close=109, high=110, atr22=None)
    assert state.trailing_stop_price == old
    assert state.effective_stop_price == old


def test_eod_exit_signal_below_stop_but_no_order_or_position_mutation():
    state = create_stop_state(D, 100, 94)
    state = update_trailing_stop(state, close=110, high=115, atr22=2)
    signal = evaluate_eod_exit_signal(state, as_of_date=D, close=104)
    assert signal.triggered
    assert signal.reason_code == "TRAILING_STOP_EOD_CLOSE_BELOW"
    assert signal.order_submitted is False
    assert signal.position_closed is False


def test_close_equal_to_effective_stop_does_not_trigger_exit():
    state = create_stop_state(D, 100, 94)
    signal = evaluate_eod_exit_signal(state, as_of_date=D, close=94)
    assert not signal.triggered


def test_high_watermark_can_be_reconstructed_after_missed_dashboard_sessions():
    state = create_stop_state(D, 100, 94)
    state = update_trailing_stop(state, close=110, high=112, atr22=2, observed_high_since_entry=125)
    assert state.highest_high_since_entry == 125
    assert state.trailing_stop_price == pytest.approx(119)
