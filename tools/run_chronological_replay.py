"""Run sequential OHLCV state replay for known rebase/pivot regression symbols."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard_v2.services.chronological_replay import replay_symbol

DEFAULT_SYMBOLS = ("AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/regression/chronological_replay.csv")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    all_rows = []
    missing = []
    for symbol in args.symbols:
        path = args.input_dir / f"{symbol.upper()}.csv"
        if not path.exists():
            missing.append(symbol.upper())
            print(f"{symbol.upper()}: MISSING DATA ({path})")
            continue
        frame = pd.read_csv(path)
        replay = replay_symbol(symbol, frame)
        all_rows.append(replay)
        print(f"{symbol.upper()}: replayed {len(replay)} completed bars")
    if all_rows:
        result = pd.concat(all_rows, ignore_index=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output, index=False)
        print(f"Replay output: {args.output}")
    if missing:
        print("Missing OHLCV files: " + ", ".join(missing))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
