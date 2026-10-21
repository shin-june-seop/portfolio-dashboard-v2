from datetime import date, timedelta
from pathlib import Path
import json

import pandas as pd

from tools.audit_historical_ohlcv import audit_symbol
from tools.build_rebase_review import build_review_rows


def valid_bars(n=90):
    dates = []
    day = date(2024, 1, 2)
    while len(dates) < n:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    # Calm trend then a >10% fall from the 20-bar high, forming one RB-01 proxy.
    close = [100 + 0.15 * i for i in range(55)]
    close.extend([close[-1] * (1 - 0.018 * (i + 1)) for i in range(n - len(close))])
    close = close[:n]
    rows = []
    for d, c in zip(dates, close):
        rows.append({"Date": d.isoformat(), "Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000})
    return pd.DataFrame(rows)


def test_audit_passes_valid_csv_when_no_metadata(tmp_path):
    path = tmp_path / "AMAT.csv"
    valid_bars().to_csv(path, index=False)
    result = audit_symbol("AMAT", path)
    assert result["status"] == "PASS"
    assert result["rows"] == 90


def test_audit_detects_metadata_mismatch(tmp_path):
    path = tmp_path / "AMAT.csv"
    bars = valid_bars()
    bars.to_csv(path, index=False)
    metadata = {"auto_adjust": True, "symbols": {"AMAT": {"status": "OK", "rows": 88, "first_date": "2024-01-02", "last_date": bars.iloc[-1]["Date"]}}}
    result = audit_symbol("AMAT", path, metadata)
    assert result["status"] == "FAIL"
    assert result["metadata_row_match"] == "MISMATCH"


def test_audit_checks_missing_file(tmp_path):
    result = audit_symbol("AMAT", tmp_path / "missing.csv")
    assert result["status"] == "MISSING_FILE"


def test_rebase_review_rows_are_observations_not_ground_truth():
    rows = build_review_rows("AMAT", valid_bars())
    assert rows
    assert all(row["review_status"] == "REVIEW_REQUIRED" for row in rows)
    assert all("not ground truth" in row["limitation"].lower() for row in rows)
    assert all("ungated_pattern" in row and "rebase_gated_pattern" in row for row in rows)


def test_rebase_review_does_not_create_rows_for_short_history():
    assert build_review_rows("AMAT", valid_bars(10)) == []


def test_large_close_move_review_includes_event_date_and_neighbor_context():
    from dashboard_v2.services.historical_review import large_close_move_context
    closes = [100.0, 101.0, 130.0, 128.0, 127.0]
    rows = []
    for i, close in enumerate(closes):
        rows.append({"Date": f"2024-01-{i + 2:02d}", "Open": close, "High": close * 1.01,
                     "Low": close * 0.99, "Close": close, "Volume": 1000})
    result = large_close_move_context("META", pd.DataFrame(rows), threshold=0.20, context_bars=1)
    assert result
    event_rows = [r for r in result if r["is_event_bar"]]
    assert len(event_rows) == 1
    assert event_rows[0]["event_date"] == "2024-01-04"
    assert event_rows[0]["event_change_pct"] > 20
    assert {r["context_offset_bars"] for r in result} == {-1, 0, 1}
    assert "does not determine" in event_rows[0]["price_basis_note"]


def test_rebase_summary_marks_stale_pattern_and_first_sampled_new_pattern():
    from dashboard_v2.services.historical_review import summarize_rebase_review
    label = "BASE FORMING / NO VALID PATTERN"
    frame = pd.DataFrame([
        {"ticker": "AMAT", "cycle_id": "AMAT_RB01_001", "rebase_started_at_rb01_proxy": "2024-03-01",
         "observation_date": "2024-03-01", "offset_bars_from_trigger": 0, "close_vs_20d_high_pct": -12.0,
         "ungated_pattern": "Double Bottom", "ungated_candidate_pivot": 120, "ungated_pattern_start": "2024-01-01",
         "rebase_gated_pattern": label, "rebase_gated_candidate_pivot": "", "rebase_gated_pattern_start": ""},
        {"ticker": "AMAT", "cycle_id": "AMAT_RB01_001", "rebase_started_at_rb01_proxy": "2024-03-01",
         "observation_date": "2024-04-15", "offset_bars_from_trigger": 34, "close_vs_20d_high_pct": -2.0,
         "ungated_pattern": "Double Bottom", "ungated_candidate_pivot": 120, "ungated_pattern_start": "2024-01-01",
         "rebase_gated_pattern": "Flat Base", "rebase_gated_candidate_pivot": 115, "rebase_gated_pattern_start": "2024-03-01"},
    ])
    rows = summarize_rebase_review(frame)
    assert len(rows) == 1
    row = rows[0]
    assert row["ungated_pattern_predates_trigger"] is True
    assert row["gated_pattern_at_trigger"] == label
    assert row["first_sampled_valid_post_rebase_pattern"] == "Flat Base"
    assert row["first_sampled_valid_post_rebase_pattern_offset"] == 34
    assert row["review_status"] == "REVIEW_REQUIRED"
    assert row["expected_pattern_manual"] == ""
