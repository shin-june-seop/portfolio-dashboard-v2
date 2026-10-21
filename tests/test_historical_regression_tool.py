from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

from tools.run_historical_regression import run_case


def write_bars(path: Path):
    n = 80
    x = np.arange(n)
    close = np.concatenate([
        np.linspace(50, 100, 50),
        100 + 1.0 * np.sin(np.arange(30) / 2),
    ])
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    pd.DataFrame({
        "Date": dates,
        "Open": close,
        "High": close + 2,
        "Low": close - 2,
        "Close": close,
        "Volume": np.full(n, 100.0),
    }).to_csv(path, index=False)


def test_regression_case_requires_explicit_date(tmp_path):
    result = run_case({"case_id": "x", "ticker": "AMAT", "as_of_date": "", "scenario": "test"}, tmp_path)
    assert result["status"] == "NEEDS_LABEL"


def test_regression_case_reports_missing_fixture(tmp_path):
    result = run_case({"case_id": "x", "ticker": "AMAT", "as_of_date": "2024-04-01", "scenario": "test"}, tmp_path)
    assert result["status"] == "MISSING_OHLCV"


def test_regression_case_observes_but_does_not_claim_validation_without_oracle(tmp_path):
    write_bars(tmp_path / "AMAT.csv")
    result = run_case({"case_id": "x", "ticker": "AMAT", "as_of_date": "2024-04-01", "scenario": "test"}, tmp_path)
    assert result["status"] == "OBSERVED_NOT_VALIDATED"
    assert result["bar_count"] > 0
    assert result["last_bar_date"] <= "2024-04-01"


def test_regression_case_matches_a_labelled_expected_pattern(tmp_path):
    write_bars(tmp_path / "AMAT.csv")
    # Fixture produces a flat-base candidate under the synthetic pattern test setup.
    result = run_case({
        "case_id": "x", "ticker": "AMAT", "as_of_date": "2024-04-01", "scenario": "test",
        "expected_pattern": "Flat Base", "expected_candidate_pivot_min": "90", "expected_candidate_pivot_max": "110",
    }, tmp_path)
    assert result["status"] == "PASS"


def test_regression_case_flags_wrong_expected_pattern(tmp_path):
    write_bars(tmp_path / "AMAT.csv")
    result = run_case({
        "case_id": "x", "ticker": "AMAT", "as_of_date": "2024-04-01", "scenario": "test",
        "expected_pattern": "Cup & Handle",
    }, tmp_path)
    assert result["status"] == "FAIL"
