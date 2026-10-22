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


def test_stx_spiky_cup_candidate_is_rejected_before_it_creates_a_false_breakout_cycle():
    # Manual chart review found a material interior spike and an ~18% pullback on
    # the right-side recovery path. Neither cup detector should create the old
    # Sep-21 candidate or the consequent Sep-23 breakout / Sep-24 failure events.
    for day in (
        "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
        "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30",
    ):
        row = _row("STX", day)
        assert bool(row["rebase_active"])
        assert row["pattern"] == "BASE FORMING / NO VALID PATTERN"
        assert pd.isna(row["candidate_pivot"])
        assert not bool(row["breakout_event"])
        assert not bool(row["breakout_failure"])
        assert not bool(row["breakout_cycle_active"])


def test_bundled_seed_reflects_corrected_stx_pattern_history():
    # The app starts incremental replay from this file, so update the seed after
    # rejecting the historical false cup; otherwise old pivot/cycle state persists.
    from dashboard_v2.services.screening_service import load_seed_snapshots

    seed = load_seed_snapshots().get("STX")
    assert seed is not None
    assert pd.Timestamp(seed["as_of_date"]).date().isoformat() == "2026-10-19"
    assert seed["breakout_pivot"] == pytest.approx(861.344931, abs=0.02)
    assert seed["breakout_date"] == "2026-05-27"
    assert bool(seed["rebase_active"])
    assert seed["rebase_started_at"] == "2026-10-02"
    assert seed["rebase_reason"] == "CANDIDATE_PIVOT_DRAWDOWN"
    assert pd.isna(seed["candidate_pivot"])
    assert not bool(seed["breakout_cycle_active"])


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
