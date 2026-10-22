"""Download Yahoo EOD OHLCV plus Adj Close for manual chart-regression review.

This writes local CSVs only. It does not connect to Supabase or submit orders.
The captured bars follow DATA_SOURCE_POLICY.md and must still be manually
labelled against independent chart evidence before a case is called validated.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider
DEFAULT_SYMBOLS = ("AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--history-days", type=int, default=5 * 365)
    args = parser.parse_args()

    provider = YFinanceDailyProvider(history_days=args.history_days)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "yfinance/Yahoo Finance (V2 EOD policy; upstream may revise data)",
        "technical_price_basis": "YAHOO_OHLC_AUTO_ADJUST_FALSE",
        "return_price_basis": "YAHOO_ADJ_CLOSE_SPLIT_AND_DISTRIBUTION_ADJUSTED",
        "auto_adjust": False,
        "interval": "1d",
        "historical_download_policy": "latest expected XNYS session required; current session withheld until 16:15 America/New_York; explicit historical cutoff must contain the expected prior/current XNYS session",
        "symbols": {},
        "important": "OHLC is used for patterns/pivots; Adj Close is used for returns/RS. Review all bars around each labelled event before treating the event as regression ground truth.",
    }
    errors = 0
    for symbol in args.symbols:
        ticker = symbol.upper().strip()
        try:
            bars = provider.daily_bars(ticker)
            if bars.empty:
                errors += 1
                metadata["symbols"][ticker] = {"status": "EMPTY", "rows": 0}
                print(f"{ticker}: no rows returned")
                continue
            out = args.output_dir / f"{ticker}.csv"
            bars.to_csv(out, index=False, date_format="%Y-%m-%d")
            metadata["symbols"][ticker] = {
                "status": "OK", "rows": int(len(bars)),
                "first_date": bars.iloc[0]["Date"].date().isoformat(),
                "last_date": bars.iloc[-1]["Date"].date().isoformat(),
                "file": out.name,
            }
            print(f"{ticker}: {len(bars)} bars → {out}")
        except Exception as exc:
            errors += 1
            metadata["symbols"][ticker] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            print(f"{ticker}: ERROR {type(exc).__name__}: {exc}")
    metadata_path = args.output_dir / "capture_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Metadata: {metadata_path}")
    print("Reminder: capture is not a labelled regression test; fill manifest dates and expected outcomes after chart review.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
