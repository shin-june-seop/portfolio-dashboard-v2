from pathlib import Path
import pandas as pd

from dashboard_v2.services.screening_service import analyze_symbol, load_seed_snapshots


def _bars(n=70):
    dates = pd.bdate_range("2026-01-01", periods=n)
    close = [100 + i * 0.15 for i in range(n)]
    return pd.DataFrame({
        "Date": dates,
        "Open": [x - 0.4 for x in close],
        "High": [x + 1.0 for x in close],
        "Low": [x - 1.0 for x in close],
        "Close": close,
        "Volume": [100000 + i * 100 for i in range(n)],
    })


def test_analyze_symbol_returns_shared_screen_contract():
    result = analyze_symbol("TEST", _bars())
    for key in (
        "ticker", "as_of_date", "pattern", "candidate_pivot", "breakout_pivot",
        "candidate_distance_pct", "breakout_distance_pct", "twenty_day_reference",
        "stage", "rebase_active", "rvol", "replay_provenance", "bars",
    ):
        assert key in result
    assert result["ticker"] == "TEST"
    assert result["replay_provenance"] == "ROLLING_260_BAR_REPLAY"
    assert result["twenty_day_reference"] is not None


def test_seed_snapshot_loader_returns_latest_row_per_symbol():
    root = Path(__file__).resolve().parents[1]
    snapshots = load_seed_snapshots(root / "data" / "regression" / "chronological_replay.csv")
    assert {"AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX"}.issubset(snapshots)
    assert snapshots["AMAT"]["as_of_date"] is not None


def test_rebase_snapshot_cannot_display_breakout_stage():
    bars = _bars()
    seed = {
        "ticker": "TEST",
        "as_of_date": bars.iloc[-1]["Date"].date().isoformat(),
        "close": float(bars.iloc[-1]["Close"]),
        "pattern": "OLD PATTERN",
        "candidate_pivot": None,
        "breakout_pivot": 90.0,
        "breakout_date": "2026-01-15",
        "breakout_cycle_active": True,
        "stage": "BREAKOUT",
        "rebase_active": True,
        "rebase_started_at": "2026-03-01",
        "rebase_low": 95.0,
        "rebase_low_date": "2026-03-02",
        "rebase_reason": "20D_HIGH_DRAWDOWN",
        "rebase_secondary_reasons": "",
        "breakout_event": False,
        "breakout_failure": False,
        "validation_status": "OBSERVED_NOT_VALIDATED",
    }
    result = analyze_symbol("TEST", bars, seed_snapshot=seed)
    assert result["rebase_active"] is True
    assert result["pattern"] == "BASE FORMING / NO VALID PATTERN"
    assert result["candidate_pivot"] is None
    assert result["stage"] == "BASE"
    assert result["breakout_pivot"] == 90.0


def test_analyze_symbol_resumes_after_bundled_snapshot_when_new_bar_arrives():
    root = Path(__file__).resolve().parents[1]
    seed_map = load_seed_snapshots(root / "data" / "regression" / "chronological_replay.csv")
    bars = pd.read_csv(root / "data" / "regression" / "ohlcv" / "AMAT.csv")
    last = bars.iloc[-1].copy()
    next_day = pd.Timestamp(last["Date"]) + pd.offsets.BDay(1)
    new_close = float(last["Close"]) * 1.005
    appended = pd.DataFrame([{
        "Date": next_day,
        "Open": new_close * 0.998,
        "High": new_close * 1.01,
        "Low": new_close * 0.99,
        "Close": new_close,
        "Volume": float(last["Volume"]),
    }])
    bars = pd.concat([bars, appended], ignore_index=True)
    result = analyze_symbol("AMAT", bars, seed_snapshot=seed_map["AMAT"])
    assert result["as_of_date"] == pd.Timestamp(next_day).date().isoformat()
    assert result["replay_provenance"] == "INCREMENTAL_FROM_BUNDLED_SNAPSHOT"
    assert result["rebase_active"] is True
    assert result["candidate_pivot"] is None
    assert result["stage"] == "BASE"
    assert result["breakout_pivot"] == seed_map["AMAT"]["breakout_pivot"]
