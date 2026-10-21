"""Create an event-date worksheet for >20% close-to-close moves in captured OHLCV."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard_v2.services.historical_review import large_close_move_context

DEFAULT_SYMBOLS = ("AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ohlcv-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--threshold", type=float, default=0.20, help="Absolute close-to-close threshold; default 0.20 = 20%%")
    parser.add_argument("--context-bars", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "data/regression/audit/large_close_move_review.csv")
    args = parser.parse_args()
    all_rows = []
    missing = []
    for symbol in args.symbols:
        path = args.ohlcv_dir / f"{symbol.upper()}.csv"
        if not path.exists():
            missing.append(symbol.upper())
            continue
        all_rows.extend(large_close_move_context(symbol, pd.read_csv(path), threshold=args.threshold, context_bars=args.context_bars))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["ticker", "event_date", "event_previous_date", "event_previous_close", "event_close", "event_change_pct",
               "context_offset_bars", "context_date", "Open", "High", "Low", "Close", "Volume", "is_event_bar", "price_basis_note"]
    pd.DataFrame(all_rows, columns=columns).to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Large close-move review: {len(all_rows)} context rows across {len(args.symbols) - len(missing)} tickers")
    if missing:
        print("Missing CSVs: " + ", ".join(missing))
    print(f"Output: {args.output}")
    print("The report identifies dates and nearby bars only; it does not infer the cause of a move.")
    return 1 if missing else 0
if __name__ == "__main__":
    raise SystemExit(main())
