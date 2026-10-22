"""Guarded recovery of historical breakout pivots after the EOD OHLC-basis change."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from dashboard_v2.services import screening_service as screening

ROOT = Path(__file__).resolve().parents[1]


def _stx_raw_basis_fixture() -> pd.DataFrame:
    """Convert the legacy adjusted-OHLC fixture to an approximate raw OHLC basis.

    The true historical Yahoo values around the reviewed event are also pinned to
    the observed OHLC values so this test has a stable crossing fixture independent
    of small retrospective adjustment-factor changes.
    """
    adjusted = pd.read_csv(ROOT / "data" / "regression" / "ohlcv" / "STX.csv")
    adjusted["Date"] = pd.to_datetime(adjusted["Date"])
    for idx, row in adjusted.iterrows():
        multiplier = 1.00326 if row["Date"] < pd.Timestamp("2026-03-25") else 1.001515
        for column in ("Open", "High", "Low", "Close"):
            adjusted.at[idx, column] = float(row[column]) * multiplier

    # Pin the documented current-basis bars used by the May 2026 event check.
    # The current detector must form the pivot on May 26; May 27 closes above it.
    pinned = {
        "2026-05-26": {"Open": 831.17, "High": 862.65, "Low": 816.98, "Close": 845.76},
        "2026-05-27": {"Open": 881.14, "High": 888.95, "Low": 846.07, "Close": 870.66},
    }
    for day, values in pinned.items():
        mask = adjusted["Date"].eq(pd.Timestamp(day))
        assert mask.any(), f"STX fixture missing reviewed session {day}"
        for column, value in values.items():
            adjusted.loc[mask, column] = value

    # Keep a separate adjusted close column solely for the return-series contract.
    adjusted["Adj Close"] = pd.to_numeric(pd.read_csv(ROOT / "data" / "regression" / "ohlcv" / "STX.csv")["Close"])
    return adjusted


def test_stx_may_breakout_is_revalidated_on_current_ohlc_basis():
    bars = _stx_raw_basis_fixture()
    events = screening._revalidate_seed_breakout_events("STX", bars)
    assert events
    latest = events[-1]
    assert latest["event_date"] == "2026-05-27"
    assert latest["pattern"] == "High Tight Flag"
    assert abs(latest["candidate_pivot"] - 862.65) < 0.05
    assert latest["previous_close"] <= latest["candidate_pivot"] < latest["event_close"]




def test_stx_seed_pattern_label_change_does_not_erase_current_basis_cross(tmp_path):
    """The old label is a hint; the current primary pattern and EOD cross govern."""
    bars = _stx_raw_basis_fixture()
    seed = pd.read_csv(ROOT / "data" / "regression" / "chronological_replay.csv")
    mask = (seed["ticker"].astype(str).str.upper() == "STX") & (seed["as_of_date"].astype(str) == "2026-05-27")
    assert mask.any()
    seed.loc[mask, "pattern"] = "Cup & Handle"  # intentionally stale label
    seed_path = tmp_path / "seed_with_stale_pattern_label.csv"
    seed.to_csv(seed_path, index=False)

    events = screening._revalidate_seed_breakout_events("STX", bars, seed_path=seed_path)
    may_event = [event for event in events if event["event_date"] == "2026-05-27"]
    assert may_event
    assert may_event[-1]["pattern"] == "High Tight Flag"
    assert may_event[-1]["seed_pattern"] == "Cup & Handle"
    assert may_event[-1]["seed_pattern_matches_current"] is False
    assert abs(may_event[-1]["candidate_pivot"] - 862.65) < 0.05


def test_stx_seed_event_is_not_recovered_without_current_basis_close_cross():
    bars = _stx_raw_basis_fixture()
    mask = bars["Date"].eq(pd.Timestamp("2026-05-27"))
    assert mask.any()
    # Keep OHLC valid but set the breakout day's close below the freshly detected pivot.
    bars.loc[mask, "Open"] = 849.0
    bars.loc[mask, "Close"] = 850.0
    bars.loc[mask, "High"] = 855.0
    bars.loc[mask, "Low"] = 846.07
    attempts = []
    events = screening._revalidate_seed_breakout_events("STX", bars, attempts=attempts)
    assert not any(event["event_date"] == "2026-05-27" for event in events)
    may_attempt = [item for item in attempts if item.get("event_date") == "2026-05-27"]
    assert may_attempt
    assert may_attempt[-1]["reason"] == "NO_CURRENT_BASIS_UPWARD_CLOSE_CROSS"


def test_analyze_symbol_recovers_only_pivot_metadata_and_keeps_rebase_state(monkeypatch):
    bars = _stx_raw_basis_fixture()
    latest = pd.Timestamp(bars["Date"].max()).date().isoformat()
    fake_replay_state = {
        "ticker": "STX",
        "as_of_date": latest,
        "pattern": "BASE FORMING / NO VALID PATTERN",
        "pattern_start_date": None,
        "candidate_pivot": None,
        "breakout_pivot": None,
        "breakout_date": None,
        "breakout_cycle_active": False,
        "stage": "BASE",
        "rebase_active": True,
        "rebase_started_at": "2026-10-02",
        "rebase_low": 636.36,
        "rebase_low_date": "2026-10-16",
        "rebase_reason": "CANDIDATE_PIVOT_DRAWDOWN",
        "rebase_secondary_reasons": "20D_HIGH_DRAWDOWN",
        "breakout_event": False,
        "breakout_failure": False,
        "validation_status": "OBSERVED_NOT_VALIDATED",
    }
    monkeypatch.setattr(
        screening,
        "_history_snapshot",
        lambda *args, **kwargs: (dict(fake_replay_state), "ROLLING_300_BAR_REPLAY"),
    )

    result = screening.analyze_symbol("STX", bars)

    assert abs(result["breakout_pivot"] - 862.65) < 0.05
    assert result["breakout_date"] == "2026-05-27"
    assert result["historical_breakout_recovered"] is True
    assert result["historical_breakout_recovery_pattern"] == "High Tight Flag"
    assert result["historical_breakout_recovery_event_date"] == "2026-05-27"
    assert result["historical_breakout_recovery_reason"] == "SEED_EVENT_REVALIDATED_ON_CURRENT_OHLC"
    # Do not resurrect the old cycle or current pattern/candidate state.
    assert result["rebase_active"] is True
    assert result["rebase_started_at"] == "2026-10-02"
    assert result["candidate_pivot"] is None
    assert result["pattern"] == "BASE FORMING / NO VALID PATTERN"
    assert result["breakout_cycle_active"] is False


def test_reconciliation_does_not_replace_a_newer_current_basis_breakout():
    bars = _stx_raw_basis_fixture()
    latest = pd.Timestamp(bars["Date"].max()).date().isoformat()
    current_state = {
        "as_of_date": latest,
        "breakout_pivot": 950.0,
        "breakout_date": "2026-06-30",
        "candidate_pivot": None,
        "rebase_active": True,
        "rebase_started_at": "2026-10-02",
        "breakout_cycle_active": False,
        "stage": "BASE",
    }
    result, diagnostic = screening._reconcile_historical_breakout(
        "STX", bars, current_state, "ROLLING_300_BAR_REPLAY"
    )
    assert result["breakout_pivot"] == 950.0
    assert result["breakout_date"] == "2026-06-30"
    assert result["rebase_active"] is True
    assert result["breakout_cycle_active"] is False
    assert diagnostic["historical_breakout_recovered"] is False
    assert diagnostic["historical_breakout_recovery_reason"] == "CURRENT_REPLAY_HAS_NEWER_BREAKOUT"
