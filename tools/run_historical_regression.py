"""Replay manually labelled historical chart cases against V2 pattern detection.

No network or database calls. Input is a user-reviewed manifest plus per-ticker
canonical OHLCV CSVs in data/regression/ohlcv/<TICKER>.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from dashboard_v2.engines.data_validation import normalize_ohlcv
from dashboard_v2.engines.pattern_detectors import detect_pattern_candidates
from dashboard_v2.engines.pattern_engine import select_primary_pattern


def _float_or_none(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    return float(value)


def run_case(case: dict[str, str], data_dir: Path) -> dict[str, Any]:
    case_id = case.get("case_id", "").strip()
    ticker = case.get("ticker", "").strip().upper()
    result: dict[str, Any] = {
        "case_id": case_id,
        "ticker": ticker,
        "scenario": case.get("scenario", ""),
        "as_of_date": case.get("as_of_date", ""),
        "status": "PENDING",
        "bar_count": 0,
        "last_bar_date": "",
        "observed_pattern": "",
        "observed_candidate_pivot": "",
        "observed_pattern_start": "",
        "all_valid_candidates": "[]",
        "verification_detail": "",
    }
    as_of = case.get("as_of_date", "").strip()
    if not case_id or not ticker:
        result.update(status="INVALID_MANIFEST", verification_detail="case_id and ticker are required")
        return result
    if not as_of:
        result.update(status="NEEDS_LABEL", verification_detail="as_of_date is blank; do not infer a date")
        return result

    file_path = data_dir / f"{ticker}.csv"
    if not file_path.exists():
        result.update(status="MISSING_OHLCV", verification_detail=f"Expected CSV: {file_path}")
        return result
    try:
        frame = normalize_ohlcv(pd.read_csv(file_path), as_of_date=as_of)
    except Exception as exc:
        result.update(status="INVALID_OHLCV", verification_detail=f"{type(exc).__name__}: {exc}")
        return result
    if frame.empty:
        result.update(status="NO_BARS_THROUGH_DATE", verification_detail="No valid completed bars on/before as_of_date")
        return result

    rebase_date = case.get("rebase_started_at", "").strip() or None
    prior_pivot = _float_or_none(case.get("prior_breakout_pivot"))
    prior_breakout_date = case.get("prior_breakout_date", "").strip() or None
    candidates = detect_pattern_candidates(
        frame,
        rebase_started_at=rebase_date,
        prior_breakout_pivot=prior_pivot,
        prior_breakout_date=prior_breakout_date,
    )
    primary = select_primary_pattern(candidates)
    result.update(
        bar_count=len(frame),
        last_bar_date=frame.iloc[-1]["Date"].date().isoformat(),
        observed_pattern=primary.pattern_name if primary else "BASE FORMING / NO VALID PATTERN",
        observed_candidate_pivot=round(float(primary.candidate_pivot), 4) if primary and primary.candidate_pivot else "",
        observed_pattern_start=primary.pattern_start_date.isoformat() if primary and primary.pattern_start_date else "",
        all_valid_candidates=json.dumps([
            {"pattern": c.pattern_name, "pivot": round(float(c.candidate_pivot), 4),
             "start": c.pattern_start_date.isoformat() if c.pattern_start_date else None,
             "reasons": list(c.reason_codes), "diagnostics": c.diagnostics}
            for c in candidates
        ], ensure_ascii=False, default=str),
    )

    expected_pattern = case.get("expected_pattern", "").strip()
    pivot_min = _float_or_none(case.get("expected_candidate_pivot_min"))
    pivot_max = _float_or_none(case.get("expected_candidate_pivot_max"))
    checks: list[str] = []
    if expected_pattern:
        actual = primary.pattern_name if primary else "BASE FORMING / NO VALID PATTERN"
        if actual.casefold() != expected_pattern.casefold():
            checks.append(f"pattern expected={expected_pattern!r}, observed={actual!r}")
    if pivot_min is not None or pivot_max is not None:
        if primary is None:
            checks.append("expected pivot range but no primary pattern was detected")
        else:
            pivot = float(primary.candidate_pivot)
            if pivot_min is not None and pivot < pivot_min:
                checks.append(f"pivot {pivot:.4f} below expected minimum {pivot_min:.4f}")
            if pivot_max is not None and pivot > pivot_max:
                checks.append(f"pivot {pivot:.4f} above expected maximum {pivot_max:.4f}")
    if not expected_pattern and pivot_min is None and pivot_max is None:
        result.update(status="OBSERVED_NOT_VALIDATED", verification_detail="Observed output only; label expected pattern/pivot after chart review")
    elif checks:
        result.update(status="FAIL", verification_detail="; ".join(checks))
    else:
        result.update(status="PASS", verification_detail="All labelled pattern/pivot expectations matched")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/regression/manifest.csv")
    parser.add_argument("--ohlcv-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/regression/results.csv")
    args = parser.parse_args()
    with args.manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        cases = list(csv.DictReader(handle))
    results = [run_case(case, args.ohlcv_dir) for case in cases]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(args.output, index=False, encoding="utf-8-sig")
    counts = pd.Series([r["status"] for r in results]).value_counts().to_dict()
    print(f"Historical regression cases: {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
    print(f"Results written: {args.output}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
