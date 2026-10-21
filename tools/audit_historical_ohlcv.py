"""Audit captured daily OHLCV files without making network or DB calls."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from dashboard_v2.engines.data_validation import normalize_ohlcv, MarketDataError

DEFAULT_SYMBOLS = ("AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX")


def audit_symbol(symbol: str, csv_path: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ticker": symbol.upper(), "status": "PENDING", "rows": 0,
        "first_date": "", "last_date": "", "duplicate_dates": 0,
        "zero_volume_rows": 0, "large_close_moves_over_20pct": 0,
        "calendar_gaps_over_5_days": 0, "metadata_row_match": "UNKNOWN",
        "issues": "", "warnings": "",
    }
    errors: list[str] = []
    warnings: list[str] = []
    if not csv_path.exists():
        result.update(status="MISSING_FILE", issues=f"CSV not found: {csv_path}")
        return result

    try:
        raw = pd.read_csv(csv_path)
        date_col = next((c for c in raw.columns if str(c).strip().lower() in {"date", "datetime", "timestamp"}), None)
        if date_col is None:
            errors.append("Missing Date column")
            result.update(status="INVALID_DATA", issues="; ".join(errors))
            return result
        parsed_dates = pd.to_datetime(raw[date_col], errors="coerce")
        result["duplicate_dates"] = int(parsed_dates.duplicated(keep=False).sum())
        if result["duplicate_dates"]:
            errors.append(f"{result['duplicate_dates']} rows belong to duplicate dates")
        try:
            frame = normalize_ohlcv(raw)
        except Exception as exc:
            result.update(status="INVALID_DATA", issues=f"{type(exc).__name__}: {exc}")
            return result

        if frame.empty:
            result.update(status="EMPTY", issues="No usable OHLCV rows")
            return result
        result["rows"] = int(len(frame))
        result["first_date"] = frame.iloc[0]["Date"].date().isoformat()
        result["last_date"] = frame.iloc[-1]["Date"].date().isoformat()

        price_cols = ["Open", "High", "Low", "Close"]
        if (frame[price_cols] <= 0).any().any():
            errors.append("Non-positive OHLC price found")
        result["zero_volume_rows"] = int((frame["Volume"] == 0).sum())
        if result["zero_volume_rows"]:
            warnings.append(f"{result['zero_volume_rows']} rows have zero volume")

        # These are review warnings, not automatic failures: large moves may be genuine.
        pct_change = frame["Close"].pct_change().abs()
        result["large_close_moves_over_20pct"] = int((pct_change > 0.20).sum())
        if result["large_close_moves_over_20pct"]:
            warnings.append(f"{result['large_close_moves_over_20pct']} close-to-close moves exceed 20%; inspect corporate actions/news")
        day_gaps = frame["Date"].diff().dt.days
        result["calendar_gaps_over_5_days"] = int((day_gaps > 5).sum())
        if result["calendar_gaps_over_5_days"]:
            warnings.append(f"{result['calendar_gaps_over_5_days']} calendar gaps exceed 5 days")

        if metadata:
            meta_symbol = (metadata.get("symbols") or {}).get(symbol.upper(), {})
            if meta_symbol.get("status") == "OK":
                match = (
                    int(meta_symbol.get("rows", -1)) == len(frame)
                    and str(meta_symbol.get("first_date", "")) == result["first_date"]
                    and str(meta_symbol.get("last_date", "")) == result["last_date"]
                )
                result["metadata_row_match"] = "MATCH" if match else "MISMATCH"
                if not match:
                    errors.append("CSV rows/date range do not match capture_metadata.json")
            else:
                result["metadata_row_match"] = "NO_OK_METADATA"
                warnings.append("No successful metadata record for this ticker")

        if metadata and metadata.get("auto_adjust") is True:
            warnings.append("auto_adjust=true: verify this price basis matches old dashboard/journal before numeric pivot comparisons")
        elif metadata and metadata.get("auto_adjust") is False:
            warnings.append("auto_adjust=false: verify split/dividend adjustment compatibility before numeric pivot comparisons")

    except Exception as exc:
        result.update(status="AUDIT_ERROR", issues=f"{type(exc).__name__}: {exc}")
        return result

    result["issues"] = "; ".join(errors)
    result["warnings"] = "; ".join(warnings)
    result["status"] = "FAIL" if errors else ("REVIEW" if warnings else "PASS")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ohlcv-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/regression/audit")
    args = parser.parse_args()
    meta_path = args.ohlcv_dir / "capture_metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    results = [audit_symbol(sym, args.ohlcv_dir / f"{sym.upper()}.csv", metadata) for sym in args.symbols]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.output_dir / "ohlcv_audit.csv"
    pd.DataFrame(results).to_csv(rows_path, index=False, encoding="utf-8-sig")
    status_counts = pd.Series([r["status"] for r in results]).value_counts().to_dict()
    summary = {
        "symbols_checked": len(results), "status_counts": status_counts,
        "all_files_present": all(r["status"] not in {"MISSING_FILE", "EMPTY", "INVALID_DATA", "AUDIT_ERROR"} for r in results),
        "all_rows_match_capture_metadata": all(r["metadata_row_match"] == "MATCH" for r in results),
        "important": "REVIEW warnings are not failures. This audit checks data consistency, not provider correctness or adjustment-policy approval.",
        "details_file": str(rows_path),
    }
    summary_path = args.output_dir / "ohlcv_audit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"OHLCV audit: {len(results)} symbols")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"Details: {rows_path}")
    print(f"Summary: {summary_path}")
    return 1 if any(r["status"] in {"FAIL", "MISSING_FILE", "EMPTY", "INVALID_DATA", "AUDIT_ERROR"} for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
