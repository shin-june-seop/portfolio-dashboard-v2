"""Focused historical replay invariants for the rebase lifecycle.

These cases protect engine consistency against the bundled OHLCV fixture; they are
not independently chart-validated trading ground truth.
"""
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest

from dashboard_v2.services.chronological_replay import replay_symbol

ROOT = Path(__file__).resolve().parents[1]
REPLAY_START = "2026-06-01"


@lru_cache(maxsize=None)
def _replay(symbol: str) -> pd.DataFrame:
    source = ROOT / "data" / "regression" / "ohlcv" / f"{symbol}.csv"
    frame = pd.read_csv(source)
    return replay_symbol(symbol, frame, start_date=REPLAY_START)


def _row(symbol: str, day: str) -> pd.Series:
    result = _replay(symbol)
    matched = result.loc[result["as_of_date"].eq(day)]
    assert len(matched) == 1, f"Expected one {symbol} row on {day}"
    return matched.iloc[0]


def test_amat_valid_pattern_ends_rebase_and_later_drawdown_starts_a_new_cycle():
    # A new valid pattern closes the earlier rebase; a later candidate drawdown
    # starts a new cycle instead of carrying the old cycle's state forward.
    recovered = _row("AMAT", "2026-09-29")
    assert not bool(recovered["rebase_active"])
    assert recovered["pattern"] == "Cup Without Handle"
    assert recovered["candidate_pivot"] == pytest.approx(539.233414, abs=0.02)

    restarted = _row("AMAT", "2026-10-13")
    assert bool(restarted["rebase_active"])
    assert restarted["rebase_started_at"] == "2026-10-13"
    assert restarted["rebase_reason"] == "CANDIDATE_PIVOT_DRAWDOWN"
    assert pd.isna(restarted["candidate_pivot"])

    low = _row("AMAT", "2026-10-16")
    assert low["rebase_low"] == pytest.approx(407.260010, abs=0.02)
    assert low["rebase_low_date"] == "2026-10-16"


def test_stx_candidate_breakout_failure_and_rebase_are_distinct_events():
    candidate = _row("STX", "2026-09-21")
    assert not bool(candidate["rebase_active"])
    assert candidate["pattern"] == "Cup Without Handle"
    assert candidate["candidate_pivot"] == pytest.approx(921.041716, abs=0.02)

    breakout = _row("STX", "2026-09-23")
    assert bool(breakout["breakout_event"])
    assert breakout["stage"] == "BREAKOUT"
    assert bool(breakout["breakout_cycle_active"])

    failure = _row("STX", "2026-09-24")
    assert bool(failure["breakout_failure"])
    assert failure["stage"] == "BREAKOUT FAILURE"
    assert bool(failure["rebase_active"])
    assert failure["rebase_reason"] == "BREAKOUT_FAILURE"
    assert failure["breakout_pivot"] == pytest.approx(921.041716, abs=0.02)
    assert not bool(failure["breakout_cycle_active"])

    later_low = _row("STX", "2026-10-16")
    assert later_low["rebase_started_at"] == "2026-09-24"
    assert later_low["rebase_low"] == pytest.approx(636.359985, abs=0.02)
    assert later_low["rebase_low_date"] == "2026-10-16"


def test_active_rebase_suppresses_breakout_stage_except_failure_event():
    # The EOD failure event retains priority; otherwise an active rebase must not
    # be displayed as an ordinary BREAKOUT merely because an old breakout cycle
    # is still represented in state.
    for symbol in ("AMAT", "STX"):
        result = _replay(symbol)
        rebasing = result.loc[result["rebase_active"].astype(bool)]
        invalid = rebasing.loc[~rebasing["stage"].isin(["BASE", "BREAKOUT FAILURE"])]
        assert invalid.empty, (
            f"{symbol}: active rebase had contradictory stages: "
            f"{invalid[['as_of_date', 'stage', 'rebase_started_at']].to_dict('records')}"
        )
