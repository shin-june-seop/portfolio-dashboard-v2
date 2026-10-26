from __future__ import annotations
from datetime import date
import pandas as pd
import pytest

from dashboard_v2.services.pp_journal_repository import (
    PPJournalValidationError,
    normalize_pp_signal,
    _normalize_entry,
    add_pp_trade_exit,
    save_pp_signals,
)
from dashboard_v2.services.pp_journal_analytics import summarize_trade, summarize_trades, analyze_pp_signal_outcome


def test_signal_validation_normalizes_ticker_and_keeps_snapshot_fields():
    row = normalize_pp_signal({
        "ticker": " nvda ", "signal_date": "2026-10-22", "signal_price": 180,
        "signal_volume": 5_000_000, "max_prior_down_day_volume": 4_000_000,
        "reference_ma": "MA10", "pattern_name": "Flat Base", "entry_stage": "BASE + PP",
        "candidate_pivot": 185, "breakout_pivot": None, "context_valid": True,
    }, user_id="user-a")
    assert row["ticker"] == "NVDA"
    assert row["signal_date"] == "2026-10-22"
    assert row["candidate_pivot"] == 185.0
    assert row["user_id"] == "user-a"


def test_signal_validation_fails_closed_on_invalid_signal_or_reference_ma():
    base = {"ticker": "NVDA", "signal_date": "2026-10-22", "signal_price": 180,
            "signal_volume": 100, "max_prior_down_day_volume": 90}
    with pytest.raises(PPJournalValidationError, match="유효하지 않은 PP"):
        normalize_pp_signal({**base, "valid_signal": False}, user_id="u")
    with pytest.raises(PPJournalValidationError, match="참조 이동평균선"):
        normalize_pp_signal({**base, "reference_ma": "MA20"}, user_id="u")


def test_entry_validation_requires_positive_price_and_stop_below_entry():
    with pytest.raises(PPJournalValidationError, match="초기 손절선"):
        _normalize_entry(entry_date="2026-10-22", entry_price=100, shares=2, initial_stop_price=101)
    with pytest.raises(PPJournalValidationError, match="0보다 커야"):
        _normalize_entry(entry_date="2026-10-22", entry_price=100, shares=0)


def test_partial_exit_keeps_position_partial_and_fifo_pnl():
    trade = {"id": "t1", "ticker": "NVDA", "initial_entry_date": "2026-01-01",
             "initial_entry_price": 100, "initial_shares": 100,
             "initial_stop_price": 90, "initial_entry_stage": "BASE + PP", "created_at": "2026-01-01T09:00:00Z"}
    adds = [{"trade_id": "t1", "entry_date": "2026-01-05", "entry_price": 120, "shares": 50,
             "initial_stop_price": 108, "created_at": "2026-01-05T09:00:00Z"}]
    exits = [{"trade_id": "t1", "exit_date": "2026-01-10", "exit_price": 130, "shares": 90,
              "created_at": "2026-01-10T09:00:00Z"}]
    result = summarize_trade(trade, adds, exits)
    assert result["status"] == "PARTIAL"
    assert result["total_entered_shares"] == 150
    assert result["total_exited_shares"] == 90
    assert result["remaining_shares"] == 60
    # FIFO: first 90 shares are from the initial $100 lot.
    assert result["realized_pnl"] == pytest.approx(2700)
    assert result["realized_return_pct"] == pytest.approx(30)


def test_full_exit_marks_closed_only_when_all_shares_sold():
    trade = {"id": "t1", "ticker": "META", "initial_entry_date": "2026-01-01",
             "initial_entry_price": 100, "initial_shares": 10, "initial_entry_stage": "BASE", "created_at": "a"}
    exits = [{"trade_id": "t1", "exit_date": "2026-01-03", "exit_price": 90, "shares": 9, "created_at": "b"}]
    partial = summarize_trade(trade, [], exits)
    assert partial["status"] == "PARTIAL"
    assert partial["remaining_shares"] == pytest.approx(1)
    exits.append({"trade_id": "t1", "exit_date": "2026-01-04", "exit_price": 95, "shares": 1, "created_at": "c"})
    closed = summarize_trade(trade, [], exits)
    assert closed["status"] == "CLOSED"
    assert closed["remaining_shares"] == 0
    assert closed["realized_pnl"] == pytest.approx(-95)


def test_trade_statistics_exclude_open_and_partial_positions_from_win_rate():
    rows = [
        {"status": "CLOSED", "realized_pnl": 10, "realized_return_pct": 10, "r_multiple": 1.0},
        {"status": "CLOSED", "realized_pnl": -5, "realized_return_pct": -5, "r_multiple": -0.5},
        {"status": "PARTIAL", "realized_pnl": 99, "realized_return_pct": 12, "r_multiple": 0.3},
    ]
    stats = summarize_trades(rows)
    assert stats["trade_count"] == 3
    assert stats["closed_count"] == 2
    assert stats["win_rate_pct"] == pytest.approx(50)
    assert stats["avg_win_pct"] == pytest.approx(10)
    assert stats["avg_loss_pct"] == pytest.approx(-5)


def test_signal_outcomes_use_only_bars_after_signal_date_and_fail_closed_when_none():
    signal = {"ticker": "NVDA", "signal_date": "2026-10-20", "signal_price": 100, "candidate_pivot": 108}
    bars = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22"]),
        "High": [120, 109, 121], "Low": [80, 99, 101], "Close": [100, 105, 115],
    })
    result = analyze_pp_signal_outcome(signal, bars)
    assert result["observed_bars"] == 2
    assert result["candidate_pivot_touched"] is True
    assert result["hit_plus_5_pct"] is True
    assert result["hit_plus_10_pct"] is True
    assert result["hit_plus_20_pct"] is True
    assert result["reference_pivot_reached"] is True
    assert result["breakout_reached"] is True
    assert result["breakout_failure_reached"] is False
    assert result["mfe_pct"] == pytest.approx(21)
    assert result["mae_pct"] == pytest.approx(-1)
    no_future = analyze_pp_signal_outcome(signal, bars.iloc[:1])
    assert no_future["mfe_pct"] is None
    assert no_future["hit_plus_10_pct"] is None


def test_signal_outcome_observes_close_breakout_then_two_percent_pivot_failure():
    signal = {"ticker": "NVDA", "signal_date": "2026-10-20", "signal_price": 100,
              "candidate_pivot": 108, "breakout_pivot": 110}
    bars = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]),
        "High": [125, 112, 111, 108], "Low": [90, 100, 104, 102],
        "Close": [100, 111, 109, 107.8],
    })
    result = analyze_pp_signal_outcome(signal, bars)
    assert result["breakout_reached"] is True
    assert result["breakout_failure_reached"] is True
    assert result["breakout_failure_date"] == "2026-10-23"
    # The signal-day spike is excluded: MFE remains based on the later 112 high.
    assert result["mfe_pct"] == pytest.approx(12)


def test_signal_snapshot_metrics_are_optional_and_validated():
    base = {"ticker": "NVDA", "signal_date": "2026-10-22", "signal_price": 180,
            "signal_volume": 5_000_000, "max_prior_down_day_volume": 4_000_000,
            "rs_rating": 86.5, "momentum_score": 77, "pp_quality": "B",
            "market_regime": "🟠 조정 / 방어", "pivot_dist_pct": -4.5}
    saved = normalize_pp_signal(base, user_id="u")
    assert saved["rs_rating"] == pytest.approx(86.5)
    assert saved["momentum_score"] == pytest.approx(77)
    assert saved["pp_quality"] == "B"
    assert saved["pivot_dist_pct"] == pytest.approx(-4.5)
    with pytest.raises(PPJournalValidationError, match="RS는 0~100"):
        normalize_pp_signal({**base, "rs_rating": 120}, user_id="u")
    with pytest.raises(PPJournalValidationError, match="PP Quality"):
        normalize_pp_signal({**base, "pp_quality": "D"}, user_id="u")


def test_tracking_metrics_migration_is_additive_and_does_not_delete_data():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "sql" / "v2_pp_journal_tracking_metrics.sql").read_text().lower()
    for column in ("rs_rating", "momentum_score", "pp_quality", "market_regime", "pivot_dist_pct"):
        assert f"add column if not exists {column}" in sql
    assert "drop table" not in sql
    assert "delete from" not in sql
    assert "update public.v2_pp_signals" not in sql


def test_pp_journal_navigation_has_tracking_trade_analytics_and_removes_unused_tabs():
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    pages = app.split("PAGES = [", 1)[1].split("]", 1)[0]
    assert '"App Monitor"' not in pages
    assert '"ADD Monitor"' not in pages
    assert '"📡 PP Tracking"' in app
    assert '"📝 PP Trade"' in app
    assert '"📊 PP Analytics"' in app
    assert 'elif page == "App Monitor"' not in app



def test_list_signals_falls_back_to_base_schema_before_metrics_migration():
    from dashboard_v2.services.pp_journal_repository import list_pp_signals
    selects = []
    class Response:
        def __init__(self, data): self.data = data
    class Query:
        def __init__(self): self.columns = ""
        def select(self, columns):
            self.columns = columns
            selects.append(columns)
            return self
        def eq(self, *_): return self
        def order(self, *_args, **_kwargs): return self
        def limit(self, *_): return self
        def execute(self):
            if "rs_rating" in self.columns:
                raise RuntimeError("Could not find the 'rs_rating' column in the schema cache")
            return Response([{"ticker": "NVDA", "signal_date": "2026-10-22"}])
    class Client:
        def table(self, _): return Query()
    rows = list_pp_signals(Client(), "user-a")
    assert len(selects) == 2
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["rs_rating"] is None
    assert rows[0]["pp_quality"] is None


def test_signal_repository_deduplicates_by_user_ticker_and_date():
    calls = []
    existing = [{"ticker": "NVDA", "signal_date": "2026-10-22"}]
    class Response:
        def __init__(self, data): self.data = data
    class Query:
        def __init__(self, table): self.table_name = table; self.filters = []; self.rows = None
        def select(self, cols): calls.append(("select", self.table_name, cols)); return self
        def eq(self, key, value): self.filters.append((key, value)); return self
        def in_(self, key, values): self.filters.append((key, tuple(values))); return self
        def insert(self, rows): self.rows = rows; calls.append(("insert", self.table_name, rows)); return self
        def execute(self):
            if self.table_name == "v2_pp_signals" and self.rows is not None: return Response(self.rows)
            return Response(existing)
    class Client:
        def table(self, name): return Query(name)
    base = {"signal_date": "2026-10-22", "signal_price": 100, "signal_volume": 1000,
            "max_prior_down_day_volume": 900, "reference_ma": "MA10", "candidate_pivot": 110}
    result = save_pp_signals(Client(), "u1", [{**base, "ticker": "NVDA"}, {**base, "ticker": "NVDA"}])
    assert result == {"inserted": 0, "duplicates": 2}
    assert not any(call[0] == "insert" for call in calls)


def test_exit_guard_rejects_exit_larger_than_shares_as_of_exit_date():
    trade = {"id": "t1", "initial_entry_date": "2026-10-01", "initial_shares": 10}
    with pytest.raises(PPJournalValidationError, match="잔여 수량"):
        add_pp_trade_exit(object(), "u1", trade, exits=[], adds=[], exit_date="2026-10-02", exit_price=100, shares=11)


def test_pp_journal_schema_has_rls_for_all_tables_and_only_append_grants():
    from pathlib import Path
    schema = (Path(__file__).resolve().parents[1] / "sql" / "v2_pp_journal_schema.sql").read_text().lower()
    for table in ("v2_pp_signals", "v2_pp_trades", "v2_pp_trade_adds", "v2_pp_trade_exits"):
        assert f"alter table public.{table} enable row level security" in schema
        assert f"on public.{table}" in schema
    assert "auth.uid() = user_id" in schema
    assert "grant select, insert" in schema
    assert "grant update" not in schema
    assert "grant delete" not in schema
    assert "new.shares > available_shares" in schema
    assert "v2_pp_trade_signal_ticker_validate" in schema
    assert "public.pp_trades" not in schema


def test_trade_add_and_exit_tables_are_not_legacy_names():
    from dashboard_v2.services.pp_journal_repository import PP_ADDS_TABLE, PP_EXITS_TABLE, PP_SIGNALS_TABLE, PP_TRADES_TABLE
    assert (PP_SIGNALS_TABLE, PP_TRADES_TABLE, PP_ADDS_TABLE, PP_EXITS_TABLE) == (
        "v2_pp_signals", "v2_pp_trades", "v2_pp_trade_adds", "v2_pp_trade_exits"
    )
